//! The commands the interface calls (app/ui/src/backend.ts). A failure comes back
//! as the text to show; a secret never comes back at all.

use awgscale_client::api::{AppClient, Destination, Exit, Me};
use awgscale_client::invite::{InvitationClient, InvitationInfo};
use awgscale_client::join::JoinLink;
use awgscale_client::profile::{Profile, ProfileShown, ProfileStore};
use awgscale_client::tunnel::{TunnelReport, TunnelState};
use awgscale_client::Error;
use std::sync::Arc;

use tauri::State;

use crate::state::AppState;

type Answer<T> = Result<T, String>;

fn shown(error: Error) -> String {
    error.to_string()
}

fn saved(state: &AppState) -> Result<Profile, Error> {
    state.store.load()?.ok_or(Error::Refused("Устройство ещё не в сети".to_owned()))
}

fn api(state: &AppState) -> Result<AppClient, Error> {
    AppClient::new(saved(state)?.entry)
}

#[tauri::command]
pub fn profile(state: State<'_, AppState>) -> Answer<Option<ProfileShown>> {
    state.store.load().map(|profile| profile.map(|profile| profile.shown())).map_err(shown)
}

#[tauri::command]
pub async fn look(link: String) -> Answer<InvitationInfo> {
    let link = JoinLink::parse(&link).map_err(shown)?;
    let client = InvitationClient::new(&link).map_err(shown)?;
    awgscale_client::look(&client).await.map_err(shown)
}

#[tauri::command]
pub async fn join(link: String, device: String, state: State<'_, AppState>) -> Answer<ProfileShown> {
    let link = JoinLink::parse(&link).map_err(shown)?;
    let client = InvitationClient::new(&link).map_err(shown)?;
    let profile = awgscale_client::join(&client, &link, &device, &state.store).await.map_err(shown)?;
    Ok(profile.shown())
}

/// The tunnel's service may take up to a minute (it waits for the server): never on the window's thread.
async fn blocking<T: Send + 'static>(work: impl FnOnce() -> Result<T, Error> + Send + 'static) -> Answer<T> {
    tauri::async_runtime::spawn_blocking(work).await.map_err(|_| "сбой".to_owned())?.map_err(shown)
}

#[tauri::command]
pub async fn connect(state: State<'_, AppState>) -> Answer<TunnelState> {
    let profile = saved(&state).map_err(shown)?;
    let tunnel = Arc::clone(&state.tunnel);
    blocking(move || {
        tunnel.up(&profile.conf)?;
        Ok(tunnel.state())
    })
    .await
}

#[tauri::command]
pub async fn disconnect(state: State<'_, AppState>) -> Answer<TunnelState> {
    let tunnel = Arc::clone(&state.tunnel);
    blocking(move || {
        tunnel.down()?;
        Ok(tunnel.state())
    })
    .await
}

#[tauri::command]
pub async fn tunnel_state(state: State<'_, AppState>) -> Answer<TunnelReport> {
    let tunnel = Arc::clone(&state.tunnel);
    blocking(move || Ok(tunnel.report())).await
}

#[tauri::command]
pub async fn me(state: State<'_, AppState>) -> Answer<Me> {
    let client = api(&state).map_err(shown)?;
    client.me().await.map_err(shown)
}

#[tauri::command]
pub async fn exits(state: State<'_, AppState>) -> Answer<Vec<Exit>> {
    let client = api(&state).map_err(shown)?;
    client.exits().await.map_err(shown)
}

#[tauri::command]
pub async fn choose(destination: Destination, state: State<'_, AppState>) -> Answer<()> {
    let client = api(&state).map_err(shown)?;
    client.choose(destination).await.map_err(shown)
}

/// The device forgets the network: the tunnel goes down, the profile is deleted.
#[tauri::command]
pub async fn leave(state: State<'_, AppState>) -> Answer<()> {
    let tunnel = Arc::clone(&state.tunnel);
    blocking(move || tunnel.down()).await?;
    state.store.forget().map_err(shown)
}
