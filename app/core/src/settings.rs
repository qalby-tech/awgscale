//! The person's choices on this device, kept next to the network's profile.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::error::Error;
use crate::tunnel::TunnelOptions;

const SETTINGS_FILE: &str = "settings.json";

/// What the person chose in Настройки.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeviceSettings {
    /// «Без VPN нет интернета»: nothing leaves the device around the tunnel while it is on.
    #[serde(default)]
    pub kill_switch: bool,
}

impl DeviceSettings {
    pub fn tunnel_options(&self) -> TunnelOptions {
        TunnelOptions { kill_switch: self.kill_switch }
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
        file.save(&DeviceSettings { kill_switch: true }).unwrap();
        assert!(file.load().tunnel_options().kill_switch);
    }
}
