"use client";

/** What is known about the thing selected on the map. */
import type { ReactNode } from "react";

import { ago, ddmm, fmtBps, fmtBytes, hostOf } from "@/lib/format";
import { Presence, presenceOf } from "@/lib/presence";
import { CodeTab, ExitState, Route, TrafficSpan, type Client, type Exit, type Speed, type Traffic } from "@/lib/types";
import { hrefOf, navigate } from "@/store/core";
import { exitItems } from "@/store/exits";
import { trafficText, trafficTotal } from "@/store/traffic";
import { openCode, setCardOpen } from "@/store/users";
import { IconButton } from "@/ui/controls";
import { IconName } from "@/ui/Icon";
import { menuButton } from "@/ui/menu";
import { PresenceMark } from "@/ui/PresenceMark";

import { BUSY_BPS, rateOf, type Bucket, type People } from "./mapLayout";
import { keyOf, kindOf, mapSelect, SelKind, userSel, type Selection } from "./selection";
import { TrafficBars } from "./TrafficBars";

/** The thing a selection points at, when it is still there. */
export type Selected =
  { kind: SelKind.Group; bucket: Bucket } | { kind: SelKind.User; client: Client } | { kind: SelKind.Exit; exit: Exit };

export function findSelected(selection: Selection | null, people: People, exits: Exit[]): Selected | null {
  if (!selection) return null;
  const key = keyOf(selection);
  switch (kindOf(selection)) {
    case SelKind.Group: {
      const bucket = people.left.find(bucket => bucket.key === key);
      return bucket ? { kind: SelKind.Group, bucket } : null;
    }
    case SelKind.User: {
      const client = people.clients.find(client => client.name === key);
      return client ? { kind: SelKind.User, client } : null;
    }
    case SelKind.Exit: {
      const exit = exits.find(exit => String(exit.index) === key);
      return exit ? { kind: SelKind.Exit, exit } : null;
    }
    default:
      return null;
  }
}

/** What the panel needs besides the selected thing itself. */
interface Around {
  people: People;
  exits: Exit[];
  traffic: Traffic | null;
  /** The server's clock, seconds. */
  now: number;
  /** Grows with every poll: the rows are written anew each time, the chart is not. */
  onClose: () => void;
}

const SPANS = [
  ["за сутки", TrafficSpan.Day],
  ["за 30 дней", TrafficSpan.Month],
] as const;

const speedText = (speed: Speed): string => "↓ " + fmtBps(speed.down_bps) + "  ↑ " + fmtBps(speed.up_bps);
const onlineFirst = (left: Client, right: Client): number => (right.online ? 1 : 0) - (left.online ? 1 : 0);
const exitNameIn = (exits: Exit[], index: number): string => exits.find(exit => exit.index === index)?.name ?? "—";

function Kv({ label, value, bad, title }: { label: string; value: string; bad?: boolean; title?: string }) {
  return (
    <div className="kv" title={title}>
      <span>{label}</span>
      <b className={bad ? "bad" : ""}>{value}</b>
    </div>
  );
}

function Head({ title, sub, onClose }: { title: string; sub?: string; onClose: () => void }) {
  return (
    <div className="panel-head">
      <div className="panel-title">
        <h2>{title}</h2>
        {sub ? <span className="mono ink2">{sub}</span> : null}
      </div>
      <IconButton icon={IconName.X} label="Закрыть" onClick={onClose} />
    </div>
  );
}

/** The traffic of these users over the last day and over 30 days. */
function history(around: Around, names: string[], prefix?: string, title?: string): ReactNode[] {
  return SPANS.map(([text, span]) => (
    <Kv
      key={span}
      label={prefix ? prefix + " " + text : "За" + text.slice(2)}
      value={trafficText(trafficTotal(around.traffic, names, span))}
      title={title}
    />
  ));
}

/** What a line of the list says about a user: paused, how fast, or when last seen. */
function lineNote(client: Client, now: number): string {
  switch (presenceOf(client)) {
    case Presence.Paused:
      return "пауза";
    case Presence.Online:
      return rateOf(client) > BUSY_BPS ? fmtBps(rateOf(client)) : "онлайн";
    case Presence.Seen:
      return ago(client.handshake, now);
    case Presence.Never:
      return "не подключался";
  }
}

/** The status of a user in words. */
function statusOf(client: Client, now: number): string {
  switch (presenceOf(client)) {
    case Presence.Paused:
      return "пауза";
    case Presence.Online:
      return "онлайн";
    case Presence.Seen:
      return "был " + ago(client.handshake, now);
    case Presence.Never:
      return "не подключался";
  }
}

function UserLine({ client, now }: { client: Client; now: number }) {
  const note = lineNote(client, now);
  return (
    <button
      type="button"
      className={"side-user" + (client.online ? "" : " off")}
      data-k={"s:" + client.name}
      onClick={() => mapSelect(userSel(client.name))}
    >
      <PresenceMark presence={presenceOf(client)} />
      <span>{client.name}</span>
      <small>{note}</small>
    </button>
  );
}

const UserList = ({ list, now }: { list: Client[]; now: number }) => (
  <div className="side-list">
    {list.map(client => (
      <UserLine key={client.name} client={client} now={now} />
    ))}
  </div>
);

function GroupSide({ bucket, around }: { bucket: Bucket; around: Around }) {
  const on = bucket.list.filter(client => client.online);
  const names = bucket.names ?? bucket.list.map(client => client.name);
  const byRate = bucket.list
    .slice()
    .sort(
      (left, right) => onlineFirst(left, right) || rateOf(right) - rateOf(left) || left.name.localeCompare(right.name),
    );
  const used = [...new Set(on.flatMap(client => (client.exit == null ? [] : [exitNameIn(around.exits, client.exit)])))];
  return (
    <>
      <Head title={bucket.name} onClose={around.onClose} />
      <Kv label="Онлайн" value={bucket.count.online + " из " + bucket.count.users} />
      <Kv label="Сейчас" value={speedText(bucket.count)} />
      <Kv label="Выход" value={used.length ? used.join(", ") : "—"} />
      <Kv label="С запуска туннеля" value={fmtBytes((bucket.count.rx || 0) + (bucket.count.tx || 0))} />
      {history(around, names)}
      <TrafficBars data={around.traffic} names={names} />
      <UserList list={byRate} now={around.now} />
      <div className="row">
        <a className="btn" href={hrefOf(Route.Users)} onClick={() => setCardOpen(bucket.key, true)}>
          Открыть в списке
        </a>
      </div>
    </>
  );
}

function UserSide({ client, around }: { client: Client; around: Around }) {
  const status = statusOf(client, around.now);
  const exit =
    client.exit != null
      ? exitNameIn(around.exits, client.exit) + (client.pin != null && client.pin === client.exit ? " · вручную" : "")
      : "—";
  const check = () => {
    navigate(Route.Users);
    setTimeout(() => void openCode(client.name, CodeTab.Diag), 0);
  };
  return (
    <>
      <Head title={client.name} sub={client.ip} onClose={around.onClose} />
      <Kv label="Статус" value={status} bad={presenceOf(client) === Presence.Paused} />
      <Kv label="Выход" value={exit} />
      <Kv label="Сейчас" value={client.online ? speedText(client) : "—"} />
      <Kv label="С запуска туннеля" value={fmtBytes((client.rx || 0) + (client.tx || 0))} />
      {history(around, [client.name])}
      <TrafficBars data={around.traffic} names={[client.name]} />
      {client.expires_at ? <Kv label="Срок" value={"до " + ddmm(client.expires_at)} /> : null}
      <div className="row">
        <button type="button" className="btn" onClick={check}>
          Проверить
        </button>
      </div>
    </>
  );
}

function ExitSide({ exit, around }: { exit: Exit; around: Around }) {
  const mine = around.people.clients.filter(client => client.exit === exit.index);
  const up = exit.state === ExitState.Up;
  return (
    <>
      <Head title={exit.name} sub={hostOf(exit.endpoint)} onClose={around.onClose} />
      <Kv label="Статус" value={up ? "работает" : "недоступен"} bad={!up} />
      <Kv label="Сейчас" value={speedText(exit)} />
      <Kv label="Связь" value={exit.handshake ? ago(exit.handshake, around.now) : "—"} />
      <Kv label="Пользователи" value={(exit.online ?? 0) + " онлайн из " + (exit.users ?? 0)} />
      {/* the history is kept per user, not per exit: these two are of the users on the exit now (as far as they are read), and are named so */}
      {history(
        around,
        mine.map(client => client.name),
        "Их трафик",
        "Трафик пользователей, которые сейчас на этом выходе",
      )}
      <div className="row">
        <button type="button" className="btn" data-k={"xa:" + exit.index} {...menuButton(() => exitItems(exit))}>
          Действия
        </button>
      </div>
      <UserList
        list={mine.slice().sort((left, right) => onlineFirst(left, right) || rateOf(right) - rateOf(left))}
        now={around.now}
      />
    </>
  );
}

export function MapSideBody({ selected, ...around }: Around & { selected: Selected }) {
  switch (selected.kind) {
    case SelKind.Group:
      return <GroupSide bucket={selected.bucket} around={around} />;
    case SelKind.User:
      return <UserSide client={selected.client} around={around} />;
    case SelKind.Exit:
      return <ExitSide exit={selected.exit} around={around} />;
  }
}
