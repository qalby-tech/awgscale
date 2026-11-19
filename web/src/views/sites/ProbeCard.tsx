"use client";

import { siteDomain } from "@/lib/entries";
import { RoutingMode } from "@/lib/types";
import { Dot, DotKind } from "@/ui/controls";
import { menuButton } from "@/ui/menu";

import { addFromProbe, routeOf, runProbe } from "./store";
import { PROBE_OK, ProbeVia, type DraftGroup, type Probe, type ProbeWay, type Sites } from "./types";

interface RowProps {
  label: string;
  text: string;
  state?: string;
}

/** The class of a row's verdict: nothing while there is no verdict yet. */
function verdictClass(state: string | undefined): string {
  if (!state) return "";
  return state === PROBE_OK ? "good" : "bad";
}

function ProbeRow({ label, text, state }: RowProps) {
  const good = state === PROBE_OK;
  return (
    <div className="probe-row">
      <span>{label}</span>
      <b className={verdictClass(state)}>
        {state ? <Dot kind={good ? DotKind.Good : DotKind.Crit} /> : null}
        <span>{text}</span>
      </b>
    </div>
  );
}

function probeButtonText(probe: Probe | null): string {
  if (probe?.pending) return "Проверяю…";
  return probe?.ways ? "Ещё раз" : "Проверить";
}

const wayLabel = (way: ProbeWay): string => (way.via === ProbeVia.Direct ? "Напрямую" : "Через " + way.name);
const wayText = (way: ProbeWay): string => way.text + (way.ms && way.state === PROBE_OK ? " · " + way.ms + " мс" : "");

interface ProbeCardProps {
  host: string;
  sites: Sites;
  draft: DraftGroup[];
  probe: Probe | null;
}

/**
 * «Не открывается сайт»: a whole address in the search gets a card that says how
 * it is routed now, whether it opens directly and through each exit, and adds it.
 */
export function ProbeCard({ host, sites, draft, probe }: ProbeCardProps) {
  const checked = probe?.host === host ? probe : null;
  const { through, idle } = routeOf(draft, host);
  const foreign = !!sites.mode && sites.mode !== RoutingMode.List; // everything foreign goes abroad by its address
  const now = through
    ? "через заграницу · " + through.name
    : (foreign ? "по адресу: зарубежное — через заграницу" : "напрямую из России") +
      (idle ? " · в выключенной группе «" + idle.name + "»" : "");
  const domain = siteDomain(host);
  const targets = () =>
    draft
      .filter(group => !group.url && !group.auto)
      .map(group => ({ label: group.name, run: () => addFromProbe(group.key, domain) }));
  return (
    <section className="card group probe" aria-label={"Проверка " + host}>
      <div className="group-head">
        <h2 className="group-name mono" title={host}>
          {host}
        </h2>
        <span className="spacer" />
        <button
          type="button"
          className="btn small"
          id="probeGo"
          disabled={!!checked?.pending}
          onClick={() => void runProbe(host)}
        >
          {probeButtonText(checked)}
        </button>
        {!through && !idle ? (
          <button type="button" className="btn small" id="probeAdd" {...menuButton(targets, "Сначала создайте группу")}>
            {"Добавить " + domain}
          </button>
        ) : null}
      </div>
      <div className="probe-rows">
        <ProbeRow label="Сейчас идёт" text={now} />
        {checked?.ways?.map((way, index) => (
          <ProbeRow key={index} label={wayLabel(way)} text={wayText(way)} state={way.state} />
        ))}
        {checked?.error ? (
          <div className="err" role="alert">
            {checked.error}
          </div>
        ) : null}
      </div>
    </section>
  );
}
