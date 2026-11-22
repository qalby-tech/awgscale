//! The language of what the core says (the app's choice, Настройки → Язык). The texts of the service
//! (app/qalby) and of the server come in Russian; the ones the app is known to meet are said in
//! English here too, the rest as they came.

use std::sync::atomic::{AtomicBool, Ordering};

static ENGLISH: AtomicBool = AtomicBool::new(false);

/// The app's language.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Language {
    Russian,
    English,
}

pub fn set(language: Language) {
    ENGLISH.store(language == Language::English, Ordering::Relaxed);
}

/// The text in the app's language.
pub fn say(russian: &str, english: &str) -> String {
    if ENGLISH.load(Ordering::Relaxed) {
        english.to_owned()
    } else {
        russian.to_owned()
    }
}

/// The Russian texts of the service and the server the app meets, and their English; a text that
/// begins with one of them gets its English beginning.
const KNOWN: &[(&str, &str)] = &[
    (
        "сервер не ответил: нет связи с ним или ключ устройства больше не действует",
        "the server did not answer: no connection to it, or the device's key is no longer valid",
    ),
    (
        "через туннель ничего не приходит: система отбрасывает ответы сервера",
        "nothing comes back through the tunnel: the system drops the server's answers",
    ),
    ("через туннель ничего не приходит", "nothing comes back through the tunnel"),
    ("не удалось найти сервер", "could not find the server"),
    ("не удалось настроить сеть", "could not set up the network"),
    ("не удалось создать интерфейс", "could not create the network interface"),
    ("не удалось включить интерфейс", "could not bring the interface up"),
    ("сервер прислал конфиг, который не подходит этой версии", "the server's config does not suit this version"),
    ("конфиг устройства не подходит", "the device's config does not fit"),
    (
        "kill switch нужен туннель для всего трафика, а сервер отдал не весь",
        "the kill switch needs a tunnel for all traffic, and the server gave less",
    ),
    ("слишком много сетей мимо VPN", "too many networks bypass the VPN"),
    ("мимо VPN нельзя отправить всё", "not everything can bypass the VPN"),
    ("нет прав управлять VPN", "no right to run the VPN"),
    ("служба awgscale не запущена", "the awgscale service is not running"),
    ("служба awgscale не ответила", "the awgscale service did not answer"),
    ("служба awgscale ответила непонятно", "the awgscale service gave an unclear answer"),
    ("непонятный запрос", "an unclear request"),
    ("Windows не настроил адаптер вовремя", "Windows did not set the adapter up in time"),
    ("Windows не принял адрес туннеля", "Windows did not take the tunnel's address"),
    ("это устройство не найдено в сети", "this device is not found in the network"),
    (
        "защиту этого устройства задаёт взрослый: в детской группе её не поменять",
        "an adult sets this device's protection: it is not changed in a children's group",
    ),
    ("Защита сохранена, но не применилась", "Protection is saved but not applied"),
    (
        "в этой сети нет зарубежного выхода: весь трафик и так идёт с сервера",
        "this network has no exit abroad: all traffic already leaves from its server",
    ),
    (
        "сохранено, но список пользователей не прочитан — применится при следующем опросе",
        "saved, but the list of users was not read: it applies at the next check",
    ),
    ("Ссылка недействительна или уже использована", "The link is not valid or has been used"),
    ("слишком много запросов", "too many requests"),
];

/// A text of the service or the server, in the app's language when it is a known one.
pub fn translated(text: &str) -> String {
    if !ENGLISH.load(Ordering::Relaxed) {
        return text.to_owned();
    }
    for (russian, english) in KNOWN {
        if let Some(rest) = text.strip_prefix(russian) {
            return format!("{english}{rest}");
        }
    }
    text.to_owned()
}
