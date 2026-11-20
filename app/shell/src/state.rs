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

/// On a computer the service holds the tunnel (app/qalby); where it is not installed, or on a
/// system that has no tunnel of its own yet, the app says it cannot switch one on.
#[cfg(not(feature = "pretend-tunnel"))]
fn tunnel() -> Arc<dyn Tunnel> {
    #[cfg(target_os = "linux")]
    if let Some(service) = awgscale_client::service::ServiceTunnel::installed() {
        return Arc::new(service);
    }
    Arc::new(awgscale_client::tunnel::NoTunnel)
}

/// Only for the pictures of the screens (app/tests/screens.sh): a tunnel that pretends to be up.
#[cfg(feature = "pretend-tunnel")]
fn tunnel() -> Arc<dyn Tunnel> {
    Arc::new(awgscale_client::tunnel::FakeTunnel::default())
}
