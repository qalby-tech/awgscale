// What goes around the tunnel, as the service on a computer does it (app/qalby/conf: LocalNetworks,
// Routed): the networks the person sent around it and, when chosen, the local ones go around; the
// device's own network (its /24: the DNS, the app API, the devices of a space) and the DNS servers stay
// inside even when a local network covers them.

import Foundation
import Network
import WireGuardKit

enum Routes {
    /// The private ranges, carrier NAT's shared space (Tailscale and the like), link-local, IPv6 unique local.
    static let localNetworks = [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    ]

    /// The size of a network's subnet of devices (the server's CLIENT_SUBNET).
    static let networkBits: UInt8 = 24

    static func apply(to tunnel: TunnelConfiguration, around: [String], localAround: Bool) {
        let excluded = (around + (localAround ? localNetworks : [])).compactMap { IPAddressRange(from: $0) }
        let own = ownNetworks(tunnel.interface)
        for index in tunnel.peers.indices {
            tunnel.peers[index].excludeIPs += excluded
            let allowed = tunnel.peers[index].allowedIPs
            let kept = own.filter { network in
                allowed.contains { range in
                    range != network && range.networkPrefixLength <= network.networkPrefixLength && contains(range, network.address)
                }
            }
            tunnel.peers[index].allowedIPs += kept
        }
    }

    /// The /24 of each IPv4 address of the device, and each DNS server on its own.
    private static func ownNetworks(_ interface: InterfaceConfiguration) -> [IPAddressRange] {
        var own = [IPAddressRange]()
        for range in interface.addresses where range.address is IPv4Address {
            let bytes = [UInt8](range.address.rawValue)
            if let network = IPAddressRange(from: "\(bytes[0]).\(bytes[1]).\(bytes[2]).0/\(networkBits)") {
                own.append(network)
            }
        }
        for server in interface.dns {
            let bits = server.address is IPv4Address ? 32 : 128
            if let single = IPAddressRange(from: "\(server.address)/\(bits)") {
                own.append(single)
            }
        }
        return own
    }

    /// Whether the address lies in the range (same family, the first networkPrefixLength bits equal).
    private static func contains(_ range: IPAddressRange, _ address: IPAddress) -> Bool {
        let network = [UInt8](range.address.rawValue)
        let candidate = [UInt8](address.rawValue)
        guard network.count == candidate.count else { return false }
        var bits = Int(range.networkPrefixLength)
        for index in network.indices where bits > 0 {
            let take = min(bits, 8)
            let mask = UInt8(truncatingIfNeeded: 0xFF << (8 - take))
            if network[index] & mask != candidate[index] & mask {
                return false
            }
            bits -= take
        }
        return true
    }
}
