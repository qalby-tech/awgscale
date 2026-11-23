// The app's side of the tunnel on iOS. The tunnel itself runs in the Network Extension
// (app/ios/tunnel, bundle id <app>.tunnel); this plugin installs it as the device's VPN configuration
// (NETunnelProviderManager), starts and stops it, and says how it is. Only Rust calls it
// (app/tunnel-ios/src/ios.rs), never the interface. Every command answers {state, warning?}, the
// core's TunnelReport; a failure is a rejection whose message the window shows.

import Foundation
import NetworkExtension
import SwiftRs
import Tauri
import UIKit
import WebKit

/// The keys of the provider configuration; the extension reads the same (PacketTunnelProvider.swift).
enum ProviderKey {
    static let conf = "conf"
    static let around = "around"
    static let localAround = "localAround"
}

/// What the extension leaves in the app group when it could not start (PacketTunnelProvider.swift).
enum SharedFile {
    static let lastError = "last-error.txt"
}

/// The core's TunnelState, as Rust reads it.
enum ReportedState: String, Encodable {
    case down
    case connecting
    case up
}

struct Report: Encodable {
    let state: ReportedState
    let warning: String?
}

struct UpArgs: Decodable {
    let conf: String
    let killSwitch: Bool
    let localAround: Bool
    let around: [String]
}

/// How long the system may take to bring the tunnel up or down before the app stops waiting.
enum Timeout {
    static let up: TimeInterval = 30
    static let down: TimeInterval = 15
    /// The server answers while its last handshake is younger than this (as on a computer: 3 minutes).
    static let handshake: TimeInterval = 180
}

/// Waits for a connection's status to become one of `wanted`, or for the time to run out; `done` is
/// called once, with the status at that moment.
final class StatusWait {
    private let connection: NEVPNConnection
    private let wanted: [NEVPNStatus]
    private let done: (NEVPNStatus) -> Void
    private let lock = NSLock()
    private var observer: NSObjectProtocol?
    private var finished = false

    init(_ connection: NEVPNConnection, until wanted: [NEVPNStatus], seconds: TimeInterval, done: @escaping (NEVPNStatus) -> Void) {
        self.connection = connection
        self.wanted = wanted
        self.done = done
        observer = NotificationCenter.default.addObserver(forName: .NEVPNStatusDidChange, object: connection, queue: nil) { _ in
            if wanted.contains(connection.status) {
                self.finish()
            }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + seconds) {
            self.finish()
        }
    }

    private func finish() {
        lock.lock()
        let first = !finished
        finished = true
        let registered = observer
        observer = nil
        lock.unlock()
        guard first else { return }
        if let registered = registered {
            NotificationCenter.default.removeObserver(registered)
        }
        done(connection.status)
    }
}

class TunnelPlugin: Plugin {
    /// The extension's bundle id: the app's, with ".tunnel".
    private static var extensionIdentifier: String {
        (Bundle.main.bundleIdentifier ?? "tech.qalby.awgscale") + ".tunnel"
    }

    /// The app group the app and the extension share: "group." and the app's bundle id.
    private static var appGroup: String {
        "group." + (Bundle.main.bundleIdentifier ?? "tech.qalby.awgscale")
    }

    private static func report(_ status: NEVPNStatus) -> Report {
        switch status {
        case .connected:
            return Report(state: .up, warning: nil)
        case .connecting, .reasserting:
            return Report(state: .connecting, warning: nil)
        default:
            return Report(state: .down, warning: nil)
        }
    }

    /// The server's address as the configuration shows it (iOS wants one; the tunnel does not use it).
    private static func serverAddress(_ conf: String) -> String {
        for line in conf.split(whereSeparator: \.isNewline) {
            let parts = line.split(separator: "=", maxSplits: 1)
            if parts.count == 2, parts[0].trimmingCharacters(in: .whitespaces).lowercased() == "endpoint" {
                return parts[1].trimmingCharacters(in: .whitespaces)
            }
        }
        return "awgscale"
    }

    /// The device's configuration for our extension, if it has one.
    private func loadManager(_ done: @escaping (NETunnelProviderManager?, Error?) -> Void) {
        NETunnelProviderManager.loadAllFromPreferences { managers, error in
            if let error = error {
                done(nil, error)
                return
            }
            let ours = managers?.first { manager in
                (manager.protocolConfiguration as? NETunnelProviderProtocol)?.providerBundleIdentifier == TunnelPlugin.extensionIdentifier
            }
            done(ours, nil)
        }
    }

    /// Why the extension stopped, as well as the system or the extension can say it.
    private func lastError(_ connection: NEVPNConnection, _ done: @escaping (String) -> Void) {
        let written = FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: TunnelPlugin.appGroup)
            .flatMap { try? String(contentsOf: $0.appendingPathComponent(SharedFile.lastError), encoding: .utf8) }
        if let written = written, !written.isEmpty {
            done(written)
            return
        }
        if #available(iOS 16.0, *) {
            connection.fetchLastDisconnectError { error in
                done(error?.localizedDescription ?? "the system stopped the tunnel")
            }
        } else {
            done("the system stopped the tunnel")
        }
    }

    @objc public func up(_ invoke: Invoke) throws {
        let args = try invoke.parseArgs(UpArgs.self)
        loadManager { found, error in
            if let error = error {
                invoke.reject(error.localizedDescription)
                return
            }
            let manager = found ?? NETunnelProviderManager()
            let configuration = NETunnelProviderProtocol()
            configuration.providerBundleIdentifier = TunnelPlugin.extensionIdentifier
            configuration.serverAddress = TunnelPlugin.serverAddress(args.conf)
            configuration.providerConfiguration = [
                ProviderKey.conf: args.conf,
                ProviderKey.around: args.around,
                ProviderKey.localAround: args.localAround,
            ]
            // the kill switch: nothing leaves the device around the tunnel but what is sent around it
            configuration.includeAllNetworks = args.killSwitch
            configuration.excludeLocalNetworks = args.localAround
            manager.protocolConfiguration = configuration
            manager.localizedDescription = "awgscale"
            manager.isEnabled = true
            manager.saveToPreferences { error in
                if let error = error {
                    invoke.reject(error.localizedDescription)
                    return
                }
                // the system wants the saved configuration loaded again before it starts it
                manager.loadFromPreferences { error in
                    if let error = error {
                        invoke.reject(error.localizedDescription)
                        return
                    }
                    self.start(manager, invoke)
                }
            }
        }
    }

    /// A tunnel that is up is brought up again, with the new configuration.
    private func start(_ manager: NETunnelProviderManager, _ invoke: Invoke) {
        let connection = manager.connection
        if connection.status != .disconnected && connection.status != .invalid {
            _ = StatusWait(connection, until: [.disconnected, .invalid], seconds: Timeout.down) { _ in
                self.startStopped(manager, invoke)
            }
            connection.stopVPNTunnel()
            return
        }
        startStopped(manager, invoke)
    }

    private func startStopped(_ manager: NETunnelProviderManager, _ invoke: Invoke) {
        let connection = manager.connection
        if let container = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: TunnelPlugin.appGroup) {
            try? FileManager.default.removeItem(at: container.appendingPathComponent(SharedFile.lastError))
        }
        _ = StatusWait(connection, until: [.connected, .disconnected, .invalid], seconds: Timeout.up) { status in
            if status == .connected || status == .connecting || status == .reasserting {
                invoke.resolve(TunnelPlugin.report(status))
                return
            }
            self.lastError(connection) { reason in
                invoke.reject(reason)
            }
        }
        do {
            try connection.startVPNTunnel()
        } catch {
            invoke.reject(error.localizedDescription)
        }
    }

    @objc public func down(_ invoke: Invoke) {
        loadManager { found, _ in
            guard let manager = found else {
                invoke.resolve(Report(state: .down, warning: nil))
                return
            }
            let connection = manager.connection
            if connection.status == .disconnected || connection.status == .invalid {
                invoke.resolve(Report(state: .down, warning: nil))
                return
            }
            _ = StatusWait(connection, until: [.disconnected, .invalid], seconds: Timeout.down) { status in
                invoke.resolve(TunnelPlugin.report(status))
            }
            connection.stopVPNTunnel()
        }
    }

    /// Up only while the server answers: the extension is asked for its last handshake (message 0:
    /// the runtime configuration, as wireguard-apple's app asks it).
    @objc public func state(_ invoke: Invoke) {
        loadManager { found, _ in
            guard let manager = found else {
                invoke.resolve(Report(state: .down, warning: nil))
                return
            }
            let connection = manager.connection
            guard connection.status == .connected, let session = connection as? NETunnelProviderSession else {
                invoke.resolve(TunnelPlugin.report(connection.status))
                return
            }
            do {
                try session.sendProviderMessage(Data([0])) { answer in
                    let text = answer.flatMap { String(data: $0, encoding: .utf8) } ?? ""
                    let prefix = "last_handshake_time_sec="
                    let handshakes = text.split(separator: "\n")
                        .filter { $0.hasPrefix(prefix) }
                        .compactMap { TimeInterval($0.dropFirst(prefix.count)) }
                    let now = Date().timeIntervalSince1970
                    let answering = handshakes.contains { $0 > 0 && now - $0 < Timeout.handshake }
                    invoke.resolve(Report(state: answering ? .up : .connecting, warning: nil))
                }
            } catch {
                invoke.resolve(Report(state: .connecting, warning: nil))
            }
        }
    }
}

@_cdecl("init_plugin_awgscale_tunnel")
public func initPlugin() -> Plugin {
    return TunnelPlugin()
}
