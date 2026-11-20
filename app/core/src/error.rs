//! What can go wrong, in words the app can show as they are.

/// A failure the app shows to the person; the text is theirs to read.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("Это не ссылка-приглашение awgscale")]
    BadLink,
    #[error("Нет связи с сервером сети — проверьте интернет")]
    Unreachable,
    #[error("Нет связи с сетью — подключитесь к VPN")]
    OutsideTunnel,
    #[error("Ссылка недействительна или уже использована")]
    InvitationGone,
    /// What the server wrote for the person: shown as it is.
    #[error("{0}")]
    Refused(String),
    #[error("Сервер ответил непонятно — обновите приложение")]
    BadAnswer,
    #[error("Не удалось сохранить настройки на устройстве")]
    Storage,
    #[error("Не удалось включить VPN: {0}")]
    Tunnel(String),
}
