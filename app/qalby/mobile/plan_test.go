package mobile

import (
	"errors"
	"net/netip"
	"slices"
	"strings"
	"testing"
)

// made-up keys of 32 bytes
const (
	keyA = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
	keyB = "YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmI="
)

// phoneConf: a config of the shape the panel writes, AllowedIPs of `allowed`.
func phoneConf(allowed string) string {
	return "[Interface]\nPrivateKey = " + keyA + "\nAddress = 172.16.17.15/32\nDNS = 172.16.17.1\nMTU = 1280\nJc = 5\n\n" +
		"[Peer]\nPublicKey = " + keyB + "\nEndpoint = vpn.example.org:51820\nAllowedIPs = " + allowed + "\nPersistentKeepalive = 25\n"
}

// routed: an address goes into the tunnel by one of the plan's routes.
func routed(plan *Plan, text string) bool {
	address := netip.MustParseAddr(text)
	return slices.ContainsFunc(plan.Routes, func(route string) bool { return netip.MustParsePrefix(route).Contains(address) })
}

func TestPlanOfAFullTunnel(t *testing.T) {
	plan, err := MakePlan(phoneConf("0.0.0.0/0, ::/0"), Options{LocalAround: true})
	if err != nil {
		t.Fatal(err)
	}
	if plan.EndpointHost != "vpn.example.org" || plan.EndpointPort != 51820 || plan.MTU != 1280 {
		t.Fatalf("endpoint or MTU: %+v", plan)
	}
	if !slices.Equal(plan.Addresses, []string{"172.16.17.15/32"}) || !slices.Equal(plan.DNS, []string{"172.16.17.1"}) {
		t.Fatalf("addresses or DNS: %+v", plan)
	}
	for _, inside := range []string{"203.0.113.9", "172.16.17.1", "172.16.17.200", "2001:db8::1"} {
		if !routed(plan, inside) {
			t.Fatalf("%s is not in the tunnel: %v", inside, plan.Routes)
		}
	}
	for _, around := range []string{"192.168.1.10", "10.0.0.1", "172.16.18.1", "fd00::1"} {
		if routed(plan, around) {
			t.Fatalf("%s, a local network, goes into the tunnel: %v", around, plan.Routes)
		}
	}
}

// AllowedIPs of IPv4 only: no IPv6 route at all, so Android sends IPv6 by the network, as the
// computer's service does (it routes only the families whose /0 the config holds).
func TestPlanOfAnIPv4OnlyConfig(t *testing.T) {
	plan, err := MakePlan(phoneConf("0.0.0.0/0"), Options{})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(plan.Routes, []string{"0.0.0.0/0"}) {
		t.Fatalf("routes: %v", plan.Routes)
	}
	for _, route := range plan.Routes {
		if netip.MustParsePrefix(route).Addr().Is6() {
			t.Fatalf("an IPv6 route of an IPv4-only config: %v", plan.Routes)
		}
	}
	if _, err := MakePlan(phoneConf("0.0.0.0/0"), Options{KillSwitch: true}); err != nil {
		t.Fatalf("the kill switch needs all of IPv4, which this config gives: %v", err)
	}
}

// IPv6 of everything with no IPv6 address: routed into the tunnel anyway, so that it goes nowhere
// rather than around it (as on a computer).
func TestPlanRoutesIPv6WithoutAnIPv6Address(t *testing.T) {
	plan, err := MakePlan(phoneConf("0.0.0.0/0, ::/0"), Options{})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(plan.Routes, "::/0") || slices.ContainsFunc(plan.Addresses, func(address string) bool { return strings.Contains(address, ":") }) {
		t.Fatalf("routes %v, addresses %v", plan.Routes, plan.Addresses)
	}
}

func TestKillSwitchNeedsTheWholeTunnel(t *testing.T) {
	_, err := MakePlan(phoneConf("172.16.17.0/24"), Options{KillSwitch: true})
	if !errors.Is(err, ErrKillSwitch) {
		t.Fatalf("a split tunnel with the kill switch: %v", err)
	}
	if _, err := MakePlan(phoneConf("172.16.17.0/24"), Options{}); err != nil {
		t.Fatalf("a split tunnel without the kill switch: %v", err)
	}
	if _, err := MakePlan(phoneConf("::/0, 172.16.17.0/24"), Options{KillSwitch: true}); !errors.Is(err, ErrKillSwitch) {
		t.Fatalf("all of IPv6 is not all of IPv4: %v", err)
	}
}

func TestPlanErrorsDoNotRepeatTheConfig(t *testing.T) {
	_, err := MakePlan(strings.Replace(phoneConf("0.0.0.0/0"), "PrivateKey = "+keyA, "PrivateKey = "+keyA+"x", 1), Options{})
	if err == nil || strings.Contains(err.Error(), keyA) {
		t.Fatalf("error: %v", err)
	}
	if _, err := MakePlan(phoneConf("0.0.0.0/0"), Options{Around: []string{"0.0.0.0/0"}}); err == nil {
		t.Fatal("everything around the tunnel was taken")
	}
}
