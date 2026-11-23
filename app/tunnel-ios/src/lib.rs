//! The tunnel on iOS. The system runs it in a Network Extension (app/ios/tunnel, amneziawg-apple's
//! WireGuardKit); the app's Swift side (ios/Sources, a Tauri mobile plugin) installs it as the device's
//! VPN through NETunnelProviderManager, starts and stops it and says how it is. Here it is the core's
//! `Tunnel`, so the commands of the window are the same as on a computer. On any other system only the
//! words of its refusals are built (refusal.rs), for their tests.

#[cfg(target_os = "ios")]
mod ios;
// on every system, so that its tests run anywhere
#[cfg_attr(not(target_os = "ios"), allow(dead_code))]
mod refusal;

#[cfg(target_os = "ios")]
pub use ios::{plugin, tunnel};
