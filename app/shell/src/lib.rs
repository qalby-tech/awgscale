//! The awgscale app: a window with the interface (app/ui) and the commands it calls.
//! Everything that talks to the network or keeps a secret is here, in Rust; the
//! interface only ever sees what `ProfileShown` and the API's answers carry.

mod commands;
#[cfg(feature = "pictures")]
mod pictures;
mod state;

use tauri::Manager;

use state::AppState;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();
    #[cfg(target_os = "android")]
    let builder = builder.plugin(awgscale_android::init());
    #[cfg(target_os = "ios")]
    let builder = builder.plugin(awgscale_tunnel_ios::plugin());
    // the camera reads an invitation's or a connection's QR code; only a phone has the button
    #[cfg(mobile)]
    let builder = builder.plugin(tauri_plugin_barcode_scanner::init());
    builder
        .setup(|app| {
            let directory = app.path().app_data_dir()?;
            let tunnel = state::tunnel(app.handle());
            app.manage(AppState::new(directory, tunnel));
            #[cfg(feature = "pictures")]
            pictures::press_from_environment(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::profile,
            commands::look,
            commands::join,
            commands::import_connection,
            commands::can_scan,
            commands::connect,
            commands::disconnect,
            commands::tunnel_state,
            commands::discover,
            commands::me,
            commands::exits,
            commands::choose,
            commands::choose_filter,
            commands::choose_route,
            commands::network,
            commands::journal,
            commands::route_rules,
            commands::set_shares,
            commands::invite,
            commands::leave,
            commands::settings,
            commands::use_language,
            commands::set_settings,
            commands::opened_link,
        ])
        .build(tauri::generate_context!())
        .expect("the app's window did not start")
        .run(opened);
}

/// The event the interface listens to: a link has come for it to take (`opened_link`).
#[cfg(target_os = "ios")]
const LINK_OPENED: &str = "link-opened";
/// The scheme of the links the app opens.
#[cfg(target_os = "ios")]
const LINK_SCHEME: &str = "awgscale";

/// iOS hands the app an `awgscale://` link it was opened with (its URL scheme, ios/project.yml) as an event:
/// it is kept until the interface asks for it, and the interface is told. Android's come by its intent.
fn opened<R: tauri::Runtime>(_app: &tauri::AppHandle<R>, _event: tauri::RunEvent) {
    #[cfg(target_os = "ios")]
    if let tauri::RunEvent::Opened { urls } = _event {
        use tauri::Emitter;
        let Some(link) = urls.into_iter().find(|url| url.scheme() == LINK_SCHEME) else {
            return;
        };
        if let Some(state) = _app.try_state::<AppState>() {
            if let Ok(mut opened) = state.opened.lock() {
                *opened = Some(link.to_string());
            }
        }
        let _ = _app.emit(LINK_OPENED, ());
    }
}
