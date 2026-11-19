/**
 * Entries of the site lists — a domain, a pattern with stars, a network — read
 * and compared by the same rules as on the server. Nothing here touches the page.
 */

export enum EntryKind {
  Domain = "domain",
  Glob = "glob",
  Net = "net",
  /** Nothing was typed. */
  Empty = "empty",
  /** What was typed is not an entry. */
  Bad = "bad",
}

type RealKind = EntryKind.Domain | EntryKind.Glob | EntryKind.Net;

/** An entry a list can hold. */
export interface Entry {
  kind: RealKind;
  text: string;
}

/** What a typed text turned out to be. */
export type ParsedEntry = Entry | { kind: EntryKind.Empty } | { kind: EntryKind.Bad; why: string };

export const isEntry = (entry: ParsedEntry): entry is Entry =>
  entry.kind !== EntryKind.Empty && entry.kind !== EntryKind.Bad;

const DOMAIN_RE = /^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;
const GLOB_RE = /^(?=.{1,253}$)[a-z0-9*-]+(\.[a-z0-9*-]+)*\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.[a-z]{2,24}$/;
const NET_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(\/(\d{1,2}))?$/;
const HOST_RE = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$/;
const LINK_RE = /^https:\/\/[^\s/]+\.[^\s/]+\/\S+$/i;

/** The wildcard of a pattern. */
const STAR = "*";
const MAX_OCTET = 255;
const MIN_PREFIX = 8;
const ADDRESS_BITS = 32;

/** A whole host name (not a part of one). */
export const isHost = (text: string): boolean => HOST_RE.test(text);
/** A link to a list file: https only. */
export const isListLink = (url: string): boolean => LINK_RE.test(url);

export function parseEntry(raw: string | null | undefined, allowNet: boolean): ParsedEntry {
  let cleaned = String(raw ?? "")
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "");
  if (!cleaned) return { kind: EntryKind.Empty };
  const match = NET_RE.exec(cleaned);
  if (match) {
    if (!allowNet) return { kind: EntryKind.Bad, why: "сети здесь не указываются" };
    const prefix = match[6];
    if (
      [1, 2, 3, 4].some(index => +match[index] > MAX_OCTET) ||
      (prefix && !(+prefix >= MIN_PREFIX && +prefix <= ADDRESS_BITS))
    )
      return { kind: EntryKind.Bad, why: "такой сети не бывает" };
    return { kind: EntryKind.Net, text: cleaned };
  }
  cleaned = cleaned.split("/")[0].replace(/:\d+$/, "").replace(/\.+$/, "").replace(/^\*\./, "");
  if (!cleaned) return { kind: EntryKind.Empty };
  if (cleaned.includes("*")) {
    return GLOB_RE.test(cleaned)
      ? { kind: EntryKind.Glob, text: cleaned }
      : { kind: EntryKind.Bad, why: "слишком широко: звёздочка не может стоять в двух последних частях имени" };
  }
  return DOMAIN_RE.test(cleaned)
    ? { kind: EntryKind.Domain, text: cleaned }
    : { kind: EntryKind.Bad, why: "не похоже на домен" };
}

export function kindOf(text: string): EntryKind {
  if (NET_RE.test(text)) return EntryKind.Net;
  return text.includes("*") ? EntryKind.Glob : EntryKind.Domain;
}

const globRx = (text: string): RegExp =>
  new RegExp("^" + text.replace(/[.+?^${}()|[\]\\-]/g, "\\$&").replace(/\*/g, ".*") + "$");

interface Cidr {
  addr: number;
  bits: number;
  size: number;
  base: number;
}

function cidr(text: string): Cidr {
  const [ip, prefix] = text.split("/");
  const bits = prefix == null ? ADDRESS_BITS : +prefix;
  const addr = ip.split(".").reduce((sum, octet) => sum * 256 + +octet, 0);
  const size = Math.pow(2, ADDRESS_BITS - bits);
  return { addr, bits, size, base: Math.floor(addr / size) * size };
}

/** Which existing entries a new entry makes redundant. */
export function coverFn(entry: ParsedEntry): (text: string) => boolean {
  if (!isEntry(entry)) return () => false;
  const own = entry.text;
  if (entry.kind === EntryKind.Domain) {
    const suffix = "." + own;
    return text => text !== own && !NET_RE.test(text) && text.endsWith(suffix);
  }
  if (entry.kind === EntryKind.Glob) {
    const pattern = globRx(own);
    return text => text !== own && kindOf(text) === EntryKind.Domain && pattern.test(text);
  }
  const outer = cidr(own);
  return text => {
    if (text === own || !NET_RE.test(text)) return false;
    const inner = cidr(text);
    return inner.bits >= outer.bits && inner.addr >= outer.base && inner.addr < outer.base + outer.size;
  };
}

const underDomain = (domain: string, host: string): boolean => host === domain || host.endsWith("." + domain);

/** Does a list entry apply to this host name. */
export function entryMatches(text: string, host: string): boolean {
  const kind = kindOf(text);
  if (kind === EntryKind.Domain) return underDomain(text, host);
  if (kind === EntryKind.Glob) return globRx(text).test(host);
  return false;
}

/**
 * Does a list entry send this address abroad — judged the way the VPN side
 * judges it: there a pattern acts as the domain it is widened to.
 */
export function routeMatches(text: string, host: string): boolean {
  const kind = kindOf(text);
  if (kind === EntryKind.Domain) return underDomain(text, host);
  if (kind === EntryKind.Glob) {
    if (globRx(text).test(host)) return true;
    const labels = text.split(".");
    let last = -1;
    labels.forEach((label, index) => {
      if (label.includes("*")) last = index;
    });
    const base = labels.slice(last + 1).join(".");
    return !!base && underDomain(base, host);
  }
  if (!NET_RE.test(host)) return false;
  const network = cidr(text);
  const address = cidr(host);
  return address.addr >= network.base && address.addr < network.base + network.size;
}

/** What is typed in a search, reduced to what entries are compared with. */
export const queryOf = (raw: string): string =>
  String(raw)
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .split("/")[0]
    .replace(/\.+$/, "");

const MAX_PATH = 600;
const PATH_RE = new RegExp("^(?:https?:\\/\\/)?[^\\/\\s]+(\\/[A-Za-z0-9._~%!$&'()*+,;=:@\\/?-]{0," + MAX_PATH + "})$");
/** The page of a pasted link, "/" when there is none. */
export const pathOf = (raw: string): string => PATH_RE.exec(String(raw).trim())?.[1] ?? "/";

const SECOND_LEVEL = /^(co|com|org|net|gov|edu|ac)$/;
/** The registrable domain of a host: what one adds to a list for a site. */
export function siteDomain(host: string): string {
  const parts = host.split(".");
  const count =
    parts.length > 2 && parts[parts.length - 1].length === 2 && SECOND_LEVEL.test(parts[parts.length - 2]) ? 3 : 2;
  return parts.slice(-count).join(".");
}

/**
 * Search: an entry is found when the text is part of it, or when the text is a
 * whole address that the entry covers.
 */
export function hitFn(query: string, covers: (text: string, host: string) => boolean): (text: string) => boolean {
  if (!query) return () => false;
  const whole = HOST_RE.test(query) || NET_RE.test(query);
  return text => text.includes(query) || (whole && covers(text, query));
}

/** The text of a field after its star button was pressed, and where the cursor goes. */
export interface StarEdit {
  value: string;
  caret: number;
}

export function withStar(current: string, selStart: number | null, selEnd: number | null): StarEdit {
  let start = selStart ?? current.length;
  const end = selEnd ?? current.length;
  let next = current;
  const entry = parseEntry(current, true);
  if (start === end && start >= current.length && (entry.kind === EntryKind.Domain || entry.kind === EntryKind.Glob)) {
    if (current.charAt(0) !== STAR) next = (/^[a-z0-9]/i.test(current) ? STAR + "." : STAR) + current;
    start = next.length;
  } else if (current.charAt(start - 1) !== STAR && current.charAt(end) !== STAR) {
    next = current.slice(0, start) + STAR + current.slice(end);
    start = start + 1;
  }
  return { value: next, caret: start };
}

/** Where a list by link comes from: its host and folder. */
export function sourceOf(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.host + parsed.pathname.replace(/\/[^/]*$/, "");
  } catch {
    return "";
  }
}
