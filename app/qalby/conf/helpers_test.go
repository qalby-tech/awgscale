package conf

import "net/netip"

func netipAddrPort(text string) netip.AddrPort { return netip.MustParseAddrPort(text) }
