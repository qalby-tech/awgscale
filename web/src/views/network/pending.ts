/**
 * What the admin changed on the canvas and has not applied yet. Nothing reaches the server before «Применить»;
 * then the changes go out one kind after another, through the same actions the other sections use, and the
 * first refusal stops the rest (what was not sent stays staged, and the admin is told).
 */
import { create } from "zustand";

import { post } from "@/lib/api";
import { GroupFilter, type Exit, type RoutingMode, type UserGroup } from "@/lib/types";
import { isLocked, Lock, withLock } from "@/store/core";
import { refreshSoon, saveShares } from "@/store/exits";
import { saveUserGroups } from "@/store/users";
import { fail, toast, toastError } from "@/ui/toast";

import { openSites, setMode, useSites } from "../sites/store";
import { loadRules, useRules } from "./rules";

/** Where a rule leads: a group of sites goes abroad, directly, is blocked, or is off (the server's own words). */
export enum RuleRoute {
  Exit = "exit",
  Direct = "direct",
  Block = "block",
  Off = "off",
}

/** A group's own settings as staged: only what was changed is here. */
export interface GroupEdit {
  filter?: GroupFilter;
  /** The exit the group is kept on; null is «as the server decides». */
  exit?: number | null;
}

interface Pending {
  groups: Record<string, GroupEdit>;
  /** Rules sent elsewhere, by their id. */
  routes: Record<string, RuleRoute>;
  /** The groups of people a rule is staged for, by rule id: empty is everybody. */
  scopes: Record<string, string[]>;
  /** Every rule's id in a new order, or null. */
  order: string[] | null;
  mode: RoutingMode | null;
  /** New shares in percent, in the order of `sharesBase`. */
  shares: number[] | null;
  sharesBase: Exit[] | null;
  applying: boolean;
}

const NOTHING: Omit<Pending, "applying"> = {
  groups: {},
  routes: {},
  scopes: {},
  order: null,
  mode: null,
  shares: null,
  sharesBase: null,
};

export const usePending = create<Pending>(() => ({ ...NOTHING, applying: false }));

const set = usePending.setState;
const get = usePending.getState;

export function stageGroup(group: UserGroup, edit: GroupEdit): void {
  if (group.id == null) return;
  const id = group.id;
  set(state => {
    const next: GroupEdit = { ...state.groups[id], ...edit };
    if (next.filter === (group.filter || GroupFilter.Standard)) delete next.filter;
    if (next.exit !== undefined && next.exit === (group.exit ?? null)) delete next.exit;
    const others = Object.entries(state.groups).filter(([key]) => key !== id);
    return { groups: Object.fromEntries(Object.keys(next).length ? [...others, [id, next]] : others) };
  });
}

/** A rule sent elsewhere; sending it back to where the server has it unstages it. */
export function stageRoute(id: string, route: RuleRoute, now: RuleRoute): void {
  set(state => {
    const others = Object.entries(state.routes).filter(([key]) => key !== id);
    return { routes: Object.fromEntries(route === now ? others : [...others, [id, route]]) };
  });
}

/** The groups of people a rule is for (`now`: as the server keeps them); the same ones unstage it. */
export function stageScope(id: string, groups: string[], now: string[]): void {
  const sorted = [...groups].sort();
  set(state => {
    const others = Object.entries(state.scopes).filter(([key]) => key !== id);
    const same = sorted.join() === [...now].sort().join();
    return { scopes: Object.fromEntries(same ? others : [...others, [id, sorted]]) };
  });
}

/** A new order of the rules (`now`: as the server keeps it); the same order unstages it. */
export function stageOrder(order: string[], now: string[]): void {
  set({ order: order.join() === now.join() ? null : order });
}

export function stageMode(mode: RoutingMode, now: RoutingMode): void {
  set({ mode: mode === now ? null : mode });
}

export function stageShares(base: Exit[], percents: number[], now: number[]): void {
  const same = percents.every((value, index) => value === now[index]);
  set({ shares: same ? null : percents, sharesBase: same ? null : base });
}

/** How many things are staged. */
export function pendingCount(state: Pending): number {
  return (
    Object.keys(state.groups).length +
    new Set([...Object.keys(state.routes), ...Object.keys(state.scopes)]).size +
    (state.order ? 1 : 0) +
    (state.mode ? 1 : 0) +
    (state.shares ? 1 : 0)
  );
}

/** Is anything staged now (for the guard on the way out). */
export const anythingStaged = (): boolean => pendingCount(get()) > 0;

export function discard(): void {
  set({ ...NOTHING });
}

/** Forgets what was staged, for a fresh visit of the section. */
export const forget = discard;

/**
 * Every staged rule, in one request: the server saves the sites once however many changed (a save that changes
 * what goes abroad restarts DNS for about a second). → whether it took them (a refusal is shown).
 */
interface RuleChange {
  rule: string;
  route?: RuleRoute;
  groups?: string[];
}

/** One change per rule: where it leads, for whom, or both. */
function changesOf(routes: Record<string, RuleRoute>, scopes: Record<string, string[]>): RuleChange[] {
  return [...new Set([...Object.keys(routes), ...Object.keys(scopes)])].map(rule => ({
    rule,
    ...(rule in routes ? { route: routes[rule] } : {}),
    ...(rule in scopes ? { groups: scopes[rule] } : {}),
  }));
}

async function sendRoutes(changes: RuleChange[], order: string[] | null): Promise<boolean> {
  if (isLocked(Lock.Sites)) return false;
  return withLock(Lock.Sites, async () => {
    try {
      await post("/api/rules/route", {
        changes,
        ...(order ? { order } : {}),
        base: useRules.getState().version, // the list as the canvas showed it: a change made elsewhere since is not undone
      });
      return true;
    } catch (error) {
      fail(error);
      return false;
    }
  });
}

/** One kind of change after another; false at the first that was refused. */
async function applySteps(): Promise<boolean> {
  const groups = get().groups;
  if (Object.keys(groups).length) {
    const saved = await saveUserGroups(draft => {
      for (const group of draft) {
        const edit = group.id != null ? groups[group.id] : undefined;
        if (!edit) continue;
        if (edit.filter !== undefined) group.filter = edit.filter;
        if (edit.exit !== undefined) group.exit = edit.exit;
      }
    });
    if (!saved) return false;
    set({ groups: {} });
  }
  const changes = changesOf(get().routes, get().scopes);
  const order = get().order;
  if (changes.length || order) {
    try {
      if (!(await sendRoutes(changes, order))) return false;
      set({ routes: {}, scopes: {}, order: null });
    } finally {
      // the list as it is now (another window may have changed it: then nothing was sent, and it is shown fresh)
      await Promise.all([loadRules(), openSites()]);
    }
  }
  const mode = get().mode;
  if (mode) {
    await setMode(mode); // asks the admin first, as in «Сайты»
    if (useSites.getState().sites?.mode !== mode) return false;
    set({ mode: null });
  }
  const { shares, sharesBase } = get();
  if (shares && sharesBase) {
    if (!(await saveShares(sharesBase, shares))) return false;
    set({ shares: null, sharesBase: null });
  }
  return true;
}

export async function applyPending(): Promise<void> {
  if (get().applying) return;
  set({ applying: true });
  try {
    if (await applySteps()) toast("Изменения применены");
    else toastError("Применено не всё: оставшиеся изменения ждут на холсте");
  } finally {
    set({ applying: false });
    refreshSoon();
  }
}
