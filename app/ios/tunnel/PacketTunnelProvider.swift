// The tunnel on iOS: a Packet Tunnel Provider the system runs in its own process. The app
// (app/tunnel-ios/ios/Sources/TunnelPlugin.swift) saves the network's config and the person's choices in
// the provider configuration and starts it; WireGuardKit of amneziawg-apple (amneziawg-go inside)
// carries the packets. Built only when the extension is (AWGSCALE_IOS_TUNNEL=YES, app/ios/tunnel.yml).

import Foundation
import NetworkExtension
import os
import WireGuardKit

/// The keys of the provider configuration; the app writes the same (TunnelPlugin.swift).
enum ProviderKey {
    static let conf = "conf"
    static let around = "around"
    static let localAround = "localAround"
}

/// What the extension leaves in the app group when it could not start, for the app to show.
enum SharedFile {
    static let lastError = "last-error.txt"
}

/// A reason the tunnel did not start, in words the app shows.
struct TunnelFailure: LocalizedError {
    let reason: String

    var errorDescription: String? { reason }
}

final class PacketTunnelProvider: NEPacketTunnelProvider {
    private let log = OSLog(subsystem: "tech.qalby.awgscale.tunnel", category: "tunnel")

    private lazy var adapter: WireGuardAdapter = {
        WireGuardAdapter(with: self) { [log] _, message in
            os_log("%{public}@", log: log, type: .info, message)
        }
    }()

    /// The app group shared with the app: "group." and the app's bundle id (this one's without ".tunnel").
    private var sharedContainer: URL? {
        let identifier = Bundle.main.bundleIdentifier ?? "tech.qalby.awgscale.tunnel"
        let app = identifier.hasSuffix(".tunnel") ? String(identifier.dropLast(".tunnel".count)) : identifier
        return FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: "group." + app)
    }

    private func failed(_ reason: String, _ completionHandler: @escaping (Error?) -> Void) {
        os_log("the tunnel did not start: %{public}@", log: log, type: .error, reason)
        if let container = sharedContainer {
            try? reason.write(to: container.appendingPathComponent(SharedFile.lastError), atomically: true, encoding: .utf8)
        }
        completionHandler(TunnelFailure(reason: reason))
    }

    override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        guard let configuration = protocolConfiguration as? NETunnelProviderProtocol,
              let saved = configuration.providerConfiguration,
              let conf = saved[ProviderKey.conf] as? String else {
            failed("the VPN configuration on the device is incomplete: connect from the app again", completionHandler)
            return
        }
        let tunnel: TunnelConfiguration
        do {
            tunnel = try TunnelConfiguration(fromWgQuickConfig: conf, called: "awgscale")
        } catch {
            failed("the network's config cannot be read: \(error)", completionHandler)
            return
        }
        Routes.apply(
            to: tunnel,
            around: saved[ProviderKey.around] as? [String] ?? [],
            localAround: saved[ProviderKey.localAround] as? Bool ?? false)
        adapter.start(tunnelConfiguration: tunnel) { error in
            guard let error = error else {
                completionHandler(nil)
                return
            }
            switch error {
            case .dnsResolution(let failures):
                self.failed("the server's name is not found: " + failures.map { $0.address }.joined(separator: ", "), completionHandler)
            case .setNetworkSettings(let reason):
                self.failed("the system refused the tunnel's settings: \(reason.localizedDescription)", completionHandler)
            case .startWireGuardBackend(let code):
                self.failed("the tunnel's engine did not start (\(code))", completionHandler)
            case .cannotLocateTunnelFileDescriptor:
                self.failed("the system gave the tunnel no interface", completionHandler)
            case .invalidState:
                self.failed("the tunnel is already starting", completionHandler)
            }
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        adapter.stop { _ in
            completionHandler()
        }
    }

    /// Message 0 (the only one): the runtime configuration, whose last_handshake_time_sec says
    /// whether the server answers.
    override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)? = nil) {
        guard let completionHandler = completionHandler else { return }
        adapter.getRuntimeConfiguration { settings in
            completionHandler(settings?.data(using: .utf8))
        }
    }
}
