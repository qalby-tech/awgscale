//go:build linux

package tunnel

import (
	"errors"
	"fmt"
	"net/netip"
	"os"
	"os/exec"
	"strconv"
	"strings"

	"github.com/amnezia-vpn/amneziawg-go/v3/tun"

	"github.com/qalby-tech/awgscale/app/qalby/conf"
)

// The nftables table of the tunnel's rules (awg-quick names its own wg-quick-<interface>).
const firewallTable = "awgscale"

// The tunnel's two routing rules have places of their own, so that only they are ever taken away:
// other VPNs (wg-quick, awg-quick) add rules that look the same.
const (
	prioritySuppress = "31890" // table main suppress_prefixlength 0: the LAN and the server's own route stay
	priorityTunnel   = "31891" // not fwmark → the tunnel's table
)

func createDevice(mtu int) (tun.Device, error) {
	return tun.CreateTUN(InterfaceName, mtu)
}

// systemNetwork is what was changed, so that exactly that is taken back.
type systemNetwork struct {
	families []string
	dns      DNSState
}

// configure gives the interface its address and sends the traffic into it, the way awg-quick does
// for AllowedIPs of /0: the tunnel's own packets carry the mark and keep to the main table, all
// others go by a table of their own whose default route is the tunnel.
func configure(config *conf.Config, endpoint netip.AddrPort) (*systemNetwork, error) {
	network := &systemNetwork{}
	for _, address := range config.Addresses {
		if err := run("ip", family(address.Addr()), "address", "add", address.String(), "dev", InterfaceName); err != nil {
			return network, err
		}
	}
	if err := run("ip", "link", "set", "dev", InterfaceName, "mtu", strconv.Itoa(config.MTU), "up"); err != nil {
		return network, err
	}
	table := strconv.Itoa(Mark)
	for _, prefix := range config.Peer.AllowedIPs {
		if prefix.Bits() == 0 {
			continue
		}
		if err := run("ip", family(prefix.Addr()), "route", "add", prefix.String(), "dev", InterfaceName); err != nil {
			return network, err
		}
	}
	for _, ipv6 := range []bool{false, true} {
		if !config.FullTunnel(ipv6) || (ipv6 && !hasIPv6()) {
			continue
		}
		name := "-4"
		everything := "0.0.0.0/0"
		if ipv6 {
			name = "-6"
			everything = "::/0"
		}
		network.families = append(network.families, name)
		// a device with no IPv6 address still sends IPv6 into the tunnel: it goes nowhere instead of around it
		if err := run("ip", name, "route", "add", everything, "dev", InterfaceName, "table", table); err != nil {
			return network, err
		}
		if err := run("ip", name, "rule", "add", "not", "fwmark", table, "table", table, "priority", priorityTunnel); err != nil {
			return network, err
		}
		if err := run("ip", name, "rule", "add", "table", "main", "suppress_prefixlength", "0", "priority", prioritySuppress); err != nil {
			return network, err
		}
	}
	if len(network.families) > 0 {
		if err := validMarks(); err != nil {
			return network, err
		}
		if err := firewall(config, table); err != nil {
			return network, err
		}
	}
	network.dns = DNSNone
	if len(config.DNS) > 0 {
		network.dns = setDNS(config.DNS)
	}
	return network, nil
}

// hasIPv6: a machine with IPv6 switched off has no IPv6 to keep out of the tunnel's way.
func hasIPv6() bool {
	switched, err := os.ReadFile("/proc/sys/net/ipv6/conf/all/disable_ipv6")
	return err == nil && strings.TrimSpace(string(switched)) == "0"
}

// validMarks lets the answers to the tunnel's own packets pass the reverse path check (awg-quick does
// the same); already on, it is left alone.
func validMarks() error {
	const path = "/proc/sys/net/ipv4/conf/all/src_valid_mark"
	if current, err := os.ReadFile(path); err == nil && strings.TrimSpace(string(current)) == "1" {
		return nil
	}
	if err := os.WriteFile(path, []byte("1"), 0o644); err != nil {
		return fmt.Errorf("src_valid_mark: %w", err)
	}
	return nil
}

// firewall: nothing from outside may pretend to be the tunnel's address, and the answers to the
// tunnel's own UDP packets keep their mark (the rules of awg-quick, add_default).
func firewall(config *conf.Config, table string) error {
	var rules strings.Builder
	fmt.Fprintf(&rules, "add table inet %s\n", firewallTable)
	fmt.Fprintf(&rules, "add chain inet %s preraw { type filter hook prerouting priority -300; }\n", firewallTable)
	fmt.Fprintf(&rules, "add chain inet %s premangle { type filter hook prerouting priority -150; }\n", firewallTable)
	fmt.Fprintf(&rules, "add chain inet %s postmangle { type filter hook postrouting priority -150; }\n", firewallTable)
	for _, address := range config.Addresses {
		name := "ip"
		if address.Addr().Is6() {
			name = "ip6"
		}
		fmt.Fprintf(&rules, "add rule inet %s preraw iifname != \"%s\" %s daddr %s fib saddr type != local drop\n",
			firewallTable, InterfaceName, name, address.Addr())
	}
	fmt.Fprintf(&rules, "add rule inet %s postmangle meta l4proto udp mark %s ct mark set mark\n", firewallTable, table)
	fmt.Fprintf(&rules, "add rule inet %s premangle meta l4proto udp meta mark set ct mark\n", firewallTable)
	command := exec.Command("nft", "-f", "-")
	command.Stdin = strings.NewReader(rules.String())
	if output, err := command.CombinedOutput(); err != nil {
		return fmt.Errorf("nft: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

// setDNS hands the tunnel's DNS to systemd-resolved for every name; without it the system keeps its
// own, and the status says so (the network's smart DNS then does not decide the routes).
func setDNS(servers []netip.Addr) DNSState {
	if _, err := exec.LookPath("resolvectl"); err != nil {
		return DNSUnmanaged
	}
	arguments := []string{"dns", InterfaceName}
	for _, server := range servers {
		arguments = append(arguments, server.String())
	}
	if run("resolvectl", arguments...) != nil || run("resolvectl", "domain", InterfaceName, "~.") != nil {
		return DNSFailed
	}
	return DNSSet
}

func (network *systemNetwork) undo() {
	if network.dns == DNSSet {
		_ = run("resolvectl", "revert", InterfaceName)
	}
}

func (network *systemNetwork) dnsState() DNSState { return network.dns }

// cleanup takes away whatever a tunnel of this service left, even of a run that was killed.
func cleanup() {
	table := strconv.Itoa(Mark)
	for _, name := range []string{"-4", "-6"} {
		for _, priority := range []string{priorityTunnel, prioritySuppress} {
			for run("ip", name, "rule", "delete", "priority", priority) == nil {
			}
		}
		_ = run("ip", name, "route", "flush", "table", table)
	}
	_ = run("nft", "delete", "table", "inet", firewallTable)
	_ = run("ip", "link", "delete", "dev", InterfaceName)
}

func family(address netip.Addr) string {
	if address.Is6() {
		return "-6"
	}
	return "-4"
}

func run(name string, arguments ...string) error {
	output, err := exec.Command(name, arguments...).CombinedOutput()
	if err != nil {
		text := strings.TrimSpace(string(output))
		if text == "" {
			return err
		}
		return errors.New(name + ": " + text)
	}
	return nil
}
