/** The users of the network by group, as the canvas and its inspector show them. Pure: nothing here knows about the page. */
import type { Client, Count, Speed, UserCounts, UserGroup } from "@/lib/types";

/** The speed above which a link is drawn as flowing and a figure is written next to a name. */
export const BUSY_BPS = 50000;

export const rateOf = (speed: Speed): number => (speed.down_bps || 0) + (speed.up_bps || 0);

/** A group of users as the map shows it; "" is the key of those in no group. */
export interface Bucket {
  key: string;
  name: string;
  /** How many, how many online, how fast: as the server counts them. */
  count: Count;
  /** Its users as far as they are read: only an unfolded group's are. */
  list: Client[];
  /** Whose history makes its traffic: a group's members; null for those in no group. */
  names: string[] | null;
}

export interface People {
  /** The users read for the map: those of the unfolded groups and of the selected exit. */
  clients: Client[];
  left: Bucket[];
  groupOf: (name: string) => string;
}

const LOOSE_KEY = "";
const NOBODY: Count = { users: 0, online: 0 };

const onlineFirst = (left: Client, right: Client): number =>
  (right.online ? 1 : 0) - (left.online ? 1 : 0) || left.name.localeCompare(right.name);

/**
 * The groups as the server counts them, with the users read for the map put into theirs (online first,
 * then by name). A group none of whose people is a user now is left out; a group just made, with nobody in it yet,
 * stays (it can be kept on an exit before anyone joins). `listOf` gives the users read for a group's key.
 */
export function gather(
  counts: UserCounts,
  groups: UserGroup[],
  listOf: (key: string) => Client[],
  more: Client[],
): People {
  const where = new Map<string, string>();
  groups.forEach(group => group.members.forEach(name => where.set(name, group.id ?? LOOSE_KEY)));
  const countOf = (key: string): Count => counts.groups?.find(group => group.id === key) ?? NOBODY;
  const buckets: Bucket[] = groups.map(group => ({
    key: group.id ?? LOOSE_KEY,
    name: group.name,
    count: countOf(group.id ?? LOOSE_KEY),
    list: listOf(group.id ?? LOOSE_KEY)
      .slice()
      .sort(onlineFirst),
    names: group.members,
  }));
  const loose: Bucket = {
    key: LOOSE_KEY,
    name: groups.length ? "Без группы" : "Пользователи",
    count: groups.length ? (counts.ungrouped ?? NOBODY) : counts,
    list: listOf(LOOSE_KEY).slice().sort(onlineFirst),
    names: null,
  };
  const left = buckets
    .filter(bucket => bucket.count.users || !bucket.names?.length)
    .concat(loose.count.users ? [loose] : []);
  const seen = new Set<string>();
  const clients = [...left.flatMap(bucket => bucket.list), ...more].filter(client => {
    if (seen.has(client.name)) return false;
    seen.add(client.name);
    return true;
  });
  return { clients, left, groupOf: name => where.get(name) ?? LOOSE_KEY };
}
