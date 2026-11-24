//! A connection given by hand, without a panel: what the AmneziaVPN app imports too.
//!
//! * an Amnezia key, `vpn://…`: URL-safe base64 (no padding) of the JSON of a server, compressed the
//!   way Qt's qCompress does it (the length, 4 bytes big-endian, then zlib) or not compressed at all;
//!   its AmneziaWG container holds the config (`last_config`, itself JSON) — what our panel and bot
//!   hand out (src/awgscale/users/device.py) and what the AmneziaVPN app shares;
//! * the QR code of such a key as the AmneziaVPN app reads it: a frame (magic 1984, how many codes,
//!   which one, the compressed key as Qt writes a byte array), URL-safe base64; only a key in one code;
//! * an AmneziaWG config itself, `[Interface] … [Peer] …`.
//!
//! Whatever the form, the config is read line by line and written anew with only the keys every
//! tunnel of the app takes (app/qalby/conf, the Network Extension's parser): a key that only means
//! something to wg-quick on a computer (PostUp, Table…) is left out, anything else unknown is refused.
//! Nothing of a key or a config is ever put into an error.

use std::io::Read;
use std::net::{IpAddr, Ipv4Addr};

use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use base64::Engine;
use serde_json::Value;
use zeroize::Zeroizing;

use crate::error::Error;
use crate::secret::Secret;

/// The scheme of an Amnezia key.
pub const KEY_SCHEME: &str = "vpn://";
/// The first two bytes of an AmneziaVPN QR frame (1984).
const FRAME_MAGIC: [u8; 2] = [0x07, 0xC0];
/// magic, how many codes, which one, the data's length
const FRAME_HEADER_LENGTH: usize = 2 + 1 + 1 + 4;
const QT_LENGTH_BYTES: usize = 4;
/// Nothing given or unpacked is longer: a config is a few hundred bytes, a key with all of Amnezia's
/// containers a few kilobytes.
const MAX_LENGTH: usize = 64 * 1024;
const KEY_LENGTH: usize = 32;
const KEY_NAME_SHOWN_MAX: usize = 32;
const PRIMARY_DNS: &str = "$PRIMARY_DNS";
const SECONDARY_DNS: &str = "$SECONDARY_DNS";
const INTERFACE: &str = "[Interface]";
const PEER: &str = "[Peer]";

/// The keys of [Interface] every tunnel takes, as they are written.
const INTERFACE_KEYS: &[&str] = &[
    "PrivateKey",
    "Address",
    "DNS",
    "MTU",
    "Jc",
    "Jmin",
    "Jmax",
    "S1",
    "S2",
    "S3",
    "S4",
    "H1",
    "H2",
    "H3",
    "H4",
    "I1",
    "I2",
    "I3",
    "I4",
    "I5",
    "HeaderProtectionKey",
    "ContentPaddingAddition",
];
/// The keys of [Peer] every tunnel takes.
const PEER_KEYS: &[&str] = &["PublicKey", "PresharedKey", "Endpoint", "AllowedIPs", "PersistentKeepalive"];
/// What only wg-quick on a computer reads (the port it listens on, its routing table, its commands):
/// a phone or the app's service has no use for it, so it is left out.
const LOCAL_ONLY_KEYS: &[&str] = &["ListenPort", "FwMark", "Table", "SaveConfig", "PreUp", "PostUp", "PreDown", "PostDown"];
/// The obfuscation keys of an Amnezia key's `last_config`, the same names as in a config.
const OBFUSCATION_KEYS: &[&str] = &[
    "Jc",
    "Jmin",
    "Jmax",
    "S1",
    "S2",
    "S3",
    "S4",
    "H1",
    "H2",
    "H3",
    "H4",
    "I1",
    "I2",
    "I3",
    "I4",
    "I5",
    "HeaderProtectionKey",
    "ContentPaddingAddition",
];
/// The keys whose value is a list, which wg-quick also takes over several lines.
const LIST_KEYS: &[&str] = &["Address", "DNS", "AllowedIPs"];
const DEFAULT_ALLOWED_IPS: &str = "0.0.0.0/0, ::/0";

/// A connection given by hand: its config, written anew, and what the app shows of it.
#[derive(Debug)]
pub struct Manual {
    /// How the connection is called: the key's description, or the server's name.
    pub name: String,
    /// The server's name or address (the config's Endpoint, without its port).
    pub server: String,
    /// The device's own IPv4 address in the tunnel.
    pub address: Ipv4Addr,
    /// The tunnel's DNS server, when the config names an IPv4 one.
    pub dns: Option<Ipv4Addr>,
    /// The config, with only the keys the tunnels take.
    pub conf: Secret,
}

/// Reads what the person pasted, typed, opened or scanned: an Amnezia key, its QR frame or a config.
pub fn read(text: &str) -> Result<Manual, Error> {
    let text = text.trim();
    if text.len() > MAX_LENGTH {
        return Err(Error::NotAConnection);
    }
    if let Some(key) =
        text.get(..KEY_SCHEME.len()).filter(|scheme| scheme.eq_ignore_ascii_case(KEY_SCHEME)).and(text.get(KEY_SCHEME.len()..))
    {
        let packed = Zeroizing::new(URL_SAFE_NO_PAD.decode(base64_of(key).as_bytes()).map_err(|_| Error::NotAConnection)?);
        return amnezia(&packed);
    }
    if text.to_ascii_lowercase().contains(&INTERFACE.to_ascii_lowercase()) {
        return config(text, None);
    }
    let frame = Zeroizing::new(URL_SAFE_NO_PAD.decode(base64_of(text).as_bytes()).map_err(|_| Error::NotAConnection)?);
    amnezia(&framed(&frame)?)
}

/// A key's base64 as a messenger may hand it over: wrapped over lines, with or without its padding.
fn base64_of(text: &str) -> Zeroizing<String> {
    Zeroizing::new(text.chars().filter(|letter| !letter.is_whitespace() && *letter != '=').collect())
}

/// The data of an AmneziaVPN QR frame: the compressed key.
fn framed(frame: &[u8]) -> Result<Vec<u8>, Error> {
    if frame.len() < FRAME_HEADER_LENGTH || frame[..2] != FRAME_MAGIC {
        return Err(Error::NotAConnection);
    }
    // the AmneziaVPN app counts its codes by an integer division: a key shorter than one code's 850 bytes
    // says 0 (amnezia-client, qrCodeUtils.cpp); ours says 1
    let codes = frame[2];
    let index = frame[3];
    if codes > 1 || index != 0 {
        return Err(Error::OneOfManyCodes);
    }
    let length = u32::from_be_bytes([frame[4], frame[5], frame[6], frame[7]]) as usize;
    let data = &frame[FRAME_HEADER_LENGTH..];
    if length != data.len() {
        return Err(Error::NotAConnection);
    }
    Ok(data.to_vec())
}

/// Qt's qUncompress: the length the data unpacks to (4 bytes, big-endian), then zlib. A key the
/// AmneziaVPN app did not compress is its JSON itself.
fn uncompressed(packed: &[u8]) -> Result<Zeroizing<Vec<u8>>, Error> {
    if packed.first() == Some(&b'{') {
        return Ok(Zeroizing::new(packed.to_vec()));
    }
    if packed.len() <= QT_LENGTH_BYTES {
        return Err(Error::NotAConnection);
    }
    let expected = u32::from_be_bytes([packed[0], packed[1], packed[2], packed[3]]) as usize;
    if expected > MAX_LENGTH {
        return Err(Error::NotAConnection);
    }
    let mut json = Zeroizing::new(Vec::with_capacity(expected));
    flate2::read::ZlibDecoder::new(&packed[QT_LENGTH_BYTES..])
        .take(MAX_LENGTH as u64 + 1)
        .read_to_end(&mut json)
        .map_err(|_| Error::NotAConnection)?;
    if json.len() > MAX_LENGTH {
        return Err(Error::NotAConnection);
    }
    Ok(json)
}

/// A value of Amnezia's JSON as text: it writes some numbers as numbers, others as strings, and a list
/// (allowed_ips) as an array.
fn text_of(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::String(text) if !text.trim().is_empty() => Some(text.trim().to_owned()),
        Value::Number(number) => Some(number.to_string()),
        Value::Array(items) => {
            let items: Vec<String> = items.iter().filter_map(|item| text_of(Some(item))).collect();
            (!items.is_empty()).then(|| items.join(", "))
        }
        _ => None,
    }
}

/// A container's AmneziaWG part, when it is one.
fn awg_of(container: &Value) -> Option<&Value> {
    container.get("awg").filter(|awg| awg.is_object())
}

/// The key's JSON: its AmneziaWG container (the default one when it has several), the config in it.
fn amnezia(packed: &[u8]) -> Result<Manual, Error> {
    let json = uncompressed(packed)?;
    let server: Value = serde_json::from_slice(&json).map_err(|_| Error::NotAConnection)?;
    let containers = server.get("containers").and_then(Value::as_array).ok_or(Error::NotAConnection)?;
    let default = server.get("defaultContainer").and_then(Value::as_str);
    let chosen = containers
        .iter()
        .find(|container| container.get("container").and_then(Value::as_str) == default && awg_of(container).is_some())
        .or_else(|| containers.iter().find(|container| awg_of(container).is_some()))
        .and_then(awg_of)
        .ok_or(Error::NoAmneziaWg)?;
    let last = Zeroizing::new(chosen.get("last_config").and_then(Value::as_str).ok_or(Error::NotAConnection)?.to_owned());
    let inner: Value = serde_json::from_str(&last).map_err(|_| Error::NotAConnection)?;
    let dns: Vec<String> = ["dns1", "dns2"].iter().filter_map(|key| text_of(server.get(*key))).collect();
    let conf = match text_of(inner.get("config")) {
        Some(written) => Zeroizing::new(with_dns(&written, &dns)),
        None => Zeroizing::new(from_fields(&inner, &server, &dns)?),
    };
    let name = text_of(server.get("description"));
    config(&conf, name)
}

/// The config Amnezia's servers write leaves the DNS to the app: `DNS = $PRIMARY_DNS, $SECONDARY_DNS`,
/// filled from the key's dns1 and dns2 (an empty one dropped).
fn with_dns(conf: &str, dns: &[String]) -> String {
    let primary = dns.first().map(String::as_str).unwrap_or("");
    let secondary = dns.get(1).map(String::as_str).unwrap_or("");
    conf.replace(PRIMARY_DNS, primary).replace(SECONDARY_DNS, secondary)
}

/// A key whose `last_config` has no config text: the config made from its fields, as the AmneziaVPN app does.
fn from_fields(inner: &Value, server: &Value, dns: &[String]) -> Result<String, Error> {
    let field = |key: &str| text_of(inner.get(key));
    let required = |key: &str| field(key).ok_or(Error::NotAConnection);
    let host = field("hostName").or_else(|| text_of(server.get("hostName"))).ok_or(Error::NotAConnection)?;
    let mut lines = vec![INTERFACE.to_owned()];
    lines.push(format!("PrivateKey = {}", required("client_priv_key")?));
    let address = required("client_ip")?;
    lines.push(if address.contains('/') { format!("Address = {address}") } else { format!("Address = {address}/32") });
    if !dns.is_empty() {
        lines.push(format!("DNS = {}", dns.join(", ")));
    }
    if let Some(mtu) = field("mtu") {
        lines.push(format!("MTU = {mtu}"));
    }
    for key in OBFUSCATION_KEYS {
        if let Some(value) = field(key) {
            lines.push(format!("{key} = {value}"));
        }
    }
    lines.push(String::new());
    lines.push(PEER.to_owned());
    lines.push(format!("PublicKey = {}", required("server_pub_key")?));
    if let Some(preshared) = field("psk_key") {
        lines.push(format!("PresharedKey = {preshared}"));
    }
    lines.push(format!("Endpoint = {host}:{}", required("port")?));
    lines.push(format!("AllowedIPs = {}", field("allowed_ips").unwrap_or_else(|| DEFAULT_ALLOWED_IPS.to_owned())));
    if let Some(keepalive) = field("persistent_keep_alive") {
        lines.push(format!("PersistentKeepalive = {keepalive}"));
    }
    Ok(lines.join("\n") + "\n")
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Section {
    None,
    Interface,
    Peer,
}

/// The config read line by line and written anew: [Interface], then the one [Peer], with only the keys
/// the tunnels take, each once.
fn config(text: &str, name: Option<String>) -> Result<Manual, Error> {
    let mut section = Section::None;
    let mut interface: Vec<(&'static str, String)> = Vec::new();
    let mut peer: Vec<(&'static str, String)> = Vec::new();
    let mut peers = 0;
    let mut interfaces = 0;
    // lines end in \n, \r\n (Windows) or \r (what some text fields hand over)
    for line in text.split(['\n', '\r']).map(str::trim) {
        if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
            continue; // only whole lines are comments: I1…I5 may hold '#' inside their tags
        }
        if line.starts_with('[') {
            if line.eq_ignore_ascii_case(INTERFACE) {
                section = Section::Interface;
                interfaces += 1;
            } else if line.eq_ignore_ascii_case(PEER) {
                section = Section::Peer;
                peers += 1;
            } else {
                return Err(Error::NotAConnection);
            }
            continue;
        }
        let (key, value) = line.split_once('=').ok_or(Error::NotAConnection)?;
        let key = key.trim();
        let value = value.trim();
        if LOCAL_ONLY_KEYS.iter().any(|known| known.eq_ignore_ascii_case(key)) {
            continue;
        }
        let (known, values) = match section {
            Section::Interface => (INTERFACE_KEYS, &mut interface),
            Section::Peer => (PEER_KEYS, &mut peer),
            Section::None => return Err(Error::NotAConnection),
        };
        let Some(name) = known.iter().find(|known| known.eq_ignore_ascii_case(key)) else {
            return Err(Error::UnknownKey(key.chars().take(KEY_NAME_SHOWN_MAX).collect()));
        };
        // an unfilled template ($WIREGUARD_CLIENT_PRIVATE_KEY…) is not a config yet
        if (value.is_empty() && *name != "DNS") || value.contains('$') {
            return Err(Error::NotAConnection);
        }
        match values.iter_mut().find(|(taken, _)| taken == name) {
            // a list may be given on several lines, as wg-quick takes it
            Some((_, list)) if LIST_KEYS.contains(name) => {
                if !value.is_empty() {
                    *list = if list.is_empty() { value.to_owned() } else { format!("{list}, {value}") };
                }
            }
            Some(_) => return Err(Error::NotAConnection),
            None => values.push((name, value.to_owned())),
        }
    }
    if interfaces != 1 || peers != 1 {
        return Err(Error::NotAConnection);
    }
    let value_of = |values: &[(&str, String)], key: &str| values.iter().find(|(taken, _)| *taken == key).map(|(_, value)| value.clone());
    for (values, key) in [(&interface, "PrivateKey"), (&peer, "PublicKey"), (&peer, "PresharedKey"), (&interface, "HeaderProtectionKey")] {
        if let Some(value) = value_of(values, key) {
            let decoded = Zeroizing::new(STANDARD.decode(&value).map_err(|_| Error::NotAConnection)?);
            if decoded.len() != KEY_LENGTH {
                return Err(Error::NotAConnection);
            }
        }
    }
    let list = |value: Option<String>| {
        value.map(|value| value.split(',').map(|item| item.trim().to_owned()).filter(|item| !item.is_empty()).collect::<Vec<_>>())
    };
    let address = list(value_of(&interface, "Address"))
        .unwrap_or_default()
        .iter()
        .find_map(|item| item.split('/').next().and_then(|address| address.parse::<Ipv4Addr>().ok()))
        .ok_or(Error::NotAConnection)?;
    // a DNS line may name search domains too: only the addresses are kept
    let dns: Vec<IpAddr> = list(value_of(&interface, "DNS")).unwrap_or_default().iter().filter_map(|item| item.parse().ok()).collect();
    let addresses = dns.iter().map(IpAddr::to_string).collect::<Vec<_>>().join(", ");
    for (key, value) in interface.iter_mut() {
        if *key == "DNS" {
            *value = addresses.clone();
        }
    }
    interface.retain(|(key, value)| *key != "DNS" || !value.is_empty());
    let endpoint = value_of(&peer, "Endpoint").ok_or(Error::NotAConnection)?;
    let (host, port) = endpoint.rsplit_once(':').ok_or(Error::NotAConnection)?;
    let server = host.trim_matches(['[', ']']).to_owned();
    if server.is_empty() || server.contains(['/', '@', ' ']) || port.parse::<u16>().map_or(true, |port| port == 0) {
        return Err(Error::NotAConnection);
    }
    if value_of(&interface, "PrivateKey").is_none() || value_of(&peer, "PublicKey").is_none() || value_of(&peer, "AllowedIPs").is_none() {
        return Err(Error::NotAConnection);
    }
    let mut written = String::new();
    for (header, values) in [(INTERFACE, &interface), (PEER, &peer)] {
        if !written.is_empty() {
            written.push('\n');
        }
        written.push_str(header);
        written.push('\n');
        for (key, value) in values.iter() {
            written.push_str(&format!("{key} = {value}\n"));
        }
    }
    let dns = dns.iter().find_map(|address| match address {
        IpAddr::V4(address) => Some(*address),
        IpAddr::V6(_) => None,
    });
    Ok(Manual { name: name.unwrap_or_else(|| server.clone()), server, address, dns, conf: Secret::new(written) })
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use super::*;

    // Made for these tests by the panel's own generator (src/awgscale/users/device.py) with throwaway keys
    // that never belonged to any device: its config, its vpn:// key and the QR frame of that key.
    const OUR_CONF: &str = include_str!("../tests/fixtures/our.conf");
    const OUR_KEY: &str = include_str!("../tests/fixtures/our.vpn");
    const OUR_FRAME: &str = include_str!("../tests/fixtures/our.frame");
    fn private_key() -> String {
        OUR_CONF.lines().find_map(|line| line.strip_prefix("PrivateKey = ")).unwrap().to_owned()
    }

    fn server_key() -> String {
        OUR_CONF.lines().find_map(|line| line.strip_prefix("PublicKey = ")).unwrap().to_owned()
    }

    fn preshared_key() -> String {
        OUR_CONF.lines().find_map(|line| line.strip_prefix("PresharedKey = ")).unwrap().to_owned()
    }

    fn qt_compressed(json: &str) -> Vec<u8> {
        let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
        encoder.write_all(json.as_bytes()).unwrap();
        let mut packed = (json.len() as u32).to_be_bytes().to_vec();
        packed.extend(encoder.finish().unwrap());
        packed
    }

    fn checks_ours(manual: &Manual) {
        assert_eq!(manual.server, "vpn.example.org");
        assert_eq!(manual.address, Ipv4Addr::new(172, 16, 17, 15));
        assert_eq!(manual.dns, Some(Ipv4Addr::new(172, 16, 17, 1)));
        let conf = manual.conf.expose();
        for line in [
            "Jc = 4",
            "Jmin = 10",
            "Jmax = 50",
            "S1 = 51",
            "S2 = 82",
            "S3 = 17",
            "S4 = 9",
            "H1 = 1102466812-1102466900",
            "H4 = 2833215180",
            "I1 = <b 0xc6000000010843290a47ba8ba2ed000044d0><r 16>",
            "Endpoint = vpn.example.org:51820",
            "PersistentKeepalive = 33",
        ] {
            assert!(conf.contains(line), "{line}");
        }
        assert!(conf.contains(&format!("PrivateKey = {}", private_key())));
        assert!(conf.contains(&format!("PublicKey = {}", server_key())));
        assert!(conf.contains(&format!("PresharedKey = {}", preshared_key())));
    }

    #[test]
    fn reads_our_config() {
        let manual = read(OUR_CONF).unwrap();
        checks_ours(&manual);
        assert_eq!(manual.name, "vpn.example.org");
    }

    #[test]
    fn writes_what_the_computers_service_is_tested_with() {
        assert_eq!(read(OUR_CONF).unwrap().conf.expose(), include_str!("../../qalby/conf/testdata/manual.conf"));
    }

    #[test]
    fn reads_our_amnezia_key() {
        let manual = read(OUR_KEY).unwrap();
        checks_ours(&manual);
        assert_eq!(manual.name, "Example network");
    }

    #[test]
    fn reads_a_key_wrapped_over_lines_or_padded() {
        let key = OUR_KEY.trim();
        let lines: Vec<String> = key.chars().collect::<Vec<_>>().chunks(64).map(|line| line.iter().collect()).collect();
        checks_ours(&read(&lines.join("\n")).unwrap());
        checks_ours(&read(&format!("{key}==")).unwrap());
    }

    #[test]
    fn reads_the_qr_frame_of_our_key() {
        let manual = read(OUR_FRAME).unwrap();
        checks_ours(&manual);
    }

    #[test]
    fn the_key_and_its_config_give_the_same_tunnel() {
        assert_eq!(read(OUR_KEY).unwrap().conf, read(OUR_CONF).unwrap().conf);
    }

    /// The shape of a key the AmneziaVPN app shares (its own servers): several containers, the config
    /// with the DNS left to the app, the numbers as numbers.
    fn amnezia_json(last_config: &str) -> String {
        serde_json::json!({
            "containers": [
                {"container": "amnezia-openvpn", "openvpn": {"last_config": "{}"}},
                {"container": "amnezia-awg", "awg": {"last_config": last_config, "port": "40001", "transport_proto": "udp"}},
            ],
            "defaultContainer": "amnezia-awg",
            "description": "Amnezia server",
            "dns1": "1.1.1.1",
            "dns2": "1.0.0.1",
            "hostName": "203.0.113.7",
        })
        .to_string()
    }

    fn amnezia_config() -> String {
        format!(
            "[Interface]\nAddress = 10.8.1.2/32\nDNS = $PRIMARY_DNS, $SECONDARY_DNS\nPrivateKey = {}\nJc = 3\nJmin = 10\nJmax = 30\n\
             S1 = 15\nS2 = 18\nH1 = 1020325451\nH2 = 3288052141\nH3 = 1766607858\nH4 = 2528465083\n\n\
             [Peer]\nPublicKey = {}\nPresharedKey = {}\nAllowedIPs = 0.0.0.0/0, ::/0\nEndpoint = 203.0.113.7:40001\nPersistentKeepalive = 25\n",
            private_key(),
            server_key(),
            preshared_key()
        )
    }

    #[test]
    fn reads_an_amnezia_app_key() {
        let inner = serde_json::json!({"config": amnezia_config(), "port": 40001, "mtu": "1376"}).to_string();
        let key = format!("vpn://{}", URL_SAFE_NO_PAD.encode(qt_compressed(&amnezia_json(&inner))));
        let manual = read(&key).unwrap();
        assert_eq!(manual.name, "Amnezia server");
        assert_eq!(manual.server, "203.0.113.7");
        assert_eq!(manual.address, Ipv4Addr::new(10, 8, 1, 2));
        assert_eq!(manual.dns, Some(Ipv4Addr::new(1, 1, 1, 1)));
        let conf = manual.conf.expose();
        assert!(conf.contains("DNS = 1.1.1.1, 1.0.0.1"));
        assert!(conf.contains("H2 = 3288052141") && conf.contains("Endpoint = 203.0.113.7:40001"));
        // the same key, not compressed: the AmneziaVPN app takes that too
        let plain = format!("vpn://{}", URL_SAFE_NO_PAD.encode(amnezia_json(&inner)));
        assert_eq!(read(&plain).unwrap().conf, manual.conf);
    }

    #[test]
    fn makes_the_config_from_the_fields_when_the_key_has_none() {
        let inner = serde_json::json!({
            "client_priv_key": private_key(), "client_ip": "10.8.1.2", "server_pub_key": server_key(),
            "psk_key": preshared_key(), "hostName": "203.0.113.7", "port": 40001, "mtu": "1376",
            "allowed_ips": ["0.0.0.0/0", "::/0"],
            "persistent_keep_alive": "25", "Jc": "3", "Jmin": "10", "Jmax": "30", "S1": "15", "S2": "18",
            "H1": "1", "H2": "2", "H3": "3", "H4": "4", "I1": "",
        })
        .to_string();
        let key = format!("vpn://{}", URL_SAFE_NO_PAD.encode(qt_compressed(&amnezia_json(&inner))));
        let conf = read(&key).unwrap().conf;
        let expected = format!(
            "[Interface]\nPrivateKey = {}\nAddress = 10.8.1.2/32\nDNS = 1.1.1.1, 1.0.0.1\nMTU = 1376\nJc = 3\nJmin = 10\nJmax = 30\n\
             S1 = 15\nS2 = 18\nH1 = 1\nH2 = 2\nH3 = 3\nH4 = 4\n\n[Peer]\nPublicKey = {}\nPresharedKey = {}\n\
             Endpoint = 203.0.113.7:40001\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n",
            private_key(),
            server_key(),
            preshared_key()
        );
        assert_eq!(conf.expose(), expected);
    }

    #[test]
    fn leaves_out_what_only_wg_quick_reads() {
        let conf = OUR_CONF.replace("[Interface]\n", "[Interface]\nListenPort = 51820\nTable = off\nPostUp = rm -rf /\n");
        let written = read(&conf).unwrap().conf;
        assert!(!written.expose().contains("PostUp") && !written.expose().contains("Table") && !written.expose().contains("ListenPort"));
        assert_eq!(written, read(OUR_CONF).unwrap().conf);
    }

    #[test]
    fn reads_any_line_ends() {
        let expected = read(OUR_CONF).unwrap().conf;
        assert_eq!(read(&OUR_CONF.replace('\n', "\r\n")).unwrap().conf, expected);
        assert_eq!(read(&OUR_CONF.replace('\n', "\r")).unwrap().conf, expected);
    }

    #[test]
    fn takes_a_list_over_several_lines() {
        let conf = OUR_CONF.replace("AllowedIPs = 0.0.0.0/0, ::/0", "AllowedIPs = 0.0.0.0/0\nAllowedIPs = ::/0");
        assert_eq!(read(&conf).unwrap().conf, read(OUR_CONF).unwrap().conf);
        assert!(read(&OUR_CONF.replace("MTU = 1280", "MTU = 1280\nMTU = 1420")).is_err());
    }

    #[test]
    fn refuses_any_text_without_a_panic() {
        for text in ["я тест", "впн://ключ", "vpn:/", "v", "😀😀😀😀😀😀😀", "B8\u{0}\u{1}", "[Interface]\nЯ = ты\n"]
        {
            assert!(read(text).is_err(), "{text}");
        }
    }

    #[test]
    fn keeps_only_the_dns_addresses() {
        let conf = OUR_CONF.replace("DNS = 172.16.17.1", "DNS = 172.16.17.1, home.arpa");
        assert!(read(&conf).unwrap().conf.expose().contains("DNS = 172.16.17.1\n"));
    }

    #[test]
    fn refuses_what_is_not_a_connection() {
        let refused = [
            "".to_owned(),
            "hello".to_owned(),
            "awgscale://join?h=vpn.example.org&c=Zx3kQ0aB1cD2eF3gH4iJ5k".to_owned(),
            "vpn://not base64!".to_owned(),
            format!("vpn://{}", URL_SAFE_NO_PAD.encode(b"{\"containers\": []}")),
            OUR_CONF.replace("[Peer]", "[Peer]\nPublicKey = AAAA"),
            OUR_CONF.replace("Endpoint = vpn.example.org:51820", "Endpoint = vpn.example.org"),
            OUR_CONF.replace("Address = 172.16.17.15/32", "Address = fd00::2/128"),
            format!("{OUR_CONF}\n[Peer]\nPublicKey = {}\nEndpoint = b.example.org:1\nAllowedIPs = 0.0.0.0/0\n", server_key()),
            OUR_CONF.replace(&private_key(), "$WIREGUARD_CLIENT_PRIVATE_KEY"),
            OUR_CONF.replace(&private_key(), "c2hvcnQ="),
        ];
        for text in &refused {
            assert!(read(text).is_err(), "{text}");
        }
        assert!(matches!(read(&format!("vpn://{}", URL_SAFE_NO_PAD.encode(b"{\"containers\": []}"))), Err(Error::NoAmneziaWg)));
    }

    #[test]
    fn names_an_unknown_key_but_never_a_value() {
        let error = read(&OUR_CONF.replace("MTU = 1280", "Mystery = secret-value")).unwrap_err();
        assert!(matches!(&error, Error::UnknownKey(key) if key == "Mystery"));
        assert!(!error.to_string().contains("secret-value"));
    }

    #[test]
    fn names_the_newer_amnezia_keys_it_does_not_take() {
        // the AmneziaVPN app's template has these too (amnezia-client, server_scripts/awg/template.conf);
        // the app's tunnels (amneziawg-go, the Network Extension's parser) do not know them yet
        let error = read(&OUR_CONF.replace("MTU = 1280", "MTU = 1280\nRandomTrailers = 1")).unwrap_err();
        assert!(matches!(&error, Error::UnknownKey(key) if key == "RandomTrailers"));
    }

    #[test]
    fn refuses_one_of_many_codes() {
        let mut frame = URL_SAFE_NO_PAD.decode(OUR_FRAME.trim()).unwrap();
        frame[2] = 2;
        assert!(matches!(read(&URL_SAFE_NO_PAD.encode(&frame)), Err(Error::OneOfManyCodes)));
        frame[2] = 1;
        frame[3] = 1;
        assert!(matches!(read(&URL_SAFE_NO_PAD.encode(&frame)), Err(Error::OneOfManyCodes)));
    }

    #[test]
    fn reads_the_amnezia_apps_single_code_which_says_none() {
        let mut frame = URL_SAFE_NO_PAD.decode(OUR_FRAME.trim()).unwrap();
        frame[2] = 0;
        checks_ours(&read(&URL_SAFE_NO_PAD.encode(&frame)).unwrap());
    }

    #[test]
    fn refuses_a_bomb() {
        let huge = format!("{{\"containers\": \"{}\"}}", "a".repeat(MAX_LENGTH * 4));
        assert!(read(&format!("vpn://{}", URL_SAFE_NO_PAD.encode(qt_compressed(&huge)))).is_err());
    }

    #[test]
    fn never_prints_a_key() {
        let manual = read(OUR_CONF).unwrap();
        assert!(!format!("{manual:?}").contains(&private_key()));
    }
}
