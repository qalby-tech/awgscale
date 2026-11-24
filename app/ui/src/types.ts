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

// How the device came into its network (app/core profile.rs NetworkKind)
export enum NetworkKind {
  // by an awgscale invitation: the network's panel and app API are there
  Invited = "invited",
  // by an AmneziaWG key or config given by hand: a tunnel, and a network only when its server turns out to
  // be an awgscale one (ProfileShown.panel)
  Manual = "manual",
}

// The network this device belongs to, without its secret
export interface ProfileShown {
  kind: NetworkKind;
  // the network's domain; for a connection given by hand, its server
  host: string;
  name: string;
  address: string;
  owner: boolean;
  // the network's app API is there to ask: always for an invited device, for a connection given by hand once
  // its server answered as an awgscale network
  panel: boolean;
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

export interface FilterChoice {
  applies: Filter;
  own: Filter | null;
  locked: boolean;
}

export enum FilterWishKind {
  Auto = "auto",
  Level = "level",
}

export type FilterWish = { kind: FilterWishKind.Auto } | { kind: FilterWishKind.Level; filter: Filter };

export enum Route {
  Smart = "smart",
  All = "all",
}

export interface RouteChoice {
  all: boolean;
  available: boolean;
}

export interface Me {
  name: string;
  address: string;
  owner: boolean;
  expires_at: number | null;
  exit: ExitChoice;
  group: Group | null;
  // absent from a server older than the choice of one's own filtering
  filter?: FilterChoice | null;
  // absent from a server older than the choice of one's own route
  route?: RouteChoice | null;
}

// An exit as the app's Rust side hands it over: from an older server address is "", country null,
// users and online 0.
export interface Exit {
  index: number;
  name: string;
  up: boolean;
  share: number;
  // its public IPv4: what a person abroad is seen as
  address: string;
  // ISO 3166-1 alpha-2, lower case; null when unknown
  country: string | null;
  // the people kept on it now, and of them the ones online
  users: number;
  online: number;
}

// The person's choices on this device
export interface DeviceSettings {
  kill_switch: boolean;
  local_around: boolean;
  around: string[];
  language: string | null;
}

// What «Локальные сети» (Раздельное туннелирование) sends around the tunnel (LocalNetworks in app/qalby/conf), as shown
export enum LocalNetwork {
  Private = "private",
  Tailscale = "tailscale",
  LinkLocal = "linkLocal",
  Ipv6 = "ipv6",
}

export const LOCAL_NETWORKS: { kind: LocalNetwork; networks: string }[] = [
  { kind: LocalNetwork.Private, networks: "10/8 · 172.16/12 · 192.168/16" },
  { kind: LocalNetwork.Tailscale, networks: "100.64/10" },
  { kind: LocalNetwork.LinkLocal, networks: "169.254/16" },
  { kind: LocalNetwork.Ipv6, networks: "fc00::/7 · fe80::/10" },
];
