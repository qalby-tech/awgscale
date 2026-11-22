/**
 * What the admin changed on the canvas and has not applied yet. Nothing reaches the server before «Применить»;
 * then the changes go out one kind after another, through the same actions the other sections use, and the
 * first refusal stops the rest (what was not sent stays staged, and the admin is told).
 *
 * The site groups are staged in the draft of «Сайты» itself (its store), the rest here.
 */
import { create } from "zustand";

import { GroupFilter, type RoutingMode, type Exit, type UserGroup } from "@/lib/types";
import { refreshSoon, saveShares } from "@/store/exits";
import { saveUserGroups } from "@/store/users";
import { toast, toastError } from "@/ui/toast";

import { openBlocked, saveBlock } from "../blocked/store";
import { applySites, cancelChanges, dirtyCount, setMode, useSites } from "../sites/store";

/** A group's own settings as staged: only what was changed is here. */
export interface GroupEdit {
  filter?: GroupFilter;
  /** The exit the group is kept on; null is «as the server decides». */
  exit?: number | null;
}

interface Pending {
  groups: Record<string, GroupEdit>;
  /** Block groups switched on or off, by their id. */
  blocks: Record<string, boolean>;
  mode: RoutingMode | null;
  /** New shares in percent, in the order of `sharesBase`. */
  shares: number[] | null;
  sharesBase: Exit[] | null;
  applying: boolean;
}

const NOTHING: Omit<Pending, "applying"> = { groups: {}, blocks: {}, mode: null, shares: null, sharesBase: null };

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

/** A block group switched; switching it back unstages it. `on` is what the server has now. */
export function stageBlock(id: string, on: boolean): void {
  set(state => {
    const others = Object.entries(state.blocks).filter(([key]) => key !== id);
    return { blocks: Object.fromEntries(id in state.blocks ? others : [...others, [id, !on]]) };
  });
}

export function stageMode(mode: RoutingMode, now: RoutingMode): void {
  set({ mode: mode === now ? null : mode });
}

export function stageShares(base: Exit[], percents: number[], now: number[]): void {
  const same = percents.every((value, index) => value === now[index]);
  set({ shares: same ? null : percents, sharesBase: same ? null : base });
}

/** How many things are staged, the site groups of «Сайты» included. */
export function pendingCount(state: Pending, sitesDirty: number): number {
  return (
    Object.keys(state.groups).length +
    Object.keys(state.blocks).length +
    (state.mode ? 1 : 0) +
    (state.shares ? 1 : 0) +
    sitesDirty
  );
}

export const useSitesDirty = (): number => useSites(state => dirtyCount(state.sites, state.draft));

/** Is anything staged now (for the guard on the way out). */
export const anythingStaged = (): boolean => {
  const sites = useSites.getState();
  return pendingCount(get(), dirtyCount(sites.sites, sites.draft)) > 0;
};

export function discard(): void {
  set({ ...NOTHING });
  cancelChanges();
}

/** Forgets what was staged, for a fresh visit of the section. */
export function forget(): void {
  set({ ...NOTHING });
}

const sitesStaged = (): boolean => {
  const sites = useSites.getState();
  return dirtyCount(sites.sites, sites.draft) > 0;
};

/** One kind of change after another; false at the first that was refused. */
async function applySteps(): Promise<boolean> {
  const state = get();
  const groups = state.groups;
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
  if (sitesStaged() && !(await applySites())) return false;
  const blocks = get().blocks;
  if (Object.keys(blocks).length) {
    await openBlocked(); // the whole list is sent back: it is read again, so a change made elsewhere is not overwritten
    const saved = await saveBlock(draft => {
      for (const group of draft.groups) if (group.id && group.id in blocks) group.on = blocks[group.id];
    });
    if (!saved) return false;
    set({ blocks: {} });
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
