use std::sync::Arc;

use awgscale_client::secret::Secret;
use awgscale_client::tunnel::{Tunnel, TunnelOptions, TunnelReport, TunnelState};
use awgscale_client::Error;
use serde::{Deserialize, Serialize};
use tauri::plugin::{Builder, PluginHandle, TauriPlugin};
use tauri::{AppHandle, Manager, Wry};

use crate::refusal;

/// The plugin's name; also what the Swift side is registered under.
const PLUGIN: &str = "awgscale-tunnel";

// TunnelPlugin.swift: @_cdecl("init_plugin_awgscale_tunnel")
tauri::ios_plugin_binding!(init_plugin_awgscale_tunnel);

/// The Swift side's commands (TunnelPlugin.swift: one @objc method each).
mod command {
    pub const UP: &str = "up";
    pub const DOWN: &str = "down";
    pub const STATE: &str = "state";
}

/// What `up` gets: the config (wg-quick text, AmneziaWG keys included) and the person's choices.
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct UpRequest<'a> {
    conf: &'a str,
    kill_switch: bool,
    local_around: bool,
    around: &'a [String],
}

/// What every command answers: the tunnel as it is now.
#[derive(Deserialize)]
struct Answer {
    state: TunnelState,
    #[serde(default)]
    warning: Option<String>,
}

impl From<Answer> for TunnelReport {
    fn from(answer: Answer) -> Self {
        TunnelReport { state: answer.state, warning: answer.warning }
    }
}

/// The plugin's handle: the Swift side answers on its own queue, the caller waits (never on the
/// window's thread: the commands run these in spawn_blocking).
struct IosTunnel(PluginHandle<Wry>);

impl IosTunnel {
    /// A refusal of the Swift side is its fixed code, said in the app's words (refusal.rs); anything
    /// else that failed is said as nothing more than a failure.
    fn ask(&self, command: &str, payload: impl Serialize) -> Result<Answer, Error> {
        self.0.run_mobile_plugin::<Answer>(command, payload).map_err(|failure| match failure {
            tauri::plugin::mobile::PluginInvokeError::InvokeRejected(response) => {
                refusal::refused(response.code.as_deref(), response.message.as_deref().unwrap_or_default())
            }
            _ => Error::Tunnel("сбой".to_owned()),
        })
    }
}

impl Tunnel for IosTunnel {
    fn up(&self, conf: &Secret, options: TunnelOptions) -> Result<(), Error> {
        // as the service on a computer: no kill switch for a tunnel that does not take all traffic
        if options.kill_switch && !refusal::full_tunnel(conf.expose()) {
            return Err(refusal::kill_switch_refused());
        }
        let request = UpRequest {
            conf: conf.expose(),
            kill_switch: options.kill_switch,
            local_around: options.local_around,
            around: &options.around,
        };
        self.ask(command::UP, request).map(|_| ())
    }

    fn down(&self) -> Result<(), Error> {
        self.ask(command::DOWN, ()).map(|_| ())
    }

    fn state(&self) -> TunnelState {
        self.report().state
    }

    fn report(&self) -> TunnelReport {
        match self.ask(command::STATE, ()) {
            Ok(answer) => answer.into(),
            Err(failure) => TunnelReport { state: TunnelState::Down, warning: Some(failure.to_string()) },
        }
    }
}

/// What the plugin keeps in the app's state once the Swift side is registered.
struct Registered(Arc<IosTunnel>);

/// The plugin: registers the Swift side when the app starts.
pub fn plugin() -> TauriPlugin<Wry> {
    Builder::new(PLUGIN)
        .setup(|app, api| {
            let handle = api.register_ios_plugin(init_plugin_awgscale_tunnel)?;
            app.manage(Registered(Arc::new(IosTunnel(handle))));
            Ok(())
        })
        .build()
}

/// The device's tunnel, once `plugin()` has been set up (it is, before the app's own setup runs).
pub fn tunnel(app: &AppHandle<Wry>) -> Option<Arc<dyn Tunnel>> {
    app.try_state::<Registered>().map(|registered| Arc::clone(&registered.0) as Arc<dyn Tunnel>)
}
