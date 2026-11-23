//! The tunnel on iOS. The system runs it in a Network Extension (app/ios/tunnel, amneziawg-apple's
//! WireGuardKit); the app's Swift side (ios/Sources, a Tauri mobile plugin) installs it as the device's
//! VPN through NETunnelProviderManager, starts and stops it and says how it is. Here it is the core's
//! `Tunnel`, so the commands of the window are the same as on a computer. On any other system this crate
//! is empty.

#[cfg(target_os = "ios")]
mod ios;

#[cfg(target_os = "ios")]
pub use ios::{plugin, tunnel};
