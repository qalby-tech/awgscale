"use client";

/**
 * «Пользователи → Список»: one card per group of users, then the users who
 * are in no group. A card says how many users it has (the server counts
 * them) and unfolds into rows, asked of the server a page at a time. A search
 * is made on the server too. A group switched to «Пространство» is a device
 * space of exactly its members.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import { pointerIsDown, typingIn } from "@/lib/pointer";
import { NO_GROUP_QUERY, type Client, type ClientsQuery, type UserGroup, type UserGroups } from "@/lib/types";
import { dropList, showList, showMore, useClients, useLists, type ClientList } from "@/store/clients";
import { exitsOf, groupCountOf, isLocked, Lock, nowOf, useCore, usersOf } from "@/store/core";
import { loadTraffic, useTraffic } from "@/store/traffic";
import { closeInvite, groupsOf, loadGroups, readOpenCards, saveUserGroups, useUsers, withTerm } from "@/store/users";
import { NameCard } from "@/ui/groups";
import { Icon, IconName } from "@/ui/Icon";
import { menuIsOpen } from "@/ui/menu";
import { PageHead } from "@/ui/PageHead";
import { USER_TABS } from "@/views/tabs";

import { CodePanel } from "./CodePanel";
import { GroupCard, NO_GROUP, type CardView } from "./GroupCard";
import { useHoldReload, useListSync } from "./hooks";

const LIST_ID = "userBlocks";
const GROUP_NAME_LENGTH = 40;
/** The search is asked of the server once the typing pauses for this long. */
const SEARCH_DELAY_MS = 150;
const SEARCH_LIST = "search";
const CODE_LIST = "code";
const NO_LIST: ClientList | undefined = undefined;

/** The list a card shows its users from. */
export const cardList = (key: string): string => "card:" + key;

const cardQuery = (key: string, flat: boolean): ClientsQuery => (flat ? {} : { group: key || NO_GROUP_QUERY });

interface ListInput {
  groups: UserGroup[];
  lists: Record<string, ClientList>;
  pending: string[];
  hidden: string[];
  openKeys: string[];
  /** The search, lower case. */
  query: string;
  adding: string | null;
  shown: (client: Client) => Client;
}

/** Everyone of a list, once all of them are read; null before. */
const allRead = (list: ClientList | undefined): string[] | null =>
  list && list.total != null && list.clients.length >= list.total ? list.clients.map(client => client.name) : null;

/** Who is in which card, and what of it a search leaves. */
function cardsOf({ groups, lists, pending, hidden, openKeys, query, adding, shown }: ListInput): CardView[] {
  const flat = !groups.length;
  const home = new Map<string, string>();
  groups.forEach(group => group.members.forEach(name => home.set(name, group.id ?? NO_GROUP)));
  const visible = (clients: Client[]) => clients.filter(client => !hidden.includes(client.name)).map(shown);
  const found = query ? lists[SEARCH_LIST] : NO_LIST;
  const card = (group: UserGroup | null): CardView => {
    const key = group?.id ?? NO_GROUP;
    const own = lists[cardList(key)];
    const waiting = pending.filter(name => (home.get(name) ?? NO_GROUP) === key);
    // a group found by its own name shows everyone
    const whole = !query || (!!group && group.name.toLowerCase().includes(query));
    const hits = visible((found?.clients ?? []).filter(client => flat || (client.group || NO_GROUP) === key));
    const rows = whole ? visible(own?.clients ?? []) : hits;
    const open = flat || openKeys.includes(key) || hits.length > 0;
    return {
      group,
      flat,
      open,
      rows,
      waiting,
      // «Без группы» shows only when someone is in it
      empty: !group && !flat && adding !== NO_GROUP,
      hidden: !!query && !whole && !hits.length,
      list: whole ? cardList(key) : null,
      more: whole && !!own && own.total != null && own.clients.length < own.total,
      // the names whose history makes the card's traffic: a group's members, or everyone in the card once all are read
      names: group ? group.members : allRead(own),
    };
  };
  return [...groups.map(card), card(null)];
}

/** The lists the cards need now: every unfolded card that shows all its users, «Без группы» always, the search. */
function neededLists(views: CardView[], query: string): Record<string, ClientsQuery> {
  const needed: Record<string, ClientsQuery> = {};
  views.forEach(view => {
    const key = view.group?.id ?? NO_GROUP;
    if (view.list && (view.open || !view.group)) needed[view.list] = cardQuery(key, view.flat);
  });
  if (query) needed[SEARCH_LIST] = { search: query };
  return needed;
}

/** What the section does when it opens and while it is open: reads the groups and the traffic, keeps the groups in step with other windows. */
function useUsersSection(creating: boolean): void {
  const version = useCore(core => core.state?.ugroups_v);
  const tick = useCore(core => core.tick);
  const held = useUsers(users => users.groups);

  useEffect(() => {
    readOpenCards();
    closeInvite();
    loadTraffic(true);
    void loadGroups();
  }, []);
  useEffect(() => {
    loadTraffic();
  }, [tick]);

  useListSync<UserGroups>({
    version,
    held: held?.v,
    loaded: !!held,
    busy: () => isLocked(Lock.Users) || menuIsOpen() || pointerIsDown() || creating || typingIn(LIST_ID),
    read: () => api<UserGroups>("/api/ugroups"),
    take: list => useUsers.setState({ groups: list, groupsFailed: false }),
  });
}

/** The search box: what is typed is asked of the server once the typing pauses. */
function UserSearch({ onSearch }: { onSearch: (query: string) => void }) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const typed = (text: string) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onSearch(text.trim().toLowerCase()), SEARCH_DELAY_MS);
  };
  return (
    <label className="search">
      <Icon name={IconName.Search} />
      <input
        id="userSearch"
        type="search"
        placeholder="Найти пользователя"
        aria-label="Найти пользователя"
        autoComplete="off"
        autoCapitalize="none"
        spellCheck={false}
        onInput={event => typed(event.currentTarget.value)}
      />
    </label>
  );
}

/** The user whose code is open: as a list on the page has them, or asked by name. */
function useCodeUser(name: string | null): Client | undefined {
  useEffect(() => {
    if (!name) return;
    showList(CODE_LIST, { names: [name] });
    return () => dropList(CODE_LIST);
  }, [name]);
  return useClients(store => {
    if (!name) return undefined;
    for (const list of Object.values(store.lists)) {
      const found = list.clients.find(client => client.name === name);
      if (found) return found;
    }
    return undefined;
  });
}

export function UsersPage() {
  const state = useCore(core => core.state);
  const traffic = useTraffic(history => history.data);
  const lists = useClients(store => store.lists);
  const { groups: held, groupsFailed, code, invite, pending, hidden, openCards: openKeys, termOver } = useUsers();
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const newName = useRef<HTMLInputElement>(null);
  useUsersSection(creating);
  useHoldReload(creating || adding != null || !!invite || !!code);

  const groups = groupsOf(held);
  const exits = exitsOf(state);
  // the card a field was open in, or the group an invitation was for, may be gone
  useEffect(() => {
    if (!held) return;
    if (adding && !groups.some(group => group.id === adding)) setAdding(null);
    if (invite?.groupId && !groups.some(group => group.id === invite.groupId)) closeInvite();
  }, [held, groups, adding, invite]);

  const cards = useMemo(
    () =>
      cardsOf({
        groups,
        lists,
        pending,
        hidden,
        openKeys,
        query,
        adding,
        shown: client => withTerm(client, termOver),
      }),
    [groups, lists, pending, hidden, openKeys, query, adding, termOver],
  );
  useLists(held || groupsFailed ? neededLists(cards, query) : {});
  const codeUser = useCodeUser(code?.name ?? null);
  const found = query ? lists[SEARCH_LIST] : NO_LIST;
  const exitName = (index: number) => exits.find(exit => exit.index === index)?.name ?? "—";

  const newGroup = () => {
    if (creating) {
      newName.current?.focus();
      return;
    }
    if (held) setCreating(true);
  };
  const create = (name: string) => {
    setCreating(false);
    void saveUserGroups(draft => {
      draft.unshift({ id: null, name, space: false, members: [] });
    });
  };
  const groupCard = (view: CardView) => {
    const key = view.group?.id ?? NO_GROUP;
    return (
      <GroupCard
        key={key}
        view={view}
        count={view.flat ? usersOf(state) : groupCountOf(state, key)}
        adding={adding === key}
        onAdding={open => setAdding(open ? key : null)}
        exitName={exitName}
        traffic={traffic}
        now={nowOf(state)}
        hasState={!!state}
        codeName={code?.name ?? null}
      />
    );
  };

  return (
    <>
      <PageHead title="Пользователи" tabs={USER_TABS}>
        <div className="actions">
          <button type="button" className="btn" id="ugAdd" onClick={newGroup}>
            <Icon name={IconName.Plus} stroke={2} />
            <span>Новая группа</span>
          </button>
        </div>
      </PageHead>
      <div className="split">
        <div className="grow ucol">
          <UserSearch onSearch={setQuery} />
          <div className="grid" id={LIST_ID}>
            {creating ? (
              <NameCard
                placeholder="Название группы"
                maxLength={GROUP_NAME_LENGTH}
                inputRef={newName}
                onCreate={create}
                onCancel={() => setCreating(false)}
              />
            ) : null}
            {held || groupsFailed ? cards.map(groupCard) : null}
          </div>
          {found && found.total != null && found.clients.length < found.total ? (
            <button type="button" className="btn more" id="searchMore" onClick={() => showMore(SEARCH_LIST)}>
              Показать ещё
            </button>
          ) : null}
        </div>
        <CodePanel code={code} invite={invite} user={codeUser} />
      </div>
    </>
  );
}
