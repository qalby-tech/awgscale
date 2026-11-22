"use client";

import { useEffect, useRef } from "react";

import type { PanelState } from "@/lib/types";
import { loadGroupsQuietly, useUsers } from "@/store/users";

/** The groups name the blocks of «Обзор» and «Сеть»: read once, and again when another window changed them. */
export function useGroupsFresh(state: PanelState | null): void {
  const have = useUsers(store => store.groups);
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
