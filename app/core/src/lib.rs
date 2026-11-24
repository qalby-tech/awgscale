//! What every awgscale app shares, whatever draws it: the desktop and phone apps
//! (Tauri) and the Linux command line.
//!
//! A device joins a network with a link (`join`), exchanges it for a user of its
//! own (`invite`), keeps the result (`profile`), brings its tunnel up (`tunnel`)
//! and, inside the tunnel, asks the network about itself (`api`).

pub mod api;
pub mod error;
pub mod invite;
pub mod join;
pub mod lang;
pub mod manual;
pub mod profile;
pub mod secret;
pub mod service;
pub mod settings;
pub mod tunnel;

pub use error::Error;

use api::AppClient;
use invite::{InvitationClient, InvitationInfo};
use join::JoinLink;
use profile::{Panel, Profile, ProfileStore};

/// Joins the network a link names: the device gets a user called `device_name`,
/// and the profile is kept in `store` before anything else happens. The store is
/// asked first: a link may work only once, and a device that cannot keep what it
/// gets for it must not spend it.
pub async fn join(client: &InvitationClient, link: &JoinLink, device_name: &str, store: &dyn ProfileStore) -> Result<Profile, Error> {
    store.writable()?;
    let redeemed = client.redeem(device_name).await?;
    let profile = Profile::joined(link.host(), redeemed)?;
    store.save(&profile)?;
    Ok(profile)
}

/// Takes a connection given by hand (an Amnezia key, its QR code, a config) as the device's network: kept
/// in `store` as it is, nothing asked of anyone.
pub fn import(text: &str, store: &dyn ProfileStore) -> Result<Profile, Error> {
    store.writable()?;
    let profile = Profile::manual(manual::read(text)?);
    store.save(&profile)?;
    Ok(profile)
}

/// What a link is for, before the person names the device.
pub async fn look(client: &InvitationClient) -> Result<InvitationInfo, Error> {
    client.info().await
}

/// Whether a connection given by hand leads into an awgscale network: the app API is asked for this device,
/// once and briefly, where an invited device's answers (`Profile::panel_candidate`), through the tunnel
/// that must be up. It is ours when it answers as ours (who the device is); None: nobody answered so.
pub async fn discover(profile: &Profile, loopback: bool) -> Option<Panel> {
    let base = profile.panel_candidate(loopback)?;
    let me = AppClient::probing(base.clone()).ok()?.me().await.ok()?;
    Some(Panel { base: base.to_string(), name: me.name })
}
