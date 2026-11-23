/**
 * The rules as one list (GET /api/rules): every group of sites, whether it goes abroad, directly or is blocked, in
 * the order that decides — the first rule that names a site is the one that acts. Read for the canvas, and again
 * after «Применить».
 */
import { create } from "zustand";

import { api, isAuthError } from "@/lib/api";
import { fail } from "@/ui/toast";

/** Where a rule leads when it is on (stored with it). */
export enum RuleWay {
  Exit = "exit",
  Direct = "direct",
  Block = "block",
}

export interface Rule {
  id: string;
  name: string;
  on: boolean;
  route: RuleWay;
  items: string[];
  /** A list by link: its entries are the file's. */
  url?: string | null;
  /** The group whose networks are kept up to date by themselves. */
  auto?: string | null;
  /** The groups of people it is for; absent or empty: everybody. */
  groups?: string[];
}

/** Entries a rule above already names: {rule id: {entry: that rule's name}}. */
export type Covered = Record<string, Record<string, string>>;

interface RulesAnswer {
  rules: Rule[];
  covered: Covered;
  v: string;
}

interface RulesState {
  rules: Rule[] | null;
  covered: Covered;
  /** The version of the list as read: a change based on another one is refused. */
  version: string;
}

export const useRules = create<RulesState>(() => ({ rules: null, covered: {}, version: "" }));

export async function loadRules(): Promise<void> {
  try {
    const answer = await api<RulesAnswer>("/api/rules");
    useRules.setState({ rules: answer.rules, covered: answer.covered ?? {}, version: answer.v });
  } catch (error) {
    if (isAuthError(error)) fail(error);
  }
}

export const forgetRules = (): void => useRules.setState({ rules: null, covered: {}, version: "" });
