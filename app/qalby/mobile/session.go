//go:build linux

package mobile

import (
	"bufio"
	"errors"
	"fmt"
	"net/netip"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"github.com/amnezia-vpn/amneziawg-go/v3/device"
	"github.com/amnezia-vpn/amneziawg-go/v3/tun"

	"github.com/qalby-tech/awgscale/app/qalby/conf"
)

// State is what the app shows (TunnelState of app/core).
type State string

const (
	Down       State = "down"
	Connecting State = "connecting"
	Up         State = "up"
)

// handshakeFresh: a handshake older than this means the server stopped answering; the same rule as
// the computer's service (handshakeFresh in app/qalby/tunnel), AWGSCALE_HANDSHAKE_FRESH included.
var handshakeFresh = freshness()

func freshness() time.Duration {
	if seconds, err := strconv.Atoi(os.Getenv("AWGSCALE_HANDSHAKE_FRESH")); err == nil && seconds > 0 {
		return time.Duration(seconds) * time.Second
	}
	return 180 * time.Second
}

// Status is the tunnel as it is now.
type Status struct {
	State State `json:"state"`
	// Handshake is the moment of the last handshake, Unix seconds; 0 is none yet.
	Handshake int64 `json:"handshake"`
	Received  int64 `json:"rx"`
	Sent      int64 `json:"tx"`
}

// Session is one running tunnel on an interface Android made.
type Session struct {
	device *device.Device
	bind   conn.Bind
}

// Logger gets the library's errors (the app sends them to Android's log).
var Logger = func(string) {}

// Start runs the tunnel of `text` on the interface `descriptor` (Builder.establish().detachFd(),
// owned by the session from now on), talking to the server at `endpoint` (an address: Android
// resolved its name before the interface took the routes). The config is checked as on a computer;
// no firewall mark: the tunnel's own sockets are kept out of it by VpnService.protect (Sockets).
func Start(descriptor int, text string, endpoint string) (*Session, error) {
	config, err := conf.Parse(text)
	if err != nil {
		return nil, err
	}
	server, err := netip.ParseAddrPort(endpoint)
	if err != nil {
		return nil, errors.New("bad endpoint")
	}
	tunDevice, _, err := tun.CreateUnmonitoredTUNFromFD(descriptor)
	if err != nil {
		return nil, err
	}
	bind := conn.NewDefaultBind()
	errorf := func(format string, arguments ...any) { Logger(fmt.Sprintf(format, arguments...)) }
	session := &Session{device: device.NewDevice(tunDevice, bind, &device.Logger{Verbosef: device.DiscardLogf, Errorf: errorf}), bind: bind}
	if err := session.device.IpcSet(config.UAPI(server, 0)); err != nil {
		session.Stop()
		return nil, errors.New("сервер прислал конфиг, который не подходит этой версии")
	}
	if err := session.device.Up(); err != nil {
		session.Stop()
		return nil, err
	}
	return session, nil
}

// Sockets are the descriptors of the tunnel's own UDP sockets (-1 where there is none), for
// VpnService.protect: their packets go by the network, not into the tunnel.
func (session *Session) Sockets() (int, int) {
	peek, ok := session.bind.(conn.PeekLookAtSocketFd)
	if !ok {
		return -1, -1
	}
	four, err := peek.PeekLookAtSocketFd4()
	if err != nil {
		four = -1
	}
	six, err := peek.PeekLookAtSocketFd6()
	if err != nil {
		six = -1
	}
	return four, six
}

// Status reads the device's own report (UAPI get): the handshake and the bytes; up only while the
// last handshake is fresh.
func (session *Session) Status() Status {
	status := Status{State: Connecting}
	report, err := session.device.IpcGet()
	if err != nil {
		return status
	}
	scanner := bufio.NewScanner(strings.NewReader(report))
	for scanner.Scan() {
		key, value, found := strings.Cut(scanner.Text(), "=")
		if !found {
			continue
		}
		number, _ := strconv.ParseInt(value, 10, 64)
		switch key {
		case "last_handshake_time_sec":
			status.Handshake = number
		case "rx_bytes":
			status.Received = number
		case "tx_bytes":
			status.Sent = number
		}
	}
	if status.Handshake != 0 && time.Since(time.Unix(status.Handshake, 0)) < handshakeFresh {
		status.State = Up
	}
	return status
}

// Stop takes the tunnel away; the interface closes with it.
func (session *Session) Stop() {
	session.device.Close()
}

// sessions: the running tunnels by the number the app holds (one at a time in practice).
var sessions = struct {
	sync.Mutex
	next int
	all  map[int]*Session
}{all: map[int]*Session{}}

// Keep gives the session a number for the app.
func Keep(session *Session) int {
	sessions.Lock()
	defer sessions.Unlock()
	sessions.next++
	sessions.all[sessions.next] = session
	return sessions.next
}

// Find is the session of a number, or nil.
func Find(handle int) *Session {
	sessions.Lock()
	defer sessions.Unlock()
	return sessions.all[handle]
}

// Forget stops the session of a number and forgets it.
func Forget(handle int) {
	sessions.Lock()
	session := sessions.all[handle]
	delete(sessions.all, handle)
	sessions.Unlock()
	if session != nil {
		session.Stop()
	}
}
