// The app's words in each of its languages. One object per language with the same keys: the types make
// sure no text is missing in either. The language is the person's choice (Настройки → Язык), or the
// system's when they made none.
import { createContext, useContext } from "react";

import { Filter, Route, TunnelState } from "./types";

export enum Language {
  Russian = "ru",
  English = "en",
}

const RUSSIAN_PREFIX = "ru";

const russian = {
  locale: Language.Russian as Language,
  connectTitle: "Подключитесь к своей сети",
  inviteLink: "Ссылка-приглашение",
  checking: "Проверяем…",
  continueButton: "Продолжить",
  invitationLeft: (left: number, total: number, group: string | null) =>
    `Ссылка работает ещё для ${left} из ${total} устройств` + (group == null ? "" : ` · группа «${group}»`),
  deviceName: "Как назвать это устройство",
  deviceNameRule: "Латинские буквы, цифры, _ и -, до 32 символов",
  joining: "Создаём подключение…",
  join: "Подключить устройство",
  anotherLink: "Другая ссылка",
  givenHint: "Или ключ AmneziaVPN (vpn://…), или конфиг AmneziaWG",
  scanCode: "Сканировать QR-код",
  importFile: "Открыть файл",
  scanHint: "Наведите камеру на QR-код",
  cameraRefused: "Приложению не разрешено пользоваться камерой — разрешите это в настройках телефона",
  notConnection: "Это не приглашение awgscale и не ключ или конфиг AmneziaWG",
  manualNetwork: "Подключение AmneziaWG",

  myNetwork: "Моя сеть",
  owner: "владелец",
  connect: "Подключить",
  disconnect: "Отключить",
  states: {
    [TunnelState.Down]: "Отключено",
    [TunnelState.Connecting]: "Подключаемся…",
    [TunnelState.Up]: "Подключено",
  } as Record<TunnelState, string>,
  auto: "Авто",
  pinned: "зафиксирован",
  route: "Маршрут",
  routes: {
    [Route.Smart]: "Умный",
    [Route.All]: "Весь трафик",
  } as Record<Route, string>,
  protection: "Защита",
  setByAdult: "задаёт взрослый",
  aboutProtection: "О режимах защиты",
  protectionLevels: "Режимы защиты",
  // one short line each, from what the network's filtering does (agent/adguard)
  levelNotes: {
    [Filter.Off]: "ничего не блокируется, даже опасные сайты",
    [Filter.Standard]: "реклама и опасные сайты (вирусы, фишинг)",
    [Filter.Pro]: "ещё трекеры и больше скрытой рекламы",
    [Filter.ProPlus]: "строже: ещё больше трекеров, изредка ломает сайты",
    [Filter.Ultimate]: "самая строгая: некоторые сайты могут не работать",
    [Filter.Kids]: "ещё сайты для взрослых, безопасный поиск",
  } as Record<Filter, string>,
  filters: {
    [Filter.Standard]: "Обычная",
    [Filter.Off]: "Выключена",
    [Filter.Kids]: "Детская",
    [Filter.Pro]: "Pro",
    [Filter.ProPlus]: "Pro++",
    [Filter.Ultimate]: "Ultimate",
  } as Record<Filter, string>,

  location: "Локация",
  close: "Закрыть",
  exitDown: "не работает",
  back: "Назад",
  search: "Поиск",
  searchPlaceholder: "Страна",
  recent: "Недавние",
  countries: "Страны",
  otherCountries: "Другие",
  workingServers: (count: number) => `Работающих серверов: ${count}`,
  nothingFound: "Ничего не нашлось",
  shownOf: (shown: number, total: number) => `Показаны ${shown} из ${total} — уточните поиск`,
  bestServer: "Лучший сервер",
  fixedIp: "Постоянный IP",
  fixedOff: "Сервер выбирается автоматически, IP может меняться",
  fixedOn: (address: string | null) =>
    address == null
      ? "Всегда этот сервер: сайты видят один и тот же IP"
      : `Всегда этот сервер: сайты видят один и тот же IP ${address}`,
  leastLoaded: "Наименее загруженные",
  server: "Сервер",
  noWorking: "Сейчас здесь нет работающих серверов",

  tabs: { connection: "Подключение", network: "Сеть", journal: "Журнал", settings: "Настройки" },
  ownerLater: "Управлять сетью из приложения можно будет в следующей версии.",
  ownerLaterPanel: "Пока это делается в панели:",

  settings: "Настройки",
  device: "Устройство",
  killSwitch: "Kill switch",
  killSwitchNote: "Блокировать интернет, если VPN отключился",
  splitTunneling: "Раздельное туннелирование",
  splitNote: "Что здесь указано, идёт мимо VPN",
  localNetworks: "Локальные сети",
  localNetworksNote: "Роутер, принтер и другие устройства дома",
  autoTag: "авто",
  localNames: { private: "Частные сети", tailscale: "Tailscale", linkLocal: "Link-local", ipv6: "Локальные IPv6" },
  add: "Добавить",
  remove: (item: string) => `Убрать ${item}`,
  account: "Аккаунт",
  network: "Сеть",
  thisDevice: "Это устройство",
  myAddress: "Мой адрес в сети",
  signOut: "Выйти",
  linkWhileJoined:
    "Открыта ссылка-приглашение. Это устройство уже в сети: чтобы подключиться по ссылке, сначала нажмите «Выйти» в Настройках и откройте её снова.",
  signOutQuestion: "Выйти?",
  signOutNote: "Устройство забудет сеть: чтобы подключиться снова, понадобится новое приглашение.",
  removeConnection: "Удалить подключение",
  removeQuestion: "Удалить подключение?",
  removeYes: "Удалить",
  cancel: "Отмена",
  about: "О приложении",
  language: "Язык",
  version: "Версия",

  somethingWrong: "Что-то пошло не так — попробуйте ещё раз",
};

type Texts = typeof russian;

const english: Texts = {
  locale: Language.English,
  connectTitle: "Connect to your network",
  inviteLink: "Invitation link",
  checking: "Checking…",
  continueButton: "Continue",
  invitationLeft: (left, total, group) =>
    `The link works for ${left} more of ${total} devices` + (group == null ? "" : ` · group “${group}”`),
  deviceName: "Name this device",
  deviceNameRule: "Latin letters, digits, _ and -, up to 32 characters",
  joining: "Setting up the connection…",
  join: "Connect this device",
  anotherLink: "Another link",
  givenHint: "Or an AmneziaVPN key (vpn://…), or an AmneziaWG config",
  scanCode: "Scan QR code",
  importFile: "Open a file",
  scanHint: "Point the camera at the QR code",
  cameraRefused: "The app may not use the camera — allow it in the phone's settings",
  notConnection: "This is neither an awgscale invitation nor an AmneziaWG key or config",
  manualNetwork: "AmneziaWG connection",

  myNetwork: "My network",
  owner: "owner",
  connect: "Connect",
  disconnect: "Disconnect",
  states: {
    [TunnelState.Down]: "Disconnected",
    [TunnelState.Connecting]: "Connecting…",
    [TunnelState.Up]: "Connected",
  },
  auto: "Auto",
  pinned: "pinned",
  route: "Route",
  routes: {
    [Route.Smart]: "Smart",
    [Route.All]: "All traffic",
  },
  protection: "Protection",
  setByAdult: "set by an adult",
  aboutProtection: "About the protection levels",
  protectionLevels: "Protection levels",
  levelNotes: {
    [Filter.Off]: "nothing is blocked, not even dangerous sites",
    [Filter.Standard]: "blocks ads and dangerous sites (malware, phishing)",
    [Filter.Pro]: "also trackers and more hidden ads",
    [Filter.ProPlus]: "stricter: even more trackers, may break a site now and then",
    [Filter.Ultimate]: "the strictest: some sites may not work",
    [Filter.Kids]: "also adult sites, with safe search",
  },
  filters: {
    [Filter.Standard]: "Standard",
    [Filter.Off]: "Off",
    [Filter.Kids]: "Kids",
    [Filter.Pro]: "Pro",
    [Filter.ProPlus]: "Pro++",
    [Filter.Ultimate]: "Ultimate",
  },

  location: "Location",
  close: "Close",
  exitDown: "down",
  back: "Back",
  search: "Search",
  searchPlaceholder: "Country",
  recent: "Recent",
  countries: "Countries",
  otherCountries: "Other",
  workingServers: count => `Working servers: ${count}`,
  nothingFound: "Nothing found",
  shownOf: (shown, total) => `Showing ${shown} of ${total} — narrow the search`,
  bestServer: "Best server",
  fixedIp: "Fixed IP",
  fixedOff: "A server is picked automatically; your IP may change",
  fixedOn: address =>
    address == null
      ? "Always this server: sites see the same IP"
      : `Always this server: sites see the same IP ${address}`,
  leastLoaded: "Least loaded",
  server: "Server",
  noWorking: "No working servers here right now",

  tabs: { connection: "Connection", network: "Network", journal: "Journal", settings: "Settings" },
  ownerLater: "Running the network from the app comes in a later version.",
  ownerLaterPanel: "For now it is done in the panel:",

  settings: "Settings",
  device: "Device",
  killSwitch: "Kill switch",
  killSwitchNote: "Block the internet if the VPN drops",
  splitTunneling: "Split tunneling",
  splitNote: "What is listed here bypasses the VPN",
  localNetworks: "Local networks",
  localNetworksNote: "Your router, printer and other devices at home",
  autoTag: "auto",
  localNames: { private: "Private networks", tailscale: "Tailscale", linkLocal: "Link-local", ipv6: "Local IPv6" },
  add: "Add",
  remove: item => `Remove ${item}`,
  account: "Account",
  network: "Network",
  thisDevice: "This device",
  myAddress: "My address in the network",
  signOut: "Sign out",
  linkWhileJoined:
    "An invitation link was opened. This device is in a network already: to join by the link, sign out in Settings first, then open it again.",
  signOutQuestion: "Sign out?",
  signOutNote: "This device forgets the network; you will need a new invitation to connect again.",
  removeConnection: "Remove the connection",
  removeQuestion: "Remove the connection?",
  removeYes: "Remove",
  cancel: "Cancel",
  about: "About",
  language: "Language",
  version: "Version",

  somethingWrong: "Something went wrong — try again",
};

export const TEXTS: Record<Language, Texts> = { [Language.Russian]: russian, [Language.English]: english };

export const LANGUAGE_NAMES: Record<Language, string> = {
  [Language.Russian]: "Русский",
  [Language.English]: "English",
};

// The system's language when the person chose none: Russian for Russian, English for anything else.
export function systemLanguage(): Language {
  return navigator.language.toLowerCase().startsWith(RUSSIAN_PREFIX) ? Language.Russian : Language.English;
}

export function languageOf(chosen: string | null): Language {
  return Object.values(Language).find(language => language === chosen) ?? systemLanguage();
}

export const TextsContext = createContext<Texts>(russian);

export function useTexts(): Texts {
  return useContext(TextsContext);
}
