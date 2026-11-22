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

import { openBlocked } from "../blocked/store";
import { openSites, setMode, useSites } from "../sites/store";

/** Where a rule leads: a group of sites goes abroad, is blocked, or is off (the server's own words). */
export enum RuleRoute {
  Exit = "exit",
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
  mode: RoutingMode | null;
  /** New shares in percent, in the order of `sharesBase`. */
  shares: number[] | null;
  sharesBase: Exit[] | null;
  applying: boolean;
}

const NOTHING: Omit<Pending, "applying"> = { groups: {}, routes: {}, mode: null, shares: null, sharesBase: null };

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
    Object.keys(state.groups).length + Object.keys(state.routes).length + (state.mode ? 1 : 0) + (state.shares ? 1 : 0)
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
async function sendRoutes(routes: Array<[string, RuleRoute]>): Promise<boolean> {
  if (isLocked(Lock.Sites)) return false;
  return withLock(Lock.Sites, async () => {
    try {
      await post("/api/rules/route", { changes: routes.map(([rule, route]) => ({ rule, route })) });
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
  const routes = Object.entries(get().routes);
  if (routes.length) {
    try {
      if (!(await sendRoutes(routes))) return false;
      set({ routes: {} });
    } finally {
      // a rule that moved between the sites and the blocking has a new id: both lists are read again
      await Promise.all([openSites(), openBlocked()]);
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
