// Where a person may be kept, as the location screens show it: the exits by country, the least loaded
// first, the search, and the exits chosen lately on this device. Pure functions; the views stay thin.
import { type Exit, type ExitChoice } from "./types";

// How many servers of a country are offered at once, and how many search results are shown
export const SERVERS_SHOWN = 10;
export const RESULTS_SHOWN = 20;
// How many exits chosen lately are kept (the one the person is on now is shown besides them)
const RECENT_KEPT = 3;
export const RECENT_SHOWN = 2;
const RECENT_KEY_PREFIX = "awgscale.recent.";

// What a search for an address looks like: digits and dots only ("198.51", "203.0.113.7")
const ADDRESS_QUERY = /^[0-9.]+$/;

// The exits of one country (or of none known: code null, «Другие»)
export interface Country {
  code: string | null;
  name: string;
  // the working ones, the least loaded first
  working: Exit[];
}

// The fewest online first, then the fewest people, then the larger share. The share is the operator's
// weight for the exit (how many of the people Авто sends there), not its load: of two equally loaded
// exits the one the operator wants used more comes first.
export function byLoad(first: Exit, second: Exit): number {
  return (
    first.online - second.online ||
    first.users - second.users ||
    second.share - first.share ||
    first.index - second.index
  );
}

// A country's best server among its working ones (the least loaded first): never one the operator gave no
// share to, unless every working one has none
export function bestOf(working: Exit[]): Exit | null {
  return working.find(exit => exit.share > 0) ?? working.at(0) ?? null;
}

// The exit as a person sees it: its address; its name only from an older server, which sends none
export function shownAddress(exit: Exit): string {
  return exit.address.length > 0 ? exit.address : exit.name;
}

// The badge of Авто, where a country's code stands for a server
export const AUTO_BADGE = "A";

export function badgeOf(code: string | null): string {
  return code == null ? "··" : code.toUpperCase();
}

const regionNames = new Map<string, Intl.DisplayNames>();

// The country's name in the app's language, or its code when the system does not know it
export function countryName(code: string, locale: string): string {
  try {
    let names = regionNames.get(locale);
    if (names == null) {
      names = new Intl.DisplayNames([locale], { type: "region" });
      regionNames.set(locale, names);
    }
    return names.of(code.toUpperCase()) ?? code.toUpperCase();
  } catch {
    return code.toUpperCase();
  }
}

// Every country that has an exit, by name, the unknown ones last under one row
export function countriesOf(exits: Exit[], locale: string, otherName: string): Country[] {
  const grouped = new Map<string | null, Exit[]>();
  for (const exit of exits) {
    const code = exit.country;
    const known = grouped.get(code);
    if (known == null) {
      grouped.set(code, [exit]);
    } else {
      known.push(exit);
    }
  }
  const countries: Country[] = [];
  let other: Country | null = null;
  for (const [code, members] of grouped) {
    const working = members.filter(exit => exit.up).sort(byLoad);
    if (code == null) {
      other = { code, name: otherName, working };
    } else {
      countries.push({ code, name: countryName(code, locale), working });
    }
  }
  countries.sort((first, second) => first.name.localeCompare(second.name, locale));
  return other == null ? countries : [...countries, other];
}

export function isAddressQuery(query: string): boolean {
  return ADDRESS_QUERY.test(query);
}

// The countries whose name (in the app's language) holds the query, or whose code starts with it
export function countriesMatching(countries: Country[], query: string, locale: string): Country[] {
  const wanted = query.trim().toLocaleLowerCase(locale);
  return countries.filter(
    country =>
      country.name.toLocaleLowerCase(locale).includes(wanted) ||
      (country.code != null && country.code.startsWith(wanted)),
  );
}

// Whether an exit answers a search: by what the person sees — its address, or its name when it has none
// (an older server sends no address, only the name)
export function exitMatches(exit: Exit, query: string): boolean {
  const wanted = query.trim();
  if (exit.address.length > 0) return exit.address.startsWith(wanted);
  return exit.name.toLowerCase().includes(wanted.toLowerCase());
}

// The working exits that answer a search, the least loaded first
export function exitsMatching(exits: Exit[], query: string): Exit[] {
  return exits.filter(exit => exit.up && exitMatches(exit, query)).sort(byLoad);
}

// The exit the person is kept on now: the one they chose, or their group's; none when the shares decide
export function currentExit(choice: ExitChoice | null, exits: Exit[]): Exit | null {
  const index = choice?.pinned ?? choice?.group ?? null;
  if (index == null) {
    return null;
  }
  return exits.find(exit => exit.index === index) ?? null;
}

function recentKey(host: string): string {
  return RECENT_KEY_PREFIX + host;
}

// The exits chosen lately in this network, the latest first; the indexes mean nothing in another one
export function recentChoices(host: string): number[] {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(recentKey(host)) ?? "[]");
    return Array.isArray(saved) ? saved.filter((index): index is number => Number.isInteger(index)) : [];
  } catch {
    return [];
  }
}

export function rememberChoice(host: string, index: number) {
  const kept = [index, ...recentChoices(host).filter(earlier => earlier !== index)].slice(0, RECENT_KEPT);
  try {
    window.localStorage.setItem(recentKey(host), JSON.stringify(kept));
  } catch {
    // a device that keeps nothing simply shows no recent choices
  }
}

// The recent ones besides the current: still there and working, at most RECENT_SHOWN
export function recentExits(host: string, exits: Exit[], current: Exit | null): Exit[] {
  const shown: Exit[] = [];
  for (const index of recentChoices(host)) {
    const exit = exits.find(candidate => candidate.index === index);
    if (exit != null && exit.up && exit.index !== current?.index && shown.length < RECENT_SHOWN) {
      shown.push(exit);
    }
  }
  return shown;
}
