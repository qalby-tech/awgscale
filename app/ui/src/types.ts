// What the app's own Rust side hands over (app/shell, app/core): the shapes mirror its serde types.

export enum TunnelState {
  Down = "down",
  Connecting = "connecting",
  Up = "up",
}

export interface TunnelReport {
  state: TunnelState;
  warning: string | null;
}

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

// The person's choices on this device
export interface DeviceSettings {
  kill_switch: boolean;
  local_around: boolean;
  around: string[];
}

// What «Локальные сети мимо VPN» sends around the tunnel (LocalNetworks in app/qalby/conf), as shown
export const LOCAL_NETWORKS: { name: string; networks: string }[] = [
  { name: "Частные сети", networks: "10/8 · 172.16/12 · 192.168/16" },
  { name: "Tailscale", networks: "100.64/10" },
  { name: "Link-local", networks: "169.254/16" },
  { name: "Локальные IPv6", networks: "fc00::/7 · fe80::/10" },
];
