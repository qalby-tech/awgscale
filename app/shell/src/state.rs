//! What the commands share: where the network is kept and the device's tunnel.

use std::path::PathBuf;
use std::sync::Arc;

use awgscale_client::profile::FileStore;
use awgscale_client::tunnel::Tunnel;

pub struct AppState {
    pub store: FileStore,
    pub tunnel: Arc<dyn Tunnel>,
}

impl AppState {
    pub fn new(directory: PathBuf) -> Self {
        Self { store: FileStore::new(directory), tunnel: tunnel() }
    }
}

/// On Linux the service holds the tunnel (app/qalby; when it is not running the app says so); a
/// system that has no tunnel of its own yet says it cannot switch one on.
#[cfg(not(feature = "pretend-tunnel"))]
fn tunnel() -> Arc<dyn Tunnel> {
    #[cfg(target_os = "linux")]
    return Arc::new(awgscale_client::service::ServiceTunnel::usual());
    #[cfg(not(target_os = "linux"))]
    Arc::new(awgscale_client::tunnel::NoTunnel)
}

/// Only for the pictures of the screens (app/tests/screens.sh): a tunnel that pretends to be up.
#[cfg(feature = "pretend-tunnel")]
fn tunnel() -> Arc<dyn Tunnel> {
    Arc::new(awgscale_client::tunnel::FakeTunnel::default())
}
