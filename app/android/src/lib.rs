//! The awgscale app on Android. The tunnel is the app's VpnService (android/, Kotlin): it asks
//! Android once for the right to be the VPN, then runs amneziawg-go (app/qalby/cmd/libawgscale) on
//! the interface Android gives it, in a foreground service that outlives the window. This crate is
//! the Rust side of that plugin: `Tunnel` for the core, and the `awgscale://` link the phone opened
//! the app with.

use std::sync::mpsc;
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::plugin::{Builder, PluginHandle, TauriPlugin};
use tauri::{AppHandle, Manager, Runtime};

use awgscale_client::lang::say;
use awgscale_client::secret::Secret;
use awgscale_client::tunnel::{Tunnel, TunnelOptions, TunnelReport, TunnelState};
use awgscale_client::Error;

/// The plugin's name for Tauri, and where its Kotlin class lives.
const PLUGIN: &str = "awgscale-android";
const KOTLIN_PACKAGE: &str = "tech.qalby.awgscale.tunnel";
const KOTLIN_CLASS: &str = "TunnelPlugin";

/// What the Kotlin side was asked (the names of TunnelPlugin's commands).
mod command {
    pub const UP: &str = "up";
    pub const DOWN: &str = "down";
    pub const STATUS: &str = "status";
    pub const OPENED_LINK: &str = "openedLink";
}

/// Why the Kotlin side refused (the codes of TunnelPlugin's rejections).
mod refusal {
    pub const CONSENT: &str = "consent";
    pub const CONFIG: &str = "config";
    pub const RESOLVE: &str = "resolve";
    pub const ESTABLISH: &str = "establish";
    pub const HANDSHAKE: &str = "handshake";
    pub const KILL_SWITCH: &str = "kill_switch";
}

/// How long the Kotlin side may take to answer. Bringing the tunnel up waits for the person's answer
/// to Android's question (once), then for the server's name and its first answer (UP_WAIT_MS there);
/// anything else is answered at once by the tunnel's service (or not at all: its process died).
const UP_WAIT: Duration = Duration::from_secs(300);
const ASK_WAIT: Duration = Duration::from_secs(20);

#[derive(Serialize)]
struct UpRequest<'a> {
    conf: &'a str,
    kill_switch: bool,
    local_around: bool,
    around: &'a [String],
}

/// The service's state, as the Kotlin side reports it.
#[derive(Deserialize)]
struct Report {
    state: TunnelState,
    /// The kill switch was asked for, and Android's own («Блокировать соединения без VPN») is off.
    #[serde(default)]
    lockdown_missing: bool,
}

impl Report {
    fn shown(&self) -> TunnelReport {
        let warning = (self.state != TunnelState::Down && self.lockdown_missing).then(|| {
            say(
                "«Без VPN нет интернета» на Android включает система: Настройки → Сеть → VPN → awgscale → «Постоянная VPN» и «Блокировать соединения без VPN»",
                "On Android the system keeps the internet off without the VPN: Settings → Network → VPN → awgscale → “Always-on VPN” and “Block connections without VPN”",
            )
        });
        TunnelReport { state: self.state, warning }
    }
}

#[derive(Deserialize)]
struct OpenedLink {
    link: Option<String>,
}

/// The phone's tunnel: every question goes to the service, which may have outlived an earlier window.
pub struct PhoneTunnel<R: Runtime> {
    plugin: PluginHandle<R>,
}

impl<R: Runtime> PhoneTunnel<R> {
    fn ask(&self, command: &'static str, payload: impl Serialize, wait: Duration) -> Result<Report, Error> {
        let payload = serde_json::to_value(payload).map_err(|_| Error::Tunnel("сбой".to_owned()))?;
        let plugin = self.plugin.clone();
        within(wait, move || plugin.run_mobile_plugin::<Report>(command, payload))
            .ok_or_else(|| Error::Tunnel("служба awgscale не ответила".to_owned()))?
            .map_err(refused)
    }
}

/// `work`'s result, or None when it took longer than `wait` (it is then left to finish on its own).
fn within<T: Send + 'static>(wait: Duration, work: impl FnOnce() -> T + Send + 'static) -> Option<T> {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let _ = sender.send(work());
    });
    receiver.recv_timeout(wait).ok()
}

/// The Kotlin side's refusal, in the words of the computer's service where there are such.
fn refused(error: tauri::plugin::mobile::PluginInvokeError) -> Error {
    use tauri::plugin::mobile::PluginInvokeError;
    let PluginInvokeError::InvokeRejected(rejection) = error else {
        return Error::Tunnel("сбой".to_owned());
    };
    let detail = rejection.message.unwrap_or_default();
    let reason = match rejection.code.as_deref() {
        Some(refusal::CONSENT) => "Android не получил разрешения на VPN".to_owned(),
        Some(refusal::CONFIG) if detail.is_empty() => "конфиг устройства не подходит".to_owned(),
        Some(refusal::CONFIG) => format!("конфиг устройства не подходит: {detail}"),
        Some(refusal::KILL_SWITCH) => "kill switch нужен туннель для всего трафика, а сервер отдал не весь".to_owned(),
        Some(refusal::RESOLVE) => format!("не удалось найти сервер {detail}"),
        Some(refusal::ESTABLISH) => "Android не дал создать VPN: возможно, включена постоянная VPN другого приложения".to_owned(),
        Some(refusal::HANDSHAKE) => "сервер не ответил: нет связи с ним или ключ устройства больше не действует".to_owned(),
        _ if detail.is_empty() => "сбой".to_owned(),
        _ => detail,
    };
    Error::Tunnel(reason)
}

impl<R: Runtime> Tunnel for PhoneTunnel<R> {
    fn up(&self, conf: &Secret, options: TunnelOptions) -> Result<(), Error> {
        let request = UpRequest {
            conf: conf.expose(),
            kill_switch: options.kill_switch,
            local_around: options.local_around,
            around: &options.around,
        };
        self.ask(command::UP, request, UP_WAIT).map(|_| ())
    }

    fn down(&self) -> Result<(), Error> {
        self.ask(command::DOWN, (), ASK_WAIT).map(|_| ())
    }

    fn state(&self) -> TunnelState {
        self.report().state
    }

    fn report(&self) -> TunnelReport {
        self.ask(command::STATUS, (), ASK_WAIT)
            .map(|report| report.shown())
            .unwrap_or(TunnelReport { state: TunnelState::Down, warning: None })
    }
}

/// The plugin: registers the Kotlin side and keeps its handle for `tunnel` and `opened_link`.
pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new(PLUGIN)
        .setup(|app, api| {
            let plugin = api.register_android_plugin(KOTLIN_PACKAGE, KOTLIN_CLASS)?;
            app.manage(Arc::new(PhoneTunnel { plugin }));
            Ok(())
        })
        .build()
}

/// The phone's tunnel, once `init` has run.
pub fn tunnel<R: Runtime>(app: &AppHandle<R>) -> Arc<dyn Tunnel> {
    let tunnel: Arc<PhoneTunnel<R>> = Arc::clone(&app.state::<Arc<PhoneTunnel<R>>>());
    tunnel
}

/// The `awgscale://` link the phone opened the app with since it was last asked, if any.
pub fn opened_link<R: Runtime>(app: &AppHandle<R>) -> Option<String> {
    let plugin = app.try_state::<Arc<PhoneTunnel<R>>>()?.plugin.clone();
    within(ASK_WAIT, move || plugin.run_mobile_plugin::<OpenedLink>(command::OPENED_LINK, ())).and_then(Result::ok)?.link
}
