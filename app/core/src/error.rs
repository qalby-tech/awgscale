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
    /// Neither an invitation nor an AmneziaWG key or config.
    NotAConnection,
    /// An Amnezia key without an AmneziaWG connection in it.
    NoAmneziaWg,
    /// One of the QR codes the AmneziaVPN app splits a long key into.
    OneOfManyCodes,
    /// A config's key the tunnels do not take (its name only, never its value).
    UnknownKey(String),
    /// What only a network with a panel has (a connection given by hand has none).
    NoPanel,
    /// The owner's part of the app API refused: this device's user does not own the network.
    NotOwner,
    /// The server has no owner's part of the app API yet (an older one).
    NoOwnerPart,
    /// An invitation is made at the network's domain: a connection given by hand has none to make it at.
    NoDomain,
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
            Self::NotAConnection => say(
                "Это не приглашение awgscale и не ключ или конфиг AmneziaWG",
                "This is neither an awgscale invitation nor an AmneziaWG key or config",
            ),
            Self::NoAmneziaWg => say("В этом ключе нет подключения AmneziaWG", "This key holds no AmneziaWG connection"),
            Self::OneOfManyCodes => say(
                "Это один из нескольких QR-кодов — вставьте ключ vpn:// или конфиг целиком",
                "This is one of several QR codes — paste the whole vpn:// key or the config instead",
            ),
            Self::UnknownKey(key) => {
                say("В конфиге есть строка, которую приложение не принимает: ", "The config has a line the app does not take: ") + key
            }
            Self::NoPanel => say("У этого подключения нет панели сети", "This connection has no network panel"),
            Self::NotOwner => say("Это может только владелец сети", "Only the network's owner can do this"),
            Self::NoOwnerPart => {
                say("Сервер сети ещё не умеет этого — его нужно обновить", "The network's server cannot do this yet — it needs an update")
            }
            Self::NoDomain => {
                say("Приглашение делается в сети, куда вступили по ссылке", "Invitations are made in a network joined by a link")
            }
        };
        formatter.write_str(&text)
    }
}

impl std::error::Error for Error {}
