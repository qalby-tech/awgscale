//! What the Swift side's refusals mean (Refusal and ConfigFault in ios/Sources/TunnelPlugin.swift and
//! app/ios/tunnel/PacketTunnelProvider.swift), in the app's words: the service's on a computer where it
//! has such. A refusal is a fixed code; what may go with it is a known config fault or a system error's
//! number. Anything else the Swift side might send is not shown, so no key or system text reaches the
//! window. Plain Rust, so that it is tested on any machine.

use awgscale_client::lang::say;
use awgscale_client::Error;

/// The codes of a refusal.
mod code {
    pub const INCOMPLETE: &str = "incomplete";
    pub const CONFIG: &str = "config";
    pub const RESOLVE: &str = "resolve";
    pub const NETWORK: &str = "network";
    pub const ENGINE: &str = "engine";
    pub const INTERFACE: &str = "interface";
    pub const BUSY: &str = "busy";
    pub const PREFERENCES: &str = "preferences";
    pub const START: &str = "start";
    pub const STOPPED: &str = "stopped";
}

/// What is wrong with a config (the extension's ParseError codes), as qalby's conf.Parse says it.
const CONFIG_FAULTS: &[(&str, &str)] = &[
    ("line", "not a key = value"),
    ("no-interface", "no [Interface]"),
    ("interfaces", "more than one [Interface]"),
    ("no-private-key", "no PrivateKey"),
    ("private-key", "bad PrivateKey"),
    ("listen-port", "bad ListenPort"),
    ("address", "bad Address"),
    ("dns", "bad DNS"),
    ("mtu", "bad MTU"),
    ("interface-key", "a key of [Interface] is not taken"),
    ("obfuscation", "bad value"),
    ("header-protection-key", "bad HeaderProtectionKey"),
    ("no-public-key", "no PublicKey"),
    ("public-key", "bad PublicKey"),
    ("preshared-key", "bad PresharedKey"),
    ("allowed-ips", "bad AllowedIPs"),
    ("endpoint", "bad Endpoint"),
    ("keepalive", "bad PersistentKeepalive"),
    ("transfer", "bad value"),
    ("handshake-time", "bad value"),
    ("peer-key", "a key of [Peer] is not taken"),
    ("peers", "two [Peer] with one PublicKey"),
    ("repeated-key", "a key is given twice"),
];

/// A system error's number, when that is what came: "(код 5)".
fn number(detail: &str) -> String {
    if !detail.is_empty() && detail.len() <= 12 && detail.trim_start_matches('-').chars().all(|digit| digit.is_ascii_digit()) {
        format!(" ({} {detail})", say("код", "code"))
    } else {
        String::new()
    }
}

/// The refusal of the Swift side (its code and its detail), as the window says it.
pub fn refused(code: Option<&str>, detail: &str) -> Error {
    let reason = match code {
        Some(code::INCOMPLETE) => say(
            "настройки VPN на устройстве неполные: включите VPN из приложения ещё раз",
            "the VPN settings on the device are incomplete: turn the VPN on from the app again",
        ),
        Some(code::CONFIG) => match CONFIG_FAULTS.iter().find(|(fault, _)| *fault == detail) {
            Some((_, said)) => format!("конфиг устройства не подходит: {said}"),
            None => "конфиг устройства не подходит".to_owned(),
        },
        Some(code::RESOLVE) => "не удалось найти сервер".to_owned(),
        Some(code::NETWORK) => "не удалось настроить сеть".to_owned(),
        Some(code::ENGINE) => "не удалось включить интерфейс".to_owned(),
        Some(code::INTERFACE) => "не удалось создать интерфейс".to_owned(),
        Some(code::BUSY) => say("VPN уже включается", "the VPN is already being turned on"),
        Some(code::PREFERENCES) => {
            say(
                "iOS не сохранил настройки VPN: возможно, разрешение на VPN не дано",
                "iOS did not save the VPN settings: perhaps the VPN was not allowed",
            ) + &number(detail)
        }
        Some(code::START) => say("iOS не запустил VPN", "iOS did not start the VPN") + &number(detail),
        Some(code::STOPPED) => say("iOS остановил VPN", "iOS stopped the VPN") + &number(detail),
        _ => "сбой".to_owned(),
    };
    Error::Tunnel(reason)
}

/// Whether the config sends all IPv4 traffic into the tunnel (an AllowedIPs of /0): what a kill switch
/// needs, as the service on a computer checks it (qalby conf.FullTunnel(false)).
pub fn full_tunnel(conf: &str) -> bool {
    conf.lines()
        .map(|line| line.split('#').next().unwrap_or_default())
        .filter_map(|line| line.split_once('='))
        .filter(|(key, _)| key.trim().eq_ignore_ascii_case("AllowedIPs"))
        .flat_map(|(_, value)| value.split(','))
        .filter_map(|prefix| prefix.trim().split_once('/'))
        .any(|(address, bits)| bits.trim() == "0" && address.trim().parse::<std::net::Ipv4Addr>().is_ok())
}

/// The service's own words on a computer (app/qalby/tunnel: linux.go, windows.go).
pub fn kill_switch_refused() -> Error {
    Error::Tunnel("kill switch нужен туннель для всего трафика, а сервер отдал не весь".to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shown(error: Error) -> String {
        match error {
            Error::Tunnel(reason) => reason,
            _ => panic!("not a tunnel's error"),
        }
    }

    #[test]
    fn a_config_fault_is_said_only_when_known() {
        assert_eq!(shown(refused(Some("config"), "private-key")), "конфиг устройства не подходит: bad PrivateKey");
        // anything else that came with it (as a key would) is not shown
        let key = "made+up+not+a+key+made+up+not+a+key+made+up=";
        assert_eq!(shown(refused(Some("config"), key)), "конфиг устройства не подходит");
        assert!(!shown(refused(Some("stopped"), key)).contains(key));
        assert!(!shown(refused(None, key)).contains(key));
        assert_eq!(shown(refused(Some("whatever"), key)), "сбой");
    }

    #[test]
    fn a_system_error_is_its_number_only() {
        assert!(shown(refused(Some("preferences"), "5")).ends_with("(код 5)"));
        assert!(shown(refused(Some("start"), "-1")).ends_with("(код -1)"));
        assert_eq!(shown(refused(Some("stopped"), "")), "iOS остановил VPN");
        assert_eq!(shown(refused(Some("stopped"), "The operation couldn’t be completed")), "iOS остановил VPN");
    }

    #[test]
    fn the_desktop_words_where_it_has_them() {
        assert_eq!(shown(refused(Some("resolve"), "vpn.example.org")), "не удалось найти сервер");
        assert_eq!(shown(refused(Some("network"), "")), "не удалось настроить сеть");
        assert_eq!(shown(kill_switch_refused()), "kill switch нужен туннель для всего трафика, а сервер отдал не весь");
    }

    #[test]
    fn full_only_with_an_ipv4_default_route() {
        let peer = |allowed: &str| format!("[Interface]\nPrivateKey = made-up\n\n[Peer]\nPublicKey = made-up\nAllowedIPs = {allowed}\n");
        assert!(full_tunnel(&peer("0.0.0.0/0")));
        assert!(full_tunnel(&peer("172.16.17.0/24, 0.0.0.0/0, ::/0")));
        assert!(full_tunnel("[Peer]\nallowedips=0.0.0.0/0"));
        assert!(!full_tunnel(&peer("172.16.17.0/24")));
        assert!(!full_tunnel(&peer("::/0")));
        assert!(!full_tunnel(&peer("172.16.17.0/24 # 0.0.0.0/0")));
        assert!(!full_tunnel("[Interface]\nPrivateKey = made-up\n"));
    }
}
