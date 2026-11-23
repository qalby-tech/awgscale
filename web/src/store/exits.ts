/**
 * The exits as servers: a rented server becomes an exit, an exit is renamed,
 * told where it is, updated, removed. The work on the server is a job of the exits service; the
 * page shows its steps and never waits for it.
 */
import { create } from "zustand";

import { api, ApiError, post } from "@/lib/api";
import { JobState, type Exit, type ExitNode, type ExitsRegistry } from "@/lib/types";
import type { MenuItem } from "@/ui/menu";
import { fail, toast } from "@/ui/toast";

import { isLocked, Lock, refreshState, withLock } from "./core";

interface ExitsState {
  registry: ExitsRegistry | null;
  /** The «new exit» form is open (the map opens it too). */
  formOpen: boolean;
  sharesOpen: boolean;
}

export const useExits = create<ExitsState>(() => ({ registry: null, formOpen: false, sharesOpen: false }));

const set = useExits.setState;

/** The server's own copy of the state is a few seconds old: ask again then. */
const SETTLE_MS = 6000;
export function refreshSoon(): void {
  void refreshState();
  setTimeout(() => void refreshState(), SETTLE_MS);
}

export async function loadExits(): Promise<void> {
  try {
    set({ registry: await api<ExitsRegistry>("/api/exits") });
  } catch {
    /* the table works without it */
  }
}

export const openExitForm = (open: boolean): void => set({ formOpen: open });
export const openShares = (open: boolean): void => set({ sharesOpen: open });

export const exitNode = (registry: ExitsRegistry | null, index: number): ExitNode | null =>
  registry?.nodes?.[index] ?? null;
/** Where an exit is (ISO 3166 code, lower case); null while the server does not know. */
export const exitCountry = (registry: ExitsRegistry | null, index: number): string | null =>
  registry?.countries?.[index] ?? null;

/** What the admin may answer instead of a code: the server finds the country again by the exit's address. */
export enum CountryChoice {
  Auto = "auto",
}

export const jobRunning = (registry: ExitsRegistry | null): boolean => registry?.job?.state === JobState.Running;

/** The shares of the exits in percent: whole numbers that add up to 100 (the largest takes what rounding leaves) — the same sum the server makes. */
export function sharesOf(exits: Exit[]): number[] {
  const weights = exits.map(exit => exit.weight || 0);
  const total = weights.reduce((sum, x) => sum + x, 0);
  if (!total) return weights.map(() => 0);
  const percents = weights.map(x => Math.round((100 * x) / total));
  percents[weights.indexOf(Math.max(...weights))] += 100 - percents.reduce((sum, x) => sum + x, 0);
  return percents;
}

/**
 * New shares of the exits, in percent, for `base` (the exits as the page showed them: the server refuses a change made
 * on older numbers). → whether they were saved; a refusal is shown.
 */
export async function saveShares(base: Exit[], percents: number[]): Promise<boolean> {
  const weights = Object.fromEntries(base.map((exit, index) => [exit.index, percents[index]]));
  const shown = Object.fromEntries(base.map(exit => [exit.index, exit.weight || 1]));
  try {
    return await exitCall("/api/exits/weights", { weights, base: shown }, "Доли сохранены");
  } catch (error) {
    fail(error);
    return false;
  }
}

/** One change of the exits; throws what the server refused. False when another change is still running. */
export async function exitCall(path: string, body: unknown, okText?: string): Promise<boolean> {
  if (isLocked(Lock.Users)) return false;
  try {
    await withLock(Lock.Users, () => post(path, body));
    if (okText) toast(okText);
    return true;
  } finally {
    void loadExits();
    refreshSoon();
  }
}

export function renameExit(exit: Exit): void {
  const value = prompt("Новое имя выхода (латиница, цифры, точка, дефис)", exit.name)?.trim();
  if (!value || value === exit.name) return;
  exitCall("/api/exits/rename", { index: exit.index, name: value }, "Выход переименован").catch(fail);
}

export async function setExitCountry(exit: Exit): Promise<void> {
  // the network page asks too: the registry is loaded first, so a known country is never offered as «auto»
  if (!useExits.getState().registry) await loadExits();
  const registry = useExits.getState().registry;
  if (!registry) {
    fail(new Error("Страна выхода сейчас неизвестна — попробуйте ещё раз"));
    return;
  }
  const known = exitCountry(registry, exit.index);
  const value = prompt(
    "Страна выхода: код из двух латинских букв (nl, de, fi…) или auto — найти по адресу сервера",
    known ?? CountryChoice.Auto,
  )
    ?.trim()
    .toLowerCase();
  if (!value || value === known) return;
  exitCall("/api/exits/country", { index: exit.index, country: value }, "Страна выхода сохранена").catch(fail);
}

export function updateExit(exit: Exit, reboot: boolean): void {
  if (
    reboot &&
    !confirm(
      "Обновить и перезагрузить «" + exit.name + "»? Его пользователи на несколько минут перейдут на другие выходы.",
    )
  )
    return;
  exitCall("/api/exits/update", { index: exit.index, reboot }, exit.name + ": обновление начато").catch(fail);
}

export async function removeExit(exit: Exit, force = false): Promise<void> {
  if (!force && !confirm("Удалить выход «" + exit.name + "»? Его пользователи перейдут на другие выходы.")) return;
  try {
    await exitCall("/api/exits/remove", { index: exit.index, force }, "Выход удалён");
  } catch (error) {
    const needsConfirm = error instanceof ApiError && (error.data as { confirm?: boolean } | null)?.confirm;
    if (needsConfirm && confirm(error.message + ". Всё равно удалить?")) return removeExit(exit, true);
    fail(error);
  }
}

/** What can be done with an exit: the same list on the map and in the table. */
export function exitItems(exit: Exit): MenuItem[] {
  const managed = !!exitNode(useExits.getState().registry, exit.index)?.managed;
  return [
    { label: "Переименовать", run: () => renameExit(exit) },
    { label: "Страна", run: () => void setExitCountry(exit) },
    ...(managed
      ? [
          { label: "Обновить", sub: "пакеты и настройки", run: () => updateExit(exit, false) },
          { label: "Обновить и перезагрузить", run: () => updateExit(exit, true) },
        ]
      : []),
    { label: "Удалить", danger: true, run: () => void removeExit(exit) },
  ];
}
