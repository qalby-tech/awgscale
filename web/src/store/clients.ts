/**
 * The users, a page at a time. The state of the cascade only counts them; a
 * section that shows users asks the server for the pages it shows (searched,
 * filtered and ordered there), and they are asked again with every poll while
 * they are on the page. Each list has a key of its own: a card of the list, a
 * search, a group unfolded on the map.
 */
import { useEffect, useRef } from "react";
import { create } from "zustand";

import { api } from "@/lib/api";
import { type Client, type ClientsPage, type ClientsQuery } from "@/lib/types";

import { onLogout, useCore } from "./core";

/** How many users a list shows at first, and how many more «Показать ещё» brings. */
export const PAGE_SIZE = 50;
/** The most the server gives in one answer. */
const MAX_PAGE = 100;

export interface ClientList {
  query: ClientsQuery;
  clients: Client[];
  /** How many users fit the query in all; null until the first answer. */
  total: number | null;
  loading: boolean;
  failed: boolean;
}

export const useClients = create<{ lists: Record<string, ClientList> }>(() => ({ lists: {} }));

const set = useClients.setState;
const get = useClients.getState;

/** Every list is asked anew by its own turn; a later answer to an older question is not taken. */
const turns = new Map<string, number>();
const nextTurn = (key: string): number => {
  const turn = (turns.get(key) ?? 0) + 1;
  turns.set(key, turn);
  return turn;
};

function pathOf(query: ClientsQuery, offset: number, limit: number): string {
  const params = new URLSearchParams();
  if (query.search) params.set("q", query.search);
  if (query.group != null) params.set("group", query.group);
  if (query.exit != null) params.set("exit", String(query.exit));
  if (query.status) params.set("status", query.status);
  if (query.sort) params.set("sort", query.sort);
  if (query.desc) params.set("desc", "true");
  (query.names ?? []).forEach(name => params.append("names", name));
  params.set("offset", String(offset));
  params.set("limit", String(limit));
  return "/api/clients?" + params.toString();
}

/** One page of users as the server answers it. */
export const fetchPage = (query: ClientsQuery, offset: number, limit: number): Promise<ClientsPage> =>
  api<ClientsPage>(pathOf(query, offset, limit));

/** The first `count` users of a query, in pages the server gives. */
async function fetchFirst(query: ClientsQuery, count: number): Promise<ClientsPage> {
  const first = await fetchPage(query, 0, Math.min(count, MAX_PAGE));
  const clients = [...first.clients];
  while (clients.length < Math.min(count, first.total)) {
    const next = await fetchPage(query, clients.length, Math.min(count - clients.length, MAX_PAGE));
    if (!next.clients.length) break;
    clients.push(...next.clients);
  }
  return { total: first.total, offset: 0, clients };
}

const sameQuery = (left: ClientsQuery, right: ClientsQuery): boolean => JSON.stringify(left) === JSON.stringify(right);

function patchList(key: string, patch: Partial<ClientList>): void {
  set(state => (state.lists[key] ? { lists: { ...state.lists, [key]: { ...state.lists[key], ...patch } } } : state));
}

async function load(key: string, count: number): Promise<void> {
  const list = get().lists[key];
  if (!list) return;
  const turn = nextTurn(key);
  patchList(key, { loading: true });
  try {
    const page = await fetchFirst(list.query, count);
    if (turns.get(key) === turn)
      patchList(key, { clients: page.clients, total: page.total, loading: false, failed: false });
  } catch {
    if (turns.get(key) === turn) patchList(key, { loading: false, failed: true });
  }
}

/** A list is on the page: its first page is asked for (again only when the query changed). */
export function showList(key: string, query: ClientsQuery): void {
  const held = get().lists[key];
  if (held && sameQuery(held.query, query)) return;
  set(state => ({
    lists: { ...state.lists, [key]: { query, clients: [], total: null, loading: true, failed: false } },
  }));
  void load(key, PAGE_SIZE);
}

/** «Показать ещё»: the next page joins the list. */
export function showMore(key: string): void {
  const list = get().lists[key];
  if (!list || list.loading) return;
  void load(key, list.clients.length + PAGE_SIZE);
}

/** The list is no longer on the page: it is not asked for any more. */
export function dropList(key: string): void {
  turns.delete(key);
  set(state => {
    if (!state.lists[key]) return state;
    return { lists: Object.fromEntries(Object.entries(state.lists).filter(([other]) => other !== key)) };
  });
}

/** Every list on the page, asked anew for as many users as it shows (with every poll: those not still being read). */
export async function refreshLists(evenLoading = true): Promise<void> {
  await Promise.all(
    Object.entries(get().lists)
      .filter(([, list]) => evenLoading || !list.loading)
      .map(([key, list]) => load(key, Math.max(PAGE_SIZE, list.clients.length))),
  );
}

/** A user as one of the lists on the page has them. */
export function clientNamed(name: string): Client | undefined {
  for (const list of Object.values(get().lists)) {
    const found = list.clients.find(client => client.name === name);
    if (found) return found;
  }
  return undefined;
}

/** Exactly these users, asked of the server (a hundred at a time). */
export async function findClients(names: string[]): Promise<Client[]> {
  const found: Client[] = [];
  for (let start = 0; start < names.length; start += MAX_PAGE) {
    const chunk = names.slice(start, start + MAX_PAGE);
    found.push(...(await fetchPage({ names: chunk }, 0, chunk.length)).clients);
  }
  return found;
}

/** Shown at once after a change; the server's own answer follows with the next poll. */
export function patchClients(names: string[], patch: Partial<Client>): void {
  set(state => ({
    lists: Object.fromEntries(
      Object.entries(state.lists).map(([key, list]) => [
        key,
        {
          ...list,
          clients: list.clients.map(client => (names.includes(client.name) ? { ...client, ...patch } : client)),
        },
      ]),
    ),
  }));
}

/** Keeps exactly these lists on the page; those no longer needed are dropped. */
export function useLists(needed: Record<string, ClientsQuery>): void {
  const shownKeys = useRef<string[]>([]);
  const wanted = JSON.stringify(needed);
  useEffect(() => {
    const lists: Record<string, ClientsQuery> = JSON.parse(wanted);
    Object.entries(lists).forEach(([key, query]) => showList(key, query));
    shownKeys.current.filter(key => !(key in lists)).forEach(dropList);
    shownKeys.current = Object.keys(lists);
  }, [wanted]);
  useEffect(
    () => () => {
      shownKeys.current.forEach(dropList);
      shownKeys.current = [];
    },
    [],
  );
}

// every poll brings the lists on the page up to date
useCore.subscribe((state, previous) => {
  if (state.tick !== previous.tick && state.state) void refreshLists(false);
});
onLogout(() => {
  turns.clear();
  set({ lists: {} });
});
