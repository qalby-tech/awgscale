//! What the commands share: where the network is kept and the device's tunnel.

use std::path::PathBuf;

use awgscale_client::profile::FileStore;
use awgscale_client::tunnel::Tunnel;

pub struct AppState {
    pub store: FileStore,
    pub tunnel: Box<dyn Tunnel>,
}

impl AppState {
    pub fn new(directory: PathBuf) -> Self {
        Self { store: FileStore::new(directory), tunnel: tunnel() }
    }
}

/// Until each platform brings its own tunnel, the app says it cannot switch one on.
#[cfg(not(feature = "pretend-tunnel"))]
fn tunnel() -> Box<dyn Tunnel> {
    Box::new(awgscale_client::tunnel::NoTunnel)
}

/// Only for the pictures of the screens (app/screens.sh): a tunnel that pretends to be up.
#[cfg(feature = "pretend-tunnel")]
fn tunnel() -> Box<dyn Tunnel> {
    Box::new(awgscale_client::tunnel::FakeTunnel::default())
}
