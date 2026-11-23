// The app's side of the tunnel on iOS. The tunnel itself runs in the Network Extension
// (app/ios/tunnel, bundle id <app>.tunnel); this plugin installs it as the device's VPN configuration
// (NETunnelProviderManager), starts and stops it, and says how it is. Only Rust calls it
// (app/tunnel-ios/src/ios.rs), never the interface. Every command answers {state, warning?}, the
// core's TunnelReport; a failure is a rejection with a fixed code (Refusal) that the Rust side says in the
// app's words (app/tunnel-ios/src/refusal.rs). No rejection carries a value or a system's error text.

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

/// Why a command failed: the codes of the extension (PacketTunnelProvider.swift) and the app's own;
/// app/tunnel-ios/src/refusal.rs knows the same.
enum Refusal: String {
    // the extension's
    case incomplete
    case config
    case resolve
    case network
    case engine
    case interface
    case busy
    // the app's
    case arguments
    case preferences
    case start
    case stopped
}

/// What the extension may name after "config:" (TunnelConfiguration.ParseError's codes in
/// app/ios/tunnel/WgQuickConfig.swift).
enum ConfigFault: String {
    case line
    case noInterface = "no-interface"
    case interfaces
    case noPrivateKey = "no-private-key"
    case privateKey = "private-key"
    case listenPort = "listen-port"
    case address
    case dns
    case mtu
    case interfaceKey = "interface-key"
    case obfuscation
    case headerProtectionKey = "header-protection-key"
    case noPublicKey = "no-public-key"
    case publicKey = "public-key"
    case presharedKey = "preshared-key"
    case allowedIPs = "allowed-ips"
    case endpoint
    case keepalive
    case transfer
    case handshakeTime = "handshake-time"
    case peerKey = "peer-key"
    case peers
    case repeatedKey = "repeated-key"
}

/// A refusal and what may go with it: a ConfigFault, or a system error's number. Nothing else.
struct Rejection {
    let refusal: Refusal
    let detail: String

    init(_ refusal: Refusal, _ detail: String = "") {
        self.refusal = refusal
        self.detail = detail
    }

    /// A system error as its number only: its text is the system's and is not passed on.
    init(_ refusal: Refusal, _ error: Error) {
        self.init(refusal, String((error as NSError).code))
    }

    /// What the extension wrote ("<refusal>" or "config:<fault>"), if it is one of the known codes.
    init?(written: String) {
        let parts = written.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: ":", maxSplits: 1).map(String.init)
        guard let first = parts.first, let refusal = Refusal(rawValue: first) else { return nil }
        if parts.count == 2, refusal == .config, let fault = ConfigFault(rawValue: parts[1]) {
            self.init(refusal, fault.rawValue)
        } else {
            self.init(refusal)
        }
    }
}

/// Answers a call once: a wait that times out after a failure was already answered stays silent.
final class Reply {
    private let invoke: Invoke
    private let lock = NSLock()
    private var answered = false

    init(_ invoke: Invoke) {
        self.invoke = invoke
    }

    private func first() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let first = !answered
        answered = true
        return first
    }

    func resolve(_ report: Report) {
        if first() {
            invoke.resolve(report)
        }
    }

    func reject(_ rejection: Rejection) {
        if first() {
            invoke.reject(rejection.detail, code: rejection.refusal.rawValue)
        }
    }
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

    /// Why the extension stopped, as well as the extension (its code) or the system (its number) can say it.
    private func lastError(_ connection: NEVPNConnection, _ done: @escaping (Rejection) -> Void) {
        let written = FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: TunnelPlugin.appGroup)
            .flatMap { try? String(contentsOf: $0.appendingPathComponent(SharedFile.lastError), encoding: .utf8) }
            .flatMap { Rejection(written: $0) }
        if let written = written {
            done(written)
            return
        }
        if #available(iOS 16.0, *) {
            connection.fetchLastDisconnectError { error in
                done(error.map { Rejection(.stopped, $0) } ?? Rejection(.stopped))
            }
        } else {
            done(Rejection(.stopped))
        }
    }

    @objc public func up(_ invoke: Invoke) {
        let reply = Reply(invoke)
        guard let args = try? invoke.parseArgs(UpArgs.self) else {
            reply.reject(Rejection(.arguments))
            return
        }
        loadManager { found, error in
            if let error = error {
                reply.reject(Rejection(.preferences, error))
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
            // the kill switch: nothing leaves the device around the tunnel but what is sent around it (the
            // Rust side refuses it for a config that is not for all traffic, as the service on a computer)
            configuration.includeAllNetworks = args.killSwitch
            configuration.excludeLocalNetworks = args.localAround
            manager.protocolConfiguration = configuration
            manager.localizedDescription = "awgscale"
            manager.isEnabled = true
            manager.saveToPreferences { error in
                if let error = error {
                    reply.reject(Rejection(.preferences, error))
                    return
                }
                // the system wants the saved configuration loaded again before it starts it
                manager.loadFromPreferences { error in
                    if let error = error {
                        reply.reject(Rejection(.preferences, error))
                        return
                    }
                    self.start(manager, reply)
                }
            }
        }
    }

    /// A tunnel that is up is brought up again, with the new configuration.
    private func start(_ manager: NETunnelProviderManager, _ reply: Reply) {
        let connection = manager.connection
        if connection.status != .disconnected && connection.status != .invalid {
            _ = StatusWait(connection, until: [.disconnected, .invalid], seconds: Timeout.down) { _ in
                self.startStopped(manager, reply)
            }
            connection.stopVPNTunnel()
            return
        }
        startStopped(manager, reply)
    }

    private func startStopped(_ manager: NETunnelProviderManager, _ reply: Reply) {
        let connection = manager.connection
        if let container = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: TunnelPlugin.appGroup) {
            try? FileManager.default.removeItem(at: container.appendingPathComponent(SharedFile.lastError))
        }
        _ = StatusWait(connection, until: [.connected, .disconnected, .invalid], seconds: Timeout.up) { status in
            if status == .connected || status == .connecting || status == .reasserting {
                reply.resolve(TunnelPlugin.report(status))
                return
            }
            self.lastError(connection) { rejection in
                reply.reject(rejection)
            }
        }
        do {
            try connection.startVPNTunnel()
        } catch {
            reply.reject(Rejection(.start, error))
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
