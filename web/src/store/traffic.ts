/**
 * Bytes per user by day (30) and by hour (24). The entry server adds to them
 * once a minute, so they are asked for when a section that shows them opens
 * and then about once a minute — not with every state poll.
 */
import { create } from "zustand";

import { api } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { type TrafficSpan, type Traffic, type TrafficPair, type UserTraffic } from "@/lib/types";

const REFRESH_MS = 60000;

export const useTraffic = create<{ data: Traffic | null }>(() => ({ data: null }));

let busy = false;
let askedAt = 0;

export function loadTraffic(force = false): void {
  if (busy || (!force && Math.abs(Date.now() - askedAt) < REFRESH_MS)) return;
  busy = true;
  askedAt = Date.now(); // counted from the request, so a failing one is not repeated with every poll
  api<Traffic>("/api/traffic")
    .then(
      data =>
        useTraffic.setState({
          data: data && Array.isArray(data.days) && data.users && Object.keys(data.users).length ? data : null,
        }),
      () => {
        /* what was loaded stays; without it the sections show the counters since the tunnel started */
      },
    )
    .then(() => {
      busy = false;
    });
}

const both = (pair: TrafficPair | undefined): number => (Array.isArray(pair) ? (pair[0] || 0) + (pair[1] || 0) : 0);

function sum(data: Traffic, names: string[], pick: (usage: UserTraffic) => TrafficPair | undefined): number {
  return names.reduce((total, name) => {
    const usage = data.users[name];
    return total + (usage ? both(pick(usage)) : 0);
  }, 0);
}

/** Download + upload of these users over a span; null while there is no history (callers then show the counters since start). */
export const trafficTotal = (data: Traffic | null, names: string[], span: TrafficSpan): number | null =>
  data ? sum(data, names, usage => usage.totals?.[span]) : null;

/** The same for each of the days of the history. */
export const trafficByDay = (data: Traffic, names: string[]): number[] =>
  data.days.map((_, index) => sum(data, names, usage => usage.days?.[index]));

export const trafficText = (bytes: number | null): string => (bytes == null ? "—" : fmtBytes(bytes));
export const trafficTitle = (month: number | null): string =>
  month != null ? "Трафик за 30 дней" : "Трафик с запуска туннеля";
