"use client";

/** The entry server and every exit, one row each. */
import { ago, fmtBps, hostOf, num } from "@/lib/format";
import { ExitState, type Exit, type PanelState, type ServerFacts, type Speed } from "@/lib/types";
import { exitsOf, nowOf, usersOf } from "@/store/core";
import { exitItems, openShares, sharesOf } from "@/store/exits";
import { Dot, DotKind, IconButton } from "@/ui/controls";
import { IconName } from "@/ui/Icon";
import { menuButton } from "@/ui/menu";

const COLUMNS = ["Сервер", "Статус", "Адрес", "Доля", "Пользователи", "Трафик", "Связь"] as const;
const NUMERIC_COLUMNS: ReadonlySet<string> = new Set(["Доля", "Пользователи"]);

const traffic = (speed: Speed): string => "↓ " + fmtBps(speed.down_bps) + "  ↑ " + fmtBps(speed.up_bps);

/** Load, memory and disk in one line; empty when the server reports none of them. */
function factsOf(facts: ServerFacts): string {
  const parts: string[] = [];
  if (facts.load1 != null) parts.push("нагрузка " + num(facts.load1, 2));
  if (facts.mem_total) parts.push("память " + Math.round((100 * (facts.mem_used ?? 0)) / facts.mem_total) + "%");
  if (facts.disk_pct != null) parts.push("диск " + facts.disk_pct + "%");
  return parts.join(" · ");
}

function Health({ ok, good, bad }: { ok: boolean; good: string; bad: string }) {
  return (
    <span className={"st" + (ok ? "" : " crit")}>
      <Dot kind={ok ? DotKind.Good : DotKind.Crit} />
      {ok ? good : bad}
    </span>
  );
}

function EntryRow({ state }: { state: PanelState }) {
  const entry = state.entry ?? {};
  return (
    <tr>
      <td>
        <span className="uname">Вход</span>
        <small>{factsOf(entry)}</small>
      </td>
      <td>
        <Health
          ok={!state.stale && entry.client_if_up !== false}
          good="работает"
          bad={state.stale ? "нет связи" : "туннель не поднят"}
        />
      </td>
      <td className="ink2">{entry.listen_port ? "порт " + entry.listen_port : "—"}</td>
      <td className="num muted">—</td>
      <td className="num">{usersOf(state).users}</td>
      <td>{traffic(state.totals ?? {})}</td>
      <td className="muted">—</td>
    </tr>
  );
}

interface ExitRowProps {
  exit: Exit;
  percent: number;
  alone: boolean;
  now: number;
}

function ExitRow({ exit, percent, alone, now }: ExitRowProps) {
  const up = exit.state === ExitState.Up;
  return (
    <tr>
      <td>
        <IconButton
          icon={IconName.More}
          label={"Действия с выходом " + exit.name}
          className="srv-more"
          {...menuButton(() => exitItems(exit))}
        />
        <span className="uname">{exit.name}</span>
        {exit.sys && up ? <small>{factsOf(exit.sys)}</small> : null}
      </td>
      <td>
        <Health ok={up} good="работает" bad="недоступен" />
      </td>
      <td className="mono ink2">{hostOf(exit.endpoint)}</td>
      <td className="num">
        {alone ? (
          <span>100%</span>
        ) : (
          <button
            type="button"
            className="btn small"
            data-k={"w:" + exit.index}
            title="Изменить доли выходов"
            onClick={() => openShares(true)}
          >
            {percent}%
          </button>
        )}
      </td>
      <td className="num">{exit.users ?? 0}</td>
      <td>{traffic(exit)}</td>
      <td className="ink2">{ago(exit.handshake, now)}</td>
    </tr>
  );
}

export function ServersTable({ state }: { state: PanelState | null }) {
  const exits = exitsOf(state);
  const percents = sharesOf(exits);
  return (
    <section className="card table-card" aria-label="Серверы">
      <div className="table-wrap">
        <table className="plain t-srv">
          <thead>
            <tr>
              {COLUMNS.map(title => (
                <th key={title} scope="col" className={NUMERIC_COLUMNS.has(title) ? "num" : ""}>
                  {title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody id="srvBody">
            {state ? (
              <>
                <EntryRow state={state} />
                {exits.map((exit, index) => (
                  <ExitRow
                    key={exit.index}
                    exit={exit}
                    percent={percents[index]}
                    alone={exits.length < 2}
                    now={nowOf(state)}
                  />
                ))}
              </>
            ) : (
              <tr>
                <td colSpan={COLUMNS.length} className="muted">
                  Нет данных
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
