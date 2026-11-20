//! The tunnel, as the app sees it. Each platform brings its own: a VpnService on
//! Android, a Network Extension on iOS, a service with amneziawg-go on a desktop.

use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::error::Error;
use crate::secret::Secret;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TunnelState {
    Down,
    Connecting,
    Up,
}

/// The tunnel as the app shows it: its state, and what the person should know about it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TunnelReport {
    pub state: TunnelState,
    pub warning: Option<String>,
}

/// The person's choices for the tunnel that are not in the config.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TunnelOptions {
    /// While the tunnel is on, nothing leaves the device around it.
    pub kill_switch: bool,
}

/// Turns the device's tunnel on and off.
pub trait Tunnel: Send + Sync {
    fn up(&self, conf: &Secret, options: TunnelOptions) -> Result<(), Error>;
    fn down(&self) -> Result<(), Error>;
    fn state(&self) -> TunnelState;

    fn report(&self) -> TunnelReport {
        TunnelReport { state: self.state(), warning: None }
    }
}

/// What a build without a platform's tunnel has: it says so instead of pretending.
#[derive(Default)]
pub struct NoTunnel;

impl Tunnel for NoTunnel {
    fn up(&self, _conf: &Secret, _options: TunnelOptions) -> Result<(), Error> {
        Err(Error::Tunnel("в этой версии приложения VPN ещё не включается".to_owned()))
    }

    fn down(&self) -> Result<(), Error> {
        Ok(())
    }

    fn state(&self) -> TunnelState {
        TunnelState::Down
    }
}

/// A tunnel that only remembers what it was told: for tests and for pictures of
/// the screens, never for a build a person installs.
#[derive(Default)]
pub struct FakeTunnel {
    state: Mutex<Option<TunnelState>>,
}

impl Tunnel for FakeTunnel {
    fn up(&self, _conf: &Secret, _options: TunnelOptions) -> Result<(), Error> {
        *self.state.lock().map_err(|_| Error::Tunnel("сбой".to_owned()))? = Some(TunnelState::Up);
        Ok(())
    }

    fn down(&self) -> Result<(), Error> {
        *self.state.lock().map_err(|_| Error::Tunnel("сбой".to_owned()))? = Some(TunnelState::Down);
        Ok(())
    }

    fn state(&self) -> TunnelState {
        self.state.lock().ok().and_then(|state| *state).unwrap_or(TunnelState::Down)
    }
}
