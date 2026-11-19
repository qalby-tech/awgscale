/**
 * Users and their groups: what several sections share. The users themselves
 * are asked a page at a time (store/clients); here are the groups, the user
 * whose connection code is open, and what was changed a moment ago and is
 * shown before the server's own answer catches up.
 */
import { create } from "zustand";

import { api, ApiError, del, isAuthError, post } from "@/lib/api";
import { clone, ddmm, plural } from "@/lib/format";
import { PersistedSet } from "@/lib/storage";
import {
  CodeTab,
  GroupFilter,
  KidsList,
  type Client,
  type CodeData,
  type Conflict,
  type DiagItem,
  type UserGroup,
  type UserGroups,
} from "@/lib/types";
import { fail, toast } from "@/ui/toast";

import { findClients, patchClients, refreshLists } from "./clients";
import { exitLabel, isLocked, Lock, now, onLogout, refreshState, useCore, withLock } from "./core";
import { refreshSoon } from "./exits";

export interface Diag {
  pending: boolean;
  items: DiagItem[];
  error: string | null;
}

export interface CodeView {
  name: string;
  tab: CodeTab;
  data: CodeData | null;
  error: string | null;
  diag: Diag | null;
}

/** A term set a moment ago: until when (seconds; null = none), and when it was set (ms). */
export interface TermOverride {
  at: number | null;
  setAt: number;
}

export interface Invite {
  id: string;
  group?: string | null;
  left: number;
  total: number;
  expires_at: number;
  used?: string[];
}

/** The invitation panel: a link instead of a key, for one group (or for no group). */
export interface InviteView {
  groupId: string | null;
  groupName: string;
  /** The invitations of this group that still work; null while they are being read. */
  list: Invite[] | null;
  /** The link made just now: it is shown once, here. */
  link: string | null;
  linkId: string | null;
  busy: boolean;
  uses: number;
  days: number;
}

interface UsersState {
  groups: UserGroups | null;
  /** The groups could not be read: the list still shows everyone, ungrouped. */
  groupsFailed: boolean;
  code: CodeView | null;
  /** Created a moment ago, not yet in the server's list. */
  pending: string[];
  /** Removed a moment ago, still in the server's list. */
  hidden: string[];
  /** Which cards of the list are unfolded ("" is «Без группы»). */
  openCards: string[];
  termOver: Record<string, TermOverride>;
  invite: InviteView | null;
}

/** Which cards are unfolded, remembered in this browser. */
export const openCards = new PersistedSet("ugOpen");

export const useUsers = create<UsersState>(() => ({
  groups: null,
  groupsFailed: false,
  code: null,
  pending: [],
  hidden: [],
  openCards: [],
  termOver: {},
  invite: null,
}));

const set = useUsers.setState;
const get = useUsers.getState;

const NO_GROUPS: UserGroup[] = [];
export const groupsOf = (groups: UserGroups | null): UserGroup[] => groups?.groups ?? NO_GROUPS;
export const userGroups = (): UserGroup[] => groupsOf(get().groups);

/** The cards as this browser remembers them; called once the page is in the browser. */
export function readOpenCards(): void {
  set({ openCards: openCards.values() });
}
export function setCardOpen(key: string, open: boolean): void {
  if (open) openCards.add(key);
  else openCards.delete(key);
  readOpenCards();
}

/** What was changed a moment ago gives way to the server's answer as soon as that agrees: the few users concerned are asked by name. */
async function settle(): Promise<void> {
  const users = get();
  const names = [...new Set([...users.pending, ...users.hidden, ...Object.keys(users.termOver)])];
  if (!names.length) return;
  let found: Client[];
  try {
    found = await findClients(names);
  } catch {
    return; // asked again with the next poll
  }
  const byName = new Map(found.map(client => [client.name, client]));
  const latest = get();
  const pending = latest.pending.filter(name => !byName.has(name));
  const hidden = latest.hidden.filter(name => byName.has(name));
  const live = Object.entries(latest.termOver).filter(([name, override]) => {
    const client = byName.get(name);
    return !!client && termPending(client, override);
  });
  if (pending.length !== latest.pending.length || hidden.length !== latest.hidden.length) set({ pending, hidden });
  if (live.length !== Object.keys(latest.termOver).length) set({ termOver: Object.fromEntries(live) });
}
useCore.subscribe((state, previous) => {
  if (state.tick !== previous.tick && state.state) void settle();
});
onLogout(() => set({ code: null, invite: null }));

export function markCreated(name: string): void {
  set(state => ({
    pending: [...state.pending.filter(userName => userName !== name), name],
    hidden: state.hidden.filter(userName => userName !== name),
  }));
}
export function markRemoved(names: string[]): void {
  set(state => ({
    hidden: [...new Set([...state.hidden, ...names])],
    pending: state.pending.filter(name => !names.includes(name)),
    code: state.code && names.includes(state.code.name) ? null : state.code,
  }));
}

// ── groups ───────────────────────────────────────────────────────────────────

/** Reads the groups; false when they could not be read. */
export async function loadGroups(): Promise<boolean> {
  try {
    set({ groups: await api<UserGroups>("/api/ugroups"), groupsFailed: false });
    return true;
  } catch (error) {
    fail(error);
    set({ groupsFailed: true });
    return false;
  }
}

/** The same, quietly: for the sections where the groups only decorate. */
export async function loadGroupsQuietly(): Promise<void> {
  try {
    set({ groups: await api<UserGroups>("/api/ugroups") });
  } catch {
    /* the caller shows everyone as one group */
  }
}

const toWire = (group: UserGroup) => ({
  id: group.id || null,
  name: group.name,
  space: !!group.space,
  exit: group.exit ?? null,
  filter: group.filter || "std",
  ...(group.kids_lists ? { kids_lists: group.kids_lists } : {}),
  members: group.members,
});

/** One change of the groups, made on a copy of the list. False when it was refused, or another change is still running. */
export async function saveUserGroups(change: (draft: UserGroup[]) => void): Promise<boolean> {
  const current = get().groups;
  if (isLocked(Lock.Users) || !current) return false;
  const next = clone(current.groups);
  change(next);
  return withLock(Lock.Users, async () => {
    try {
      set({ groups: await post<UserGroups>("/api/ugroups", { base: current.v || "", groups: next.map(toWire) }) });
      // who is in which group, and how many each has, as the server counts them now
      await refreshState();
      await refreshLists();
      return true;
    } catch (error) {
      fail(error);
      const data = error instanceof ApiError ? (error.data as (UserGroups & Conflict) | null) : null;
      if (data?.conflict && Array.isArray(data.groups))
        set({ groups: data }); // changed in another window: the answer carries the list as it is now
      else if (!isAuthError(error)) {
        try {
          set({ groups: await api<UserGroups>("/api/ugroups") });
        } catch {
          /* keep what we have */
        }
      }
      if (!isAuthError(error)) {
        // the lists follow the groups as the server has them
        await refreshState();
        await refreshLists();
      }
      return false;
    }
  });
}

/** A user is in one group: joining one leaves the other. */
export function moveUser(name: string, groupId: string | null): Promise<boolean> {
  return saveUserGroups(draft => {
    draft.forEach(group => {
      group.members = group.members.filter(member => member !== name);
    });
    draft.find(group => group.id === groupId)?.members.push(name);
  });
}

// ── the connection code and the check of one user ────────────────────────────

const codeIs = (name: string): boolean => get().code?.name === name;
const patchCode = (name: string, patch: Partial<CodeView>): void =>
  set(state => (state.code?.name === name ? { code: { ...state.code, ...patch } } : state));

export async function runDiag(name: string): Promise<void> {
  if (!codeIs(name)) return;
  patchCode(name, { diag: { pending: true, items: [], error: null } });
  let items: DiagItem[] = [];
  let error: string | null = null;
  try {
    items = (await api<{ items?: DiagItem[] }>("/api/diag?name=" + encodeURIComponent(name))).items ?? [];
  } catch (failure) {
    if (isAuthError(failure)) return;
    error = (failure as Error).message;
  }
  patchCode(name, { diag: { pending: false, items, error } });
}

export async function openCode(name: string, tab: CodeTab = CodeTab.Vpn): Promise<void> {
  set({ code: { name, tab, data: null, error: null, diag: null }, invite: null });
  if (tab === CodeTab.Diag) void runDiag(name);
  let data: CodeData | null = null;
  let failure: string | null = null;
  try {
    data = await api<CodeData>("/api/clients/" + encodeURIComponent(name) + "/qr");
  } catch (error) {
    if (isAuthError(error)) return;
    failure = (error as Error).message;
  }
  const shown = get().code;
  if (shown?.name !== name) return;
  const noVpnCode = !!data && !data.uri && !data.uri_png && shown.tab === CodeTab.Vpn;
  patchCode(name, { data, error: failure, ...(noVpnCode ? { tab: CodeTab.Wg } : {}) });
}

export function showCodeTab(tab: CodeTab): void {
  const code = get().code;
  if (!code) return;
  patchCode(code.name, { tab });
  if (tab === CodeTab.Diag && !code.diag) void runDiag(code.name);
}

export const closeCode = (): void => set({ code: null });

// ── one user, or all the users of a group ────────────────────────────────────

const NAME_RE = /^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}$/;
export const validUserName = (name: string): boolean => NAME_RE.test(name);
export const NAME_RULE = "Имя: латиница, цифры, _ и -, до 32 символов";

/** The users of a group that exist right now (the server lists only those as members). */
export function groupUsers(group: UserGroup): string[] {
  const hidden = get().hidden;
  return group.members.filter(name => !hidden.includes(name));
}

/** Creates a user; throws what the server refused. False when another change is still running. */
export async function createUser(name: string): Promise<boolean> {
  if (isLocked(Lock.Users)) return false;
  await withLock(Lock.Users, () => post("/api/clients", { name, expires: "" }));
  markCreated(name);
  return true;
}

/** The terms that can be given, as the server takes them. */
export enum Term {
  None = "",
  Day = "1d",
  Week = "7d",
  Month = "30d",
}
export const TERMS: ReadonlyArray<readonly [Term, string]> = [
  [Term.None, "Без срока"],
  [Term.Day, "1 день"],
  [Term.Week, "7 дней"],
  [Term.Month, "30 дней"],
];
/** The last letter of a term says what its number counts. */
enum TermUnit {
  Hour = "h",
  Day = "d",
  Week = "w",
}
const TERM_UNIT_SECONDS: Record<TermUnit, number> = {
  [TermUnit.Hour]: 3600,
  [TermUnit.Day]: 86400,
  [TermUnit.Week]: 604800,
};
const termSeconds = (term: Term): number => parseInt(term, 10) * TERM_UNIT_SECONDS[term.slice(-1) as TermUnit];

const TERM_SHOWN_MS = 20000;
const TERM_AGREES_S = 300;
/** Does a term set a moment ago still have to stand in for the server's own. */
function termPending(client: Client, override: TermOverride): boolean {
  const have = client.expires_at || null;
  const agrees = override.at == null ? have == null : have != null && Math.abs(have - override.at) < TERM_AGREES_S;
  return Date.now() - override.setAt <= TERM_SHOWN_MS && !agrees;
}
/** A term set a moment ago shows at once; the server's own list follows within seconds. */
export function withTerm(client: Client, over: Record<string, TermOverride>): Client {
  const override = over[client.name];
  return override && termPending(client, override) ? { ...client, expires_at: override.at } : client;
}

/** A term for one user or for a whole group: counted from now, or none. */
export async function setTerm(names: string[], term: Term, what: string): Promise<void> {
  if (isLocked(Lock.Users) || !names.length) return;
  await withLock(Lock.Users, async () => {
    try {
      await post("/api/clients/expire", { names, expires: term });
      const expiresAt = term ? Math.round(now() + termSeconds(term)) : null;
      const setAt = Date.now();
      set(state => ({
        termOver: { ...state.termOver, ...Object.fromEntries(names.map(name => [name, { at: expiresAt, setAt }])) },
      }));
      toast(what + ": " + (expiresAt ? "до " + ddmm(expiresAt) : "без срока"));
    } catch (error) {
      fail(error);
    }
  });
  void refreshState();
}

/** Pause or resume: the key stays, nothing passes; shown at once, the server's list follows. */
export async function setPaused(names: string[], on: boolean, what: string): Promise<void> {
  if (isLocked(Lock.Users) || !names.length) return;
  await withLock(Lock.Users, async () => {
    try {
      await post("/api/clients/suspend", { names, on });
      patchClients(names, { suspended: on });
      toast(what + ": " + (on ? "приостановлен" : "возобновлён"));
    } catch (error) {
      fail(error);
    }
  });
  void refreshState();
}

/** Keep one user on an exit by hand (null = as the group or the shares decide). A preference: while that exit is down the server uses another. */
export async function pinUser(client: Client, index: number | null): Promise<void> {
  if (isLocked(Lock.Users) || (client.pin ?? null) === index) return;
  await withLock(Lock.Users, async () => {
    try {
      await post("/api/clients/exit", { names: [client.name], exit: index });
      patchClients([client.name], { pin: index });
      toast(client.name + ": " + (index == null ? "выход авто" : "выход " + exitLabel(index)));
    } catch (error) {
      fail(error);
    }
  });
  refreshSoon();
}

/** Keep a group on one exit (null = as the server decides); found by id, the list may have been replaced meanwhile. */
export async function pinGroup(group: UserGroup, index: number | null): Promise<void> {
  if ((group.exit ?? null) === index) return;
  if (
    !(await saveUserGroups(draft => {
      const same = draft.find(each => each.id === group.id);
      if (same) same.exit = index;
    }))
  )
    return;
  toast(group.name + ": " + (index == null ? "выход по умолчанию" : "выход " + exitLabel(index)));
  refreshSoon();
}

/** How a mode of filtering is named in the group's menu and in the message after a change. */
export const FILTER_MODES: Record<GroupFilter, string> = {
  [GroupFilter.Standard]: "обычная",
  [GroupFilter.Off]: "без блокировки",
  [GroupFilter.Kids]: "детская",
  [GroupFilter.Pro]: "Pro",
  [GroupFilter.ProPlus]: "Pro++",
  [GroupFilter.Ultimate]: "Ultimate",
};

/** How the add-on lists of the children's mode are named. */
export const KIDS_LIST_NAMES: Record<KidsList, string> = {
  [KidsList.Nsfw]: "NSFW",
  [KidsList.Gambling]: "Азартные игры",
  [KidsList.Bypass]: "Обход фильтра",
  [KidsList.Social]: "Соцсети",
};

export async function filterGroup(group: UserGroup, filter: GroupFilter): Promise<void> {
  if ((group.filter || GroupFilter.Standard) === filter) return;
  if (
    await saveUserGroups(draft => {
      const same = draft.find(each => each.id === group.id);
      if (same) same.filter = filter;
    })
  )
    toast(group.name + ": фильтрация — " + FILTER_MODES[filter]);
}

/** One add-on list of a group in the children's mode switched on or off; saved at once. */
export async function toggleKidsList(group: UserGroup, kidsList: KidsList): Promise<void> {
  const on = !(group.kids_lists ?? []).includes(kidsList);
  if (
    await saveUserGroups(draft => {
      const same = draft.find(each => each.id === group.id);
      if (!same) return;
      const now = same.kids_lists ?? [];
      same.kids_lists = Object.values(KidsList).filter(each => (each === kidsList ? on : now.includes(each)));
    })
  )
    toast(group.name + ": " + KIDS_LIST_NAMES[kidsList] + (on ? " — блокируются" : " — не блокируются"));
}

async function reloadGroupsQuietly(): Promise<void> {
  try {
    set({ groups: await api<UserGroups>("/api/ugroups") });
  } catch {
    /* the list still works without it */
  }
}

/** Revoke a whole group: its users are removed, the (now empty) group stays. */
export async function revokeGroup(group: UserGroup, names: string[]): Promise<void> {
  if (isLocked(Lock.Users) || !names.length) return;
  const whom = plural(names.length, [
    "пользователь будет удалён",
    "пользователя будут удалены",
    "пользователей будут удалены",
  ]);
  if (
    !confirm(
      "Отозвать доступ у группы «" +
        group.name +
        "»? " +
        names.length +
        " " +
        whom +
        ", ключи перестанут работать сразу.",
    )
  )
    return;
  await withLock(Lock.Users, async () => {
    let gone: string[] = [];
    try {
      gone = (await post<{ removed?: string[] }>("/api/clients/remove", { names })).removed ?? names;
      toast("Доступ отозван: " + group.name);
    } catch (error) {
      fail(error);
      gone = (error instanceof ApiError && (error.data as { removed?: string[] } | null)?.removed) || [];
    } // some may be gone even when not all are
    markRemoved(gone);
    await reloadGroupsQuietly();
  });
  void refreshState();
}

export async function removeUser(name: string): Promise<void> {
  if (!confirm("Удалить пользователя «" + name + "»? Его ключ перестанет работать сразу.")) return;
  try {
    await del("/api/clients/" + encodeURIComponent(name));
    markRemoved([name]);
    toast("Пользователь удалён");
    await reloadGroupsQuietly();
  } catch (error) {
    fail(error);
  }
  void refreshState();
}

// ── invitations ──────────────────────────────────────────────────────────────

export const INVITE_USES = [1, 2, 3, 5] as const;
export const INVITE_DAYS: ReadonlyArray<readonly [number, string]> = [
  [1, "1 день"],
  [3, "3 дня"],
  [7, "7 дней"],
  [30, "30 дней"],
];
const DEFAULT_INVITE_USES = 1;
const DEFAULT_INVITE_DAYS = 3;

interface InvitesAnswer {
  invites?: Invite[];
  token?: string;
  id?: string;
}

const inviteIs = (invite: InviteView): boolean => get().invite?.groupId === invite.groupId && !!get().invite;
const patchInvite = (patch: Partial<InviteView>): void =>
  set(state => (state.invite ? { invite: { ...state.invite, ...patch } } : state));
const ofGroup = (answer: InvitesAnswer, groupId: string | null): Invite[] =>
  (answer.invites ?? []).filter(invite => (invite.group || null) === groupId);

export async function openInvite(group: UserGroup | null): Promise<void> {
  const invite: InviteView = {
    groupId: group?.id ?? null,
    groupName: group ? group.name : "Без группы",
    list: null,
    link: null,
    linkId: null,
    busy: false,
    uses: DEFAULT_INVITE_USES,
    days: DEFAULT_INVITE_DAYS,
  };
  set({ invite, code: null });
  try {
    const answer = await api<InvitesAnswer>("/api/invites");
    if (inviteIs(invite)) patchInvite({ list: ofGroup(answer, invite.groupId) });
  } catch (error) {
    if (isAuthError(error)) return;
    fail(error);
    if (inviteIs(invite)) patchInvite({ list: [] });
  }
}

export const closeInvite = (): void => set({ invite: null });
export const setInviteChoice = (choice: Partial<Pick<InviteView, "uses" | "days">>): void => patchInvite(choice);

export async function makeInvite(): Promise<void> {
  const invite = get().invite;
  if (!invite || invite.busy) return;
  patchInvite({ busy: true });
  try {
    const answer = await post<InvitesAnswer>("/api/invites", {
      group: invite.groupId,
      uses: invite.uses,
      days: invite.days,
    });
    if (inviteIs(invite))
      patchInvite({
        link: location.origin + "/i/" + answer.token,
        linkId: answer.id ?? null,
        list: ofGroup(answer, invite.groupId),
      });
  } catch (error) {
    fail(error);
  }
  if (inviteIs(invite)) patchInvite({ busy: false });
}

export async function dropInvite(id: string): Promise<void> {
  const invite = get().invite;
  if (!invite || invite.busy) return;
  patchInvite({ busy: true });
  try {
    const answer = await del<InvitesAnswer>("/api/invites/" + encodeURIComponent(id));
    if (inviteIs(invite))
      patchInvite({
        list: ofGroup(answer, invite.groupId),
        ...(id === get().invite?.linkId ? { link: null, linkId: null } : {}),
      }); // a revoked link is not offered for copying
    toast("Приглашение отозвано");
  } catch (error) {
    fail(error);
    try {
      const answer = await api<InvitesAnswer>("/api/invites");
      if (inviteIs(invite)) patchInvite({ list: ofGroup(answer, invite.groupId) });
    } catch {
      /* keep what is shown */
    }
  }
  if (inviteIs(invite)) patchInvite({ busy: false });
}
