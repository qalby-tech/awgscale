//! The person's choices on this device, kept next to the network's profile.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::error::Error;
use crate::tunnel::TunnelOptions;

const SETTINGS_FILE: &str = "settings.json";

/// The most networks a person may send around the tunnel (the service's own limit).
pub const AROUND_LIMIT: usize = 64;

/// What the person chose in Настройки.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeviceSettings {
    /// «Без VPN нет интернета»: nothing leaves the device around the tunnel while it is on.
    #[serde(default)]
    pub kill_switch: bool,
    /// «Локальные сети мимо VPN»: on unless the person switched it off.
    #[serde(default = "on")]
    pub local_around: bool,
    /// «Мимо VPN»: addresses and networks that go around the tunnel.
    #[serde(default)]
    pub around: Vec<String>,
    /// The app's language ("ru", "en"); None is the system's.
    #[serde(default)]
    pub language: Option<String>,
}

fn on() -> bool {
    true
}

impl Default for DeviceSettings {
    fn default() -> Self {
        Self { kill_switch: false, local_around: true, around: Vec::new(), language: None }
    }
}

impl DeviceSettings {
    pub fn tunnel_options(&self) -> TunnelOptions {
        TunnelOptions { kill_switch: self.kill_switch, local_around: self.local_around, around: self.around.clone() }
    }

    /// The settings as the person may have them: every network of the list a network, none of everything.
    pub fn checked(mut self) -> Result<Self, Error> {
        self.around = self.around.iter().map(|text| text.trim().to_owned()).filter(|text| !text.is_empty()).collect();
        self.around.dedup();
        if self.around.len() > AROUND_LIMIT {
            return Err(Error::Refused(crate::lang::say(
                &format!("Мимо VPN — не больше {AROUND_LIMIT} сетей"),
                &format!("No more than {AROUND_LIMIT} networks may bypass the VPN"),
            )));
        }
        if let Some(wrong) = self.around.iter().find(|text| !is_network(text)) {
            return Err(Error::Refused(crate::lang::say(
                &format!("«{wrong}» — не адрес и не сеть"),
                &format!("“{wrong}” is not an address or a network"),
            )));
        }
        Ok(self)
    }
}

/// An address (`198.51.100.7`, `2001:db8::1`) or a network (`10.96.0.0/12`), not all of them (`/0`).
pub fn is_network(text: &str) -> bool {
    let (address, bits) = match text.split_once('/') {
        Some((address, bits)) => (address, Some(bits)),
        None => (text, None),
    };
    let Ok(address) = address.parse::<std::net::IpAddr>() else {
        return false;
    };
    let most = if address.is_ipv4() { 32 } else { 128 };
    match bits {
        None => true,
        Some(bits) => bits.parse::<u8>().is_ok_and(|bits| bits >= 1 && bits <= most),
    }
}

/// The settings as a file of the app's own directory.
pub struct SettingsFile {
    path: PathBuf,
}

impl SettingsFile {
    pub fn new(directory: PathBuf) -> Self {
        Self { path: directory.join(SETTINGS_FILE) }
    }

    /// What was chosen; nothing chosen yet (or a file that cannot be read) is the defaults.
    pub fn load(&self) -> DeviceSettings {
        std::fs::read(&self.path).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default()
    }

    pub fn save(&self, settings: &DeviceSettings) -> Result<(), Error> {
        let directory = self.path.parent().ok_or(Error::Storage)?;
        std::fs::create_dir_all(directory).map_err(|_| Error::Storage)?;
        let bytes = serde_json::to_vec(settings).map_err(|_| Error::Storage)?;
        let staged = self.path.with_extension("json.new");
        std::fs::write(&staged, bytes).map_err(|_| Error::Storage)?;
        std::fs::rename(&staged, &self.path).map_err(|_| Error::Storage)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_the_choice() {
        let directory = tempfile::tempdir().unwrap();
        let file = SettingsFile::new(directory.path().to_path_buf());
        assert_eq!(file.load(), DeviceSettings::default());
        assert!(file.load().local_around, "local networks go around the tunnel unless switched off");
        file.save(&DeviceSettings { kill_switch: true, local_around: false, around: vec!["10.96.0.0/12".to_owned()], language: None })
            .unwrap();
        let options = file.load().tunnel_options();
        assert!(options.kill_switch && !options.local_around);
        assert_eq!(options.around, ["10.96.0.0/12"]);
    }

    #[test]
    fn takes_only_networks() {
        let good = DeviceSettings {
            around: vec![" 10.96.0.0/12 ".to_owned(), "198.51.100.7".to_owned(), "2001:db8::/32".to_owned()],
            ..Default::default()
        };
        assert_eq!(good.checked().unwrap().around, ["10.96.0.0/12", "198.51.100.7", "2001:db8::/32"]);
        for bad in ["0.0.0.0/0", "10.0.0.0/33", "example.com", "10.0.0.0/x", "::/0"] {
            let settings = DeviceSettings { around: vec![bad.to_owned()], ..Default::default() };
            assert!(settings.checked().is_err(), "{bad}");
        }
    }
}
