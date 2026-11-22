"use client";

import { useEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";

import type { PanelState } from "@/lib/types";
import { problems, useCore } from "@/store/core";
import { loadTraffic } from "@/store/traffic";
import { loadGroupsQuietly, useUsers } from "@/store/users";
import { Icon, IconName } from "@/ui/Icon";
import { PageHead } from "@/ui/PageHead";

import { ExitsBlock, FilterBlock, JournalBlock, PeopleBlock } from "./blocks";
import { MapCard } from "./MapCard";
import { TopSites } from "./TopSites";
import { TrafficChart } from "./TrafficChart";

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

/** The groups name the map and the people's block: read once, and again when another window changed them. */
function useGroups(state: PanelState | null): void {
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
  useGroups(state);

  return (
    <>
      <PageHead title="Обзор" />
      <Banner />
      <div className="bento">
        <TrafficChart history={state?.history ?? []} className="block wide" />
        <ExitsBlock state={state} />
        <PeopleBlock state={state} />
        <TopSites dns={state?.dns ?? {}} />
        <FilterBlock state={state} />
        <JournalBlock />
      </div>
      <MapCard />
    </>
  );
}
