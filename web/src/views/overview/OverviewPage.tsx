"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { useShallow } from "zustand/react/shallow";

import { fmtBps, num } from "@/lib/format";
import { ExitState, type DnsSummary, type PanelState } from "@/lib/types";
import { exitsOf, problems, useCore, usersOf } from "@/store/core";
import { loadTraffic } from "@/store/traffic";
import { loadGroupsQuietly, useUsers } from "@/store/users";
import { Icon, IconName } from "@/ui/Icon";
import { PageHead } from "@/ui/PageHead";

import { MapCard } from "./MapCard";
import { TopSites } from "./TopSites";
import { TrafficChart } from "./TrafficChart";

const NO_DATA = "—";
const NO_DNS: DnsSummary = {};

function Tile({ label, id, children }: { label: string; id: string; children: ReactNode }) {
  return (
    <div className="card tile">
      <div className="tile-l">{label}</div>
      <div className="tile-v" id={id}>
        {children}
      </div>
    </div>
  );
}

function Tiles({ state }: { state: PanelState | null }) {
  const totals = state?.totals ?? {};
  const exits = exitsOf(state);
  const dns = state?.dns ?? NO_DNS;
  const blocked = dns.ok ? (dns.queries ? num((100 * (dns.blocked ?? 0)) / dns.queries, 1) : "0") + "%" : NO_DATA;
  return (
    <div className="tiles">
      <Tile label="Онлайн" id="tOnline">
        {state ? (totals.online || 0) + " из " + (totals.clients ?? usersOf(state).users) : NO_DATA}
      </Tile>
      <Tile label="Скорость" id="tSpeed">
        <Icon name={IconName.Down} size={20} label="Скачивание" />
        <span>{state ? fmtBps(totals.down_bps) : NO_DATA}</span>
      </Tile>
      <Tile label="Выходы" id="tExits">
        {state ? exits.filter(exit => exit.state === ExitState.Up).length + " из " + exits.length : NO_DATA}
      </Tile>
      <Tile label="Заблокировано" id="tBlocked">
        {blocked}
      </Tile>
    </div>
  );
}

function Banner() {
  const wrong = useCore(useShallow(state => problems(state)));
  return (
    <div id="ovBanner" hidden={!wrong.length} className={wrong.length ? "banner" : ""}>
      {wrong.length ? (
        <>
          <Icon name={IconName.Alert} />
          <span>{wrong.join(" ")}</span>
        </>
      ) : null}
    </div>
  );
}

/** The groups only shape the map here: read once, and again when another window changed them. */
function useGroupsForMap(state: PanelState | null): void {
  const have = useUsers(state => state.groups);
  const asking = useRef(false);
  const stale = !have || (!!state?.ugroups_v && state.ugroups_v !== have.v);
  useEffect(() => {
    if (!stale || asking.current) return;
    asking.current = true;
    void loadGroupsQuietly().then(() => {
      asking.current = false;
    });
  }, [stale, state]);
}

export function OverviewPage() {
  const state = useCore(state => state.state);
  const tick = useCore(state => state.tick);

  useEffect(() => {
    loadTraffic(true);
  }, []);
  useEffect(() => {
    loadTraffic();
  }, [tick]);
  useGroupsForMap(state);

  return (
    <>
      <PageHead title="Обзор" />
      <Banner />
      <Tiles state={state} />
      <div className="cols">
        <MapCard />
        <TrafficChart history={state?.history ?? []} />
      </div>
      <TopSites dns={state?.dns ?? NO_DNS} />
    </>
  );
}
