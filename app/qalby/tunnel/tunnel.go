// Package tunnel brings one device's AmneziaWG tunnel up and down, with
// amneziawg-go inside this process; the network of the system (addresses,
// routes, DNS) is set by the part of each platform (linux.go, windows.go).
package tunnel

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"github.com/amnezia-vpn/amneziawg-go/v3/device"

	"github.com/qalby-tech/awgscale/app/qalby/conf"
)

// InterfaceName is the tunnel's interface on every system.
const InterfaceName = "awgscale0"

// Mark is the firewall mark of the tunnel's own packets and the number of its routing table.
const Mark = 51830

const (
	resolveTimeout   = 15 * time.Second
	handshakeTimeout = 20 * time.Second
	handshakePoll    = 200 * time.Millisecond
)

// State is what the app shows.
type State string

const (
	Down       State = "down"
	Connecting State = "connecting"
	Up         State = "up"
)

// DNSState says whether the network's DNS answers this machine's names.
type DNSState string

const (
	DNSSet       DNSState = "set"       // systemd-resolved asks the network's DNS for every name
	DNSUnmanaged DNSState = "unmanaged" // no systemd-resolved: the system keeps its own DNS
	DNSFailed    DNSState = "failed"    // systemd-resolved refused
	DNSNone      DNSState = "none"      // the config names no DNS
)

// handshakeFresh: a handshake older than this means the server stopped answering (RejectAfterTime:
// keys older than that are refused by both ends, and a live tunnel with keepalive renews them sooner).
// AWGSCALE_HANDSHAKE_FRESH (seconds) shortens it for the tests only.
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
	// Endpoint is the server's address the tunnel talks to.
	Endpoint string   `json:"endpoint,omitempty"`
	DNS      DNSState `json:"dns,omitempty"`
}

// Tunnel is the one tunnel of this machine.
type Tunnel struct {
	mutex    sync.Mutex
	device   *device.Device
	endpoint netip.AddrPort
	network  *systemNetwork
	shown    State // the state last reported, so that a change is put in the record once
}

// New gives the tunnel, after taking away what a tunnel of an earlier run left behind.
func New() *Tunnel {
	cleanup()
	return &Tunnel{}
}

// Options are the person's choices that are not in the config.
type Options struct {
	// KillSwitch: while the tunnel is on, nothing leaves the machine around it but Around.
	KillSwitch bool `json:"kill_switch"`
	// Around: networks that go around the tunnel, by the way they went before it.
	Around []netip.Prefix `json:"around"`
}

// Up replaces any tunnel there is with the one of `config`, and returns once the server answered.
func (tunnel *Tunnel) Up(config *conf.Config, options Options) error {
	tunnel.mutex.Lock()
	defer tunnel.mutex.Unlock()
	Event("up: server %s:%d, kill switch %v, %d networks around", config.Peer.EndpointHost, config.Peer.EndpointPort, options.KillSwitch, len(options.Around))
	err := tunnel.upLocked(config, options)
	if err != nil {
		Event("up failed: %v", err)
		tunnel.downLocked()
		return err
	}
	Event("up: the server answered (%s)", tunnel.endpoint)
	return nil
}

func (tunnel *Tunnel) upLocked(config *conf.Config, options Options) error {
	tunnel.downLocked()
	endpoint, err := resolve(config.Peer)
	if err != nil {
		return err
	}
	network, err := newNetwork() // on Windows it must watch for the adapter before the adapter is made
	if err != nil {
		return fmt.Errorf("не удалось настроить сеть: %w", err)
	}
	tunnel.network = network
	tunDevice, err := createDevice(config.MTU)
	if err != nil {
		return fmt.Errorf("не удалось создать интерфейс: %w", err)
	}
	bind := conn.NewDefaultBind()
	tunnelDevice := device.NewDevice(tunDevice, bind, logger())
	tunnel.device = tunnelDevice
	tunnel.endpoint = endpoint
	if err := tunnelDevice.IpcSet(config.UAPI(endpoint, Mark)); err != nil {
		return errors.New("сервер прислал конфиг, который не подходит этой версии")
	}
	if err := tunnelDevice.Up(); err != nil {
		return fmt.Errorf("не удалось включить интерфейс: %w", err)
	}
	if err := network.configure(config, endpoint, tunDevice, bind, options); err != nil {
		return fmt.Errorf("не удалось настроить сеть: %w", err)
	}
	return tunnel.awaitHandshake()
}

// Down takes the tunnel away and gives the system its network back.
func (tunnel *Tunnel) Down() {
	tunnel.mutex.Lock()
	defer tunnel.mutex.Unlock()
	if tunnel.device != nil {
		Event("down: asked")
	}
	tunnel.downLocked()
}

func (tunnel *Tunnel) downLocked() {
	if tunnel.network != nil {
		tunnel.network.undo()
		tunnel.network = nil
	}
	if tunnel.device != nil {
		tunnel.device.Close()
		tunnel.device = nil
	}
	cleanup()
}

// Status says what the tunnel is doing.
func (tunnel *Tunnel) Status() Status {
	tunnel.mutex.Lock()
	defer tunnel.mutex.Unlock()
	if tunnel.device == nil {
		tunnel.shown = Down
		return Status{State: Down}
	}
	status := readStatus(tunnel.device)
	status.Endpoint = tunnel.endpoint.String()
	if tunnel.network != nil {
		status.DNS = tunnel.network.dnsState()
	}
	status.State = Connecting
	if status.Handshake != 0 && time.Since(time.Unix(status.Handshake, 0)) < handshakeFresh {
		status.State = Up
	}
	if status.State != tunnel.shown {
		Event("state: %s (last handshake %d s ago)", status.State, time.Now().Unix()-status.Handshake)
		tunnel.shown = status.State
	}
	return status
}

func (tunnel *Tunnel) awaitHandshake() error {
	deadline := time.Now().Add(handshakeTimeout)
	for time.Now().Before(deadline) {
		if readStatus(tunnel.device).Handshake != 0 {
			return nil
		}
		time.Sleep(handshakePoll)
	}
	return errors.New("сервер не ответил: нет связи с ним или ключ устройства больше не действует")
}

// logger: the library's errors go to the service's record (Event); AWGSCALE_VERBOSE=1 adds everything
// the library tells, and AWGSCALE_LOG=<file> sends the record to that file: for finding out why a
// tunnel does not carry what it should.
func logger() *device.Logger {
	if path := os.Getenv("AWGSCALE_LOG"); path != "" {
		_ = LogTo(path)
	}
	errorf := func(format string, arguments ...any) { Event("library: "+format, arguments...) }
	if os.Getenv("AWGSCALE_VERBOSE") == "1" {
		return &device.Logger{Verbosef: errorf, Errorf: errorf}
	}
	return &device.Logger{Verbosef: device.DiscardLogf, Errorf: errorf}
}

// resolve finds the server's address before the routes change, through the system's own DNS.
func resolve(peer conf.Peer) (netip.AddrPort, error) {
	if address, err := netip.ParseAddr(peer.EndpointHost); err == nil {
		return netip.AddrPortFrom(address.Unmap(), peer.EndpointPort), nil
	}
	lookup, cancel := context.WithTimeout(context.Background(), resolveTimeout)
	defer cancel()
	addresses, err := net.DefaultResolver.LookupNetIP(lookup, "ip", peer.EndpointHost)
	if err != nil || len(addresses) == 0 {
		return netip.AddrPort{}, fmt.Errorf("не удалось найти сервер %s", peer.EndpointHost)
	}
	chosen := addresses[0]
	for _, address := range addresses {
		if address.Unmap().Is4() {
			chosen = address // the tunnel's own packets go by IPv4 when the server has it
			break
		}
	}
	return netip.AddrPortFrom(chosen.Unmap(), peer.EndpointPort), nil
}

// readStatus reads the device's own report (UAPI get): the handshake and the bytes.
func readStatus(tunnelDevice *device.Device) Status {
	var status Status
	report, err := tunnelDevice.IpcGet()
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
	return status
}
