"use client";

/** One card of the list: a group of users (or those in no group) that unfolds into rows. */
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type MouseEvent } from "react";

import { errorText, isAuthError } from "@/lib/api";
import { ago, ddmm, fmtBytes } from "@/lib/format";
import { Presence, presenceOf } from "@/lib/presence";
import { GroupFilter, TrafficSpan, type Client, type Count, type Traffic, type UserGroup } from "@/lib/types";
import { fetchPage, findClients, showMore } from "@/store/clients";
import { exitLabel, isLocked, Lock, refreshState } from "@/store/core";
import { trafficTitle, trafficTotal } from "@/store/traffic";
import {
  closeCode,
  createUser,
  moveUser,
  NAME_RULE,
  NO_DOH_HINT,
  openCode,
  saveUserGroups,
  setCardOpen,
  openCards,
  userGroups,
  useUsers,
  validUserName,
} from "@/store/users";
import { Dot, DotKind, IconButton } from "@/ui/controls";
import { RenameInput } from "@/ui/groups";
import { Icon, IconName } from "@/ui/Icon";
import { menuButton } from "@/ui/menu";
import { fail } from "@/ui/toast";

import { groupMenu, userMenu } from "./menus";
import { Key } from "@/lib/dom";

/** How the filtering of a group is named on its badge. */
const FILTER_NAMES: Record<GroupFilter, string> = {
  [GroupFilter.Standard]: "обычная",
  [GroupFilter.Off]: "без блокировки",
  [GroupFilter.Kids]: "детский режим",
  [GroupFilter.Pro]: "Pro",
  [GroupFilter.ProPlus]: "Pro++",
  [GroupFilter.Ultimate]: "Ultimate",
};
const GROUP_NAME_LENGTH = 40;
const USER_NAME_LENGTH = 32;
const COLUMNS = 5;
export const ADD_INPUT_ID = "ugAddInput";

/** The key of the card of those in no group. */
export const NO_GROUP = "";

const sinceStart = (client: Client): number => (client.rx || 0) + (client.tx || 0);

function Status({ user: client, now }: { user: Client; now: number }) {
  switch (presenceOf(client)) {
    case Presence.Paused:
      return (
        <span className="st paused">
          <Icon name={IconName.Pause} size={14} stroke={2.4} />
          пауза
        </span>
      );
    case Presence.Online:
      return (
        <span className="st">
          <Dot />
          онлайн
        </span>
      );
    case Presence.Seen:
      return (
        <span className="st off">
          <Dot kind={DotKind.Off} />
          {ago(client.handshake, now)}
        </span>
      );
    case Presence.Never:
      return (
        <span className="st off">
          <Dot kind={DotKind.Off} />
          не подключался
        </span>
      );
  }
}

interface UserRowProps {
  user: Client;
  group: UserGroup | null;
  exitName: string;
  traffic: Traffic | null;
  now: number;
  selected: boolean;
}

function UserRow({ user: client, group, exitName, traffic, now, selected }: UserRowProps) {
  const more = useRef<HTMLButtonElement>(null);
  const month = trafficTotal(traffic, [client.name], TrafficSpan.Month);
  const total = month ?? sinceStart(client);
  return (
    <tr className={selected ? "sel" : ""}>
      <td className="c-name">
        <div className="name-cell">
          <span className="uname" title={client.name}>
            {client.name}
          </span>
          {client.expires_at ? <span className="badge">{"до " + ddmm(client.expires_at)}</span> : null}
        </div>
        <div className="c-narrow">
          <Status user={client} now={now} />
        </div>
      </td>
      <td className="c-wide c-st">
        <Status user={client} now={now} />
      </td>
      <td className="ink2 c-wide c-exit">{exitName}</td>
      <td className={"c-wide c-traf num" + (total ? "" : " muted")} title={trafficTitle(month)}>
        {total ? fmtBytes(total) : "—"}
      </td>
      <td className="act">
        <div className="act-in">
          <button
            type="button"
            className={"btn" + (selected ? " pressed" : "")}
            aria-pressed={selected}
            data-k={client.name + ":code"}
            onClick={() => (selected ? closeCode() : void openCode(client.name))}
          >
            Код
          </button>
          <IconButton
            ref={more}
            icon={IconName.More}
            label={"Действия с пользователем " + client.name}
            data-k={client.name + ":more"}
            {...menuButton(() => userMenu({ user: client, group, anchor: () => more.current }))}
          />
        </div>
      </td>
    </tr>
  );
}

/** A user created a moment ago: the server's list does not have it yet. */
function WaitRow({ name }: { name: string }) {
  return (
    <tr>
      <td className="c-name">
        <div className="name-cell">
          <span className="uname">{name}</span>
        </div>
      </td>
      <td className="muted c-wide c-st">создаётся</td>
      <td className="muted c-wide c-exit">—</td>
      <td className="num muted c-wide c-traf">—</td>
      <td className="act" />
    </tr>
  );
}

interface AddRowProps {
  group: UserGroup | null;
  onClose: () => void;
}

/** Is there a user of this name: on the server (and not removed a moment ago), or created a moment ago. Asked by name. */
async function userExists(name: string): Promise<boolean> {
  const { hidden, pending } = useUsers.getState();
  if (pending.includes(name)) return true;
  const found = await findClients([name]);
  return found.some(client => client.name === name && !hidden.includes(name));
}

const OFFERED_NAMES = 20;
const OFFER_DELAY_MS = 150;

/** The names offered under the field of a group: those found on the server by what is typed, not yet in the group. */
function useOffered(group: UserGroup | null): [string[], (typed: string) => void] {
  const [offered, setOffered] = useState<string[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const typed = (text: string) => {
    if (!group) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      fetchPage({ search: text.trim() }, 0, OFFERED_NAMES).then(
        page => {
          const hidden = useUsers.getState().hidden;
          setOffered(
            page.clients
              .map(client => client.name)
              .filter(name => !hidden.includes(name) && !group.members.includes(name)),
          );
        },
        () => setOffered([]),
      );
    }, OFFER_DELAY_MS);
  };
  return [offered, typed];
}

/** Why this name cannot be added to the card, or null when it can. */
function refusalOf(name: string, group: UserGroup | null, exists: boolean): string | null {
  if (!validUserName(name)) return NAME_RULE;
  if (!exists) return null;
  if (!group) return "Такой пользователь уже есть";
  return group.members.includes(name) ? "Уже в этой группе" : null;
}

/** Moves the user into the group. A new user is moved once more when the first save was refused as stale: the refusal brought the list as it is now. */
async function putIntoGroup(name: string, groupId: string | null, isNew: boolean): Promise<void> {
  const moved = await moveUser(name, groupId);
  if (!moved && isNew) await moveUser(name, groupId);
}

/** The field under a card's «+»: a new name is created right in this group, an existing user moves here. */
function AddRow({ group: shown, onClose }: AddRowProps) {
  const field = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  const [offered, offerFor] = useOffered(shown);
  useEffect(() => {
    field.current?.focus();
    offerFor("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const refuse = (text: string) => {
    setError(text);
    field.current?.focus();
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const input = field.current;
    if (!input || isLocked(Lock.Users)) return;
    const name = input.value.trim();
    const group = shown ? userGroups().find(each => each.id === shown.id) : null;
    if (shown && !group) return;
    let exists: boolean;
    try {
      exists = await userExists(name);
    } catch (error) {
      if (isAuthError(error)) fail(error);
      else setError(errorText(error));
      return;
    }
    const refusal = refusalOf(name, group ?? null, exists);
    if (refusal) return refuse(refusal);
    if (!exists) {
      try {
        await createUser(name);
      } catch (error) {
        if (isAuthError(error)) fail(error);
        else setError(errorText(error));
        return;
      }
    }
    input.value = "";
    if (group) await putIntoGroup(name, group.id, !exists);
    if (exists) {
      field.current?.focus();
      return;
    }
    void openCode(name);
    void refreshState();
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.key === Key.Escape) {
      event.preventDefault();
      onClose();
    }
  };

  return (
    <div className="addrow">
      <form className="row" onSubmit={submit}>
        <input
          id={ADD_INPUT_ID}
          ref={field}
          className="input code"
          type="text"
          placeholder="Имя"
          maxLength={USER_NAME_LENGTH}
          list={shown ? "ugAddList" : undefined}
          aria-label={shown ? "Имя пользователя для «" + shown.name + "»" : "Имя нового пользователя"}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          onInput={event => {
            setError("");
            offerFor(event.currentTarget.value);
          }}
          onKeyDown={onKey}
        />
        <button className="btn primary" type="submit">
          Добавить
        </button>
      </form>
      <datalist id="ugAddList">{shown ? offered.map(name => <option key={name} value={name} />) : null}</datalist>
      <div className="err" id="ugAddErr" role="alert">
        {error}
      </div>
    </div>
  );
}

export interface CardView {
  group: UserGroup | null;
  /** No groups at all: one plain list, as before there were groups. */
  flat: boolean;
  open: boolean;
  /** A search left nothing of it. */
  hidden: boolean;
  /** «Без группы»: not shown while nobody is in it. */
  empty: boolean;
  /** The users the unfolded card lists, as far as they are read (a search narrows them). */
  rows: Client[];
  /** Created a moment ago, not yet in the server's answer. */
  waiting: string[];
  /** The list the rows come from (store/clients), when the card shows all its users. */
  list: string | null;
  /** The server has more of them than are read: «Показать ещё». */
  more: boolean;
  /** Whose history makes the card's traffic; null when not all of them are known. */
  names: string[] | null;
}

interface GroupCardProps {
  view: CardView;
  /** How many users the card has, as the server counts them. */
  count: Count;
  adding: boolean;
  onAdding: (open: boolean) => void;
  exitName: (index: number) => string;
  traffic: Traffic | null;
  now: number;
  hasState: boolean;
  codeName: string | null;
}

/** How a card is called: by its group; the card of everybody else is the whole list when there are no groups. */
function cardName(group: UserGroup | null, flat: boolean): string {
  if (group) return group.name;
  return flat ? "Пользователи" : "Без группы";
}

/** The group at a glance: how many, how many online (as the server counts them), and the traffic of all of them — over 30 days, or since the tunnel started while there is no history. */
function summaryOf(view: CardView, counted: Count, traffic: Traffic | null) {
  const total = counted.users;
  const online = counted.online;
  const month = view.names ? trafficTotal(traffic, view.names, TrafficSpan.Month) : null;
  const bytes = month ?? (counted.rx || 0) + (counted.tx || 0);
  const count = total ? [String(total), online ? online + " онлайн" : ""].filter(Boolean).join(" · ") : "";
  return { count, month, bytes };
}

/** What a group is marked with: the exit it is kept on, its filtering when that is not the usual one, «Без чужого DNS». */
function GroupBadges({ group }: { group: UserGroup }) {
  const filtered = !!group.filter && group.filter !== GroupFilter.Standard;
  return (
    <>
      {group.exit != null ? (
        <span className="badge" title="Группа закреплена за этим выходом">
          {exitLabel(group.exit)}
        </span>
      ) : null}
      {filtered && group.filter ? (
        <span className="badge" title="Фильтрация для этой группы">
          {FILTER_NAMES[group.filter] ?? group.filter}
        </span>
      ) : null}
      {group.no_doh && group.filter !== GroupFilter.Off ? (
        <span className="badge" title={NO_DOH_HINT}>
          без чужого DNS
        </span>
      ) : null}
    </>
  );
}

interface GroupButtonsProps {
  group: UserGroup;
  adding: boolean;
  onToggleAdd: () => void;
  onToggleSpace: () => void;
  onRename: () => void;
}

/** The buttons at the right of a group's head: «Пространство», «+» and the menu of actions. */
function GroupButtons({ group, adding, onToggleAdd, onToggleSpace, onRename }: GroupButtonsProps) {
  const more = useRef<HTMLButtonElement>(null);
  const actions = () => groupMenu({ group, anchor: () => more.current, onRename });
  return (
    <>
      <button
        type="button"
        className={"btn small" + (group.space ? " pressed" : "")}
        aria-pressed={!!group.space}
        onClick={onToggleSpace}
      >
        Пространство
      </button>
      <IconButton
        icon={IconName.Plus}
        label={"Добавить в «" + group.name + "»"}
        aria-expanded={adding}
        onClick={onToggleAdd}
      />
      <IconButton ref={more} icon={IconName.More} label={"Действия: " + group.name} {...menuButton(actions)} />
    </>
  );
}

interface CardRowsProps {
  view: GroupCardProps["view"];
  exitName: GroupCardProps["exitName"];
  traffic: Traffic | null;
  now: number;
  hasState: boolean;
  codeName: string | null;
}

/** The rows of an unfolded card: its users, those being created, or a note when the whole list is empty. */
function CardRows({ view, exitName, traffic, now, hasState, codeName }: CardRowsProps) {
  const { group, flat, rows: listed, waiting } = view;
  const shownNames = new Set(listed.map(client => client.name));
  const creating = waiting.filter(name => !shownNames.has(name));
  const empty = !listed.length && !creating.length && flat;
  return (
    <div className="table-wrap">
      <table className="t-users" aria-label="Пользователи">
        <tbody>
          {listed.map(client => (
            <UserRow
              key={client.name}
              user={client}
              group={group}
              exitName={client.exit != null ? exitName(client.exit) : "—"}
              traffic={traffic}
              now={now}
              selected={codeName === client.name}
            />
          ))}
          {creating.map(name => (
            <WaitRow key={"wait:" + name} name={name} />
          ))}
          {empty ? (
            <tr key="none">
              <td colSpan={COLUMNS} className="muted">
                {hasState ? "Пользователей пока нет" : "Нет данных"}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
      {view.more && view.list ? (
        <button type="button" className="btn more" onClick={() => view.list && showMore(view.list)}>
          Показать ещё
        </button>
      ) : null}
    </div>
  );
}

export function GroupCard({
  view,
  count,
  adding,
  onAdding,
  exitName,
  traffic,
  now,
  hasState,
  codeName,
}: GroupCardProps) {
  const { group, flat, open, waiting } = view;
  const key = group?.id ?? NO_GROUP;
  const name = cardName(group, flat);
  const foldButton = useRef<HTMLButtonElement>(null);
  const [renaming, setRenaming] = useState(false);
  const summary = summaryOf(view, count, traffic);
  const nobody = view.empty && !count.users && !waiting.length;

  const fold = () => {
    setCardOpen(key, !openCards.has(key));
    foldButton.current?.focus({ preventScroll: true });
  };
  // the whole line folds and unfolds, not only the arrow
  const onHeadClick = (event: MouseEvent) => {
    if (!(event.target as Element).closest("button,input,a,select")) fold();
  };
  const toggleAdd = () => onAdding(!adding);
  const save = (edit: (group: UserGroup) => void) =>
    void saveUserGroups(draft => {
      const same = draft.find(each => each.id === group?.id);
      if (same) edit(same);
    });
  const toggleSpace = () =>
    save(changed => {
      changed.space = !changed.space;
    });
  const finishRenaming = (next: string | null) => {
    setRenaming(false);
    if (!next) return;
    save(renamed => {
      renamed.name = next;
    });
  };

  return (
    <section
      className={"card group ublock" + (open ? " open" : "")}
      data-g={key}
      hidden={view.hidden || nobody}
      aria-label={group ? "Группа " + group.name : name}
    >
      <div className="group-head" onClick={onHeadClick}>
        {renaming && group ? (
          <RenameInput value={group.name} maxLength={GROUP_NAME_LENGTH} onDone={finishRenaming} />
        ) : (
          <h2 className="group-name" title={name}>
            <button
              type="button"
              ref={foldButton}
              className="fold"
              aria-expanded={open}
              data-k={"fold:" + key}
              onClick={fold}
            >
              <Icon name={IconName.Chevron} size={16} stroke={2.2} />
              <span>{name}</span>
            </button>
          </h2>
        )}
        <span className="group-count">{summary.count}</span>
        {group ? <GroupBadges group={group} /> : null}
        <span className="spacer" />
        <span className="group-traf" title={trafficTitle(summary.month)}>
          {summary.bytes ? fmtBytes(summary.bytes) : ""}
        </span>
        {group ? (
          <GroupButtons
            group={group}
            adding={adding}
            onToggleAdd={toggleAdd}
            onToggleSpace={toggleSpace}
            onRename={() => setRenaming(true)}
          />
        ) : (
          <IconButton icon={IconName.Plus} label="Новый пользователь" aria-expanded={adding} onClick={toggleAdd} />
        )}
      </div>
      {open ? (
        <CardRows view={view} exitName={exitName} traffic={traffic} now={now} hasState={hasState} codeName={codeName} />
      ) : null}
      {adding ? <AddRow group={group} onClose={() => onAdding(false)} /> : null}
    </section>
  );
}
