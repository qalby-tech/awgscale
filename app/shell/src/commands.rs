//! The commands the interface calls (app/ui/src/backend.ts). A failure comes back
//! as the text to show; a secret never comes back at all.

use awgscale_client::api::{AppClient, Destination, Exit, FilterWish, Me, Route};
use awgscale_client::invite::{InvitationClient, InvitationInfo};
use awgscale_client::join::JoinLink;
use awgscale_client::lang::Language;
use awgscale_client::profile::{Profile, ProfileShown, ProfileStore};
use awgscale_client::settings::DeviceSettings;
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

/// A request to the app API that found nobody: outside the tunnel that is expected; inside it the
/// server does not have the app API yet, and the person is told that, not to connect.
async fn asked<T>(state: &AppState, answer: Result<T, Error>) -> Answer<T> {
    match answer {
        Err(Error::OutsideTunnel) => {
            let tunnel = Arc::clone(&state.tunnel);
            let up = blocking(move || Ok(tunnel.state())).await? == TunnelState::Up;
            Err(if up {
                "VPN работает, но сервер сети ещё не отвечает приложению — его нужно обновить".to_owned()
            } else {
                shown(Error::OutsideTunnel)
            })
        }
        other => other.map_err(shown),
    }
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
    let options = state.settings.load().tunnel_options();
    let tunnel = Arc::clone(&state.tunnel);
    blocking(move || {
        tunnel.up(&profile.conf, options)?;
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
    asked(&state, client.me().await).await
}

#[tauri::command]
pub async fn exits(state: State<'_, AppState>) -> Answer<Vec<Exit>> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.exits().await).await
}

#[tauri::command]
pub async fn choose(destination: Destination, state: State<'_, AppState>) -> Answer<()> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.choose(destination).await).await
}

#[tauri::command]
pub async fn choose_filter(wish: FilterWish, state: State<'_, AppState>) -> Answer<()> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.choose_filter(wish).await).await
}

#[tauri::command]
pub async fn choose_route(route: Route, state: State<'_, AppState>) -> Answer<()> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.choose_route(route).await).await
}

/// The language the interface shows (the person's choice, or the system's): the core says its texts in it too.
#[tauri::command]
pub fn use_language(language: String) {
    let chosen = if language == "en" { Language::English } else { Language::Russian };
    awgscale_client::lang::set(chosen);
}

#[tauri::command]
pub fn settings(state: State<'_, AppState>) -> DeviceSettings {
    state.settings.load()
}

/// The choices are kept; a tunnel that is up is brought up again with them.
#[tauri::command]
pub async fn set_settings(settings: DeviceSettings, state: State<'_, AppState>) -> Answer<DeviceSettings> {
    let settings = settings.checked().map_err(shown)?;
    state.settings.save(&settings).map_err(shown)?;
    let tunnel = Arc::clone(&state.tunnel);
    let up = blocking({
        let tunnel = Arc::clone(&tunnel);
        move || Ok(tunnel.state())
    })
    .await?
        == TunnelState::Up;
    if up {
        let profile = saved(&state).map_err(shown)?;
        let options = settings.tunnel_options();
        blocking(move || tunnel.up(&profile.conf, options)).await?;
    }
    Ok(settings)
}

/// The device forgets the network: the tunnel goes down, the profile is deleted.
#[tauri::command]
pub async fn leave(state: State<'_, AppState>) -> Answer<()> {
    let tunnel = Arc::clone(&state.tunnel);
    blocking(move || tunnel.down()).await?;
    state.store.forget().map_err(shown)
}

/// The `awgscale://join` link the phone opened the app with (Android's intent), once; a computer has none.
#[tauri::command]
pub async fn opened_link<R: tauri::Runtime>(_app: tauri::AppHandle<R>) -> Option<String> {
    #[cfg(target_os = "android")]
    return tauri::async_runtime::spawn_blocking(move || awgscale_android::opened_link(&_app)).await.ok().flatten();
    #[cfg(not(target_os = "android"))]
    None
}
