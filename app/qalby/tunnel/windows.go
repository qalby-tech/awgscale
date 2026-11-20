//go:build windows

package tunnel

import (
	"errors"
	"net/netip"

	"github.com/amnezia-vpn/amneziawg-go/v3/tun"

	"github.com/qalby-tech/awgscale/app/qalby/conf"
)

// On Windows the interface is a Wintun adapter; its addresses, routes and DNS
// are not set yet (winipcfg), so the tunnel refuses rather than half-works.
func createDevice(mtu int) (tun.Device, error) {
	return tun.CreateTUN(InterfaceName, mtu)
}

type systemNetwork struct{}

func configure(*conf.Config, netip.AddrPort) (*systemNetwork, error) {
	return nil, errors.New("на Windows настройка сети ещё не сделана")
}

func (*systemNetwork) undo() {}

func (*systemNetwork) dnsState() DNSState { return DNSNone }

func cleanup() {}
