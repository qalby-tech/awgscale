"use client";

/** The blocks of «Обзор»: the exits, the people by group, the filter, the latest of the journal. */
import { useEffect, useState, type ReactNode } from "react";

import { api, isAuthError } from "@/lib/api";
import { fmtBps, num, plural, whenText } from "@/lib/format";
import { ExitState, Route, type PanelState } from "@/lib/types";
import { exitsOf, hrefOf, useCore, usersOf } from "@/store/core";
import { useUsers } from "@/store/users";
import { Dot, DotKind } from "@/ui/controls";
import { fail } from "@/ui/toast";

const NO_DATA = "—";
const PEOPLE: readonly [string, string, string] = ["человек", "человека", "человек"];
/** The ring of the people online: its radius and how thick it is, in the units of its 96×96 drawing. */
const RING_R = 40;
const RING_W = 10;
const RING_LENGTH = 2 * Math.PI * RING_R;
const JOURNAL_SHOWN = 5;
/** The latest of the journal is read again with every sixth answer of the state poll. */
const JOURNAL_EVERY_TICKS = 6;

export function Block({
  label,
  title,
  note,
  wide,
  id,
  children,
}: {
  label: string;
  title: string;
  note?: ReactNode;
  wide?: boolean;
  id?: string;
  children: ReactNode;
}) {
  return (
    <section className={"card block" + (wide ? " wide" : "")} aria-label={label} id={id}>
      <div className="block-head">
        <h2 className="block-title">{title}</h2>
        {note ? <span className="block-note">{note}</span> : null}
      </div>
      {children}
    </section>
  );
}

/** The exits: whether each works, how many people it has, its share of the new ones and its speed now. */
export function ExitsBlock({ state }: { state: PanelState | null }) {
  const exits = exitsOf(state);
  const single = !!state?.routing?.single_server;
  const up = exits.filter(exit => exit.state === ExitState.Up).length;
  const weights = exits.reduce((sum, exit) => sum + (exit.weight ?? 0), 0);
  return (
    <Block
      label="Выходы"
      title="Выходы"
      note={
        <>
          <span id="tExits">{state ? up + " из " + exits.length : NO_DATA}</span>
          {state && exits.length ? " работают" : ""}
        </>
      }
    >
      {single ? (
        <p className="muted block-text">Один сервер: весь трафик выходит с него самого.</p>
      ) : (
        <ul className="exits">
          {exits.map(exit => {
            const down = exit.state !== ExitState.Up;
            return (
              <li key={exit.index} className={"exit-row" + (down ? " down" : "")}>
                <div className="exit-top">
                  <Dot kind={down ? DotKind.Crit : DotKind.Good} />
                  <b>{exit.name}</b>
                  <span className="exit-speed">{down ? "не работает" : fmtBps(exit.down_bps)}</span>
                </div>
                <div className="exit-meta">
                  {(exit.users ?? 0) + " " + plural(exit.users ?? 0, PEOPLE)}
                  {weights > 0 ? " · доля " + num((100 * (exit.weight ?? 0)) / weights) + "%" : ""}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <a className="block-link" href={hrefOf(Route.Servers)}>
        {single ? "Добавить выход" : "Серверы"}
      </a>
    </Block>
  );
}

function Ring({ online, total }: { online: number; total: number }) {
  const share = total > 0 ? online / total : 0;
  return (
    <svg className="ring" width="96" height="96" viewBox="0 0 96 96" aria-hidden="true">
      <circle cx="48" cy="48" r={RING_R} className="ring-track" strokeWidth={RING_W} />
      {share > 0 ? (
        <circle
          cx="48"
          cy="48"
          r={RING_R}
          className="ring-on"
          strokeWidth={RING_W}
          strokeLinecap="round"
          strokeDasharray={RING_LENGTH * share + " " + RING_LENGTH}
          transform="rotate(-90 48 48)"
        />
      ) : null}
    </svg>
  );
}

/** The people: how many are online, in all and in each group. */
export function PeopleBlock({ state }: { state: PanelState | null }) {
  const counts = usersOf(state);
  const groups = useUsers(store => store.groups);
  const total = state?.totals?.clients ?? counts.users;
  const online = state?.totals?.online ?? counts.online;
  const nameOf = (id: string) => groups?.groups.find(group => group.id === id)?.name ?? "…";
  const rows = [
    ...(counts.groups ?? []).map(group => ({ key: group.id, name: nameOf(group.id), count: group })),
    ...(counts.ungrouped?.users ? [{ key: "", name: "Без группы", count: counts.ungrouped }] : []),
  ];
  return (
    <Block label="Люди" title="Люди" note={<a href={hrefOf(Route.Users)}>все {state ? total : ""}</a>}>
      <div className="people">
        <Ring online={state ? online : 0} total={state ? total : 0} />
        <div>
          <div className="big" id="tOnline">
            {state ? online + " из " + total : NO_DATA}
          </div>
          <div className="muted">онлайн сейчас</div>
        </div>
      </div>
      <ul className="people-groups">
        {rows.map(row => (
          <li key={row.key}>
            <span>{row.name}</span>
            <span className="muted">
              {row.count.online} / {row.count.users}
            </span>
          </li>
        ))}
      </ul>
    </Block>
  );
}

/** The filter: what share of the requests of the last day was stopped. */
export function FilterBlock({ state }: { state: PanelState | null }) {
  const dns = state?.dns ?? {};
  const queries = dns.queries ?? 0;
  const blocked = dns.blocked ?? 0;
  const share = dns.ok && queries ? (100 * blocked) / queries : 0;
  return (
    <Block label="Фильтр" title="Фильтр" note="за сутки">
      <div>
        <span className="big" id="tBlocked">
          {dns.ok ? (queries ? num(share, 1) : "0") + "%" : NO_DATA}
        </span>
        <span className="muted"> запросов остановлено</span>
      </div>
      <div className="share-bar" aria-hidden="true">
        <i style={{ width: Math.min(100, share) + "%" }} />
      </div>
      {dns.ok ? (
        <div className="kvs">
          <div>
            <span>Запросов</span>
            <b>{num(queries)}</b>
          </div>
          <div>
            <span>Остановлено</span>
            <b>{num(blocked)}</b>
          </div>
        </div>
      ) : (
        <p className="muted block-text">Блокировщик не ответил.</p>
      )}
    </Block>
  );
}

interface JournalItem {
  t: number;
  text: string;
  bad?: boolean;
}

function Latest({ items }: { items: JournalItem[] }) {
  if (!items.length) return <li className="muted">Изменений пока не было</li>;
  return (
    <>
      {items.map((item, index) => (
        <li key={index} className={item.bad ? "bad" : ""}>
          <span className="latest-when">{whenText(item.t * 1000)}</span>
          <span className="latest-text">{item.text}</span>
        </li>
      ))}
    </>
  );
}

/** The latest changes; the whole journal is a click away. */
export function JournalBlock() {
  const tick = useCore(state => state.tick);
  const [opened] = useState(tick);
  const [items, setItems] = useState<JournalItem[] | null>(null);

  useEffect(() => {
    if ((tick - opened) % JOURNAL_EVERY_TICKS) return;
    let alive = true;
    api<{ items: JournalItem[] }>("/api/journal").then(
      data => {
        if (alive) setItems(data.items.slice(0, JOURNAL_SHOWN));
      },
      error => {
        if (isAuthError(error)) fail(error);
      },
    );
    return () => {
      alive = false;
    };
  }, [tick, opened]);

  return (
    <Block label="Журнал" title="Журнал" note={<a href={hrefOf(Route.Journal)}>весь</a>} id="ovJournal">
      <ul className="latest">{items === null ? null : <Latest items={items} />}</ul>
    </Block>
  );
}
