/**
 * What every section shares: the session, the state of the cascade that is
 * asked for every few seconds, the current route, and which operations are in
 * flight.
 */
import { create } from "zustand";

import { api, errorText, isAuthError, post, setUnauthorizedHandler } from "@/lib/api";
import { BUILD_ID, readBuildId } from "@/lib/build";
import { typingAnywhere } from "@/lib/pointer";
import { ExitState, Route, type Section, type Count, type Exit, type PanelState, type UserCounts } from "@/lib/types";
import { closeMenu, menuIsOpen } from "@/ui/menu";

export enum Session {
  Unknown = "unknown",
  Login = "login",
  App = "app",
}

/** Operations that must not overlap; while one runs the page is dimmed. */
export enum Lock {
  Users = "users",
  Spaces = "spaces",
  Sites = "sites",
  Block = "block",
}

export const POLL_INTERVAL_MILLISECONDS = 5000;

interface CoreState {
  session: Session;
  state: PanelState | null;
  stateErr: string | null;
  offline: boolean;
  /** Grows with every answer (or failure) of the state poll: sections time their own requests by it. */
  tick: number;
  route: Route;
  locks: Lock[];
  /** A newer build is on the server and the page could not reload by itself. */
  newBuild: boolean;
}

export const useCore = create<CoreState>(() => ({
  session: Session.Unknown,
  state: null,
  stateErr: null,
  offline: false,
  tick: 0,
  route: Route.Overview,
  locks: [],
  newBuild: false,
}));

const set = useCore.setState;
const get = useCore.getState;

// ── reading the state ────────────────────────────────────────────────────────

const NO_EXITS: Exit[] = [];
const NO_USERS: UserCounts = { users: 0, online: 0 };
const NOBODY: Count = { users: 0, online: 0 };
/** The users as the state counts them: never listed (a page of them is in store/clients). */
export const usersOf = (state: PanelState | null): UserCounts => state?.users ?? NO_USERS;
/** How many users a group has ("" is those in no group). */
export const groupCountOf = (state: PanelState | null, groupId: string): Count => {
  const users = usersOf(state);
  if (!groupId) return users.ungrouped ?? NOBODY;
  return users.groups?.find(group => group.id === groupId) ?? NOBODY;
};
export const exitsOf = (state: PanelState | null): Exit[] => state?.exits ?? NO_EXITS;
/** The server's clock when it has one, in seconds. */
export const nowOf = (state: PanelState | null): number => state?.time || Date.now() / 1000;

export const exitsNow = (): Exit[] => exitsOf(get().state);
export const now = (): number => nowOf(get().state);
export const exitLabel = (index: number): string =>
  exitsNow().find(exit => exit.index === index)?.name ?? "выход " + index;

/** What is wrong right now, in words; empty when all is well. */
export function problems(current: Pick<CoreState, "state" | "stateErr" | "offline">): string[] {
  const parts: string[] = [];
  const state = current.state;
  if (current.offline) parts.push("Нет связи с панелью.");
  if (!state) {
    if (current.stateErr && !current.offline) parts.push("Нет данных от входного сервера: " + current.stateErr);
    return parts;
  }
  const exits = exitsOf(state);
  const up = exits.filter(exit => exit.state === ExitState.Up).length;
  if (state.stale) parts.push("Нет связи с входным сервером, данные могли устареть.");
  if (state.entry && !state.entry.client_if_up) parts.push("Туннель для пользователей на входном сервере не поднят.");
  if (exits.length && up === 0) parts.push("Все выходные серверы недоступны: зарубежные сайты не открываются.");
  else
    exits
      .filter(exit => exit.state !== ExitState.Up)
      .forEach(exit => parts.push("Выход «" + exit.name + "» недоступен, его пользователи переведены на другой."));
  return parts;
}

// ── locks ────────────────────────────────────────────────────────────────────

export const isLocked = (lock: Lock): boolean => get().locks.includes(lock);

/** Runs `work` alone under `lock`. Check `isLocked` first: a second run is not queued. */
export async function withLock<T>(lock: Lock, work: () => Promise<T>): Promise<T> {
  set(state => ({ locks: [...state.locks, lock] }));
  try {
    return await work();
  } finally {
    set(state => ({ locks: state.locks.filter(held => held !== lock) }));
  }
}

// ── routes ───────────────────────────────────────────────────────────────────

const ROUTES = Object.values(Route) as string[];
export const sectionOf = (route: Route): Section => route.split("/")[0] as Section;
export const hrefOf = (route: Route): string => "#/" + route;

function routeFromHash(): Route {
  const path = location.hash.replace(/^#\/?/, "");
  return ROUTES.includes(path) ? (path as Route) : Route.Overview;
}

export function navigate(route: Route): void {
  location.hash = hrefOf(route);
}

/** A section with unsaved work may refuse to be left; returns false to stay. */
type LeaveGuard = () => boolean;
let leaveGuard: LeaveGuard | null = null;
export function setLeaveGuard(guard: LeaveGuard | null): void {
  leaveGuard = guard;
}
/** Whether the current section holds work that leaving the page would lose. */
let unsavedWork: () => boolean = () => false;
export function setUnsavedWork(check: (() => boolean) | null): void {
  unsavedWork = check ?? (() => false);
}

let skipNextHashChange = false;
function onHashChange(): void {
  if (skipNextHashChange) {
    skipNextHashChange = false;
    return;
  }
  if (get().session !== Session.App) return;
  const next = routeFromHash();
  const current = get().route;
  if (next === current) return;
  if (leaveGuard && !leaveGuard()) {
    skipNextHashChange = true;
    location.hash = hrefOf(current);
    return;
  }
  closeMenu();
  set({ route: next });
}

// ── a newer build on the server ──────────────────────────────────────────────

/** Open forms and staged changes say so here: the page does not reload under them. */
let holds = 0;
export function holdReload(): () => void {
  holds++;
  let done = false;
  return () => {
    if (!done) {
      done = true;
      holds--;
    }
  };
}

const BUILD_ID_LENGTH = 12; // what the server puts into the page; anything else is a page nobody stamped (tests, dev)
const TRIED_KEY = "ui";

/** At most one automatic reload per build, and never under something being typed or staged. */
function buildChanged(id: string): void {
  let tried: string | null = null;
  try {
    tried = sessionStorage.getItem(TRIED_KEY);
  } catch {
    /* no storage: just offer */
  }
  const busy = typingAnywhere() || holds > 0 || unsavedWork() || menuIsOpen();
  if (!busy && tried !== id) {
    try {
      sessionStorage.setItem(TRIED_KEY, id);
      location.reload();
      return;
    } catch {
      /* fall through */
    }
  }
  set({ newBuild: true });
}

// ── the session and the poll ─────────────────────────────────────────────────

function showLogin(): void {
  closeMenu();
  set({ session: Session.Login });
}

function showApp(): void {
  if (get().session === Session.App) return;
  set({ session: Session.App, route: routeFromHash() });
}

export async function refreshState(): Promise<void> {
  let state: PanelState;
  try {
    state = await api<PanelState>("/api/state");
  } catch (error) {
    if (isAuthError(error)) return;
    set(current => ({ offline: true, tick: current.tick + 1 }));
    showApp();
    return;
  }
  if (state?.ui && BUILD_ID().length === BUILD_ID_LENGTH && state.ui !== BUILD_ID()) buildChanged(state.ui);
  const good = !!state?.time;
  set(current => ({
    offline: false,
    tick: current.tick + 1,
    state: good ? state : null,
    stateErr: good ? null : state?.poll_error || state?.error || "нет данных",
  }));
  showApp();
}

export async function login(password: string): Promise<string | null> {
  try {
    await post("/api/login", { password });
  } catch (error) {
    return errorText(error);
  }
  await refreshState();
  return null;
}

/** Called before the session is dropped, so the sections can forget what they hold. */
const logoutHooks: Array<() => void> = [];
export function onLogout(hook: () => void): void {
  logoutHooks.push(hook);
}

export async function logout(): Promise<void> {
  try {
    await post("/api/logout");
  } catch {
    /* the session is gone either way */
  }
  logoutHooks.forEach(hook => hook());
  set({ state: null });
  showLogin();
}

let started = false;
/** Once, in the browser: binds the page to its address bar and starts the poll. */
export function start(): void {
  if (started) return;
  started = true;
  readBuildId();
  setUnauthorizedHandler(showLogin);
  window.addEventListener("hashchange", onHashChange);
  window.addEventListener("beforeunload", event => {
    if (get().session === Session.App && unsavedWork()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  const visible = () => get().session === Session.App && !document.hidden;
  setInterval(() => {
    if (visible()) void refreshState();
  }, POLL_INTERVAL_MILLISECONDS);
  document.addEventListener("visibilitychange", () => {
    if (visible()) void refreshState();
  });
  void refreshState();
}
