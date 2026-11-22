"use client";

/**
 * The inspector's controls for what can be changed on the canvas. Every choice is staged (usePending, or the draft
 * of «Сайты») and drawn on the canvas at once; nothing reaches the server before «Применить».
 */
import { useState, type ReactNode } from "react";

import { GroupFilter, Route, RoutingMode, type Exit, type UserGroup } from "@/lib/types";
import { hrefOf } from "@/store/core";
import { sharesOf } from "@/store/exits";
import { FILTER_MODES } from "@/store/users";
import { IconButton } from "@/ui/controls";
import { IconName } from "@/ui/Icon";

import type { BlockGroup } from "../blocked/types";
import { toggleGroup as toggleSite } from "../sites/store";
import type { DraftGroup } from "../sites/types";
import { stageBlock, stageGroup, stageMode, stageShares, usePending } from "./pending";
import { mapSelect } from "./selection";

/** The levels offered for a group, in their order («без блокировки» is kept by the server but no longer offered). */
const FILTERS: readonly GroupFilter[] = [
  GroupFilter.Standard,
  GroupFilter.Kids,
  GroupFilter.Pro,
  GroupFilter.ProPlus,
  GroupFilter.Ultimate,
];
const AUTO_EXIT = "";
const SHOWN_ENTRIES = 12;
const WHOLE = 100;
const MIN_SHARE = 1;

function Head({ kind, title, children }: { kind: string; title: string; children?: ReactNode }) {
  return (
    <div className="panel-head">
      <div className="panel-title">
        <span className="panel-kind">{kind}</span>
        <h2>{title}</h2>
        {children}
      </div>
      <IconButton icon={IconName.X} label="Закрыть" onClick={() => mapSelect(null)} />
    </div>
  );
}

/** A choice of a few, one pressed. */
function Choice<T extends string>({
  label,
  options,
  value,
  onPick,
}: {
  label: string;
  options: ReadonlyArray<readonly [T, string]>;
  value: T;
  onPick: (value: T) => void;
}) {
  return (
    <div className="seg wrap-seg" role="group" aria-label={label}>
      {options.map(([option, text]) => (
        <button key={option} type="button" aria-pressed={option === value} onClick={() => onPick(option)}>
          {text}
        </button>
      ))}
    </div>
  );
}

function Entries({ items }: { items: string[] }) {
  if (!items.length) return <p className="muted small-note">Записей нет.</p>;
  return (
    <div className="chips net-chips">
      {items.slice(0, SHOWN_ENTRIES).map(item => (
        <span key={item} className="chip ro">
          <span className="chip-t">{item}</span>
        </span>
      ))}
      {items.length > SHOWN_ENTRIES ? (
        <span className="muted small-note">и ещё {items.length - SHOWN_ENTRIES}</span>
      ) : null}
    </div>
  );
}

/** The group's protection and its exit, staged. */
export function GroupEditor({ group, exits, single }: { group: UserGroup; exits: Exit[]; single: boolean }) {
  const edit = usePending(state => (group.id != null ? state.groups[group.id] : undefined));
  const filter = edit?.filter ?? group.filter ?? GroupFilter.Standard;
  const exit = edit?.exit !== undefined ? edit.exit : (group.exit ?? null);
  const shownFilters = FILTERS.includes(filter) ? FILTERS : [...FILTERS, filter];
  return (
    <div className="net-edit">
      <div className="net-field">
        <span>Защита</span>
        <Choice
          label="Защита группы"
          options={shownFilters.map(level => [level, FILTER_MODES[level]] as const)}
          value={filter}
          onPick={level => stageGroup(group, { filter: level })}
        />
      </div>
      {single ? null : (
        <label className="net-field">
          <span>Выход</span>
          <select
            className="select"
            value={exit == null ? AUTO_EXIT : String(exit)}
            onChange={event =>
              stageGroup(group, { exit: event.target.value === AUTO_EXIT ? null : Number(event.target.value) })
            }
          >
            <option value={AUTO_EXIT}>Как решит сервер</option>
            {exits.map(each => (
              <option key={each.index} value={String(each.index)}>
                {each.name}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}

enum Switch {
  On = "on",
  Off = "off",
}

/** A group of sites that goes abroad: on or off, staged in the draft of «Сайты». */
export function RuleSide({ group }: { group: DraftGroup }) {
  return (
    <>
      <Head kind="Правило" title={group.name} />
      <p className="muted small-note">Для всех групп людей. Сайты и сети этой группы идут через выход.</p>
      <Choice
        label="Куда идёт правило"
        options={[
          [Switch.On, "За границу"],
          [Switch.Off, "Выключено"],
        ]}
        value={group.on ? Switch.On : Switch.Off}
        onPick={value => {
          if ((value === Switch.On) !== group.on) toggleSite(group.key);
        }}
      />
      <div className="net-field">
        <span>{group.url ? "Список по ссылке" : "Сайты и сети"}</span>
        <Entries items={group.items} />
      </div>
      <a className="btn" href={hrefOf(Route.Sites)}>
        Изменить в «Сайтах»
      </a>
    </>
  );
}

/** A blocked group: on or off, staged here. `on` is what the server has. */
export function BlockSide({ group }: { group: BlockGroup }) {
  const staged = usePending(state => (group.id ? state.blocks[group.id] : undefined));
  const on = staged ?? group.on;
  return (
    <>
      <Head kind="Правило" title={group.name} />
      <p className="muted small-note">Для всех групп людей. Эти сайты не открываются.</p>
      <Choice
        label="Блокировать ли"
        options={[
          [Switch.On, "Блок"],
          [Switch.Off, "Выключено"],
        ]}
        value={on ? Switch.On : Switch.Off}
        onPick={value => {
          if (group.id && (value === Switch.On) !== on) stageBlock(group.id, group.on);
        }}
      />
      <div className="net-field">
        <span>Сайты</span>
        <Entries items={group.items} />
      </div>
      <a className="btn" href={hrefOf(Route.Blocked)}>
        Изменить в «Блокируется»
      </a>
    </>
  );
}

/** «Остальное»: what goes abroad besides the rules — the network's mode. `now` is the server's. */
export function RestSide({ now }: { now: RoutingMode }) {
  const staged = usePending(state => state.mode);
  return (
    <>
      <Head kind="Правило" title="Остальное" />
      <p className="muted small-note">Всё, чего нет в правилах выше. Меняется для всей сети.</p>
      <Choice
        label="Режим сети"
        options={[
          [RoutingMode.List, "Напрямую"],
          [RoutingMode.Foreign, "Зарубежное — за границу"],
        ]}
        value={staged ?? now}
        onPick={mode => stageMode(mode, now)}
      />
    </>
  );
}

const isShare = (value: number): boolean => Number.isInteger(value) && value >= MIN_SHARE;

/** «За границу»: how new people are shared among the exits, in percent; staged when they add up to 100. */
export function PoolSide({ exits }: { exits: Exit[] }) {
  const staged = usePending(state => state.shares);
  const now = sharesOf(exits);
  const [values, setValues] = useState(() => (staged ?? now).map(String));
  const numbers = values.map(Number);
  const total = numbers.reduce((sum, value) => sum + (value || 0), 0);
  const good = total === WHOLE && numbers.every(isShare);

  const change = (index: number, text: string) => {
    const next = values.slice();
    next[index] = text;
    const value = Number(text);
    if (next.length === 2 && isShare(value) && value < WHOLE) next[1 - index] = String(WHOLE - value);
    setValues(next);
    const parsed = next.map(Number);
    if (parsed.reduce((sum, each) => sum + each, 0) === WHOLE && parsed.every(isShare)) stageShares(exits, parsed, now);
  };

  return (
    <>
      <Head kind="Куда" title="За границу" />
      <p className="muted small-note">
        Новые люди делятся между выходами по долям. Если выход не отвечает, его люди уходят на другой; напрямую
        зарубежное не идёт.
      </p>
      {exits.length < 2 ? (
        <p className="muted small-note">{exits.length ? "Выход один: доли не нужны." : "Выходов пока нет."}</p>
      ) : (
        <div className="net-shares">
          {exits.map((exit, index) => (
            <label key={exit.index} className="share-row">
              <span>{exit.name}</span>
              <input
                className="input"
                inputMode="numeric"
                value={values[index] ?? ""}
                aria-label={"Доля " + exit.name + ", %"}
                onChange={event => change(index, event.target.value)}
              />
              <span>%</span>
            </label>
          ))}
          <p className={good ? "muted small-note" : "err"}>{good ? "В сумме 100%" : "В сумме должно быть 100%"}</p>
        </div>
      )}
      <a className="btn" href={hrefOf(Route.Servers)}>
        Серверы
      </a>
    </>
  );
}
