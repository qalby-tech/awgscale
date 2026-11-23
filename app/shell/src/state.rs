//! What the commands share: where the network is kept and the device's tunnel.

use std::path::PathBuf;
use std::sync::Arc;

use awgscale_client::profile::FileStore;
use awgscale_client::settings::SettingsFile;
use awgscale_client::tunnel::Tunnel;

pub struct AppState {
    pub store: FileStore,
    pub settings: SettingsFile,
    pub tunnel: Arc<dyn Tunnel>,
}

impl AppState {
    pub fn new(directory: PathBuf, tunnel: Arc<dyn Tunnel>) -> Self {
        Self { store: FileStore::new(directory.clone()), settings: SettingsFile::new(directory), tunnel }
    }
}

/// On a computer the service holds the tunnel (app/qalby; when it is not running the app says so); on
/// iOS the Network Extension (app/tunnel-ios); a system that has no tunnel of its own yet says it cannot
/// switch one on.
#[cfg(not(feature = "pretend-tunnel"))]
pub fn tunnel(app: &tauri::AppHandle) -> Arc<dyn Tunnel> {
    #[cfg(any(target_os = "linux", target_os = "windows"))]
    let found: Option<Arc<dyn Tunnel>> = {
        let _ = app;
        Some(Arc::new(awgscale_client::service::ServiceTunnel::usual()))
    };
    #[cfg(target_os = "ios")]
    let found = awgscale_tunnel_ios::tunnel(app);
    #[cfg(not(any(target_os = "linux", target_os = "windows", target_os = "ios")))]
    let found: Option<Arc<dyn Tunnel>> = {
        let _ = app;
        None
    };
    found.unwrap_or_else(|| Arc::new(awgscale_client::tunnel::NoTunnel))
}

/// Only for the pictures of the screens (app/tests/screens.sh, the iOS simulator's in
/// .github/workflows/app-ios.yml): a tunnel that pretends to be up.
#[cfg(feature = "pretend-tunnel")]
pub fn tunnel(_app: &tauri::AppHandle) -> Arc<dyn Tunnel> {
    Arc::new(awgscale_client::tunnel::FakeTunnel::default())
}
