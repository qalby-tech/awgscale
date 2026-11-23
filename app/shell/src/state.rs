//! What the commands share: where the network is kept and the device's tunnel.

use std::path::PathBuf;
use std::sync::Arc;

use awgscale_client::profile::FileStore;
use awgscale_client::settings::SettingsFile;
use awgscale_client::tunnel::Tunnel;
use tauri::{AppHandle, Runtime};

pub struct AppState {
    pub store: FileStore,
    pub settings: SettingsFile,
    pub tunnel: Arc<dyn Tunnel>,
}

impl AppState {
    pub fn new<R: Runtime>(directory: PathBuf, app: &AppHandle<R>) -> Self {
        Self { store: FileStore::new(directory.clone()), settings: SettingsFile::new(directory), tunnel: tunnel(app) }
    }
}

/// On a computer the service holds the tunnel (app/qalby; when it is not running the app says so);
/// on Android the app's VpnService (app/android); a system that has no tunnel of its own yet says it
/// cannot switch one on.
#[cfg(not(feature = "pretend-tunnel"))]
fn tunnel<R: Runtime>(_app: &AppHandle<R>) -> Arc<dyn Tunnel> {
    #[cfg(any(target_os = "linux", target_os = "windows"))]
    return Arc::new(awgscale_client::service::ServiceTunnel::usual());
    #[cfg(target_os = "android")]
    return awgscale_android::tunnel(_app);
    #[cfg(not(any(target_os = "linux", target_os = "windows", target_os = "android")))]
    Arc::new(awgscale_client::tunnel::NoTunnel)
}

/// Only for the pictures of the screens (app/tests/screens.sh): a tunnel that pretends to be up.
#[cfg(feature = "pretend-tunnel")]
fn tunnel<R: Runtime>(_app: &AppHandle<R>) -> Arc<dyn Tunnel> {
    Arc::new(awgscale_client::tunnel::FakeTunnel::default())
}
