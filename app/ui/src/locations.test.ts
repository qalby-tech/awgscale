// What the location screens are built from: the countries, the order by load, the best server, the
// searches, the exit the person is on and the recent choices kept on this device.
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  bestOf,
  byLoad,
  countriesMatching,
  countriesOf,
  currentExit,
  exitMatches,
  exitsMatching,
  isAddressQuery,
  recentChoices,
  recentExits,
  rememberChoice,
  shownAddress,
} from "./locations";
import { type Exit } from "./types";

const RUSSIAN = "ru";
const OTHER = "Другие";
const HOST = "vpn.example.org";
const FINLAND = "fi";
const NETHERLANDS = "nl";

function exitOf(index: number, fields: Partial<Exit>): Exit {
  return {
    index,
    name: `exit${index}`,
    up: true,
    share: 10,
    address: `198.51.100.${index}`,
    country: "nl",
    users: 0,
    online: 0,
    ...fields,
  };
}

// The exit as an older server sends it: no address, no country, no people counted
function olderExit(index: number, name: string, up: boolean): Exit {
  return { index, name, up, share: 50, address: "", country: null, users: 0, online: 0 };
}

const EXITS: Exit[] = [
  exitOf(1, { country: "nl", users: 17, online: 4 }),
  exitOf(2, { country: "nl", users: 9, online: 1 }),
  exitOf(3, { country: "fi", up: false }),
  exitOf(4, { country: "de", users: 6, online: 2, address: "203.0.113.20" }),
  exitOf(5, { country: "nl", users: 3, online: 1 }),
  exitOf(6, { country: "is", users: 1 }),
  exitOf(7, { country: null, address: "203.0.113.40" }),
];

// A device's storage as a map; what it holds can be anything
class MemoryStorage {
  readonly saved = new Map<string, string>();

  getItem(key: string): string | null {
    return this.saved.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.saved.set(key, value);
  }
}

function useStorage(storage: object) {
  vi.stubGlobal("window", { localStorage: storage });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("countriesOf", () => {
  it("groups the exits by country, by name, the unknown ones last under «Другие»", () => {
    const countries = countriesOf(EXITS, RUSSIAN, OTHER);
    expect(countries.map(country => country.name)).toEqual(["Германия", "Исландия", "Нидерланды", "Финляндия", OTHER]);
    expect(countries.at(-1)?.code).toBeNull();
    expect(countries.at(-1)?.working.map(exit => exit.index)).toEqual([7]);
  });

  it("keeps a country whose servers are all down, with none working", () => {
    const finland = countriesOf(EXITS, RUSSIAN, OTHER).find(country => country.code === FINLAND);
    expect(finland?.working).toEqual([]);
  });

  it("orders a country's working servers by load", () => {
    const netherlands = countriesOf(EXITS, RUSSIAN, OTHER).find(country => country.code === NETHERLANDS);
    expect(netherlands?.working.map(exit => exit.index)).toEqual([5, 2, 1]);
  });

  it("puts every exit of an older server under «Другие», by its name", () => {
    const older = [olderExit(1, "nl-ams-1", true), olderExit(2, "de-fra-1", true), olderExit(3, "fi-hel-1", false)];
    const countries = countriesOf(older, RUSSIAN, OTHER);
    expect(countries).toHaveLength(1);
    expect(countries[0].name).toBe(OTHER);
    expect(countries[0].working.map(shownAddress)).toEqual(["nl-ams-1", "de-fra-1"]);
  });
});

describe("byLoad", () => {
  function order(exits: Exit[]): number[] {
    return [...exits].sort(byLoad).map(exit => exit.index);
  }

  it("puts the fewest online first", () => {
    expect(order([exitOf(1, { online: 3 }), exitOf(2, { online: 1, users: 9 })])).toEqual([2, 1]);
  });

  it("then the fewest people", () => {
    expect(order([exitOf(1, { users: 5 }), exitOf(2, { users: 2, share: 0 })])).toEqual([2, 1]);
  });

  it("then the larger share: the operator's weight, not a load", () => {
    expect(order([exitOf(1, { share: 10 }), exitOf(2, { share: 40 }), exitOf(3, { share: 0 })])).toEqual([2, 1, 3]);
  });

  it("then the index", () => {
    expect(order([exitOf(4, {}), exitOf(2, {})])).toEqual([2, 4]);
  });
});

describe("bestOf", () => {
  it("is the least loaded", () => {
    expect(bestOf([exitOf(1, { share: 5 }), exitOf(2, { share: 50, users: 3 })])?.index).toBe(1);
  });

  it("is never one with no share while another works", () => {
    expect(bestOf([exitOf(1, { share: 0 }), exitOf(2, { share: 20, users: 3 })])?.index).toBe(2);
  });

  it("is the least loaded when none has a share", () => {
    expect(bestOf([exitOf(1, { share: 0 }), exitOf(2, { share: 0 })])?.index).toBe(1);
  });

  it("is none when nothing works", () => {
    expect(bestOf([])).toBeNull();
  });
});

describe("the searches", () => {
  const older = [olderExit(11, "nl-ams-1", true), olderExit(12, "nl-ams-2", false), olderExit(13, "de-fra-1", true)];

  it("tells an address from a country", () => {
    expect(isAddressQuery("198.51")).toBe(true);
    expect(isAddressQuery("203.0.113.7")).toBe(true);
    expect(isAddressQuery("Герм")).toBe(false);
    expect(isAddressQuery("exit1")).toBe(false);
  });

  it("finds the working servers whose address starts with the query, the least loaded first", () => {
    expect(exitsMatching(EXITS, "198.51.100.").map(exit => exit.index)).toEqual([6, 5, 2, 1]);
    expect(exitsMatching(EXITS, "203.0").map(exit => exit.index)).toEqual([7, 4]);
    expect(exitsMatching(EXITS, "100.4")).toEqual([]);
  });

  it("finds a server by its name, in any case and anywhere in it", () => {
    expect(exitsMatching(older, "AMS").map(exit => exit.index)).toEqual([11]);
    expect(exitsMatching(older, "fra-1").map(exit => exit.index)).toEqual([13]);
    expect(exitMatches(older[0], "  nl-ams ")).toBe(true);
    expect(exitMatches(older[0], "203.0")).toBe(false);
  });

  it("does not find a server with an address by its hidden name", () => {
    expect(exitsMatching(EXITS, "exit")).toEqual([]);
    expect(exitMatches(EXITS[0], EXITS[0].name)).toBe(false);
  });

  it("finds the address of an older server's exit by its name, which may hold digits", () => {
    expect(exitsMatching([olderExit(21, "exit-10.1", true)], "10.1").map(exit => exit.index)).toEqual([21]);
  });

  it("finds the countries by their name or the start of their code", () => {
    const countries = countriesOf(EXITS, RUSSIAN, OTHER);
    expect(countriesMatching(countries, "герм", RUSSIAN).map(country => country.code)).toEqual(["de"]);
    expect(countriesMatching(countries, "ИЯ", RUSSIAN).map(country => country.code)).toEqual(["de", "is", "fi"]);
    expect(countriesMatching(countries, "n", RUSSIAN).map(country => country.code)).toEqual(["nl"]);
    expect(countriesMatching(countries, "друг", RUSSIAN).map(country => country.code)).toEqual([null]);
    expect(countriesMatching(countries, "атлантида", RUSSIAN)).toEqual([]);
  });
});

describe("currentExit", () => {
  it("is the one the person chose", () => {
    expect(currentExit({ pinned: 2, group: 4, name: "exit2" }, EXITS)?.index).toBe(2);
  });

  it("is their group's when they chose none", () => {
    expect(currentExit({ pinned: null, group: 4, name: null }, EXITS)?.index).toBe(4);
  });

  it("is none when the shares decide, before anything is known, or when the exit is gone", () => {
    expect(currentExit({ pinned: null, group: null, name: null }, EXITS)).toBeNull();
    expect(currentExit(null, EXITS)).toBeNull();
    expect(currentExit({ pinned: 2, group: null, name: "exit2" }, [])).toBeNull();
    expect(currentExit({ pinned: 99, group: null, name: null }, EXITS)).toBeNull();
  });
});

describe("the recent choices", () => {
  it("keeps the latest first, each once, at most three", () => {
    useStorage(new MemoryStorage());
    for (const index of [1, 2, 1, 4, 5]) {
      rememberChoice(HOST, index);
    }
    expect(recentChoices(HOST)).toEqual([5, 4, 1]);
    expect(recentChoices("other.example.org")).toEqual([]);
  });

  it("shows at most two working ones besides the current", () => {
    useStorage(new MemoryStorage());
    for (const index of [2, 3, 5, 1]) {
      rememberChoice(HOST, index);
    }
    // kept: 1, 5, 3; 1 is the current, 3 is down
    expect(recentExits(HOST, EXITS, EXITS[0]).map(exit => exit.index)).toEqual([5]);
    expect(recentExits(HOST, EXITS, null).map(exit => exit.index)).toEqual([1, 5]);
  });

  it("forgets what is not a list of indexes", () => {
    const storage = new MemoryStorage();
    useStorage(storage);
    const key = `awgscale.recent.${HOST}`;
    storage.setItem(key, "{not json");
    expect(recentChoices(HOST)).toEqual([]);
    storage.setItem(key, JSON.stringify({ index: 2 }));
    expect(recentChoices(HOST)).toEqual([]);
    storage.setItem(key, JSON.stringify([2, "3", 1.5, null, 4]));
    expect(recentChoices(HOST)).toEqual([2, 4]);
    rememberChoice(HOST, 6);
    expect(recentChoices(HOST)).toEqual([6, 2, 4]);
  });

  it("shows none on a device whose storage refuses", () => {
    useStorage({
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    });
    expect(() => rememberChoice(HOST, 2)).not.toThrow();
    expect(recentChoices(HOST)).toEqual([]);
    expect(recentExits(HOST, EXITS, null)).toEqual([]);
  });

  it("shows none where there is no storage at all", () => {
    useStorage({});
    expect(() => rememberChoice(HOST, 2)).not.toThrow();
    expect(recentChoices(HOST)).toEqual([]);
  });
});
