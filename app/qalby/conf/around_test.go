package conf

import (
	"net/netip"
	"slices"
	"strings"
	"testing"
)

func prefixes(texts ...string) []netip.Prefix {
	var list []netip.Prefix
	for _, text := range texts {
		list = append(list, netip.MustParsePrefix(text))
	}
	return list
}

// holds: an address is in one of the prefixes.
func holds(list []netip.Prefix, text string) bool {
	address := netip.MustParseAddr(text)
	return slices.ContainsFunc(list, func(prefix netip.Prefix) bool { return prefix.Contains(address) })
}

func TestSubtractKeepsEverythingButTheHole(t *testing.T) {
	left := Subtract(prefixes("0.0.0.0/0"), prefixes("10.0.0.0/8"))
	if len(left) != 8 {
		t.Fatalf("0/0 without 10/8 is 8 prefixes: %v", left)
	}
	for _, inside := range []string{"0.0.0.1", "9.255.255.255", "11.0.0.0", "203.0.113.9", "255.255.255.255"} {
		if !holds(left, inside) {
			t.Fatalf("%s was lost: %v", inside, left)
		}
	}
	for _, around := range []string{"10.0.0.0", "10.96.0.10", "10.255.255.255"} {
		if holds(left, around) {
			t.Fatalf("%s still in: %v", around, left)
		}
	}
	if got := Subtract(prefixes("10.1.0.0/16"), prefixes("10.0.0.0/8")); len(got) != 0 {
		t.Fatalf("a prefix inside the hole goes: %v", got)
	}
	if got := Subtract(prefixes("::/0"), prefixes("10.0.0.0/8")); len(got) != 1 {
		t.Fatalf("another family is untouched: %v", got)
	}
}

func TestPhoneRoutesKeepTheNetworkInsideWhateverGoesAround(t *testing.T) {
	config, err := Parse(panelConf)
	if err != nil {
		t.Fatal(err)
	}
	around, err := Around(true, []string{"198.51.100.7"})
	if err != nil {
		t.Fatal(err)
	}
	routes := config.Routes(around)
	for _, inside := range []string{"203.0.113.9", "172.16.17.1", "172.16.17.200", "2001:db8::1", "1.1.1.1"} {
		if !holds(routes, inside) {
			t.Fatalf("%s should go into the tunnel: %v", inside, routes)
		}
	}
	for _, outside := range []string{"10.0.2.2", "192.168.1.1", "172.16.18.1", "198.51.100.7", "100.64.0.1", "fe80::1", "fd00::1"} {
		if holds(routes, outside) {
			t.Fatalf("%s should go around: %v", outside, routes)
		}
	}
	if all := config.Routes(nil); len(all) != 2 {
		t.Fatalf("nothing around: AllowedIPs as they are: %v", all)
	}
}

func TestAroundRefusesEverythingAndNonsense(t *testing.T) {
	if _, err := Around(false, []string{"0.0.0.0/0"}); err == nil {
		t.Fatal("everything around")
	}
	if _, err := Around(false, []string{"vpn.example.org"}); err == nil || !strings.Contains(err.Error(), "не сеть") {
		t.Fatalf("a name: %v", err)
	}
	if list, _ := Around(false, []string{"10.96.0.1/12"}); list[0].String() != "10.96.0.0/12" {
		t.Fatalf("masked: %v", list)
	}
}
