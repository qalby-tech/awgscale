// The app's Rust side, one function per command (app/shell/src/commands.rs). Every request to the
// network goes through it: the page itself talks to nobody.
import { invoke } from "@tauri-apps/api/core";

import {
  type Destination,
  type Exit,
  type InvitationInfo,
  type Me,
  type ProfileShown,
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
  Leave = "leave",
}

// A refusal of the Rust side is the text to show; anything else becomes one fixed sentence.
export function shownError(error: unknown): string {
  return typeof error === "string" && error.length > 0 ? error : "Что-то пошло не так — попробуйте ещё раз";
}

export const backend = {
  profile: () => invoke<ProfileShown | null>(CommandName.Profile),
  look: (link: string) => invoke<InvitationInfo>(CommandName.Look, { link }),
  join: (link: string, device: string) => invoke<ProfileShown>(CommandName.Join, { link, device }),
  connect: () => invoke<TunnelState>(CommandName.Connect),
  disconnect: () => invoke<TunnelState>(CommandName.Disconnect),
  tunnel: () => invoke<TunnelState>(CommandName.Tunnel),
  me: () => invoke<Me>(CommandName.Me),
  exits: () => invoke<Exit[]>(CommandName.Exits),
  choose: (destination: Destination) => invoke<null>(CommandName.Choose, { destination }),
  leave: () => invoke<null>(CommandName.Leave),
};
