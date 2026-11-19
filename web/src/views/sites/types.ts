/** «Через заграницу»: what the server keeps and what the page stages on top of it. */
import type { Conflict, RoutingMode } from "@/lib/types";

export interface SiteGroup {
  id: string;
  name: string;
  on: boolean;
  items: string[];
  /** A group that also carries networks the server keeps up to date itself. */
  auto?: string | null;
  /** A list by link: its entries are the file's. */
  url?: string | null;
  /** Seconds. */
  updated?: number | null;
  error?: string;
}

export interface Sites extends Conflict {
  v?: string;
  mode?: RoutingMode;
  /** How many entries all groups may hold together. */
  limit?: number;
  max_lists?: number;
  auto?: Record<string, { nets?: number }>;
  groups: SiteGroup[];
  /** False when the save changed nothing that had to be applied. */
  applied?: boolean;
}

/** A group as it is being edited; `id` is null until the server has seen it. */
export interface DraftGroup {
  key: string;
  id: string | null;
  name: string;
  on: boolean;
  items: string[];
  auto: string | null;
  url: string | null;
  updated: number | null;
  error: string;
}

export enum ProbeVia {
  Direct = "direct",
  Exit = "exit",
}
/** The one state of a way that means «it opens»; anything else the server names is a failure. */
export const PROBE_OK = "ok";

export interface ProbeWay {
  via: ProbeVia;
  name?: string;
  text: string;
  ms?: number;
  state?: string;
}

/** The check «does this address open», for one host. */
export interface Probe {
  host: string;
  pending: boolean;
  ways?: ProbeWay[];
  error?: string | null;
}
