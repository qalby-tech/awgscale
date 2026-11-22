/** «Блокируется»: ready-made lists, the «dangerous sites» check and the admin's own groups. */
import type { Conflict } from "@/lib/types";

export interface BlockList {
  id: number;
  title?: string;
  name: string;
  url?: string;
  /** Connected by the admin by a link (the others come with the blocker). */
  custom?: boolean;
  on: boolean;
  rules?: number;
  /** An ISO date. */
  updated?: string;
  /** How many requests it blocked in a day. */
  day?: number;
}

export interface BlockGroup {
  id: string | null;
  name: string;
  on: boolean;
  items: string[];
}

export interface TopBlocked {
  domain: string;
  count: number;
}

export interface Block extends Conflict {
  /** The fingerprint of the groups: a save based on another one is refused. */
  v?: string;
  lists?: BlockList[];
  safebrowsing?: { on?: boolean; day?: number };
  groups: BlockGroup[];
  top?: TopBlocked[];
}

/** What blocks an address, as the blocker itself says. */
export enum BlockedBy {
  List = "list",
  Safe = "safe",
  Own = "own",
}

export interface Blocked {
  kind: BlockedBy;
  id?: number | null;
  gid?: string | null;
  rule?: string;
}

/** The answer of the blocker about one whole address typed in the search. */
export interface BlockHit {
  host: string;
  pending: boolean;
  failed?: boolean;
  blocked?: Blocked | null;
}

export enum ListOp {
  Add = "add",
  Remove = "remove",
  Rename = "rename",
  Refresh = "refresh",
}

export type ListsRequest =
  | { op: ListOp.Add; name: string; url: string }
  | { op: ListOp.Remove; id: number }
  | { op: ListOp.Rename; id: number; name: string }
  | { op: ListOp.Refresh };
