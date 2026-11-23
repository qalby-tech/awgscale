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
  asGroup: "Как у группы",
  setByAdult: "задаёт взрослый",
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
  searchPlaceholder: "Страна или IP",
  recent: "Недавние",
  countries: "Страны",
  otherCountries: "Другие",
  workingServers: (count: number) => `Работающих серверов: ${count}`,
  nothingFound: "Ничего не нашлось",
  shownOf: (shown: number, total: number) => `Показаны ${shown} из ${total} — уточните поиск`,
  bestServer: "Лучший сервер",
  pinAddress: "Зафиксировать IP",
  mayChange: "Сервер может смениться",
  unpinNote: "Выключите — сервер выберется автоматически, в любой стране",
  leastLoaded: "Наименее загруженные",
  searchAddress: "Поиск по IP",
  server: "Сервер",
  noWorking: "Сейчас здесь нет работающих серверов",

  tabs: { connection: "Подключение", network: "Сеть", journal: "Журнал", settings: "Настройки" },
  ownerLater: "Управлять сетью из приложения можно будет в следующей версии.",
  ownerLaterPanel: "Пока это делается в панели:",

  settings: "Настройки",
  device: "Устройство",
  killSwitch: "Без VPN нет интернета",
  localAround: "Локальные сети мимо VPN",
  aroundVpn: "Мимо VPN",
  autoTag: "авто",
  localNames: { private: "Частные сети", tailscale: "Tailscale", linkLocal: "Link-local", ipv6: "Локальные IPv6" },
  add: "Добавить",
  remove: (item: string) => `Убрать ${item}`,
  account: "Аккаунт",
  network: "Сеть",
  thisDevice: "Это устройство",
  myAddress: "Мой адрес в сети",
  leave: "Выйти из сети",
  leaveQuestion: "Выйти из сети?",
  leaveYes: "Выйти",
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
  asGroup: "As my group",
  setByAdult: "set by an adult",
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
  searchPlaceholder: "Country or IP",
  recent: "Recent",
  countries: "Countries",
  otherCountries: "Other",
  workingServers: count => `Working servers: ${count}`,
  nothingFound: "Nothing found",
  shownOf: (shown, total) => `Showing ${shown} of ${total} — narrow the search`,
  bestServer: "Best server",
  pinAddress: "Keep this IP",
  mayChange: "The server may change",
  unpinNote: "Switch off and a server is chosen automatically, in any country",
  leastLoaded: "Least loaded",
  searchAddress: "Search by IP",
  server: "Server",
  noWorking: "No working servers here right now",

  tabs: { connection: "Connection", network: "Network", journal: "Journal", settings: "Settings" },
  ownerLater: "Running the network from the app comes in a later version.",
  ownerLaterPanel: "For now it is done in the panel:",

  settings: "Settings",
  device: "Device",
  killSwitch: "No internet without VPN",
  localAround: "Local networks bypass VPN",
  aroundVpn: "Bypass VPN",
  autoTag: "auto",
  localNames: { private: "Private networks", tailscale: "Tailscale", linkLocal: "Link-local", ipv6: "Local IPv6" },
  add: "Add",
  remove: item => `Remove ${item}`,
  account: "Account",
  network: "Network",
  thisDevice: "This device",
  myAddress: "My address in the network",
  leave: "Leave the network",
  leaveQuestion: "Leave the network?",
  leaveYes: "Leave",
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
