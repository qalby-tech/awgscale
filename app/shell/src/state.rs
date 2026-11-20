//! What the commands share: where the network is kept and the device's tunnel.

use std::path::PathBuf;

use awgscale_client::profile::FileStore;
use awgscale_client::tunnel::{FakeTunnel, Tunnel};

pub struct AppState {
    pub store: FileStore,
    /// Until each platform brings its own tunnel, one that only remembers.
    pub tunnel: Box<dyn Tunnel>,
}

impl AppState {
    pub fn new(directory: PathBuf) -> Self {
        Self { store: FileStore::new(directory), tunnel: Box::new(FakeTunnel::default()) }
    }
}
