//! The commands the interface calls (app/ui/src/backend.ts). A failure comes back
//! as the text to show; a secret never comes back at all.

use awgscale_client::api::{AppClient, Destination, Exit, FilterWish, Me, Route};
use awgscale_client::invite::{InvitationClient, InvitationInfo};
use awgscale_client::join::JoinLink;
use awgscale_client::lang::Language;
use awgscale_client::owner::{base_of, Invitation, InvitationWish, Mode, Network, Note, People, RuleChange};
use awgscale_client::profile::{NetworkKind, Profile, ProfileShown, ProfileStore};
use awgscale_client::settings::DeviceSettings;
use awgscale_client::tunnel::{TunnelReport, TunnelState};
use awgscale_client::Error;
use std::collections::BTreeMap;
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

/// The network's app API: a connection given by hand has one only once its server answered as an awgscale
/// network (`discover`); otherwise the interface does not ask it.
fn api(state: &AppState) -> Result<AppClient, Error> {
    let profile = saved(state)?;
    AppClient::at(profile.api_base().ok_or(Error::NoPanel)?)
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

/// A connection given by hand (an Amnezia key `vpn://…`, its QR code, an AmneziaWG config) becomes the
/// device's network; nobody is asked anything.
#[tauri::command]
pub fn import_connection(text: String, state: State<'_, AppState>) -> Answer<ProfileShown> {
    awgscale_client::import(&text, &state.store).map(|profile| profile.shown()).map_err(shown)
}

/// Whether the device has a camera to read a QR code with (a phone): only then the join screen offers it.
#[tauri::command]
pub fn can_scan() -> bool {
    cfg!(mobile)
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

/// A connection given by hand, its tunnel up: does an awgscale network answer behind it (a key of our own
/// panel or bot)? Asked each time the tunnel comes up, in the background; what is found is kept. A network
/// once found stays (a slow link or a server being updated must not take the person's screens away, as it
/// never does an invited device's); only what answers updates it. The profile as it is now comes back.
#[tauri::command]
pub async fn discover(state: State<'_, AppState>) -> Answer<ProfileShown> {
    let asked = saved(&state).map_err(shown)?;
    if asked.kind != NetworkKind::Manual {
        return Ok(asked.shown());
    }
    let tunnel = Arc::clone(&state.tunnel);
    if blocking(move || Ok(tunnel.state())).await? != TunnelState::Up {
        return Ok(asked.shown());
    }
    // only the pictures' build, whose tunnel pretends, finds its stand-in on 127.0.0.1
    let found = awgscale_client::discover(&asked, cfg!(feature = "pretend-tunnel")).await;
    // the person may have removed the connection, or given another, while the server was asked
    let mut now = saved(&state).map_err(shown)?;
    if let Some(found) = found.filter(|found| now == asked && now.panel.as_ref() != Some(found)) {
        now.panel = Some(found);
        state.store.save(&now).map_err(shown)?;
    }
    Ok(now.shown())
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

/// The owner's network; None: the server has no owner's part yet (the interface shows the panel instead).
#[tauri::command]
pub async fn network(state: State<'_, AppState>) -> Answer<Option<Network>> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.network().await).await
}

/// The change journal, the latest first; None as for `network`.
#[tauri::command]
pub async fn journal(state: State<'_, AppState>) -> Answer<Option<Vec<Note>>> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.journal().await).await
}

/// Rules sent elsewhere or made for other groups, and their new order (None: as it is), all the staged ones
/// together, on top of the version the interface showed.
#[tauri::command]
pub async fn route_rules(changes: Vec<RuleChange>, order: Option<Vec<String>>, base: String, state: State<'_, AppState>) -> Answer<()> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.route_rules(&changes, order.as_deref(), &base).await).await
}

/// What goes abroad: the listed sites, or everything foreign.
#[tauri::command]
pub async fn set_mode(mode: Mode, state: State<'_, AppState>) -> Answer<()> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.set_mode(mode).await).await
}

/// One page of people: of a group (its id, or "none"), or of everybody; None as for `network`.
#[tauri::command]
pub async fn people(group: Option<String>, state: State<'_, AppState>) -> Answer<Option<People>> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.people(group.as_deref()).await).await
}

/// People paused (`on`) or let back.
#[tauri::command]
pub async fn pause_people(names: Vec<String>, on: bool, state: State<'_, AppState>) -> Answer<()> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.pause(&names, on).await).await
}

/// People removed for good.
#[tauri::command]
pub async fn remove_people(names: Vec<String>, state: State<'_, AppState>) -> Answer<()> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.remove(&names).await).await
}

/// The shares of the exits in percent, by their number, on top of the weights the interface showed.
#[tauri::command]
pub async fn set_shares(shares: BTreeMap<u32, u32>, base: BTreeMap<u32, u32>, state: State<'_, AppState>) -> Answer<()> {
    let client = api(&state).map_err(shown)?;
    asked(&state, client.set_shares(&shares, &base).await).await
}

/// A new invitation, its link at the network's public name and its QR code. The server says its name; an
/// older one does not, and then the name the device joined by is used (a connection given by hand has none).
#[tauri::command]
pub async fn invite(wish: InvitationWish, state: State<'_, AppState>) -> Answer<Invitation> {
    let profile = saved(&state).map_err(shown)?;
    let client = api(&state).map_err(shown)?;
    let said = match client.network().await {
        Ok(Some(network)) => network.invite_base,
        _ => String::new(),
    };
    let base = if !said.is_empty() {
        said
    } else if profile.kind == NetworkKind::Invited {
        base_of(&profile.host)
    } else {
        return Err(shown(Error::NoDomain));
    };
    asked(&state, client.invite(&wish, &base).await).await
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

/// «Выйти»: the device forgets the network: the tunnel goes down, the profile is deleted. The network keeps
/// the device (its app API has no way for a device to remove itself): its owner removes it in the panel.
#[tauri::command]
pub async fn leave(state: State<'_, AppState>) -> Answer<()> {
    let tunnel = Arc::clone(&state.tunnel);
    blocking(move || tunnel.down()).await?;
    state.store.forget().map_err(shown)
}

/// The `awgscale://join` link the phone opened the app with (Android's intent, iOS's URL scheme), once; a
/// computer has none.
#[tauri::command]
pub async fn opened_link<R: tauri::Runtime>(_app: tauri::AppHandle<R>, _state: State<'_, AppState>) -> Result<Option<String>, ()> {
    #[cfg(target_os = "android")]
    return Ok(tauri::async_runtime::spawn_blocking(move || awgscale_android::opened_link(&_app)).await.ok().flatten());
    #[cfg(target_os = "ios")]
    return Ok(_state.opened.lock().ok().and_then(|mut opened| opened.take()));
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    Ok(None)
}
