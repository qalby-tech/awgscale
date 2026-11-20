//! The network this device belongs to, as the app keeps it between runs.

use std::net::Ipv4Addr;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::error::Error;
use crate::invite::Redeemed;
use crate::secret::Secret;

const INTERFACE_SECTION: &str = "[interface]";
const ADDRESS_KEY: &str = "address";
const DNS_KEY: &str = "dns";
const PROFILE_FILE: &str = "network.json";

/// One network and this device's place in it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Profile {
    /// The network's domain: where its panel and invitations are.
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
}

impl Profile {
    /// The profile of a device that has just redeemed a link at `host`.
    pub fn joined(host: &str, redeemed: Redeemed) -> Result<Self, Error> {
        let (address, entry) = addresses_of(redeemed.conf.expose()).ok_or(Error::BadAnswer)?;
        Ok(Self { host: host.to_owned(), name: redeemed.name, address, entry, owner: redeemed.owner, conf: redeemed.conf })
    }

    /// What the interface shows: everything but the config.
    pub fn shown(&self) -> ProfileShown {
        ProfileShown { host: self.host.clone(), name: self.name.clone(), address: self.address, owner: self.owner }
    }
}

/// A profile without its secret: what may cross into the interface.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProfileShown {
    pub host: String,
    pub name: String,
    pub address: Ipv4Addr,
    pub owner: bool,
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
    fn the_shown_profile_has_no_config() {
        let shown = serde_json::to_string(&profile().shown()).unwrap();
        assert!(!shown.contains("test-only") && !shown.contains("PrivateKey"));
    }
}
