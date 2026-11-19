/** What the panel server sends and takes, as the page sees it. */

// ── sections of the page ─────────────────────────────────────────────────────

export enum Route {
  Overview = "overview",
  Users = "users",
  Spaces = "users/spaces",
  Sites = "sites",
  Blocked = "sites/blocked",
  Dns = "dns",
  Servers = "servers",
  Journal = "journal",
}

/** The first part of a route: what the side menu shows as current. */
export enum Section {
  Overview = "overview",
  Users = "users",
  Sites = "sites",
  Dns = "dns",
  Servers = "servers",
  Journal = "journal",
}

// ── the state of the cascade (GET /api/state, every few seconds) ─────────────

export enum ExitState {
  Up = "up",
  Down = "down",
  Unknown = "unknown",
}

export interface Speed {
  down_bps?: number;
  up_bps?: number;
}

/** Load, memory and disk of a server, when it reports them. */
export interface ServerFacts {
  load1?: number;
  mem_total?: number;
  mem_used?: number;
  disk_pct?: number;
}

export interface Client extends Speed {
  name: string;
  ip: string;
  online: boolean;
  suspended?: boolean;
  /** Seconds; 0 when the user has never connected. */
  handshake?: number;
  rx?: number;
  tx?: number;
  /** The exit the user goes through now. */
  exit: number | null;
  /** The exit the admin keeps the user on by hand. */
  pin?: number | null;
  expires_at?: number | null;
  /** The id of the user's group. */
  group?: string | null;
}

export interface Exit extends Speed {
  index: number;
  name: string;
  state: ExitState;
  endpoint?: string;
  /** Relative share of new users; the page shows it in percent. */
  weight?: number;
  handshake?: number;
  /** How many users are on this exit now, and how many of them online. */
  users?: number;
  online?: number;
  sys?: ServerFacts;
}

export interface Entry extends ServerFacts {
  listen_port?: number;
  client_if_up?: boolean;
}

export interface Totals extends Speed {
  online?: number;
  clients?: number;
}

/** Users counted by the server: how many, how many online, how fast and how much since the tunnel started. */
export interface Count extends Speed {
  users: number;
  online: number;
  paused?: number;
  rx?: number;
  tx?: number;
}

export interface GroupCount extends Count {
  id: string;
}

export interface ExitCount extends Count {
  index: number;
}

/** The users as the state has them: counted, never listed (a page of them is asked of /api/clients). */
export interface UserCounts extends Count {
  paused?: number;
  never?: number;
  expiring?: number;
  groups?: GroupCount[];
  ungrouped?: Count;
  exits?: ExitCount[];
}

export interface HistoryPoint {
  t: number;
  down: number;
  up: number;
}

export interface TopSite {
  name: string;
  count: number;
}

export interface DnsSummary {
  ok?: boolean;
  queries?: number;
  blocked?: number;
  top_exit?: TopSite[];
  top_exit_total?: number;
  top_direct?: TopSite[];
  top_direct_total?: number;
}

/** What goes abroad: only the sites of the list, or everything foreign. */
export enum RoutingMode {
  List = "list",
  Foreign = "geo",
}

export interface PanelState {
  time?: number;
  stale?: boolean;
  /** The id of the page build the server hands out now. */
  ui?: string;
  entry?: Entry;
  exits?: Exit[];
  users?: UserCounts;
  totals?: Totals;
  history?: HistoryPoint[];
  dns?: DnsSummary;
  /** `single_server`: a server without exits; the pages about them are not shown.
   *  `home_country`: whose networks go direct; absent means Russia. */
  routing?: { mode?: RoutingMode; single_server?: boolean; home_country?: string };
  /** Versions of the lists: another window may have changed them. */
  ugroups_v?: string;
  sites_v?: string;
  spaces_v?: string;
  poll_error?: string;
  error?: string;
}

// ── a page of users (GET /api/clients) ───────────────────────────────────────

/** Which users a page is of. */
export enum ClientStatus {
  All = "all",
  Online = "online",
  Offline = "offline",
  Paused = "paused",
  Expiring = "expiring",
  Stale = "stale",
  Never = "never",
}

export enum ClientSort {
  Address = "address",
  Name = "name",
  Seen = "seen",
  Traffic = "traffic",
  Speed = "speed",
  Expires = "expires",
}

/** The group of those who are in none, as a query names it. */
export const NO_GROUP_QUERY = "none";

export interface ClientsQuery {
  /** Part of a name or of an address (the server's `q`). */
  search?: string;
  /** A group's id, or NO_GROUP_QUERY. */
  group?: string;
  exit?: number;
  status?: ClientStatus;
  sort?: ClientSort;
  desc?: boolean;
  names?: string[];
}

export interface ClientsPage {
  total: number;
  offset: number;
  clients: Client[];
}

// ── groups of users ──────────────────────────────────────────────────────────

/** How a group is filtered. `Off` is kept by the server but no longer offered. */
export enum GroupFilter {
  Standard = "std",
  Off = "off",
  Kids = "kids",
  /** The levels: as everybody, plus one of HaGeZi's lists for the members only. */
  Pro = "pro",
  ProPlus = "proplus",
  Ultimate = "ultimate",
}

/** HaGeZi's add-on lists of a group in the children's mode. */
export enum KidsList {
  Nsfw = "nsfw",
  Gambling = "gambling",
  Bypass = "bypass",
  Social = "social",
}

export interface UserGroup {
  id: string | null;
  name: string;
  /** A device space of exactly its members. */
  space: boolean;
  exit?: number | null;
  filter?: GroupFilter;
  /** In the children's mode only: the add-on lists switched on. */
  kids_lists?: KidsList[];
  members: string[];
}

export interface UserGroups {
  v?: string;
  groups: UserGroup[];
}

/** An answer that refuses a change made on top of an older version, and carries the list as it is now. */
export interface Conflict {
  conflict?: boolean;
}

// ── a user's connection code and check ───────────────────────────────────────

export enum CodeTab {
  Vpn = "vpn",
  Wg = "wg",
  Diag = "diag",
}

export interface CodeData {
  uri?: string;
  uri_png?: string;
  conf?: string;
  conf_png?: string;
}

export enum DiagState {
  Ok = "ok",
  Warn = "warn",
  Bad = "bad",
}

export interface DiagItem {
  state: DiagState;
  text: string;
}

// ── traffic history ──────────────────────────────────────────────────────────

/** [download, upload] in bytes. */
export type TrafficPair = [number, number];

export enum TrafficSpan {
  Day = "day",
  Month = "month",
}

export interface UserTraffic {
  totals?: Partial<Record<TrafficSpan, TrafficPair>>;
  days?: TrafficPair[];
}

export interface Traffic {
  days: string[];
  users: Record<string, UserTraffic>;
}

// ── exits as servers ─────────────────────────────────────────────────────────

export enum JobKind {
  Add = "add",
  Update = "update",
  Clean = "clean",
}
export enum JobState {
  Running = "running",
  Done = "done",
  Failed = "failed",
}
export enum StepState {
  Wait = "wait",
  Run = "run",
  Done = "done",
  Fail = "fail",
  Skip = "skip",
}

export interface JobStep {
  title: string;
  state: StepState;
}

export interface ExitJob {
  kind: JobKind;
  state: JobState;
  name?: string;
  address?: string;
  steps?: JobStep[];
  error?: string;
  finished?: number;
  reboot_needed?: boolean;
}

export interface ExitNode {
  managed?: boolean;
}

export interface ExitsRegistry {
  nodes?: Record<string, ExitNode>;
  job?: ExitJob | null;
}
