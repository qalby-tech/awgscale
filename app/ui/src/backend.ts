// The app's Rust side, one function per command (app/shell/src/commands.rs). Every request to the
// network goes through it: the page itself talks to nobody.
import { invoke } from "@tauri-apps/api/core";

import {
  type Destination,
  type DeviceSettings,
  type FilterWish,
  type Exit,
  type Invitation,
  type InvitationInfo,
  type InvitationWish,
  type Me,
  type Mode,
  type Network,
  type Note,
  type People,
  type ProfileShown,
  type Route,
  type RuleChange,
  type TunnelReport,
  type TunnelState,
} from "./types";

enum CommandName {
  Profile = "profile",
  Look = "look",
  Join = "join",
  ImportConnection = "import_connection",
  CanScan = "can_scan",
  Connect = "connect",
  Disconnect = "disconnect",
  Tunnel = "tunnel_state",
  Discover = "discover",
  Me = "me",
  Exits = "exits",
  Choose = "choose",
  ChooseFilter = "choose_filter",
  ChooseRoute = "choose_route",
  Network = "network",
  Journal = "journal",
  RouteRules = "route_rules",
  SetShares = "set_shares",
  SetMode = "set_mode",
  People = "people",
  PausePeople = "pause_people",
  RemovePeople = "remove_people",
  Invite = "invite",
  UseLanguage = "use_language",
  Leave = "leave",
  Settings = "settings",
  SetSettings = "set_settings",
  OpenedLink = "opened_link",
}

// A refusal of the Rust side is the text to show (already in the app's language); anything else becomes
// one fixed sentence, given by the caller in the app's language.
export function shownError(error: unknown, otherwise: string): string {
  return typeof error === "string" && error.length > 0 ? error : otherwise;
}

export const backend = {
  profile: () => invoke<ProfileShown | null>(CommandName.Profile),
  look: (link: string) => invoke<InvitationInfo>(CommandName.Look, { link }),
  join: (link: string, device: string) => invoke<ProfileShown>(CommandName.Join, { link, device }),
  importConnection: (text: string) => invoke<ProfileShown>(CommandName.ImportConnection, { text }),
  canScan: () => invoke<boolean>(CommandName.CanScan),
  connect: () => invoke<TunnelState>(CommandName.Connect),
  disconnect: () => invoke<TunnelState>(CommandName.Disconnect),
  tunnel: () => invoke<TunnelReport>(CommandName.Tunnel),
  discover: () => invoke<ProfileShown>(CommandName.Discover),
  me: () => invoke<Me>(CommandName.Me),
  exits: () => invoke<Exit[]>(CommandName.Exits),
  choose: (destination: Destination) => invoke<null>(CommandName.Choose, { destination }),
  chooseFilter: (wish: FilterWish) => invoke<null>(CommandName.ChooseFilter, { wish }),
  chooseRoute: (route: Route) => invoke<null>(CommandName.ChooseRoute, { route }),
  // the owner's part: null when the network's server has none yet
  network: () => invoke<Network | null>(CommandName.Network),
  journal: () => invoke<Note[] | null>(CommandName.Journal),
  routeRules: (changes: RuleChange[], order: string[] | null, base: string) =>
    invoke<null>(CommandName.RouteRules, { changes, order, base }),
  setMode: (mode: Mode) => invoke<null>(CommandName.SetMode, { mode }),
  people: (group: string | null) => invoke<People | null>(CommandName.People, { group }),
  pausePeople: (names: string[], on: boolean) => invoke<null>(CommandName.PausePeople, { names, on }),
  removePeople: (names: string[]) => invoke<null>(CommandName.RemovePeople, { names }),
  setShares: (shares: Record<number, number>, base: Record<number, number>) =>
    invoke<null>(CommandName.SetShares, { shares, base }),
  invite: (wish: InvitationWish) => invoke<Invitation>(CommandName.Invite, { wish }),
  useLanguage: (language: string) => invoke<null>(CommandName.UseLanguage, { language }),
  leave: () => invoke<null>(CommandName.Leave),
  settings: () => invoke<DeviceSettings>(CommandName.Settings),
  setSettings: (settings: DeviceSettings) => invoke<DeviceSettings>(CommandName.SetSettings, { settings }),
  openedLink: () => invoke<string | null>(CommandName.OpenedLink),
};
