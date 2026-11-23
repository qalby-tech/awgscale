package conf

import (
	"errors"
	"fmt"
	"net/netip"
)

// AroundLimit: a list longer than this is not a person's list.
const AroundLimit = 64

// Around reads what goes around the tunnel: the local networks (LocalNetworks) when `local`, and the
// person's list. Anything that is not an address or a prefix, or a prefix of everything, is refused.
func Around(local bool, texts []string) ([]netip.Prefix, error) {
	if len(texts) > AroundLimit {
		return nil, errors.New("слишком много сетей мимо VPN")
	}
	var prefixes []netip.Prefix
	if local {
		prefixes = append(prefixes, LocalNetworks...)
	}
	for _, text := range texts {
		prefix, err := netip.ParsePrefix(text)
		if err != nil {
			address, failed := netip.ParseAddr(text)
			if failed != nil {
				return nil, fmt.Errorf("не сеть: %q", text)
			}
			prefix = netip.PrefixFrom(address, address.BitLen())
		}
		if prefix.Bits() == 0 {
			return nil, errors.New("мимо VPN нельзя отправить всё")
		}
		prefixes = append(prefixes, prefix.Masked())
	}
	return prefixes, nil
}

// Routes is what a system that routes by the destination alone sends into the tunnel (a phone's VPN,
// which has no rules by source or mark, nor routes that go around it before Android 13): AllowedIPs
// without the networks around it, and, whatever goes around, what Routed adds on its own (the
// network's subnet and its DNS servers), as on a computer.
func (config *Config) Routes(around []netip.Prefix) []netip.Prefix {
	routes := Subtract(config.Peer.AllowedIPs, around)
	for _, own := range config.Routed()[len(config.Peer.AllowedIPs):] {
		if !covered(routes, own) {
			routes = append(routes, own)
		}
	}
	return routes
}

// Subtract gives the prefixes that hold exactly the addresses of `from` that are in none of `away`.
func Subtract(from, away []netip.Prefix) []netip.Prefix {
	left := append([]netip.Prefix{}, from...)
	for _, hole := range away {
		var next []netip.Prefix
		for _, prefix := range left {
			next = append(next, without(prefix, hole.Masked())...)
		}
		left = next
	}
	return left
}

// without: `prefix` with the addresses of `hole` taken away. Where the hole is inside, the prefix is
// halved down to it, and every half that does not hold it is kept.
func without(prefix, hole netip.Prefix) []netip.Prefix {
	if prefix.Addr().Is4() != hole.Addr().Is4() || !prefix.Overlaps(hole) {
		return []netip.Prefix{prefix}
	}
	if hole.Bits() <= prefix.Bits() {
		return nil // all of it goes around
	}
	var kept []netip.Prefix
	current := prefix
	for current.Bits() < hole.Bits() {
		low, high := halves(current)
		if low.Contains(hole.Addr()) {
			kept = append(kept, high)
			current = low
		} else {
			kept = append(kept, low)
			current = high
		}
	}
	return kept
}

// halves splits a prefix into its two of one bit more.
func halves(prefix netip.Prefix) (netip.Prefix, netip.Prefix) {
	bits := prefix.Bits() + 1
	low := netip.PrefixFrom(prefix.Addr(), bits).Masked()
	raw := low.Addr().AsSlice()
	raw[prefix.Bits()/8] |= 0x80 >> (prefix.Bits() % 8)
	address, _ := netip.AddrFromSlice(raw)
	return low, netip.PrefixFrom(address, bits)
}

// covered: one of `prefixes` holds all of `inner`.
func covered(prefixes []netip.Prefix, inner netip.Prefix) bool {
	for _, prefix := range prefixes {
		if prefix.Bits() <= inner.Bits() && prefix.Contains(inner.Addr()) {
			return true
		}
	}
	return false
}
