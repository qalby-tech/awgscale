// What the app's own Rust side hands over (app/shell, app/core): the shapes mirror its serde types.

export enum TunnelState {
  Down = "down",
  Connecting = "connecting",
  Up = "up",
}

// How a group's requests are filtered (GroupFilter on the server)
export enum Filter {
  Standard = "std",
  Off = "off",
  Kids = "kids",
  Pro = "pro",
  ProPlus = "proplus",
  Ultimate = "ultimate",
}

export enum DestinationKind {
  Auto = "auto",
  Exit = "exit",
}

export type Destination = { kind: DestinationKind.Auto } | { kind: DestinationKind.Exit; index: number };

// The network this device belongs to, without its secret
export interface ProfileShown {
  host: string;
  name: string;
  address: string;
  owner: boolean;
}

export interface InvitationInfo {
  group: string | null;
  left: number;
  total: number;
  expires_at: number | null;
}

export interface ExitChoice {
  pinned: number | null;
  group: number | null;
  name: string | null;
}

export interface Member {
  name: string;
  address: string;
}

export interface Group {
  name: string | null;
  filter: Filter;
  space: boolean;
  members: Member[];
}

export interface Me {
  name: string;
  address: string;
  owner: boolean;
  expires_at: number | null;
  exit: ExitChoice;
  group: Group | null;
}

export interface Exit {
  index: number;
  name: string;
  up: boolean;
  share: number;
}
