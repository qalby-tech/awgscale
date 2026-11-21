//go:build windows

package tunnel

// The tunnel's network on Windows, the way amneziawg-windows (wireguard-windows) sets it
// (tunnel/service.go, interfacewatcher.go, addressconfig.go, defaultroutemonitor.go of
// amneziawg-windows v3.1.20260814; MIT, Copyright (C) 2019-2021 WireGuard LLC): a Wintun
// adapter whose addresses, routes and DNS are set when Windows announces it, the tunnel's
// own UDP socket bound to the best other interface (that is what keeps its packets out of
// the tunnel here, not a mark), and the firewall rules of WFP that live as long as this
// process does.

import (
	"errors"
	"net"
	"net/netip"
	"sync"
	"time"

	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"github.com/amnezia-vpn/amneziawg-go/v3/tun"
	"github.com/amnezia-vpn/amneziawg-windows/v3/tunnel/winipcfg"
	"golang.org/x/sys/windows"

	"github.com/qalby-tech/awgscale/app/qalby/conf"
	"github.com/qalby-tech/awgscale/app/qalby/windows/firewall"
)

// adapterGUID is the adapter's own: the same adapter every time, so Windows keeps one network
// profile for it instead of making a new one each time the tunnel comes up.
var adapterGUID = windows.GUID{Data1: 0x6177_6773, Data2: 0x6361, Data3: 0x6c65, Data4: [8]byte{0x71, 0x61, 0x6c, 0x62, 0x79, 0x00, 0x00, 0x01}}

const configureTimeout = 10 * time.Second

func createDevice(mtu int) (tun.Device, error) {
	var created tun.Device
	var err error
	for attempt := 0; attempt < 5; attempt++ { // just after boot Wintun may not be ready yet
		if attempt > 0 {
			time.Sleep(time.Second)
		}
		created, err = tun.CreateTUNWithRequestedGUID(InterfaceName, &adapterGUID, 0)
		if err == nil {
			return created, nil
		}
	}
	return nil, err
}

type adapterEvent struct {
	luid   winipcfg.LUID
	family winipcfg.AddressFamily
}

// systemNetwork watches for the adapter and sets it up, family by family.
type systemNetwork struct {
	mutex      sync.Mutex
	watcher    winipcfg.ChangeCallback
	callbacks  map[winipcfg.AddressFamily][]winipcfg.ChangeCallback
	stored     []adapterEvent
	adapter    *tun.NativeTun
	binder     conn.BindSocketToInterface
	config     *conf.Config
	configured map[winipcfg.AddressFamily]bool
	failure    error
	firewall   bool
	dns        DNSState
	around     []netip.Prefix
}

// newNetwork starts watching before the adapter exists: Windows announces it once, maybe at once.
func newNetwork() (*systemNetwork, error) {
	network := &systemNetwork{
		callbacks:  map[winipcfg.AddressFamily][]winipcfg.ChangeCallback{},
		configured: map[winipcfg.AddressFamily]bool{},
		dns:        DNSNone,
	}
	watcher, err := winipcfg.RegisterInterfaceChangeCallback(func(notification winipcfg.MibNotificationType, row *winipcfg.MibIPInterfaceRow) {
		if notification != winipcfg.MibAddInstance {
			return
		}
		network.mutex.Lock()
		defer network.mutex.Unlock()
		if network.adapter == nil {
			network.stored = append(network.stored, adapterEvent{row.InterfaceLUID, row.Family})
			return
		}
		if row.InterfaceLUID == winipcfg.LUID(network.adapter.LUID()) {
			network.setup(row.Family)
		}
	})
	if err != nil {
		return nil, err
	}
	network.watcher = watcher
	return network, nil
}

func (network *systemNetwork) configure(config *conf.Config, _ netip.AddrPort, tunDevice tun.Device, bind conn.Bind, options Options) error {
	adapter, isNative := tunDevice.(*tun.NativeTun)
	binder, binds := bind.(conn.BindSocketToInterface)
	if !isNative || !binds {
		return errors.New("неожиданный интерфейс")
	}
	if options.KillSwitch {
		if err := network.enableFirewall(config, adapter, options); err != nil {
			return err
		}
	}
	network.mutex.Lock()
	network.adapter = adapter
	network.binder = binder
	network.config = config
	network.around = options.Around
	for _, event := range network.stored {
		if event.luid == winipcfg.LUID(adapter.LUID()) {
			network.setup(event.family)
		}
	}
	network.stored = nil
	network.mutex.Unlock()
	if err := network.awaitConfigured(); err != nil {
		return err
	}
	if len(config.DNS) == 0 {
		return nil
	}
	if !network.hasDNS() {
		Event("dns: the adapter did not take the network's DNS; trying again")
		network.setDNS()
	}
	if !network.hasDNS() {
		// better the machine's own DNS around the tunnel than no DNS at all: no rules that keep it inside
		network.mutex.Lock()
		network.dns = DNSFailed
		network.mutex.Unlock()
		Event("dns: the adapter has no DNS of the network; DNS stays the machine's own")
		return nil
	}
	Event("dns: the network's DNS is set on the adapter")
	if options.KillSwitch {
		return nil // its rules keep DNS inside already
	}
	return network.enableFirewall(config, adapter, options)
}

// hasDNS: the adapter's DNS servers, as Windows reports them, include every IPv4 one of the config.
func (network *systemNetwork) hasDNS() bool {
	luid := winipcfg.LUID(network.adapter.LUID())
	servers, err := luid.DNS()
	if err != nil {
		return false
	}
	for _, wanted := range network.config.DNS {
		if !wanted.Is4() {
			continue
		}
		found := false
		for _, server := range servers {
			found = found || net.IP(wanted.AsSlice()).Equal(server)
		}
		if !found {
			return false
		}
	}
	return true
}

func (network *systemNetwork) setDNS() {
	luid := winipcfg.LUID(network.adapter.LUID())
	var servers []net.IP
	for _, server := range network.config.DNS {
		if server.Is4() {
			servers = append(servers, net.IP(server.AsSlice()))
		}
	}
	if err := luid.SetDNS(windows.AF_INET, servers, nil); err != nil {
		Event("dns: Windows refused the network's DNS: %v", err)
	}
}

// watch: something else on the machine (another VPN's service, a network manager) may take the
// adapter's DNS away while the tunnel is on; it is put back, and the record says so.
func (network *systemNetwork) watch() {
	if network.adapter == nil || network.config == nil || len(network.config.DNS) == 0 {
		return
	}
	network.mutex.Lock()
	set := network.dns == DNSSet
	network.mutex.Unlock()
	if set && !network.hasDNS() {
		Event("dns: the adapter lost the network's DNS; set again")
		network.setDNS()
	}
}

// awaitConfigured waits until the families the config has addresses of are set up.
func (network *systemNetwork) awaitConfigured() error {
	deadline := time.Now().Add(configureTimeout)
	for time.Now().Before(deadline) {
		network.mutex.Lock()
		failure := network.failure
		done := true
		for _, family := range network.families() {
			done = done && network.configured[family]
		}
		network.mutex.Unlock()
		if failure != nil {
			return failure
		}
		if done {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return errors.New("Windows не настроил адаптер вовремя")
}

func (network *systemNetwork) families() []winipcfg.AddressFamily {
	var families []winipcfg.AddressFamily
	var four, six bool
	for _, address := range network.config.Addresses {
		four = four || address.Addr().Is4()
		six = six || address.Addr().Is6()
	}
	if four {
		families = append(families, windows.AF_INET)
	}
	if six {
		families = append(families, windows.AF_INET6)
	}
	return families
}

// setup: the tunnel's socket follows the best other interface, then the adapter gets its
// addresses, routes and DNS. Called with the mutex held.
func (network *systemNetwork) setup(family winipcfg.AddressFamily) {
	for _, callback := range network.callbacks[family] {
		_ = callback.Unregister()
	}
	callbacks, err := monitorDefaultRoutes(family, network.binder, network.config.FullTunnel(family == windows.AF_INET6), network.adapter, network.aroundOf(family))
	if err != nil {
		network.failure = err
		return
	}
	network.callbacks[family] = callbacks
	if err := network.configureFamily(family); err != nil {
		network.failure = err
		return
	}
	network.configured[family] = true
}

// aroundOf: the networks of one family that go around the tunnel.
func (network *systemNetwork) aroundOf(family winipcfg.AddressFamily) []net.IPNet {
	var nets []net.IPNet
	for _, prefix := range network.around {
		if prefix.Addr().Is6() == (family == windows.AF_INET6) {
			nets = append(nets, prefixNet(prefix.Masked()))
		}
	}
	return nets
}

func (network *systemNetwork) configureFamily(family winipcfg.AddressFamily) error {
	config := network.config
	luid := winipcfg.LUID(network.adapter.LUID())
	ipv6 := family == windows.AF_INET6
	var addresses []net.IPNet
	for _, address := range config.Addresses {
		if address.Addr().Is6() == ipv6 {
			addresses = append(addresses, prefixNet(address))
		}
	}
	var routes []*winipcfg.RouteData
	for _, allowed := range config.Routed() {
		if allowed.Addr().Is6() != ipv6 || len(addresses) == 0 {
			continue // a family the adapter has no address of cannot be routed into it
		}
		nextHop := net.IPv4zero
		if ipv6 {
			nextHop = net.IPv6zero
		}
		routes = append(routes, &winipcfg.RouteData{Destination: prefixNet(allowed), NextHop: nextHop, Metric: 0})
	}
	// the interface first: an address added later is then used at once. A tunnel's address needs
	// no duplicate check (nobody else is on a point-to-point link), and Windows would hold it
	// "tentative" meanwhile and send nothing from it.
	row, err := luid.IPInterface(family)
	if err != nil {
		return err
	}
	row.NLMTU = uint32(config.MTU)
	network.adapter.ForceMTU(config.MTU)
	if config.FullTunnel(ipv6) {
		row.UseAutomaticMetric = false
		row.Metric = 0
	}
	row.DadTransmits = 0
	if ipv6 {
		row.RouterDiscoveryBehavior = winipcfg.RouterDiscoveryDisabled
	}
	if err := row.Set(); err != nil {
		return err
	}
	if err := luid.SetIPAddressesForFamily(family, addresses); err != nil {
		return err
	}
	if err := luid.SetRoutesForFamily(family, routes); err != nil {
		return err
	}
	var servers []net.IP
	for _, server := range config.DNS {
		if server.Is6() == ipv6 {
			servers = append(servers, net.IP(server.AsSlice()))
		}
	}
	if err := luid.SetDNS(family, servers, nil); err != nil {
		network.dns = DNSFailed
		return nil // the tunnel works; the status says the DNS is not the network's
	}
	if len(config.DNS) > 0 && network.dns != DNSFailed {
		network.dns = DNSSet
	}
	return nil
}

// enableFirewall: with the kill switch nothing leaves around the tunnel (EnableFirewall of
// wireguard-windows); without it only DNS is kept inside, since Windows asks every adapter's DNS.
func (network *systemNetwork) enableFirewall(config *conf.Config, adapter *tun.NativeTun, options Options) error {
	var servers []net.IP
	for _, server := range config.DNS {
		servers = append(servers, net.IP(server.AsSlice()))
	}
	var err error
	switch {
	case options.KillSwitch && config.FullTunnel(false):
		err = firewall.EnableKillSwitch(adapter.LUID(), config.DNS, options.Around)
	case options.KillSwitch:
		return errors.New("kill switch нужен туннель для всего трафика, а сервер отдал не весь")
	case len(servers) > 0:
		err = firewall.EnableDNSOnly(servers)
	default:
		return nil
	}
	if err != nil {
		return err
	}
	network.firewall = true
	return nil
}

func (network *systemNetwork) undo() {
	network.mutex.Lock()
	defer network.mutex.Unlock()
	if network.watcher != nil {
		_ = network.watcher.Unregister()
		network.watcher = nil
	}
	for family, callbacks := range network.callbacks {
		for _, callback := range callbacks {
			_ = callback.Unregister()
		}
		delete(network.callbacks, family)
	}
	if network.firewall {
		firewall.DisableFirewall()
		network.firewall = false
	}
}

func (network *systemNetwork) dnsState() DNSState {
	network.mutex.Lock()
	defer network.mutex.Unlock()
	return network.dns
}

// cleanup: the firewall rules live in a dynamic session of this process and the adapter goes
// when the device is closed, so a new process starts clean; nothing to take away by hand.
func cleanup() {}

func prefixNet(prefix netip.Prefix) net.IPNet {
	bits := 32
	if prefix.Addr().Is6() {
		bits = 128
	}
	return net.IPNet{IP: net.IP(prefix.Addr().AsSlice()), Mask: net.CIDRMask(prefix.Bits(), bits)}
}

// monitorDefaultRoutes binds the tunnel's socket to the best interface that is not the tunnel and
// follows it as routes change; with no other interface left the socket goes nowhere rather than
// into the tunnel itself (defaultroutemonitor.go of amneziawg-windows, without its MTU part: the
// config names the MTU). The networks sent around the tunnel follow it too: routed by that
// interface's gateway.
func monitorDefaultRoutes(family winipcfg.AddressFamily, binder conn.BindSocketToInterface, blackholeWhenLoop bool, adapter *tun.NativeTun, around []net.IPNet) ([]winipcfg.ChangeCallback, error) {
	ours := winipcfg.LUID(adapter.LUID())
	lastLUID := winipcfg.LUID(0)
	lastIndex := ^uint32(0)
	var aroundOn winipcfg.LUID
	var aroundBy net.IP
	moveAround := func(luid winipcfg.LUID, gateway net.IP) {
		if luid == aroundOn && gateway.Equal(aroundBy) {
			return
		}
		for _, destination := range around {
			if aroundOn != 0 {
				_ = aroundOn.DeleteRoute(destination, aroundBy)
			}
			if luid != 0 {
				_ = luid.AddRoute(destination, gateway, 0)
			}
		}
		aroundOn = luid
		aroundBy = gateway
	}
	bindBest := func() error {
		gateway, err := bindSocketRoute(family, binder, ours, &lastLUID, &lastIndex, blackholeWhenLoop)
		if err != nil {
			return err
		}
		moveAround(lastLUID, gateway)
		return nil
	}
	if err := bindBest(); err != nil {
		return nil, err
	}
	var burstMutex sync.Mutex
	var firstBurst time.Time
	burstTimer := time.AfterFunc(time.Hour*200, func() {
		burstMutex.Lock()
		firstBurst = time.Time{}
		_ = bindBest()
		burstMutex.Unlock()
	})
	burstTimer.Stop()
	bump := func() {
		burstMutex.Lock()
		defer burstMutex.Unlock()
		burstTimer.Reset(150 * time.Millisecond)
		if firstBurst.IsZero() {
			firstBurst = time.Now()
		} else if time.Since(firstBurst) > 2*time.Second {
			firstBurst = time.Time{}
			burstTimer.Stop()
			_ = bindBest()
		}
	}
	routeCallback, err := winipcfg.RegisterRouteChangeCallback(func(_ winipcfg.MibNotificationType, route *winipcfg.MibIPforwardRow2) {
		if route != nil && route.DestinationPrefix.PrefixLength == 0 {
			bump()
		}
	})
	if err != nil {
		return nil, err
	}
	interfaceCallback, err := winipcfg.RegisterInterfaceChangeCallback(func(notification winipcfg.MibNotificationType, _ *winipcfg.MibIPInterfaceRow) {
		if notification == winipcfg.MibParameterNotification {
			bump()
		}
	})
	if err != nil {
		_ = routeCallback.Unregister()
		return nil, err
	}
	// the routes around go when the tunnel goes: a callback whose Unregister takes them away
	return []winipcfg.ChangeCallback{routeCallback, interfaceCallback, &aroundRoutes{take: func() {
		burstMutex.Lock()
		defer burstMutex.Unlock()
		moveAround(0, nil)
	}}}, nil
}

// aroundRoutes stands among the callbacks so that unregistering them takes the routes around away.
type aroundRoutes struct {
	take func()
}

func (routes *aroundRoutes) Unregister() error {
	routes.take()
	return nil
}

// bindSocketRoute binds to the best default route that is not the tunnel and says its gateway.
func bindSocketRoute(family winipcfg.AddressFamily, binder conn.BindSocketToInterface, ours winipcfg.LUID, lastLUID *winipcfg.LUID, lastIndex *uint32, blackholeWhenLoop bool) (net.IP, error) {
	routes, err := winipcfg.GetIPForwardTable2(family)
	if err != nil {
		return nil, err
	}
	lowestMetric := ^uint32(0)
	index := uint32(0) // zero is "unspecified": IP_UNICAST_IF is reset
	luid := winipcfg.LUID(0)
	var gateway net.IP
	for i := range routes {
		if routes[i].DestinationPrefix.PrefixLength != 0 || routes[i].InterfaceLUID == ours {
			continue
		}
		row, err := routes[i].InterfaceLUID.Interface()
		if err != nil || row.OperStatus != winipcfg.IfOperStatusUp {
			continue
		}
		ipInterface, err := routes[i].InterfaceLUID.IPInterface(family)
		if err != nil {
			continue
		}
		if routes[i].Metric+ipInterface.Metric < lowestMetric {
			lowestMetric = routes[i].Metric + ipInterface.Metric
			index = routes[i].InterfaceIndex
			luid = routes[i].InterfaceLUID
			gateway = routes[i].NextHop.IP()
		}
	}
	if luid == *lastLUID && index == *lastIndex {
		return gateway, nil
	}
	*lastLUID = luid
	*lastIndex = index
	blackhole := blackholeWhenLoop && index == 0
	if family == windows.AF_INET {
		return gateway, binder.BindSocketToInterface4(index, blackhole)
	}
	return gateway, binder.BindSocketToInterface6(index, blackhole)
}
