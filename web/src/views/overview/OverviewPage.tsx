"use client";

import { useEffect } from "react";
import { useShallow } from "zustand/react/shallow";

import { problems, useCore } from "@/store/core";
import { loadTraffic } from "@/store/traffic";
import { Icon, IconName } from "@/ui/Icon";
import { PageHead } from "@/ui/PageHead";

import { ExitsBlock, FilterBlock, JournalBlock, PeopleBlock } from "./blocks";
import { useGroupsFresh } from "../network/hooks";
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

export function OverviewPage() {
  const state = useCore(state => state.state);
  const tick = useCore(state => state.tick);

  useEffect(() => {
    loadTraffic(true);
  }, []);
  useEffect(() => {
    loadTraffic();
  }, [tick]);
  useGroupsFresh(state);

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
    </>
  );
}
