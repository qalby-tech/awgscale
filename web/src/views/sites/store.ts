/**
 * «Через заграницу»: the list as the server has it, and the draft the admin
 * edits. Nothing is sent until «Применить»; the bar counts what is staged.
 * The draft lives only while the section is open.
 */
import { create } from "zustand";

import { api, ApiError, errorText, isAuthError, post } from "@/lib/api";
import { countryName } from "@/lib/countries";
import {
  coverFn,
  EntryKind,
  isEntry,
  isHost,
  isListLink,
  kindOf,
  parseEntry,
  pathOf,
  queryOf,
  routeMatches,
} from "@/lib/entries";
import { pointerIsDown } from "@/lib/pointer";
import { RoutingMode, type PanelState } from "@/lib/types";
import { isLocked, Lock, refreshState, withLock } from "@/store/core";
import { menuIsOpen } from "@/ui/menu";
import { fail, toast, toastError } from "@/ui/toast";

import type { DraftGroup, Probe, ProbeWay, SiteGroup, Sites } from "./types";
import { isTextField } from "@/lib/dom";

const DEFAULT_LIMIT = 500;
const DEFAULT_MAX_LISTS = 4;
/** After a switch of the mode the state poll lags behind for a while: its word is not taken sooner. */
const MODE_SETTLE_MS = 30000;

/** Asks the page to draw the eye to a chip; `seq` makes two flashes of one text distinct. */
export interface Flash {
  text: string;
  seq: number;
}

interface SitesState {
  /** The section is on screen. */
  active: boolean;
  sites: Sites | null;
  draft: DraftGroup[] | null;
  /** The search, reduced to what entries are compared with, and the page of a pasted link. */
  query: string;
  path: string;
  /** The card that asks for the name of a new group is open. */
  naming: boolean;
  /** The key of the card whose field is open. */
  addKey: string | null;
  addError: string;
  /** Entries the text in the open field would make redundant. */
  cover: string[];
  linkOpen: boolean;
  linkError: string;
  probe: Probe | null;
  modeBusy: boolean;
  countryBusy: boolean;
  flash: Flash | null;
}

const CLOSED = {
  query: "",
  path: "/",
  naming: false,
  addKey: null,
  addError: "",
  cover: [] as string[],
  linkOpen: false,
  linkError: "",
  flash: null,
};

export const useSites = create<SitesState>(() => ({
  active: false,
  sites: null,
  draft: null,
  probe: null,
  modeBusy: false,
  countryBusy: false,
  ...CLOSED,
}));

const set = useSites.setState;
const get = useSites.getState;

let tmpSeq = 0;
let flashSeq = 0;
let modeAt = 0;
/** The version of the list the state poll last spoke of and that was dealt with. */
let seenVersion: string | null = null;

const toDraft = (groups: SiteGroup[]): DraftGroup[] =>
  groups.map(group => ({
    key: group.id,
    id: group.id,
    name: group.name,
    on: !!group.on,
    direct: !!group.direct,
    scope: group.groups?.slice() ?? [],
    items: group.items.slice(),
    auto: group.auto || null,
    url: group.url || null,
    updated: group.updated || null,
    error: group.error || "",
  }));
const tmpKey = (): string => "tmp" + ++tmpSeq;

// ── reading ──────────────────────────────────────────────────────────────────

/** How many changes are staged: entries moved, added or gone, groups renamed, switched, created or deleted. */
export function dirtyCount(sites: Sites | null, draft: DraftGroup[] | null): number {
  if (!sites || !draft) return 0;
  // a site may be in several groups: an entry counts once however many groups it left or joined (moved = one)
  const stored = new Map<string, Set<string>>();
  const staged = new Map<string, Set<string>>();
  const note = (into: Map<string, Set<string>>, item: string, group: string) => {
    const groups = into.get(item) ?? new Set<string>();
    groups.add(group);
    into.set(item, groups);
  };
  sites.groups.forEach(group => {
    if (!group.url) group.items.forEach(item => note(stored, item, group.id));
  });
  draft.forEach(group => {
    if (!group.url) group.items.forEach(item => note(staged, item, group.key));
  });
  let count = 0;
  for (const item of new Set([...stored.keys(), ...staged.keys()])) {
    const before = stored.get(item) ?? new Set<string>();
    const after = staged.get(item) ?? new Set<string>();
    const left = [...before].filter(group => !after.has(group)).length;
    const joined = [...after].filter(group => !before.has(group)).length;
    count += Math.max(left, joined);
  }
  const byId = new Map(draft.filter(group => group.id).map(group => [group.id, group]));
  sites.groups.forEach(group => {
    const staged = byId.get(group.id);
    if (!staged) count++;
    else {
      if (staged.name !== group.name) count++;
      if (staged.on !== !!group.on) count++;
    }
  });
  return count + draft.filter(group => !group.id).length;
}

export const sitesDirty = (): boolean => dirtyCount(get().sites, get().draft) > 0;
export const modeOf = (sites: Sites | null): RoutingMode =>
  sites?.mode === RoutingMode.Foreign ? RoutingMode.Foreign : RoutingMode.List;
/** The entries typed by hand in a group (a list by link has none of its own). */
const ownItems = (group: DraftGroup): string[] => (group.url ? [] : group.items);
const busy = (): boolean => isLocked(Lock.Sites);

// ── opening and leaving ──────────────────────────────────────────────────────

export async function openSites(): Promise<void> {
  seenVersion = null;
  set({ active: true, sites: null, draft: null, probe: get().probe, ...CLOSED });
  let sites: Sites;
  try {
    sites = await api<Sites>("/api/sites");
  } catch (error) {
    fail(error);
    return;
  }
  if (get().active) set({ sites, draft: toDraft(sites.groups) });
}

/** The draft does not outlive the section. */
export function leaveSites(): void {
  set({ active: false, draft: null });
}

// ── staging ──────────────────────────────────────────────────────────────────

function editDraft(change: (draft: DraftGroup[]) => DraftGroup[]): void {
  const current = get().draft;
  if (current) set({ draft: change(current) });
}
const editGroup = (key: string, patch: (group: DraftGroup) => Partial<DraftGroup>): void =>
  editDraft(draft => draft.map(group => (group.key === key ? { ...group, ...patch(group) } : group)));

export const toggleGroup = (key: string): void => editGroup(key, group => ({ on: !group.on }));
export const renameGroup = (key: string, name: string): void => editGroup(key, () => ({ name }));
export const removeItem = (key: string, text: string): void =>
  editGroup(key, group => ({ items: group.items.filter(item => item !== text) }));
export const deleteGroup = (key: string): void => {
  editDraft(draft => draft.filter(group => group.key !== key));
  if (get().addKey === key) set({ addKey: null, cover: [], addError: "" });
};

export function cancelChanges(): void {
  const sites = get().sites;
  if (sites)
    set({
      draft: toDraft(sites.groups),
      naming: false,
      addKey: null,
      addError: "",
      cover: [],
      linkOpen: false,
      linkError: "",
    });
}

export function setSearch(raw: string): void {
  set({ query: queryOf(raw), path: pathOf(raw) });
}

/** Opens the field of a card, or closes it when it is the open one. */
export function toggleAdd(key: string): void {
  set(state => ({ addKey: state.addKey === key ? null : key, cover: [], addError: "" }));
}
export const closeAdd = (): void => set({ addKey: null, cover: [], addError: "" });

/** The text in the open field changed: the message goes, the redundant entries are marked. */
export function addTyped(text: string): void {
  const entry = parseEntry(text, true);
  const test = coverFn(entry);
  const cover: string[] = [];
  if (isEntry(entry))
    get().draft?.forEach(group =>
      ownItems(group).forEach(text => {
        if (test(text)) cover.push(text);
      }),
    );
  set({ cover, addError: "" });
}

export enum AddResult {
  Added = "added",
  Refused = "refused",
  Ignored = "ignored",
}

/** Stages the typed entry in the card whose field is open; `replace` drops the entries it makes redundant. */
export function addEntry(text: string, replace: boolean): AddResult {
  const { draft, sites, addKey, cover } = get();
  if (busy() || !draft || !sites || !addKey) return AddResult.Ignored;
  const entry = parseEntry(text, true);
  if (entry.kind === EntryKind.Empty) return AddResult.Ignored;
  if (entry.kind === EntryKind.Bad) {
    set({ addError: entry.why });
    return AddResult.Refused;
  }
  // a site may be in several groups (the first in the order of the rules decides), but not twice in one
  const dup = draft.find(group => group.key === addKey && group.items.includes(entry.text));
  if (dup) {
    set({ addError: "уже есть в этой группе", flash: { text: entry.text, seq: ++flashSeq } });
    return AddResult.Refused;
  }
  const gone = replace && cover.length ? new Set(cover) : null;
  const total = draft.reduce((sum, group) => sum + ownItems(group).length, 0) - (gone ? gone.size : 0);
  if (total >= (sites.limit || DEFAULT_LIMIT)) {
    set({ addError: "в списке уже " + total + " записей — больше нельзя" });
    return AddResult.Refused;
  }
  if (!draft.some(group => group.key === addKey)) return AddResult.Ignored;
  const withEntry = (group: DraftGroup): DraftGroup => {
    const kept = gone && !group.url ? group.items.filter(text => !gone.has(text)) : group.items;
    if (group.key === addKey) return { ...group, items: [...kept, entry.text] };
    return kept === group.items ? group : { ...group, items: kept };
  };
  set({
    draft: draft.map(withEntry),
    cover: [],
    addError: "",
    flash: { text: entry.text, seq: ++flashSeq },
  });
  return AddResult.Added;
}

export const startNaming = (open: boolean): void => set({ naming: open });

/** A new group is the first card, with its field open. */
export function createGroup(name: string): void {
  const group: DraftGroup = {
    direct: false,
    scope: [],
    key: tmpKey(),
    id: null,
    name,
    on: true,
    items: [],
    auto: null,
    url: null,
    updated: null,
    error: "",
  };
  set(state => ({ draft: [group, ...(state.draft ?? [])], naming: false, addKey: group.key, cover: [], addError: "" }));
}

export const toggleLinkForm = (): void => set(state => ({ linkOpen: !state.linkOpen, linkError: "" }));
export const closeLinkForm = (): void => set({ linkOpen: false, linkError: "" });

export enum LinkRefusal {
  NoName = "name",
  BadUrl = "url",
  Other = "other",
}

/** A new list is staged like any other change: it is downloaded on «Применить». Null when it was staged. */
export function addLink(name: string, url: string): LinkRefusal | null {
  const { draft, sites } = get();
  if (busy() || !draft || !sites) return LinkRefusal.Other;
  const max = sites.max_lists || DEFAULT_MAX_LISTS;
  set({ linkError: "" });
  if (!name) return LinkRefusal.NoName;
  if (!isListLink(url)) {
    set({ linkError: "нужна ссылка на файл списка, начинается с https://" });
    return LinkRefusal.BadUrl;
  }
  if (draft.some(group => group.url === url)) {
    set({ linkError: "этот список уже подключён" });
    return LinkRefusal.Other;
  }
  if (draft.filter(group => group.url).length >= max) {
    set({ linkError: "не больше " + max + " списков по ссылке" });
    return LinkRefusal.Other;
  }
  set({
    draft: [
      {
        key: tmpKey(),
        id: null,
        name,
        on: true,
        direct: false,
        scope: [],
        items: [],
        auto: null,
        url,
        updated: null,
        error: "",
      },
      ...draft,
    ],
    linkOpen: false,
  });
  return null;
}

/** Adds a domain found by the check to a group — unless some group has it already. */
export function addFromProbe(key: string, domain: string): void {
  editDraft(draft =>
    draft.some(group => !group.url && group.items.includes(domain))
      ? draft
      : draft.map(group => (group.key === key ? { ...group, items: [...group.items, domain] } : group)),
  );
}

// ── sending ──────────────────────────────────────────────────────────────────

const toWire = (group: DraftGroup) =>
  group.url
    ? { id: group.id || null, name: group.name, on: group.on, url: group.url }
    : { id: group.id || null, name: group.name, on: group.on, items: group.items };

/** → whether the staged groups were saved (false when refused, or when another change is still running). */
export async function applySites(): Promise<boolean> {
  const { sites, draft } = get();
  if (busy() || !sites || !draft || !sitesDirty()) return false;
  return withLock(Lock.Sites, async () => {
    try {
      const answer = await post<Sites>("/api/sites", { base: sites.v, groups: draft.map(toWire) });
      const filling = draft.find(group => group.key === get().addKey)?.name;
      const next = toDraft(answer.groups);
      set({ sites: answer, draft: next, addKey: (filling && next.find(group => group.name === filling)?.key) || null });
      toast(answer.applied === false ? "Сохранено" : "Применено");
      return true;
    } catch (error) {
      const data = error instanceof ApiError ? (error.data as Sites | null) : null;
      if (data?.conflict && Array.isArray(data.groups)) {
        // changed in another window: the answer carries the list as it is now
        set({ sites: data, draft: toDraft(data.groups), naming: false, addKey: null, cover: [], addError: "" });
      }
      fail(error);
      return false;
    }
  });
}

/** Reads a list by link again, now. */
export async function refreshList(id: string): Promise<void> {
  if (busy()) return;
  if (sitesDirty()) {
    toastError("Сначала примените или отмените изменения");
    return;
  }
  await withLock(Lock.Sites, async () => {
    try {
      const answer = await post<Sites>("/api/sites/refresh", { id });
      set({ sites: answer, draft: toDraft(answer.groups) });
      const now = answer.groups.find(group => group.id === id);
      if (now?.error) toastError(now.error);
      else toast(answer.applied === false ? "Список не изменился" : "Список обновлён");
    } catch (error) {
      fail(error);
    }
  });
}

const MODE_QUESTION: Record<RoutingMode, string> = {
  [RoutingMode.Foreign]: "Пускать через заграницу всё зарубежное?",
  [RoutingMode.List]: "Пускать через заграницу только сайты из списка?",
};

export async function setMode(mode: RoutingMode): Promise<void> {
  const state = get();
  if (state.modeBusy || !state.sites || modeOf(state.sites) === mode) return;
  if (!confirm(MODE_QUESTION[mode])) return;
  set({ modeBusy: true });
  try {
    const answer = await post<{ mode: RoutingMode }>("/api/mode", { mode });
    modeAt = Date.now();
    set(current => ({ sites: current.sites && { ...current.sites, mode: answer.mode } }));
    toast("Применено");
  } catch (error) {
    fail(error);
  }
  set({ modeBusy: false });
}

/** The home country: its networks go direct, and "everything foreign" means "not from there". */
export async function setCountry(country: string, current: string): Promise<void> {
  if (get().countryBusy || country === current) return;
  if (!confirm(`Своя страна — ${countryName(country)}? Её сети пойдут напрямую, список скачается заново.`)) return;
  set({ countryBusy: true });
  try {
    await post<unknown>("/api/country", { country });
    await refreshState();
    toast("Применено");
  } catch (error) {
    fail(error);
  }
  set({ countryBusy: false });
}

// ── «не открывается сайт» ────────────────────────────────────────────────────

export async function runProbe(host: string): Promise<void> {
  if (get().probe?.pending) return;
  set({ probe: { host, pending: true } });
  let ways: ProbeWay[] | undefined;
  let error: string | null = null;
  try {
    ways = (
      await api<{ ways?: ProbeWay[] }>(
        "/api/probe?host=" + encodeURIComponent(host) + "&path=" + encodeURIComponent(get().path || "/"),
      )
    ).ways;
  } catch (failure) {
    if (isAuthError(failure)) fail(failure);
    error = errorText(failure);
  }
  set({ probe: { host, pending: false, ways, error } });
}

/** How an address is routed by the draft: the group that sends it abroad, or a switched-off group that would. */
export function routeOf(draft: DraftGroup[], host: string): { through: DraftGroup | null; idle: DraftGroup | null } {
  // where it goes for everybody: a rule for some groups of people acts only for theirs
  const has = (group: DraftGroup) =>
    !group.url &&
    !group.scope.length &&
    group.items.some(text => kindOf(text) !== EntryKind.Net && routeMatches(text, host));
  const through = draft.find(group => group.on && has(group)) ?? null;
  return { through, idle: through ? null : (draft.find(group => !group.on && has(group)) ?? null) };
}

export const probeHost = (query: string): string | null => (isHost(query) ? query : null);

// ── changes made elsewhere ───────────────────────────────────────────────────

/** Something is being typed, staged or pressed: the list is not replaced under it. */
function inUse(): boolean {
  const state = get();
  return (
    busy() ||
    sitesDirty() ||
    menuIsOpen() ||
    pointerIsDown() ||
    state.naming ||
    !!state.addKey ||
    state.linkOpen ||
    isTextField(document.activeElement)
  );
}

/**
 * Every state answer says which version of the list is stored and which mode
 * runs. An open, idle section re-reads the list; a change based on an older
 * version is refused by the server anyway.
 */
export function followState(state: PanelState | null): void {
  const current = get();
  if (!state || !current.active || !current.sites) return;
  if (current.draft && state.sites_v && state.sites_v !== seenVersion) {
    if (current.sites.v === state.sites_v) seenVersion = state.sites_v;
    else if (!inUse()) {
      seenVersion = state.sites_v;
      void reread();
    }
  }
  const mode = state.routing?.mode;
  if (mode && mode !== current.sites.mode && !current.modeBusy && Date.now() - modeAt > MODE_SETTLE_MS)
    set({ sites: { ...current.sites, mode } }); // switched in another window
}

async function reread(): Promise<void> {
  let sites: Sites;
  try {
    sites = await api<Sites>("/api/sites");
  } catch {
    seenVersion = null;
    return;
  }
  if (!get().active || inUse()) {
    seenVersion = null;
    return;
  } // something was started meanwhile: the next answer brings it again
  set({ sites, draft: toDraft(sites.groups) });
}
