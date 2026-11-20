// Package control is how the app and the command line talk to the service: one
// JSON request per connection over a local socket that only root and the group
// awgscale may use. Whoever may talk to it may send all traffic of the machine
// anywhere, so the socket is the boundary, and its peer is checked again.
package control

import (
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"time"

	"github.com/qalby-tech/awgscale/app/qalby/conf"
	"github.com/qalby-tech/awgscale/app/qalby/tunnel"
)

// Group is the group whose members may run the tunnel (the package adds the desktop's users to it).
const Group = "awgscale"

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
}

// Answer is the service's answer: the tunnel's status, or why not.
type Answer struct {
	OK     bool           `json:"ok"`
	Error  string         `json:"error,omitempty"`
	Status *tunnel.Status `json:"status,omitempty"`
}

// Serve answers on `path` until the listener fails.
func Serve(path string, machine *tunnel.Tunnel) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	_ = os.Remove(path)
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		return err
	}
	defer listener.Close()
	allowed := allowedGroup()
	if err := protect(path, allowed); err != nil {
		return err
	}
	for {
		connection, err := listener.AcceptUnix()
		if err != nil {
			return err
		}
		go answer(connection, machine, allowed)
	}
}

// protect: the socket is root's and, when the group exists, its group's; nobody else's.
func protect(path string, group int) error {
	if group < 0 {
		return os.Chmod(path, 0o600)
	}
	if err := os.Chown(path, 0, group); err != nil {
		return err
	}
	return os.Chmod(path, 0o660)
}

func allowedGroup() int {
	found, err := user.LookupGroup(Group)
	if err != nil {
		return -1
	}
	number, err := strconv.Atoi(found.Gid)
	if err != nil {
		return -1
	}
	return number
}

func answer(connection *net.UnixConn, machine *tunnel.Tunnel, group int) {
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(ioTimeout))
	reply := func(answer Answer) { _ = json.NewEncoder(connection).Encode(answer) }
	if !mayControl(connection, group) {
		reply(Answer{Error: "нет прав управлять VPN: нужен root или группа " + Group})
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
		if err := machine.Up(config); err != nil {
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
	connection, err := net.DialTimeout("unix", path, 5*time.Second)
	if err != nil {
		return nil, errors.New("служба awgscale не запущена")
	}
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(ioTimeout))
	if err := json.NewEncoder(connection).Encode(request); err != nil {
		return nil, err
	}
	var answer Answer
	if err := json.NewDecoder(connection).Decode(&answer); err != nil {
		return nil, errors.New("служба awgscale ответила непонятно")
	}
	return &answer, nil
}
