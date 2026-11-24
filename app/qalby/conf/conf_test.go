package conf

import (
	"net/netip"
	"os"
	"strings"
	"testing"
)

// made-up keys of 32 bytes
const (
	keyA = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
	keyB = "YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmI="
)

const panelConf = "[Interface]\nPrivateKey = " + keyA + "\nAddress = 172.16.17.15/32\nDNS = 172.16.17.1\nMTU = 1280\nJc = 5\nS4 = 19\n" +
	"I1 = <r 2><b 0x85#80><rc 36>\nHeaderProtectionKey = " + keyB + "\nContentPaddingAddition = 32-128\n\n" +
	"[Peer]\nPublicKey = " + keyB + "\nPresharedKey = " + keyA + "\nEndpoint = vpn.example.org:51820\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 33\n"

func TestReadsThePanelsConfig(t *testing.T) {
	config, err := Parse(panelConf)
	if err != nil {
		t.Fatal(err)
	}
	if config.Peer.EndpointHost != "vpn.example.org" || config.Peer.EndpointPort != 51820 || config.MTU != 1280 {
		t.Fatalf("endpoint or MTU: %+v", config.Peer)
	}
	if !config.FullTunnel(false) || !config.FullTunnel(true) {
		t.Fatal("both families go through the tunnel")
	}
	if config.Obfuscation["i1"] != "<r 2><b 0x85#80><rc 36>" {
		t.Fatalf("I1 is kept whole, '#' and all: %q", config.Obfuscation["i1"])
	}
}

func TestRefusesWhatCouldRunACommand(t *testing.T) {
	for _, extra := range []string{"PostUp = curl evil", "PreUp = x", "Table = off", "SaveConfig = true", "FwMark = 1"} {
		text := strings.Replace(panelConf, "MTU = 1280\n", "MTU = 1280\n"+extra+"\n", 1)
		if _, err := Parse(text); err == nil {
			t.Fatalf("%q was taken", extra)
		}
	}
	if _, err := Parse(strings.Replace(panelConf, "[Peer]", "[Peer]\n[Peer]", 1)); err == nil {
		t.Fatal("two peers")
	}
}

func TestErrorsDoNotRepeatTheSecret(t *testing.T) {
	_, err := Parse(strings.Replace(panelConf, "PrivateKey = "+keyA, "PrivateKey = "+keyA+"x", 1))
	if err == nil || strings.Contains(err.Error(), keyA) {
		t.Fatalf("error: %v", err)
	}
}

func TestUAPIHasHexKeysAndTheMark(t *testing.T) {
	config, _ := Parse(panelConf)
	text := config.UAPI(netip.MustParseAddrPort("203.0.113.5:51820"), 51830)
	for _, want := range []string{"private_key=6161", "fwmark=51830\n", "jc=5\n", "s4=19\n", "i1=<r 2>", "content_padding_addition=32-128\n",
		"header_protection_key=6262", "endpoint=203.0.113.5:51820\n", "allowed_ip=0.0.0.0/0\n", "allowed_ip=::/0\n", "persistent_keepalive_interval=33\n"} {
		if !strings.Contains(text, want) {
			t.Fatalf("no %q in\n%s", want, text)
		}
	}
}

func TestTheNetworkAndItsDNSAreRoutedOnTheirOwn(t *testing.T) {
	config, _ := Parse(panelConf)
	routed := config.Routed()
	want := []netip.Prefix{netip.MustParsePrefix("172.16.17.0/24"), netip.MustParsePrefix("172.16.17.1/32")}
	if len(routed) != 4 || routed[2] != want[0] || routed[3] != want[1] {
		t.Fatalf("routed: %v", routed)
	}
	split, _ := Parse(strings.Replace(panelConf, "AllowedIPs = 0.0.0.0/0, ::/0", "AllowedIPs = 10.9.0.0/16", 1))
	if len(split.Routed()) != 1 {
		t.Fatalf("what AllowedIPs leave out is not routed: %v", split.Routed())
	}
}

// The config of a connection given by hand, exactly as the app keeps it (app/core/src/manual.rs writes a
// pasted config or Amnezia key anew; its tests check it wrote this file): throwaway keys
func TestReadsAConnectionGivenByHand(t *testing.T) {
	text, err := os.ReadFile("testdata/manual.conf")
	if err != nil {
		t.Fatal(err)
	}
	config, err := Parse(string(text))
	if err != nil {
		t.Fatal(err)
	}
	for name, value := range map[string]string{"jc": "4", "s3": "17", "s4": "9", "h1": "1102466812-1102466900", "i1": "<b 0xc6000000010843290a47ba8ba2ed000044d0><r 16>"} {
		if config.Obfuscation[name] != value {
			t.Fatalf("%s: %q", name, config.Obfuscation[name])
		}
	}
	if config.Peer.EndpointHost != "vpn.example.org" || config.Peer.PresharedKey == nil {
		t.Fatalf("peer: %+v", config.Peer)
	}
}
