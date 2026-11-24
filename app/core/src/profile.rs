//! The network this device belongs to, as the app keeps it between runs.

use std::net::Ipv4Addr;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use url::Url;

use crate::api::base_at;
use crate::error::Error;
use crate::invite::Redeemed;
use crate::manual::Manual;
use crate::secret::Secret;

const INTERFACE_SECTION: &str = "[interface]";
const ADDRESS_KEY: &str = "address";
const DNS_KEY: &str = "dns";
const PROFILE_FILE: &str = "network.json";

/// How the device came into its network.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum NetworkKind {
    /// By an invitation of an awgscale network: its panel and app API are there.
    #[default]
    Invited,
    /// By a connection given by hand (an Amnezia key or a config): a tunnel, nothing else.
    Manual,
}

/// The app API a connection given by hand turned out to have: it leads into an awgscale network after all
/// (a key of our own panel or bot), and the app shows it as it shows an invited device's network.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Panel {
    /// Where its app API answered through the tunnel: `http://<the tunnel's DNS>:8088/app/api/`.
    pub base: String,
    /// The user this device is in that network, as its /me says.
    pub name: String,
}

/// One network and this device's place in it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Profile {
    /// How the device came into it (a profile kept before there was a choice was invited).
    #[serde(default)]
    pub kind: NetworkKind,
    /// The network's domain: where its panel and invitations are; for a connection given by hand, its server.
    pub host: String,
    /// The user made for this device.
    pub name: String,
    /// This device's address inside the tunnel.
    pub address: Ipv4Addr,
    /// The entry's address inside the tunnel: its DNS and its app API.
    pub entry: Ipv4Addr,
    /// It became an owner when it joined (the server's /me says what holds now).
    pub owner: bool,
    /// The tunnel config, with the device's private key.
    pub conf: Secret,
    /// A connection given by hand whose server answered as an awgscale network: its app API. Asked again
    /// each time the tunnel comes up, kept once found; an invited device's network always has one, at its entry.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub panel: Option<Panel>,
}

impl Profile {
    /// The profile of a device that has just redeemed a link at `host`.
    pub fn joined(host: &str, redeemed: Redeemed) -> Result<Self, Error> {
        let (address, entry) = addresses_of(redeemed.conf.expose()).ok_or(Error::BadAnswer)?;
        Ok(Self {
            kind: NetworkKind::Invited,
            host: host.to_owned(),
            name: redeemed.name,
            address,
            entry,
            owner: redeemed.owner,
            conf: redeemed.conf,
            panel: None,
        })
    }

    /// The profile of a connection given by hand. Its "entry" is its DNS (or, without one, the device's own
    /// address); whether an app API answers there is learnt only once its tunnel is up (`panel_candidate`).
    pub fn manual(given: Manual) -> Self {
        Self {
            kind: NetworkKind::Manual,
            host: given.server,
            name: given.name,
            address: given.address,
            entry: given.dns.unwrap_or(given.address),
            owner: false,
            conf: given.conf,
            panel: None,
        }
    }

    /// Where the network's app API is asked: an invited device's at its entry, a connection given by hand's
    /// where it was found; None when there is none.
    pub fn api_base(&self) -> Option<Url> {
        match self.kind {
            NetworkKind::Invited => base_at(self.entry).ok(),
            NetworkKind::Manual => self.panel.as_ref().and_then(|panel| Url::parse(&panel.base).ok()),
        }
    }

    /// Where a connection given by hand may have the app API of an awgscale network: where an invited
    /// device's is, at the tunnel's DNS server, which a key of our panel names (172.16.17.1; Traefik serves
    /// the API only for that address as the host, never for the server's public name, and only from inside
    /// the tunnel). Only a DNS of a private network is asked: a public one (1.1.1.1 of a foreign server's
    /// key) is somebody else's, and a config without a DNS has nothing to ask. `loopback` lets 127.0.0.1 be
    /// asked too: only for the pictures of the screens, whose tunnel pretends.
    pub fn panel_candidate(&self, loopback: bool) -> Option<Url> {
        let asked = self.entry.is_private() || (loopback && self.entry.is_loopback());
        if self.kind != NetworkKind::Manual || self.entry == self.address || !asked {
            return None;
        }
        base_at(self.entry).ok()
    }

    /// What the interface shows: everything but the config. A connection given by hand that leads into an
    /// awgscale network is shown by the user it is there.
    pub fn shown(&self) -> ProfileShown {
        let has_panel = self.kind == NetworkKind::Invited || self.panel.is_some();
        let name = self.panel.as_ref().map_or_else(|| self.name.clone(), |panel| panel.name.clone());
        ProfileShown { kind: self.kind, host: self.host.clone(), name, address: self.address, owner: self.owner, panel: has_panel }
    }
}

/// A profile without its secret: what may cross into the interface.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProfileShown {
    pub kind: NetworkKind,
    pub host: String,
    pub name: String,
    pub address: Ipv4Addr,
    pub owner: bool,
    /// The network's app API is there to ask (where the person is, the location, the protection).
    pub panel: bool,
}

/// The device's own address and the DNS server from the [Interface] of a config:
///
/// ```text
/// Address = 172.16.17.15/32
/// DNS = 172.16.17.1
/// ```
fn addresses_of(conf: &str) -> Option<(Ipv4Addr, Ipv4Addr)> {
    let mut in_interface = false;
    let mut address: Option<Ipv4Addr> = None;
    let mut dns: Option<Ipv4Addr> = None;
    for line in conf.lines().map(str::trim) {
        if line.starts_with('[') {
            in_interface = line.eq_ignore_ascii_case(INTERFACE_SECTION);
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if !in_interface {
            continue;
        }
        let first = value.split(',').next().unwrap_or("").trim();
        match key.trim().to_ascii_lowercase().as_str() {
            ADDRESS_KEY => address = first.split('/').next().and_then(|text| text.parse().ok()),
            DNS_KEY => dns = first.parse().ok(),
            _ => {}
        }
    }
    Some((address?, dns?))
}

/// Where the profile lives. On a phone it will be the system's keychain; the
/// file is for desktops and tests.
pub trait ProfileStore: Send + Sync {
    /// Whether a profile could be saved now: asked before a one-time link is spent.
    fn writable(&self) -> Result<(), Error>;
    fn load(&self) -> Result<Option<Profile>, Error>;
    fn save(&self, profile: &Profile) -> Result<(), Error>;
    fn forget(&self) -> Result<(), Error>;
}

/// The profile as a file only its user may read.
pub struct FileStore {
    path: PathBuf,
}

impl FileStore {
    /// `directory` is the app's own data directory.
    pub fn new(directory: PathBuf) -> Self {
        Self { path: directory.join(PROFILE_FILE) }
    }
}

impl ProfileStore for FileStore {
    fn writable(&self) -> Result<(), Error> {
        let directory = self.path.parent().ok_or(Error::Storage)?;
        std::fs::create_dir_all(directory).map_err(|_| Error::Storage)?;
        let probe = self.path.with_extension("json.probe");
        write_private(&probe, b"")?;
        std::fs::remove_file(&probe).map_err(|_| Error::Storage)
    }

    fn load(&self) -> Result<Option<Profile>, Error> {
        match std::fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map(Some).map_err(|_| Error::Storage),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(_) => Err(Error::Storage),
        }
    }

    fn save(&self, profile: &Profile) -> Result<(), Error> {
        let directory = self.path.parent().ok_or(Error::Storage)?;
        std::fs::create_dir_all(directory).map_err(|_| Error::Storage)?;
        let bytes = serde_json::to_vec(profile).map_err(|_| Error::Storage)?;
        let staged = self.path.with_extension("json.new");
        write_private(&staged, &bytes)?;
        std::fs::rename(&staged, &self.path).map_err(|_| Error::Storage)
    }

    fn forget(&self) -> Result<(), Error> {
        match std::fs::remove_file(&self.path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(_) => Err(Error::Storage),
        }
    }
}

#[cfg(unix)]
fn write_private(path: &std::path::Path, bytes: &[u8]) -> Result<(), Error> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    const OWNER_ONLY: u32 = 0o600;
    let mut file =
        std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(OWNER_ONLY).open(path).map_err(|_| Error::Storage)?;
    file.write_all(bytes).and_then(|()| file.sync_all()).map_err(|_| Error::Storage)
}

#[cfg(not(unix))]
fn write_private(path: &std::path::Path, bytes: &[u8]) -> Result<(), Error> {
    std::fs::write(path, bytes).map_err(|_| Error::Storage) // under the user's own profile directory
}

#[cfg(test)]
mod tests {
    use super::*;

    const CONF: &str = "[Interface]\nPrivateKey = test-only\nAddress = 172.16.17.15/32\nDNS = 172.16.17.1\nMTU = 1280\n\n[Peer]\nPublicKey = test-only\nAllowedIPs = 0.0.0.0/0\n";

    fn profile() -> Profile {
        let redeemed = Redeemed { name: "phone".to_owned(), conf: Secret::new(CONF.to_owned()), group: None, owner: true };
        Profile::joined("vpn.example.org", redeemed).unwrap()
    }

    #[test]
    fn reads_the_addresses_of_the_config() {
        let profile = profile();
        assert_eq!(profile.address, Ipv4Addr::new(172, 16, 17, 15));
        assert_eq!(profile.entry, Ipv4Addr::new(172, 16, 17, 1));
        assert!(profile.owner);
    }

    #[test]
    fn refuses_a_config_without_them() {
        let redeemed =
            Redeemed { name: "phone".to_owned(), conf: Secret::new("[Peer]\nDNS = 1.1.1.1\n".to_owned()), group: None, owner: false };
        assert!(Profile::joined("vpn.example.org", redeemed).is_err());
    }

    #[test]
    fn keeps_the_profile_in_a_private_file() {
        let directory = tempfile::tempdir().unwrap();
        let store = FileStore::new(directory.path().join("data"));
        assert_eq!(store.load().unwrap(), None);
        store.save(&profile()).unwrap();
        assert_eq!(store.load().unwrap(), Some(profile()));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(directory.path().join("data").join(PROFILE_FILE)).unwrap().permissions().mode();
            assert_eq!(mode & 0o077, 0, "only the owner may read it");
        }
        store.forget().unwrap();
        assert_eq!(store.load().unwrap(), None);
        store.forget().unwrap();
    }

    #[test]
    fn a_profile_kept_before_the_kinds_was_invited() {
        let mut kept = serde_json::to_value(profile()).unwrap();
        kept.as_object_mut().unwrap().remove("kind");
        let read: Profile = serde_json::from_value(kept).unwrap();
        assert_eq!(read.kind, NetworkKind::Invited);
    }

    #[test]
    fn a_connection_given_by_hand_is_kept_as_manual() {
        let manual = crate::manual::read(include_str!("../tests/fixtures/our.conf")).unwrap();
        let profile = Profile::manual(manual);
        assert_eq!(profile.kind, NetworkKind::Manual);
        assert_eq!(profile.host, "vpn.example.org");
        assert_eq!(profile.entry, Ipv4Addr::new(172, 16, 17, 1));
        let shown = serde_json::to_string(&profile.shown()).unwrap();
        assert!(shown.contains("\"kind\":\"manual\"") && !shown.contains("PrivateKey"));
    }

    fn manual(dns: &str) -> Profile {
        let conf = include_str!("../tests/fixtures/our.conf").replace("DNS = 172.16.17.1", dns);
        Profile::manual(crate::manual::read(&conf).unwrap())
    }

    #[test]
    fn a_connection_given_by_hand_asks_its_tunnels_dns_for_the_app_api() {
        let ours = manual("DNS = 172.16.17.1");
        assert_eq!(ours.panel_candidate(false).unwrap().as_str(), "http://172.16.17.1:8088/app/api/");
        assert_eq!(manual("DNS = 10.8.1.1").panel_candidate(false).unwrap().as_str(), "http://10.8.1.1:8088/app/api/");
        // not before it is found: until then it has no app API
        assert_eq!(ours.api_base(), None);
        assert!(!ours.shown().panel);
    }

    #[test]
    fn our_panels_key_asks_the_networks_address() {
        // a vpn:// key as our panel and bot write it (dns1 and dns2: the network's DNS)
        let ours = Profile::manual(crate::manual::read(include_str!("../tests/fixtures/our.vpn")).unwrap());
        assert_eq!(ours.panel_candidate(false).unwrap().as_str(), "http://172.16.17.1:8088/app/api/");
    }

    #[test]
    fn a_public_dns_or_none_is_never_asked() {
        assert_eq!(manual("DNS = 1.1.1.1, 1.0.0.1").panel_candidate(false), None);
        assert_eq!(manual("DNS = 8.8.8.8").panel_candidate(true), None);
        let without = include_str!("../tests/fixtures/our.conf").replace("DNS = 172.16.17.1\n", "");
        let without = Profile::manual(crate::manual::read(&without).unwrap());
        assert_eq!(without.entry, without.address);
        assert_eq!(without.panel_candidate(false), None);
        // the loopback only for the pictures of the screens
        assert_eq!(manual("DNS = 127.0.0.1").panel_candidate(false), None);
        assert_eq!(manual("DNS = 127.0.0.1").panel_candidate(true).unwrap().as_str(), "http://127.0.0.1:8088/app/api/");
        // an invited device's app API is at its entry already
        assert_eq!(profile().panel_candidate(false), None);
        assert_eq!(profile().api_base().unwrap().as_str(), "http://172.16.17.1:8088/app/api/");
    }

    #[test]
    fn a_connection_given_by_hand_with_an_app_api_is_shown_as_a_network() {
        let mut found = manual("DNS = 172.16.17.1");
        found.panel = Some(Panel { base: "http://172.16.17.1:8088/app/api/".to_owned(), name: "anna_phone".to_owned() });
        assert_eq!(found.api_base().unwrap().as_str(), "http://172.16.17.1:8088/app/api/");
        let shown = found.shown();
        assert!(shown.panel);
        assert_eq!(shown.kind, NetworkKind::Manual);
        assert_eq!(shown.name, "anna_phone");
        // kept between runs, and a profile kept before it had none
        let directory = tempfile::tempdir().unwrap();
        let store = FileStore::new(directory.path().to_owned());
        store.save(&found).unwrap();
        assert_eq!(store.load().unwrap(), Some(found));
        assert!(!serde_json::to_string(&profile()).unwrap().contains("panel"));
    }

    #[test]
    fn the_shown_profile_has_no_config() {
        let shown = serde_json::to_string(&profile().shown()).unwrap();
        assert!(!shown.contains("test-only") && !shown.contains("PrivateKey"));
    }
}
