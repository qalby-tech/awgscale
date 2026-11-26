/** What can be done with one user and with one group: the items of their menus. */
import { CodeTab, GroupFilter, KidsList, type Client, type UserGroup } from "@/lib/types";
import { exitLabel, exitsNow, groupCountOf, useCore } from "@/store/core";
import {
  FILTER_MODES,
  KIDS_LIST_NAMES,
  NO_DOH_NAME,
  filterGroup,
  groupUsers,
  moveUser,
  openCode,
  openInvite,
  pinGroup,
  pinUser,
  removeUser,
  revokeGroup,
  saveUserGroups,
  setPaused,
  setTerm,
  toggleKidsList,
  toggleNoDoh,
  type Term,
  TERMS,
  userGroups,
} from "@/store/users";
import { ddmm } from "@/lib/format";
import { openMenu, type MenuItem } from "@/ui/menu";

const termItems = (pick: (term: Term) => void): MenuItem[] =>
  TERMS.map(([term, label]) => ({ label, run: () => pick(term) }));

/** «Авто» and every exit; the one in force is marked. */
function exitChoice(current: number | null, pick: (index: number | null) => void): MenuItem[] {
  const mark = (on: boolean) => (on ? "сейчас" : "");
  return [
    { label: "Авто", sub: mark(current == null), run: () => pick(null) },
    ...exitsNow().map(exit => ({ label: exit.name, sub: mark(current === exit.index), run: () => pick(exit.index) })),
  ];
}

/** The modes of filtering a group is offered, in this order; `Off` is no longer one of them. */
const OFFERED_FILTERS = [
  GroupFilter.Standard,
  GroupFilter.Pro,
  GroupFilter.ProPlus,
  GroupFilter.Ultimate,
  GroupFilter.Kids,
];

const capitalized = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);

/** «Обычная», «Pro», «Pro++», «Ultimate», «Детская»; the one in force is marked. */
function filterChoice(current: GroupFilter, pick: (filter: GroupFilter) => void): MenuItem[] {
  return OFFERED_FILTERS.map(filter => ({
    label: capitalized(FILTER_MODES[filter]),
    sub: filter === current ? "сейчас" : "",
    run: () => pick(filter),
  }));
}

/** The add-on lists of the children's mode, each switched by a click; those on are marked. */
function kidsListChoice(group: UserGroup): MenuItem[] {
  const on = group.kids_lists ?? [];
  return Object.values(KidsList).map(kidsList => ({
    label: KIDS_LIST_NAMES[kidsList],
    sub: on.includes(kidsList) ? "вкл" : "",
    run: () => void toggleKidsList(group, kidsList),
  }));
}

/** A choice of exit is offered when there is one to make, or one to take back. */
const exitOffered = (current: number | null): boolean => exitsNow().length > 1 || current != null;

interface UserMenu {
  user: Client;
  group: UserGroup | null;
  /** The button the menu hangs from: the second-level menus open under it too. */
  anchor: () => HTMLElement | null;
}

export function userMenu({ user: client, group, anchor }: UserMenu): MenuItem[] {
  const under = (items: MenuItem[]) => {
    const active = anchor();
    if (active) openMenu(active, items);
  };
  const pin = client.pin ?? null;
  return [
    { label: "Проверить", run: () => void openCode(client.name, CodeTab.Diag) },
    {
      label: client.suspended ? "Возобновить" : "Приостановить",
      run: () => void setPaused([client.name], !client.suspended, client.name),
    },
    {
      label: "Срок",
      sub: client.expires_at ? "до " + ddmm(client.expires_at) : "",
      run: () => under(termItems(term => void setTerm([client.name], term, client.name))),
    },
    ...(exitOffered(pin)
      ? [
          {
            label: "Выход",
            sub: pin == null ? "авто" : exitLabel(pin),
            run: () => under(exitChoice(pin, index => void pinUser(client, index))),
          },
        ]
      : []),
    ...userGroups()
      .filter(other => !group || other.id !== group.id)
      .map(group => ({ label: "В группу «" + group.name + "»", run: () => void moveUser(client.name, group.id) })),
    ...(group ? [{ label: "Убрать из группы", run: () => void moveUser(client.name, null) }] : []),
    { label: "Удалить", danger: true, run: () => void removeUser(client.name) },
  ];
}

interface GroupMenu {
  group: UserGroup;
  anchor: () => HTMLElement | null;
  onRename: () => void;
}

/** One field of a group changed; the group is found by its id, the list may have been replaced meanwhile. */
const change = (group: UserGroup, edit: (group: UserGroup) => void) =>
  saveUserGroups(draft => {
    const same = draft.find(each => each.id === group.id);
    if (same) edit(same);
  });

export function groupMenu({ group, anchor, onRename }: GroupMenu): MenuItem[] {
  const under = (items: MenuItem[]) => {
    const active = anchor();
    if (active) openMenu(active, items);
  };
  const names = groupUsers(group);
  const pin = group.exit ?? null;
  const filter = group.filter || GroupFilter.Standard;
  // as the server counts the group's users
  const counted = groupCountOf(useCore.getState().state, group.id ?? "");
  const allPaused = counted.users > 0 && counted.paused === counted.users;
  const toggleSpace = () =>
    void change(group, group => {
      group.space = !group.space;
    });
  const remove = () => {
    if (!group.members.length || confirm("Удалить группу «" + group.name + "»? Пользователи останутся без группы."))
      void saveUserGroups(draft => {
        const index = draft.findIndex(each => each.id === group.id);
        if (index >= 0) draft.splice(index, 1);
      });
  };
  return [
    ...(names.length
      ? [{ label: "Срок для всех", run: () => under(termItems(term => void setTerm(names, term, group.name))) }]
      : []),
    { label: "Пригласить", run: () => void openInvite(group) },
    { label: "Переименовать", run: onRename },
    { label: group.space ? "Убрать пространство" : "Сделать пространством", run: toggleSpace },
    {
      label: "Фильтрация",
      sub: FILTER_MODES[filter],
      run: () => under(filterChoice(filter, chosen => void filterGroup(group, chosen))),
    },
    ...(filter === GroupFilter.Kids
      ? [{ label: "Списки детского режима", run: () => under(kidsListChoice(group)) }]
      : []),
    // no effect while nothing is filtered: not offered then
    ...(filter !== GroupFilter.Off
      ? [{ label: NO_DOH_NAME, sub: group.no_doh ? "вкл" : "", run: () => void toggleNoDoh(group) }]
      : []),
    ...(exitOffered(pin)
      ? [
          {
            label: "Выход",
            sub: pin == null ? "авто" : exitLabel(pin),
            run: () => under(exitChoice(pin, index => void pinGroup(group, index))),
          },
        ]
      : []),
    ...(names.length
      ? [
          {
            label: allPaused ? "Возобновить всех" : "Приостановить всех",
            run: () => void setPaused(names, !allPaused, group.name),
          },
        ]
      : []),
    ...(names.length ? [{ label: "Отозвать доступ", danger: true, run: () => void revokeGroup(group, names) }] : []),
    { label: "Удалить группу", danger: true, run: remove },
  ];
}
