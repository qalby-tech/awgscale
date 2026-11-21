// Package control is how the app and the command line talk to the service: one
// JSON request per connection over a local channel that only the people who may
// run the VPN can open (Linux: a Unix socket for root and the group awgscale;
// Windows: a named pipe of SYSTEM for the people logged on at the machine).
// Whoever may talk to it may send all traffic of the machine anywhere, so the
// channel is the boundary.
package control

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/netip"
	"time"

	"github.com/qalby-tech/awgscale/app/qalby/conf"
	"github.com/qalby-tech/awgscale/app/qalby/tunnel"
)

const (
	requestLimit = 64 << 10
	ioTimeout    = 60 * time.Second
)

// Command is what a request asks.
type Command string

const (
	CommandUp     Command = "up"
	CommandDown   Command = "down"
	CommandStatus Command = "status"
)

// Request is one question to the service.
type Request struct {
	Command Command `json:"command"`
	// Conf is the device's tunnel config, for up only.
	Conf string `json:"conf,omitempty"`
	// KillSwitch: for up, nothing leaves the machine around the tunnel while it is on.
	KillSwitch bool `json:"kill_switch,omitempty"`
	// LocalAround: for up, the local networks (conf.LocalNetworks) go around the tunnel.
	LocalAround bool `json:"local_around,omitempty"`
	// Around: for up, more networks around the tunnel: addresses or prefixes.
	Around []string `json:"around,omitempty"`
}

// aroundLimit: a list longer than this is not a person's list.
const aroundLimit = 64

// around reads what goes around the tunnel; anything that is not an address or a prefix, or a
// prefix of everything, is refused.
func around(request Request) ([]netip.Prefix, error) {
	if len(request.Around) > aroundLimit {
		return nil, errors.New("слишком много сетей мимо VPN")
	}
	var prefixes []netip.Prefix
	if request.LocalAround {
		prefixes = append(prefixes, conf.LocalNetworks...)
	}
	for _, text := range request.Around {
		prefix, err := netip.ParsePrefix(text)
		if err != nil {
			address, failed := netip.ParseAddr(text)
			if failed != nil {
				return nil, fmt.Errorf("не сеть: %q", text)
			}
			prefix = netip.PrefixFrom(address, address.BitLen())
		}
		if prefix.Bits() == 0 {
			return nil, errors.New("мимо VPN нельзя отправить всё")
		}
		prefixes = append(prefixes, prefix.Masked())
	}
	return prefixes, nil
}

// Answer is the service's answer: the tunnel's status, or why not.
type Answer struct {
	OK     bool           `json:"ok"`
	Error  string         `json:"error,omitempty"`
	Status *tunnel.Status `json:"status,omitempty"`
}

// Serve answers on the service's channel (`path`) until it fails.
func Serve(path string, machine *tunnel.Tunnel) error {
	listener, err := listen(path)
	if err != nil {
		return err
	}
	defer listener.Close()
	for {
		connection, err := listener.Accept()
		if err != nil {
			return err
		}
		go answer(connection, machine)
	}
}

func answer(connection net.Conn, machine *tunnel.Tunnel) {
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(ioTimeout))
	reply := func(answer Answer) { _ = json.NewEncoder(connection).Encode(answer) }
	if !mayControl(connection) {
		reply(Answer{Error: refusedText})
		return
	}
	var request Request
	if err := json.NewDecoder(io.LimitReader(connection, requestLimit)).Decode(&request); err != nil {
		reply(Answer{Error: "непонятный запрос"})
		return
	}
	switch request.Command {
	case CommandUp:
		config, err := conf.Parse(request.Conf)
		if err != nil {
			reply(Answer{Error: "конфиг устройства не подходит: " + err.Error()})
			return
		}
		prefixes, err := around(request)
		if err != nil {
			reply(Answer{Error: err.Error()})
			return
		}
		if err := machine.Up(config, tunnel.Options{KillSwitch: request.KillSwitch, Around: prefixes}); err != nil {
			reply(Answer{Error: err.Error()})
			return
		}
	case CommandDown:
		machine.Down()
	case CommandStatus:
	default:
		reply(Answer{Error: "непонятный запрос"})
		return
	}
	status := machine.Status()
	reply(Answer{OK: true, Status: &status})
}

// Ask sends one request to the service at `path`.
func Ask(path string, request Request) (*Answer, error) {
	connection, err := dial(path)
	if err != nil {
		return nil, err
	}
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(ioTimeout))
	if err := json.NewEncoder(connection).Encode(request); err != nil {
		return nil, err
	}
	var answer Answer
	if err := json.NewDecoder(connection).Decode(&answer); err != nil {
		return nil, errorText("служба awgscale ответила непонятно")
	}
	return &answer, nil
}

type errorText string

func (text errorText) Error() string { return string(text) }
