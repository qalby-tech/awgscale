//! What can go wrong, in words the app can show as they are, in its language (see lang).

use crate::lang::{say, translated};

/// A failure the app shows to the person; the text is theirs to read.
#[derive(Debug)]
pub enum Error {
    BadLink,
    Unreachable,
    OutsideTunnel,
    InvitationGone,
    /// What the server (or the core) wrote for the person: shown as it is, in English when known.
    Refused(String),
    BadAnswer,
    Storage,
    /// Why the tunnel did not come up (the service's words).
    Tunnel(String),
}

impl std::fmt::Display for Error {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let text = match self {
            Self::BadLink => say("Это не ссылка-приглашение awgscale", "This is not an awgscale invitation link"),
            Self::Unreachable => {
                say("Нет связи с сервером сети — проверьте интернет", "No connection to the network's server — check the internet")
            }
            Self::OutsideTunnel => say("Нет связи с сетью — подключитесь к VPN", "No connection to the network — connect the VPN"),
            Self::InvitationGone => say("Ссылка недействительна или уже использована", "The link is not valid or has been used"),
            Self::Refused(text) => translated(text),
            Self::BadAnswer => say("Сервер ответил непонятно — обновите приложение", "The server's answer is unclear — update the app"),
            Self::Storage => say("Не удалось сохранить настройки на устройстве", "Could not save the settings on the device"),
            Self::Tunnel(reason) => say("Не удалось включить VPN: ", "Could not turn the VPN on: ") + &translated(reason),
        };
        formatter.write_str(&text)
    }
}

impl std::error::Error for Error {}
