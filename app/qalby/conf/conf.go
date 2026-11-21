// Package conf reads a device's tunnel config, the file the panel hands out:
//
//	[Interface]
//	PrivateKey = <base64>
//	Address = 172.16.17.15/32
//	DNS = 172.16.17.1
//	MTU = 1280
//	Jc = 5 … I1 = <b 0x…> … HeaderProtectionKey = <base64>
//
//	[Peer]
//	PublicKey = <base64>
//	PresharedKey = <base64>
//	Endpoint = vpn.example.org:51820
//	AllowedIPs = 0.0.0.0/0, ::/0
//	PersistentKeepalive = 33
//
// Only the keys below are taken; anything else (PostUp, Table, SaveConfig…) is
// refused, so a config can never make the service run a command.
package conf

import (
	"bufio"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"net/netip"
	"strconv"
	"strings"
)

const keyLength = 32

// The obfuscation keys of [Interface] and their UAPI names (amneziawg-tools ipc-uapi.h).
var obfuscation = map[string]string{
	"jc": "jc", "jmin": "jmin", "jmax": "jmax",
	"s1": "s1", "s2": "s2", "s3": "s3", "s4": "s4",
	"h1": "h1", "h2": "h2", "h3": "h3", "h4": "h4",
	"i1": "i1", "i2": "i2", "i3": "i3", "i4": "i4", "i5": "i5",
	"contentpaddingaddition": "content_padding_addition",
}

// The order they are handed to the device in, so that a config always gives the same UAPI text.
var obfuscationOrder = []string{"jc", "jmin", "jmax", "s1", "s2", "s3", "s4", "h1", "h2", "h3", "h4",
	"i1", "i2", "i3", "i4", "i5", "contentpaddingaddition"}

// Key is a Curve25519 key or a preshared key.
type Key [keyLength]byte

func (key Key) hex() string { return hex.EncodeToString(key[:]) }

// Peer is the server the device talks to.
type Peer struct {
	PublicKey    Key
	PresharedKey *Key
	// Endpoint is the server's name and port, as written: it is resolved by the tunnel.
	EndpointHost string
	EndpointPort uint16
	AllowedIPs   []netip.Prefix
	Keepalive    uint16
}

// Config is one device's tunnel.
type Config struct {
	PrivateKey          Key
	Addresses           []netip.Prefix
	DNS                 []netip.Addr
	MTU                 int
	HeaderProtectionKey *Key
	// Obfuscation holds the AmneziaWG keys of [Interface] by their UAPI names.
	Obfuscation map[string]string
	Peer        Peer
}

// DefaultMTU is what the panel writes when the server names none.
const DefaultMTU = 1280

// Parse reads a config; the error says which line is wrong, never what the line holds.
func Parse(text string) (*Config, error) {
	config := &Config{MTU: DefaultMTU, Obfuscation: map[string]string{}}
	section := ""
	peers := 0
	scanner := bufio.NewScanner(strings.NewReader(text))
	number := 0
	for scanner.Scan() {
		number++
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue // only whole lines are comments: I1…I5 may hold '#' inside their tags
		}
		if strings.HasPrefix(line, "[") {
			section = strings.ToLower(line)
			if section == "[peer]" {
				peers++
			}
			if section != "[interface]" && section != "[peer]" {
				return nil, fmt.Errorf("line %d: unknown section", number)
			}
			continue
		}
		key, value, found := strings.Cut(line, "=")
		if !found {
			return nil, fmt.Errorf("line %d: not a key = value", number)
		}
		key = strings.ToLower(strings.TrimSpace(key))
		value = strings.TrimSpace(value)
		var err error
		switch section {
		case "[interface]":
			err = config.interfaceKey(key, value)
		case "[peer]":
			err = config.Peer.peerKey(key, value)
		default:
			err = fmt.Errorf("outside a section")
		}
		if err != nil {
			return nil, fmt.Errorf("line %d: %w", number, err)
		}
	}
	if peers != 1 {
		return nil, fmt.Errorf("exactly one [Peer] is needed, there are %d", peers)
	}
	if len(config.Addresses) == 0 || config.Peer.EndpointHost == "" || len(config.Peer.AllowedIPs) == 0 {
		return nil, fmt.Errorf("Address, Endpoint and AllowedIPs are needed")
	}
	return config, nil
}

func (config *Config) interfaceKey(key, value string) error {
	switch key {
	case "privatekey":
		return parseKey(value, &config.PrivateKey)
	case "address":
		for _, item := range splitList(value) {
			prefix, err := netip.ParsePrefix(item)
			if err != nil {
				return fmt.Errorf("bad Address")
			}
			config.Addresses = append(config.Addresses, prefix)
		}
	case "dns":
		for _, item := range splitList(value) {
			address, err := netip.ParseAddr(item)
			if err != nil {
				return fmt.Errorf("DNS may only hold addresses")
			}
			config.DNS = append(config.DNS, address)
		}
	case "mtu":
		mtu, err := strconv.Atoi(value)
		if err != nil || mtu < 576 || mtu > 9000 {
			return fmt.Errorf("bad MTU")
		}
		config.MTU = mtu
	case "headerprotectionkey":
		var protection Key
		if err := parseKey(value, &protection); err != nil {
			return err
		}
		config.HeaderProtectionKey = &protection
	default:
		name, known := obfuscation[key]
		if !known {
			return fmt.Errorf("the key %q is not taken", key)
		}
		if strings.ContainsAny(value, "\n\r") {
			return fmt.Errorf("bad value")
		}
		config.Obfuscation[name] = value
	}
	return nil
}

func (peer *Peer) peerKey(key, value string) error {
	switch key {
	case "publickey":
		return parseKey(value, &peer.PublicKey)
	case "presharedkey":
		var preshared Key
		if err := parseKey(value, &preshared); err != nil {
			return err
		}
		peer.PresharedKey = &preshared
	case "endpoint":
		index := strings.LastIndex(value, ":")
		if index <= 0 {
			return fmt.Errorf("bad Endpoint")
		}
		host := strings.Trim(value[:index], "[]")
		number, err := strconv.ParseUint(value[index+1:], 10, 16)
		if err != nil || number == 0 || host == "" || strings.ContainsAny(host, " /@") {
			return fmt.Errorf("bad Endpoint")
		}
		peer.EndpointHost = host
		peer.EndpointPort = uint16(number)
	case "allowedips":
		for _, item := range splitList(value) {
			prefix, err := netip.ParsePrefix(item)
			if err != nil {
				return fmt.Errorf("bad AllowedIPs")
			}
			peer.AllowedIPs = append(peer.AllowedIPs, prefix.Masked())
		}
	case "persistentkeepalive":
		seconds, err := strconv.ParseUint(value, 10, 16)
		if err != nil {
			return fmt.Errorf("bad PersistentKeepalive")
		}
		peer.Keepalive = uint16(seconds)
	default:
		return fmt.Errorf("the key %q is not taken", key)
	}
	return nil
}

func splitList(value string) []string {
	var items []string
	for _, item := range strings.Split(value, ",") {
		if item = strings.TrimSpace(item); item != "" {
			items = append(items, item)
		}
	}
	return items
}

func parseKey(value string, key *Key) error {
	raw, err := base64.StdEncoding.DecodeString(value)
	if err != nil || len(raw) != keyLength {
		return fmt.Errorf("bad key")
	}
	copy(key[:], raw)
	return nil
}

// UAPI is the text the device is configured with (amneziawg-go device/uapi.go):
// the endpoint is an address already, `mark` keeps the tunnel's own packets out of it.
func (config *Config) UAPI(endpoint netip.AddrPort, mark uint32) string {
	var text strings.Builder
	write := func(key, value string) { fmt.Fprintf(&text, "%s=%s\n", key, value) }
	write("private_key", config.PrivateKey.hex())
	if mark != 0 {
		write("fwmark", strconv.FormatUint(uint64(mark), 10))
	}
	for _, key := range obfuscationOrder {
		name := obfuscation[key]
		if value, found := config.Obfuscation[name]; found {
			write(name, value)
		}
	}
	if config.HeaderProtectionKey != nil {
		write("header_protection_key", config.HeaderProtectionKey.hex())
	}
	write("replace_peers", "true")
	write("public_key", config.Peer.PublicKey.hex())
	if config.Peer.PresharedKey != nil {
		write("preshared_key", config.Peer.PresharedKey.hex())
	}
	write("endpoint", endpoint.String())
	write("persistent_keepalive_interval", strconv.FormatUint(uint64(config.Peer.Keepalive), 10))
	write("replace_allowed_ips", "true")
	for _, prefix := range config.Peer.AllowedIPs {
		write("allowed_ip", prefix.String())
	}
	return text.String()
}

// Routed is what goes into the tunnel: AllowedIPs, and the config's DNS servers on their own, so that
// a local network that happens to cover the entry's tunnel address (say 172.16.0.0/16) cannot take
// the network's DNS and its app API away from the tunnel. A DNS server AllowedIPs leave out is not
// routed: the server would not take its packets.
func (config *Config) Routed() []netip.Prefix {
	routed := append([]netip.Prefix{}, config.Peer.AllowedIPs...)
	for _, server := range config.DNS {
		host := netip.PrefixFrom(server, server.BitLen())
		for _, allowed := range config.Peer.AllowedIPs {
			if allowed.Contains(server) {
				routed = append(routed, host)
				break
			}
		}
	}
	return routed
}

// FullTunnel says whether all of a family's traffic goes through the tunnel.
func (config *Config) FullTunnel(ipv6 bool) bool {
	for _, prefix := range config.Peer.AllowedIPs {
		if prefix.Bits() == 0 && prefix.Addr().Is6() == ipv6 {
			return true
		}
	}
	return false
}
