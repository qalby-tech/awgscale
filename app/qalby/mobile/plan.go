// Package mobile is the tunnel of a phone. On a computer the service (cmd/qalby) sets the system's
// network itself; on Android the app's VpnService does: it asks Plan what to give
// VpnService.Builder (addresses, DNS, MTU, routes), resolves the server through the system's DNS,
// establishes the interface and hands its descriptor to Start, which runs amneziawg-go on it with
// the config as the desktop does (conf.Parse, conf.UAPI: every obfuscation key the panel writes).
// The library the app loads is cmd/libawgscale.
package mobile

import (
	"net/netip"

	"github.com/qalby-tech/awgscale/app/qalby/conf"
)

// Options are the person's choices that are not in the config (TunnelOptions of app/core).
type Options struct {
	KillSwitch  bool     `json:"kill_switch"`
	LocalAround bool     `json:"local_around"`
	Around      []string `json:"around"`
}

// Plan is what the VpnService gives Android before the tunnel starts.
type Plan struct {
	Addresses []string `json:"addresses"`
	DNS       []string `json:"dns"`
	MTU       int      `json:"mtu"`
	// Routes: what goes into the tunnel (conf.Config.Routes): AllowedIPs without what goes around it.
	Routes       []string `json:"routes"`
	EndpointHost string   `json:"endpoint_host"`
	EndpointPort uint16   `json:"endpoint_port"`
}

// MakePlan reads the config and the options; the error never repeats what the config holds.
func MakePlan(text string, options Options) (*Plan, error) {
	config, err := conf.Parse(text)
	if err != nil {
		return nil, err
	}
	around, err := conf.Around(options.LocalAround, options.Around)
	if err != nil {
		return nil, err
	}
	plan := &Plan{
		Addresses:    texts(config.Addresses),
		MTU:          config.MTU,
		Routes:       texts(config.Routes(around)),
		EndpointHost: config.Peer.EndpointHost,
		EndpointPort: config.Peer.EndpointPort,
	}
	for _, server := range config.DNS {
		plan.DNS = append(plan.DNS, server.String())
	}
	return plan, nil
}

func texts(prefixes []netip.Prefix) []string {
	texts := make([]string, 0, len(prefixes))
	for _, prefix := range prefixes {
		texts = append(texts, prefix.String())
	}
	return texts
}
