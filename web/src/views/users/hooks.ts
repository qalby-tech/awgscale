"use client";

/** What the lists of this section share: picking up another window's change, and holding off a reload. */
import { useEffect, useRef } from "react";

import { holdReload, useCore } from "@/store/core";

interface ListSync<T> {
  /** The version the server says it stores now (it comes with every state answer). */
  version: string | undefined;
  /** The version of the list on the page; undefined while there is none yet. */
  held: string | undefined;
  loaded: boolean;
  /** Something is being typed, chosen or saved: the list is left alone. */
  busy: () => boolean;
  read: () => Promise<T>;
  take: (list: T) => void;
}

/**
 * Groups and spaces can be changed from another tab or device. A list that is
 * open and idle re-reads itself. (A change based on an older version is
 * refused by the server.)
 */
export function useListSync<T>({ version, held, loaded, busy, read, take }: ListSync<T>): void {
  const tick = useCore(state => state.tick);
  const seen = useRef<string | null>(null);
  const alive = useRef(true);
  const latest = useRef({ busy, read, take });
  latest.current = { busy, read, take };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (!loaded || !version || version === seen.current) return;
    if (held === version) {
      seen.current = version;
      return;
    }
    if (latest.current.busy()) return;
    seen.current = version;
    const later = () => {
      seen.current = null;
    }; // the next answer brings it again
    latest.current.read().then(list => {
      if (!alive.current || latest.current.busy()) later();
      else latest.current.take(list);
    }, later);
  }, [tick, version, held, loaded]);
}

/** While something is open that a reload would lose, the page does not reload itself for a newer build. */
export function useHoldReload(active: boolean): void {
  useEffect(() => (active ? holdReload() : undefined), [active]);
}
