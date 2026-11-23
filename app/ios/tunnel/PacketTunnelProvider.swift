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

/// Why the tunnel did not start: a fixed code, the same in the app (TunnelPlugin.swift) and in its Rust
/// (app/tunnel-ios/src/refusal.rs), which says it in the app's words. Never a value: what a library's
/// error says can hold the config's keys or the server's name, so it goes nowhere.
enum Refusal: String {
    case incomplete
    case config
    case resolve
    case network
    case engine
    case interface
    case busy
}

/// A reason the tunnel did not start, as the system is told it: the code only.
struct TunnelFailure: LocalizedError {
    let code: String

    var errorDescription: String? { code }
}

final class PacketTunnelProvider: NEPacketTunnelProvider {
    private let log = OSLog(subsystem: "tech.qalby.awgscale.tunnel", category: "tunnel")

    // The library's own lines are not logged: they name the server and repeat amneziawg-go's errors,
    // which may quote what the config holds. What failed is said by the Refusal codes below.
    private lazy var adapter: WireGuardAdapter = {
        WireGuardAdapter(with: self) { _, _ in }
    }()

    /// The app group shared with the app: "group." and the app's bundle id (this one's without ".tunnel").
    private var sharedContainer: URL? {
        let identifier = Bundle.main.bundleIdentifier ?? "tech.qalby.awgscale.tunnel"
        let app = identifier.hasSuffix(".tunnel") ? String(identifier.dropLast(".tunnel".count)) : identifier
        return FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: "group." + app)
    }

    /// `detail` is a fixed code too (a ParseError's), never a value.
    private func failed(_ refusal: Refusal, _ detail: String?, _ completionHandler: @escaping (Error?) -> Void) {
        let code = detail.map { refusal.rawValue + ":" + $0 } ?? refusal.rawValue
        os_log("the tunnel did not start: %{public}@", log: log, type: .error, code)
        if let container = sharedContainer {
            try? code.write(to: container.appendingPathComponent(SharedFile.lastError), atomically: true, encoding: .utf8)
        }
        completionHandler(TunnelFailure(code: code))
    }

    override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        guard let configuration = protocolConfiguration as? NETunnelProviderProtocol,
              let saved = configuration.providerConfiguration,
              let conf = saved[ProviderKey.conf] as? String else {
            failed(.incomplete, nil, completionHandler)
            return
        }
        let tunnel: TunnelConfiguration
        do {
            tunnel = try TunnelConfiguration(fromWgQuickConfig: conf, called: "awgscale")
        } catch let error as TunnelConfiguration.ParseError {
            failed(.config, error.rawValue, completionHandler)
            return
        } catch {
            failed(.config, nil, completionHandler)
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
            case .dnsResolution:
                self.failed(.resolve, nil, completionHandler)
            case .setNetworkSettings:
                self.failed(.network, nil, completionHandler)
            case .startWireGuardBackend:
                self.failed(.engine, nil, completionHandler)
            case .cannotLocateTunnelFileDescriptor:
                self.failed(.interface, nil, completionHandler)
            case .invalidState:
                self.failed(.busy, nil, completionHandler)
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
