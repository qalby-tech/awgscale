/** «Блокируется»: every change is sent at once; the answer is the list as it is now. */
import { create } from "zustand";

import { api, errorText, isAuthError, post } from "@/lib/api";
import { EntryKind, isHost, isListLink, parseEntry, queryOf } from "@/lib/entries";
import { clone } from "@/lib/format";
import { isLocked, Lock, withLock } from "@/store/core";
import { fail, toast } from "@/ui/toast";

import { ListOp, type Block, type Blocked, type BlockGroup, type BlockHit, type ListsRequest } from "./types";

/** A whole address is asked of the blocker once the typing pauses. */
const CHECK_DELAY_MS = 350;
/** The blocker rebuilds its rules a moment after it answers a change. */
const REBUILD_MS = 1500;

export interface Flash {
  text: string;
  seq: number;
}

interface BlockedState {
  active: boolean;
  block: Block | null;
  query: string;
  naming: boolean;
  /** The key of the own group whose field is open. */
  addKey: string | null;
  addError: string;
  linkOpen: boolean;
  linkError: string;
  linkBusy: boolean;
  hit: BlockHit | null;
  flash: Flash | null;
}

const CLOSED = {
  query: "",
  naming: false,
  addKey: null,
  addError: "",
  linkOpen: false,
  linkError: "",
  linkBusy: false,
  hit: null,
  flash: null,
};

export const useBlocked = create<BlockedState>(() => ({ active: false, block: null, ...CLOSED }));

const set = useBlocked.setState;
const get = useBlocked.getState;
const busy = (): boolean => isLocked(Lock.Block);
let flashSeq = 0;

/** A group is known by its id, or by its place while the server has not named it. */
export const groupKey = (group: BlockGroup, index: number): string => group.id || "idx" + index;

export async function openBlocked(): Promise<void> {
  set({ active: true, block: null, ...CLOSED });
  try {
    const block = await api<Block>("/api/block");
    if (get().active) set({ block });
  } catch (error) {
    fail(error);
  }
}

export function leaveBlocked(): void {
  clearTimeout(checkTimer);
  checkSeq++;
  set({ active: false });
}

// ── the search asks the blocker itself ───────────────────────────────────────

// the ready-made lists are far too large to search here
let checkTimer: ReturnType<typeof setTimeout> | undefined;
let checkSeq = 0;

function checkHost(): void {
  clearTimeout(checkTimer);
  const seq = ++checkSeq;
  const query = get().query;
  if (!isHost(query)) {
    set({ hit: null });
    return;
  }
  set({ hit: { host: query, pending: true } });
  checkTimer = setTimeout(async () => {
    let blocked: Blocked | null = null;
    let failed = false;
    try {
      blocked =
        (await api<{ blocked?: Blocked | null }>("/api/check?host=" + encodeURIComponent(query))).blocked ?? null;
    } catch {
      failed = true;
    }
    if (seq !== checkSeq || !get().active) return;
    set({ hit: { host: query, pending: false, failed, blocked } });
  }, CHECK_DELAY_MS);
}

export function setSearch(raw: string): void {
  set({ query: queryOf(raw) });
  checkHost();
}

// ── changes ──────────────────────────────────────────────────────────────────

const toWire = (block: Block) => ({
  // the version the page showed: a tab left open elsewhere, saving an older list, is refused instead of undoing what changed since
  base: block.v ?? "",
  lists: Object.fromEntries((block.lists ?? []).map(list => [String(list.id), !!list.on])),
  safebrowsing: !!block.safebrowsing?.on,
  groups: block.groups.map(group => ({
    id: group.id || null,
    name: group.name,
    on: !!group.on,
    kind: "block",
    items: group.items,
  })),
});

/** One change, made on a copy of what is shown. False when it was refused, or another change is still running. */
export async function saveBlock(change: (draft: Block) => void): Promise<boolean> {
  const current = get().block;
  if (busy() || !current) return false;
  const next = clone(current);
  change(next);
  const ok = await withLock(Lock.Block, async () => {
    try {
      set({ block: await post<Block>("/api/block", toWire(next)) });
      return true;
    } catch (error) {
      fail(error);
      if (!isAuthError(error)) {
        try {
          set({ block: await api<Block>("/api/block") });
        } catch {
          /* keep what we have */
        }
      }
      return false;
    }
  });
  if (ok && get().active && isHost(get().query))
    setTimeout(() => {
      if (get().active) checkHost();
    }, REBUILD_MS);
  return ok;
}

/** Lists by link: add, remove, rename, refresh. With `quiet` a refusal is returned, not shown. */
export async function changeLists(body: ListsRequest, done?: string, quiet = false): Promise<string | null> {
  if (busy() || !get().block) return "";
  return withLock(Lock.Block, async () => {
    try {
      set({ block: await post<Block>("/api/block/lists", body) });
      if (done) toast(done);
      return null;
    } catch (error) {
      if (quiet && !isAuthError(error)) return errorText(error);
      fail(error);
      return "";
    }
  });
}

export const toggleList = (index: number): Promise<boolean> =>
  saveBlock(draft => {
    const list = draft.lists?.[index];
    if (list) list.on = !list.on;
  });
export const toggleSafe = (): Promise<boolean> =>
  saveBlock(draft => {
    draft.safebrowsing = { on: !draft.safebrowsing?.on };
  });
export const toggleGroup = (index: number): Promise<boolean> =>
  saveBlock(draft => {
    draft.groups[index].on = !draft.groups[index].on;
  });
export const renameGroup = (index: number, name: string): Promise<boolean> =>
  saveBlock(draft => {
    draft.groups[index].name = name;
  });
export const deleteGroup = (index: number): Promise<boolean> =>
  saveBlock(draft => {
    draft.groups.splice(index, 1);
  });
export const removeItem = (index: number, text: string): Promise<boolean> =>
  saveBlock(draft => {
    draft.groups[index].items = draft.groups[index].items.filter(item => item !== text);
  });

export const toggleAdd = (key: string): void =>
  set(state => ({ addKey: state.addKey === key ? null : key, addError: "" }));
export const closeAdd = (): void => set({ addKey: null, addError: "" });
export const addTyped = (): void => {
  if (get().addError) set({ addError: "" });
};

export enum AddResult {
  Added = "added",
  Refused = "refused",
  Failed = "failed",
  Ignored = "ignored",
}

/** Adds the typed entry to the own group whose field is open; `sending` is told once the request leaves. */
export async function addEntry(text: string, sending: () => void): Promise<AddResult> {
  const { block, addKey } = get();
  if (busy() || !block || addKey == null) return AddResult.Ignored;
  const entry = parseEntry(text, false);
  if (entry.kind === EntryKind.Empty) return AddResult.Ignored;
  if (entry.kind === EntryKind.Bad) {
    set({ addError: entry.why });
    return AddResult.Refused;
  }
  const dup = block.groups.find(group => group.items.includes(entry.text));
  if (dup) {
    set({ addError: "уже есть в группе «" + dup.name + "»", flash: { text: entry.text, seq: ++flashSeq } });
    return AddResult.Refused;
  }
  sending();
  const ok = await saveBlock(draft => {
    draft.groups.find((group, index) => groupKey(group, index) === addKey)?.items.push(entry.text);
  });
  if (ok) set({ flash: { text: entry.text, seq: ++flashSeq } });
  return ok ? AddResult.Added : AddResult.Failed;
}

export const startNaming = (open: boolean): void => set({ naming: open });

/** A new group is created at once and opens with its field ready. */
export async function createGroup(name: string): Promise<void> {
  set({ naming: false });
  const ok = await saveBlock(draft => {
    draft.groups.unshift({ id: null, name, on: true, items: [] });
  });
  if (!ok || !get().active) return;
  const group = get().block?.groups.find(group => group.name === name);
  if (group?.id) set({ addKey: group.id, addError: "" });
}

export const toggleLinkForm = (): void => set(state => ({ linkOpen: !state.linkOpen, linkError: "" }));
export const closeLinkForm = (): void => set({ linkOpen: false, linkError: "" });

export enum LinkRefusal {
  NoName = "name",
  BadUrl = "url",
  Other = "other",
}

export async function addLink(name: string, url: string): Promise<LinkRefusal | null> {
  if (busy()) return LinkRefusal.Other;
  set({ linkError: "" });
  if (!name) return LinkRefusal.NoName;
  if (!isListLink(url)) {
    set({ linkError: "нужна ссылка на файл списка, начинается с https://" });
    return LinkRefusal.BadUrl;
  }
  set({ linkBusy: true });
  const refused = await changeLists({ op: ListOp.Add, name, url }, "Список подключён", true);
  set({ linkBusy: false });
  if (refused === null) {
    set({ linkOpen: false });
    return null;
  }
  set({ linkError: refused });
  return LinkRefusal.Other;
}
