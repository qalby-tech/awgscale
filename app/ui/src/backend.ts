// The app's Rust side, one function per command (app/shell/src/commands.rs). Every request to the
// network goes through it: the page itself talks to nobody.
import { invoke } from "@tauri-apps/api/core";

import {
  type Destination,
  type DeviceSettings,
  type FilterWish,
  type Exit,
  type InvitationInfo,
  type Me,
  type ProfileShown,
  type TunnelReport,
  type TunnelState,
} from "./types";

enum CommandName {
  Profile = "profile",
  Look = "look",
  Join = "join",
  Connect = "connect",
  Disconnect = "disconnect",
  Tunnel = "tunnel_state",
  Me = "me",
  Exits = "exits",
  Choose = "choose",
  ChooseFilter = "choose_filter",
  UseLanguage = "use_language",
  Leave = "leave",
  Settings = "settings",
  SetSettings = "set_settings",
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
  connect: () => invoke<TunnelState>(CommandName.Connect),
  disconnect: () => invoke<TunnelState>(CommandName.Disconnect),
  tunnel: () => invoke<TunnelReport>(CommandName.Tunnel),
  me: () => invoke<Me>(CommandName.Me),
  exits: () => invoke<Exit[]>(CommandName.Exits),
  choose: (destination: Destination) => invoke<null>(CommandName.Choose, { destination }),
  chooseFilter: (wish: FilterWish) => invoke<null>(CommandName.ChooseFilter, { wish }),
  useLanguage: (language: string) => invoke<null>(CommandName.UseLanguage, { language }),
  leave: () => invoke<null>(CommandName.Leave),
  settings: () => invoke<DeviceSettings>(CommandName.Settings),
  setSettings: (settings: DeviceSettings) => invoke<DeviceSettings>(CommandName.SetSettings, { settings }),
};
