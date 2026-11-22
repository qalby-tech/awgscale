"use client";

/**
 * The inspector's controls for what can be changed on the canvas. Every choice is staged (usePending, or the draft
 * of «Сайты») and drawn on the canvas at once; nothing reaches the server before «Применить».
 */
import { useEffect, useState, type ReactNode } from "react";

import { GroupFilter, Route, RoutingMode, type Exit, type UserGroup } from "@/lib/types";
import { hrefOf } from "@/store/core";
import { sharesOf } from "@/store/exits";
import { FILTER_MODES } from "@/store/users";
import { IconButton } from "@/ui/controls";
import { IconName } from "@/ui/Icon";

import { RuleRoute, stageGroup, stageMode, stageRoute, stageShares, usePending } from "./pending";
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

/** A rule as the inspector edits it: a group of sites that goes abroad, or of blocked names. */
export interface RuleView {
  id: string;
  name: string;
  items: string[];
  /** Where it leads on the server now. */
  now: RuleRoute;
  /** Why it cannot be blocked (a list by link, networks); null when it can. */
  noBlock: string | null;
  /** It is kept among the blocked groups (and is edited in «Блокируется»). */
  blockList: boolean;
  byLink: boolean;
}

const ROUTE_NOTES: Record<RuleRoute, string> = {
  [RuleRoute.Exit]: "Для всех групп людей. Эти сайты и сети идут через выход.",
  [RuleRoute.Block]: "Для всех групп людей. Эти сайты не открываются.",
  [RuleRoute.Off]: "Выключено: эти сайты идут, как решает «Остальное».",
};

/** Where a rule leads, staged; moving it between abroad and blocked moves it between «Сайты» and «Блокируется». */
export function RuleSide({ rule, single }: { rule: RuleView; single: boolean }) {
  const staged = usePending(state => state.routes[rule.id]);
  const route = staged ?? rule.now;
  const options: ReadonlyArray<readonly [RuleRoute, string]> = [
    ...(single ? [] : ([[RuleRoute.Exit, "За границу"]] as const)),
    ...(rule.noBlock ? [] : ([[RuleRoute.Block, "Блок"]] as const)),
    [RuleRoute.Off, "Выключено"],
  ];
  return (
    <>
      <Head kind="Правило" title={rule.name} />
      <p className="muted small-note">{ROUTE_NOTES[route]}</p>
      <Choice
        label="Куда идёт правило"
        options={options}
        value={route}
        onPick={value => stageRoute(rule.id, value, rule.now)}
      />
      {rule.noBlock ? <p className="muted small-note">{rule.noBlock}</p> : null}
      <div className="net-field">
        <span>{rule.byLink ? "Список по ссылке" : "Сайты и сети"}</span>
        <Entries items={rule.items} />
      </div>
      <a className="btn" href={hrefOf(rule.blockList ? Route.Blocked : Route.Sites)}>
        {rule.blockList ? "Изменить в «Блокируется»" : "Изменить в «Сайтах»"}
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
const sharesGood = (numbers: number[]): boolean =>
  numbers.reduce((sum, value) => sum + (value || 0), 0) === WHOLE && numbers.every(isShare);

/** «За границу»: how new people are shared among the exits, in percent; staged when they add up to 100. */
export function PoolSide({ exits }: { exits: Exit[] }) {
  const staged = usePending(state => state.shares);
  const now = sharesOf(exits);
  const [values, setValues] = useState(() => (staged ?? now).map(String));
  // nothing staged (after «Отменить», «Применить», or new shares saved elsewhere): the fields show the saved shares,
  // unless the admin is in the middle of typing ones that do not add up yet
  const saved = now.join(",");
  useEffect(() => {
    if (staged !== null) return;
    setValues(current => (sharesGood(current.map(Number)) ? saved.split(",") : current));
  }, [staged, saved]);
  const numbers = values.map(Number);
  const good = sharesGood(numbers);

  const change = (index: number, text: string) => {
    const next = values.slice();
    next[index] = text;
    const value = Number(text);
    if (next.length === 2 && isShare(value) && value < WHOLE) next[1 - index] = String(WHOLE - value);
    setValues(next);
    const parsed = next.map(Number);
    if (sharesGood(parsed)) stageShares(exits, parsed, now);
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
