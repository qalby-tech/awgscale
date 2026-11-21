//go:build windows

package firewall

// awgscale's own rules beside the ones of wireguard-windows: the kill switch with networks that
// may still go around the tunnel (the local ones, the person's list).

import (
	"encoding/binary"
	"errors"
	"net"
	"net/netip"
	"unsafe"

	"golang.org/x/sys/windows"
)

// EnableKillSwitch is EnableFirewall(luid, false, dnsServers) of wireguard-windows with one rule
// more: the networks of `around` may still be reached around the tunnel.
func EnableKillSwitch(luid uint64, dnsServers []netip.Addr, around []netip.Prefix) error {
	if wfpSession != 0 {
		return errors.New("The firewall has already been enabled")
	}
	session, err := createWfpSession()
	if err != nil {
		return wrapErr(err)
	}
	var servers []net.IP
	for _, server := range dnsServers {
		servers = append(servers, net.IP(server.AsSlice()))
	}
	objectInstaller := func(session uintptr) error {
		baseObjects, err := registerBaseObjects(session)
		if err != nil {
			return wrapErr(err)
		}
		steps := []func() error{
			func() error { return permitWireGuardService(session, baseObjects, 15) },
			func() error {
				if len(servers) == 0 {
					return nil
				}
				return blockDNS(servers, session, baseObjects, 15, 14)
			},
			func() error { return permitLoopback(session, baseObjects, 13) },
			func() error { return permitTunInterface(session, baseObjects, 12, luid) },
			func() error { return permitDHCPIPv4(session, baseObjects, 12) },
			func() error { return permitDHCPIPv6(session, baseObjects, 12) },
			func() error { return permitNdp(session, baseObjects, 12) },
			func() error { return permitRemotePrefixes(session, baseObjects, 12, around) },
			func() error { return blockAll(session, baseObjects, 0) },
		}
		for _, step := range steps {
			if err := step(); err != nil {
				return wrapErr(err)
			}
		}
		return nil
	}
	if err := runTransaction(session, objectInstaller); err != nil {
		fwpmEngineClose0(session)
		return wrapErr(err)
	}
	wfpSession = session
	return nil
}

// permitRemotePrefixes lets connections to and from the prefixes through, both ways and both
// families; the conditions on one field are OR'ed by WFP.
func permitRemotePrefixes(session uintptr, baseObjects *baseObjects, weight uint8, prefixes []netip.Prefix) error {
	var four []wtFwpmFilterCondition0
	var six []wtFwpmFilterCondition0
	var keptFour []*wtFwpV4AddrAndMask
	var keptSix []*wtFwpV6AddrAndMask
	for _, prefix := range prefixes {
		prefix = prefix.Masked()
		if prefix.Addr().Is4() {
			bytes := prefix.Addr().As4()
			value := &wtFwpV4AddrAndMask{addr: binary.BigEndian.Uint32(bytes[:]), mask: ^uint32(0) << (32 - prefix.Bits())}
			if prefix.Bits() == 0 {
				value.mask = 0
			}
			keptFour = append(keptFour, value)
			four = append(four, wtFwpmFilterCondition0{
				fieldKey:       cFWPM_CONDITION_IP_REMOTE_ADDRESS,
				matchType:      cFWP_MATCH_EQUAL,
				conditionValue: wtFwpConditionValue0{_type: cFWP_V4_ADDR_MASK, value: uintptr(unsafe.Pointer(value))},
			})
			continue
		}
		value := &wtFwpV6AddrAndMask{addr: prefix.Addr().As16(), prefixLength: uint8(prefix.Bits())}
		keptSix = append(keptSix, value)
		six = append(six, wtFwpmFilterCondition0{
			fieldKey:       cFWPM_CONDITION_IP_REMOTE_ADDRESS,
			matchType:      cFWP_MATCH_EQUAL,
			conditionValue: wtFwpConditionValue0{_type: cFWP_V6_ADDR_MASK, value: uintptr(unsafe.Pointer(value))},
		})
	}
	add := func(conditions []wtFwpmFilterCondition0, layer windows.GUID, name string) error {
		if len(conditions) == 0 {
			return nil
		}
		displayData, err := createWtFwpmDisplayData0(name, "")
		if err != nil {
			return err
		}
		filter := wtFwpmFilter0{
			displayData:         *displayData,
			providerKey:         &baseObjects.provider,
			layerKey:            layer,
			subLayerKey:         baseObjects.filters,
			weight:              filterWeight(weight),
			numFilterConditions: uint32(len(conditions)),
			filterCondition:     (*wtFwpmFilterCondition0)(unsafe.Pointer(&conditions[0])),
			action:              wtFwpmAction0{_type: cFWP_ACTION_PERMIT},
		}
		filterID := uint64(0)
		return fwpmFilterAdd0(session, &filter, 0, &filterID)
	}
	for _, rule := range []struct {
		conditions []wtFwpmFilterCondition0
		layer      windows.GUID
		name       string
	}{
		{four, cFWPM_LAYER_ALE_AUTH_CONNECT_V4, "Permit networks around the tunnel (IPv4 out)"},
		{four, cFWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V4, "Permit networks around the tunnel (IPv4 in)"},
		{six, cFWPM_LAYER_ALE_AUTH_CONNECT_V6, "Permit networks around the tunnel (IPv6 out)"},
		{six, cFWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V6, "Permit networks around the tunnel (IPv6 in)"},
	} {
		if err := add(rule.conditions, rule.layer, rule.name); err != nil {
			return err
		}
	}
	_ = keptFour // the values must live until the filters are added
	_ = keptSix
	return nil
}
