// The app's words in each of its languages. One object per language with the same keys: the types make
// sure no text is missing in either. The language is the person's choice (Настройки → Язык), or the
// system's when they made none.
import { createContext, useContext } from "react";

import { Branch } from "./network";
import { Author, ExitState, Filter, Mode, Route, TunnelState, Way } from "./types";

export enum Language {
  Russian = "ru",
  English = "en",
}

const RUSSIAN_PREFIX = "ru";

// "1 человек", "2 человека", "5 человек": the form a Russian number wants
function russianCount(count: number, one: string, few: string, many: string): string {
  const lastTwo = count % 100;
  const last = count % 10;
  if (lastTwo >= 11 && lastTwo <= 14) {
    return `${count} ${many}`;
  }
  if (last === 1) {
    return `${count} ${one}`;
  }
  return `${count} ${last >= 2 && last <= 4 ? few : many}`;
}

function englishCount(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

const russian = {
  locale: Language.Russian as Language,
  connectTitle: "Подключитесь к своей сети",
  inviteLinkOrKey: "Ссылка или ключ",
  checking: "Проверяем…",
  continueButton: "Продолжить",
  invitationLeft: (left: number, total: number, group: string | null) =>
    `Ссылка работает ещё для ${left} из ${total} устройств` + (group == null ? "" : ` · группа «${group}»`),
  deviceName: "Как назвать это устройство",
  deviceNameRule: "Латинские буквы, цифры, _ и -, до 32 символов",
  joining: "Создаём подключение…",
  join: "Подключить устройство",
  anotherLink: "Другая ссылка",
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
  ownerLater: "Сервер сети ещё не умеет управлять ею из приложения — его нужно обновить.",
  ownerLaterPanel: "Пока это делается в панели:",

  settings: "Настройки",
  device: "Устройство",
  killSwitch: "Kill switch",
  killSwitchNote: "Блокировать интернет, если VPN отключился",
  splitTunneling: "Раздельное туннелирование",
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

  net: {
    summary: (people: number, exits: number, down: number) =>
      [russianCount(people, "человек", "человека", "человек"), russianCount(exits, "выход", "выхода", "выходов")]
        .concat(down > 0 ? [`${down} недоступен`] : [])
        .join(" · "),
    apply: (count: number) => `Применить · ${count}`,
    applying: "Применяем…",
    applied: "Готово: сеть применила изменения",
    whereTraffic: "Куда идут запросы сегодня",
    pickBranch: "нажмите на ветку",
    onlyBranch: (branch: string) => `только «${branch}» · снять`,
    people: (count: number) => `Люди ${count}`,
    branches: {
      [Branch.Exit]: "Выход",
      [Branch.Direct]: "Напрямую",
      [Branch.Block]: "Блок",
    } as Record<Branch, string>,
    ways: {
      [Way.Exit]: "Выход",
      [Way.Direct]: "Напрямую",
      [Way.Block]: "Блок",
      [Way.Off]: "Выкл",
    } as Record<Way, string>,
    parts: { rules: "Правила", exits: "Выходы", people: "Люди" },
    rulesHint: "сверху вниз, первое совпадение",
    rulesTap: "метка → сменить",
    ruleSites: (count: number) => russianCount(count, "сайт", "сайта", "сайтов"),
    byLink: "список по ссылке",
    onlyFor: (groups: string) => `только: ${groups}`,
    noRules: "Правил пока нет",
    exitsHint: "нагрузка · доля (нажмите — выключить)",
    sharesSum: (sum: number) => `сумма ${sum}% — должно быть 100%`,
    exitStates: {
      [ExitState.Up]: "",
      [ExitState.Down]: "недоступен · люди перенесены",
      [ExitState.Unknown]: "нет данных",
    } as Record<ExitState, string>,
    peopleOn: (users: number, online: number) =>
      `${russianCount(users, "человек", "человека", "человек")} · в сети ${online}`,
    noExits: "У сети один сервер: выходов нет",
    lessShare: "Меньше",
    moreShare: "Больше",
    noGroup: "Без группы",
    space: "общая сеть",
    invite: "Пригласить по QR",
    invitation: "Приглашение",
    devices: "Устройств",
    days: "Дней",
    group: "Группа",
    make: "Создать",
    inviteHint: "Наведите камеру другого телефона на код или отправьте ссылку",
    copy: "Скопировать ссылку",
    copied: "Скопировано",
    stale: "Данные устарели: сервер давно не отвечал",
    modes: { [Mode.List]: "за границу по списку", [Mode.Foreign]: "за границу всё зарубежное" } as Record<Mode, string>,
    abroad: "За границу",
    modeChoices: { [Mode.List]: "По списку", [Mode.Foreign]: "Всё зарубежное" } as Record<Mode, string>,
    moveUp: "Выше",
    moveDown: "Ниже",
    forWhom: "Для кого",
    forWhomHint: "Правило действует для всех или только для выбранных групп",
    everybody: "Для всех",
    done: "Готово",
    allOff: "все выходы выключены — так нельзя",
    exitOff: "выключен",
    off: "выкл",
    switchOff: "Выключить",
    switchOn: "Включить",
    peopleOf: "Люди группы",
    groups: "Группы",
    nobody: "Здесь пока никого",
    thisDevice: "это устройство",
    owner: "владелец",
    paused: "на паузе",
    online: "в сети",
    offline: "не в сети",
    pause: "Пауза",
    resume: "Вернуть",
    remove: "Удалить",
    removeYes: "Удалить навсегда",
    removeNote: (name: string) => `${name} потеряет доступ к сети навсегда: вернуть можно только новым приглашением.`,
  },
  journal: {
    empty: "Изменений пока не было",
    authors: { [Author.Panel]: "панель", [Author.Bot]: "бот", [Author.App]: "приложение" } as Record<Author, string>,
    system: "система",
  },

  somethingWrong: "Что-то пошло не так — попробуйте ещё раз",
};

type Texts = typeof russian;

const english: Texts = {
  locale: Language.English,
  connectTitle: "Connect to your network",
  inviteLinkOrKey: "Invitation link or key",
  checking: "Checking…",
  continueButton: "Continue",
  invitationLeft: (left, total, group) =>
    `The link works for ${left} more of ${total} devices` + (group == null ? "" : ` · group “${group}”`),
  deviceName: "Name this device",
  deviceNameRule: "Latin letters, digits, _ and -, up to 32 characters",
  joining: "Setting up the connection…",
  join: "Connect this device",
  anotherLink: "Another link",
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
  ownerLater: "The network's server cannot be run from the app yet — it needs an update.",
  ownerLaterPanel: "For now it is done in the panel:",

  settings: "Settings",
  device: "Device",
  killSwitch: "Kill switch",
  killSwitchNote: "Block the internet if the VPN drops",
  splitTunneling: "Split tunneling",
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

  net: {
    summary: (people: number, exits: number, down: number) =>
      [englishCount(people, "person", "people"), englishCount(exits, "exit", "exits")]
        .concat(down > 0 ? [`${down} down`] : [])
        .join(" · "),
    apply: (count: number) => `Apply · ${count}`,
    applying: "Applying…",
    applied: "Done: the network applied the changes",
    whereTraffic: "Where today's lookups go",
    pickBranch: "tap a branch",
    onlyBranch: (branch: string) => `only “${branch}” · clear`,
    people: (count: number) => `People ${count}`,
    branches: {
      [Branch.Exit]: "Exit",
      [Branch.Direct]: "Direct",
      [Branch.Block]: "Block",
    },
    ways: {
      [Way.Exit]: "Exit",
      [Way.Direct]: "Direct",
      [Way.Block]: "Block",
      [Way.Off]: "Off",
    },
    parts: { rules: "Rules", exits: "Exits", people: "People" },
    rulesHint: "top to bottom, first match",
    rulesTap: "tap a label to change",
    ruleSites: (count: number) => englishCount(count, "site", "sites"),
    byLink: "list by link",
    onlyFor: (groups: string) => `only: ${groups}`,
    noRules: "No rules yet",
    exitsHint: "load · share (tap to switch off)",
    sharesSum: (sum: number) => `the sum is ${sum}% — it must be 100%`,
    exitStates: {
      [ExitState.Up]: "",
      [ExitState.Down]: "down · people moved",
      [ExitState.Unknown]: "no data",
    },
    peopleOn: (users: number, online: number) => `${englishCount(users, "person", "people")} · ${online} online`,
    noExits: "The network is one server: no exits",
    lessShare: "Less",
    moreShare: "More",
    noGroup: "No group",
    space: "shared network",
    invite: "Invite by QR",
    invitation: "Invitation",
    devices: "Devices",
    days: "Days",
    group: "Group",
    make: "Create",
    inviteHint: "Point another phone's camera at the code or send the link",
    copy: "Copy the link",
    copied: "Copied",
    stale: "The data is old: the server has not answered for a while",
    modes: { [Mode.List]: "abroad by the list", [Mode.Foreign]: "everything foreign abroad" },
    abroad: "Abroad",
    modeChoices: { [Mode.List]: "By the list", [Mode.Foreign]: "All foreign" },
    moveUp: "Up",
    moveDown: "Down",
    forWhom: "For whom",
    forWhomHint: "The rule is for everybody or only for the groups chosen",
    everybody: "Everybody",
    done: "Done",
    allOff: "every exit is off — that is not allowed",
    exitOff: "off",
    off: "off",
    switchOff: "Switch off",
    switchOn: "Switch on",
    peopleOf: "People of",
    groups: "Groups",
    nobody: "Nobody here yet",
    thisDevice: "this device",
    owner: "owner",
    paused: "paused",
    online: "online",
    offline: "offline",
    pause: "Pause",
    resume: "Resume",
    remove: "Remove",
    removeYes: "Remove for good",
    removeNote: (name: string) =>
      `${name} loses access to the network for good: only a new invitation brings them back.`,
  },
  journal: {
    empty: "No changes yet",
    authors: { [Author.Panel]: "panel", [Author.Bot]: "bot", [Author.App]: "app" },
    system: "system",
  },

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
