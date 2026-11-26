"use client";

/**
 * The inspector's controls for what can be changed on the canvas. Every choice is staged (usePending, or the draft
 * of «Сайты») and drawn on the canvas at once; nothing reaches the server before «Применить».
 */
import { useEffect, useState, type ReactNode } from "react";

import { GroupFilter, Route, RoutingMode, type Exit, type UserGroup } from "@/lib/types";
import { hrefOf } from "@/store/core";
import { isShare, sharesAddUp, sharesOf, WHOLE_SHARE } from "@/store/exits";
import { FILTER_MODES } from "@/store/users";
import { IconButton } from "@/ui/controls";
import { IconName } from "@/ui/Icon";

import { shadowedTitle } from "../sites/SiteCards";
import { RuleRoute, stageScope, stageGroup, stageMode, stageRoute, stageShares, usePending } from "./pending";
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
const NONE_COVERED: Record<string, string> = {};
const SHOWN_ENTRIES = 12;

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

function Entries({ items, covered = NONE_COVERED }: { items: string[]; covered?: Record<string, string> }) {
  if (!items.length) return <p className="muted small-note">Записей нет.</p>;
  return (
    <div className="chips net-chips">
      {items.slice(0, SHOWN_ENTRIES).map(item => (
        <span
          key={item}
          className={"chip ro" + (item in covered ? " shadowed" : "")}
          title={item in covered ? shadowedTitle(covered[item]) : undefined}
        >
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
  /** Why it cannot be blocked or sent directly (a list by link, networks): both work by names. Null when it can. */
  namesOnly: string | null;
  /** It is kept among the blocked groups (and is edited in «Блокируется»). */
  blockList: boolean;
  byLink: boolean;
  /** Its entries a rule above already names, and by which rule. */
  covered: Record<string, string>;
  /** The groups of people it is for (as kept); empty: everybody. */
  scope: string[];
  /** Its place in the list (staged or kept), from 0, and how many rules there are. */
  place: number;
  count: number;
}

const ROUTE_NOTES: Record<RuleRoute, string> = {
  [RuleRoute.Exit]: "Для всех групп людей. Эти сайты и сети идут через выход.",
  [RuleRoute.Direct]:
    "Для всех групп людей. Эти сайты идут со входа напрямую — и в режиме «всё зарубежное», и у тех, кто выбрал «Весь трафик».",
  [RuleRoute.Block]: "Для всех групп людей. Эти сайты не открываются.",
  [RuleRoute.Off]: "Выключено: эти сайты идут, как решает «Остальное».",
};

/** Where a rule leads, staged; moving it between abroad and blocked moves it between «Сайты» and «Блокируется». */
/** Moves a rule one place up or down in the staged order. */
export type Mover = (id: string, step: number) => void;

/** «Для кого»: everybody, or some groups of people (also while the rule is off; on a single server, blocking only). */
function ScopeChoice({
  rule,
  people,
  route,
  single,
}: {
  rule: RuleView;
  people: UserGroup[];
  route: RuleRoute;
  single: boolean;
}) {
  const staged = usePending(state => state.scopes[rule.id]);
  const scope = staged ?? rule.scope;
  const blocking = route === RuleRoute.Block;
  const open = !rule.namesOnly && (blocking || !single);
  const pick = (id: string | null) => {
    if (id === null) stageScope(rule.id, [], rule.scope);
    else stageScope(rule.id, scope.includes(id) ? scope.filter(each => each !== id) : [...scope, id], rule.scope);
  };
  return (
    <div className="net-field">
      <span>Для кого</span>
      <div className="chips net-chips" role="group" aria-label="Для кого">
        <button type="button" className="chip-pick" aria-pressed={!scope.length} onClick={() => pick(null)}>
          Все
        </button>
        {people.map(group =>
          group.id ? (
            <button
              key={group.id}
              type="button"
              className="chip-pick"
              aria-pressed={scope.includes(group.id)}
              disabled={!open}
              onClick={() => pick(group.id)}
            >
              {group.name}
            </button>
          ) : null,
        )}
      </div>
      {single && !blocking ? (
        <p className="muted small-note">На сервере без выходов для отдельных групп людей можно только блокировать.</p>
      ) : null}
    </div>
  );
}

const SCOPED_NOTES: Record<RuleRoute, string> = {
  [RuleRoute.Exit]:
    "Только для выбранных групп: их люди открывают эти сайты через выход. Остальные — по правилам ниже.",
  [RuleRoute.Direct]:
    "Только для выбранных групп: у их людей эти сайты идут со входа напрямую. Остальные — по правилам ниже.",
  [RuleRoute.Block]: "Только для выбранных групп: у их людей эти сайты не открываются.",
  [RuleRoute.Off]: ROUTE_NOTES[RuleRoute.Off],
};

export function RuleSide({
  rule,
  single,
  move,
  people,
}: {
  rule: RuleView;
  single: boolean;
  move: Mover;
  people: UserGroup[];
}) {
  const staged = usePending(state => state.routes[rule.id]);
  const route = staged ?? rule.now;
  const scoped = (usePending(state => state.scopes[rule.id]) ?? rule.scope).length > 0;
  const options: ReadonlyArray<readonly [RuleRoute, string]> = [
    ...(single || (scoped && rule.namesOnly) ? [] : ([[RuleRoute.Exit, "За границу"]] as const)),
    ...(rule.namesOnly || single ? [] : ([[RuleRoute.Direct, "Напрямую"]] as const)),
    ...(rule.namesOnly ? [] : ([[RuleRoute.Block, "Блок"]] as const)),
    [RuleRoute.Off, "Выключено"],
  ];
  return (
    <>
      <Head kind="Правило" title={rule.name} />
      <p className="muted small-note">{scoped ? SCOPED_NOTES[route] : ROUTE_NOTES[route]}</p>
      <Choice
        label="Куда идёт правило"
        options={options}
        value={route}
        onPick={value => stageRoute(rule.id, value, rule.now)}
      />
      {rule.namesOnly ? <p className="muted small-note">{rule.namesOnly}</p> : null}
      <ScopeChoice rule={rule} people={people} route={route} single={single} />
      <div className="net-field">
        <span>
          Место в списке: {rule.place + 1} из {rule.count} — срабатывает верхнее правило, которое называет сайт
        </span>
        <div className="row">
          <button type="button" className="btn" disabled={rule.place === 0} onClick={() => move(rule.id, -1)}>
            Выше
          </button>
          <button
            type="button"
            className="btn"
            disabled={rule.place >= rule.count - 1}
            onClick={() => move(rule.id, 1)}
          >
            Ниже
          </button>
        </div>
      </div>
      <div className="net-field">
        <span>{rule.byLink ? "Список по ссылке" : "Сайты и сети"}</span>
        <Entries items={rule.items} covered={rule.covered} />
      </div>
      {!rule.blockList && rule.scope.length ? (
        <p className="muted small-note">
          В «Сайтах» это правило помечено «только: …» — там меняются его сайты, а для кого оно, остаётся как здесь.
        </p>
      ) : null}
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
    setValues(current => (sharesAddUp(current) ? saved.split(",") : current));
  }, [staged, saved]);
  const good = sharesAddUp(values);

  const change = (index: number, text: string) => {
    const next = values.slice();
    next[index] = text;
    if (next.length === 2 && isShare(text)) next[1 - index] = String(WHOLE_SHARE - Number(text));
    setValues(next);
    if (sharesAddUp(next)) stageShares(exits, next.map(Number), now);
  };

  return (
    <>
      <Head kind="Куда" title="За границу" />
      <p className="muted small-note">
        Новые люди делятся между выходами по долям; 0 — выход выключен. Если выход не отвечает, его люди уходят на
        другой; напрямую зарубежное не идёт.
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
