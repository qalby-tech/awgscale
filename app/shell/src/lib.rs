//! The awgscale app: a window with the interface (app/ui) and the commands it calls.
//! Everything that talks to the network or keeps a secret is here, in Rust; the
//! interface only ever sees what `ProfileShown` and the API's answers carry.

mod commands;
mod state;

use tauri::Manager;

use state::AppState;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();
    #[cfg(target_os = "android")]
    let builder = builder.plugin(awgscale_android::init());
    builder
        .setup(|app| {
            let directory = app.path().app_data_dir()?;
            app.manage(AppState::new(directory, app.handle()));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::profile,
            commands::look,
            commands::join,
            commands::connect,
            commands::disconnect,
            commands::tunnel_state,
            commands::me,
            commands::exits,
            commands::choose,
            commands::choose_filter,
            commands::choose_route,
            commands::leave,
            commands::settings,
            commands::use_language,
            commands::set_settings,
            commands::opened_link,
        ])
        .run(tauri::generate_context!())
        .expect("the app's window did not start");
}
