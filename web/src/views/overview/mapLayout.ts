/**
 * Where everything stands on the map: groups of users at the left, the entry
 * in the middle, the exits at the right, and the links between them. Pure
 * arithmetic — nothing here knows about the page.
 */
import {
  ExitState,
  type Client,
  type Count,
  type Exit,
  type Speed,
  type UserCounts,
  type UserGroup,
} from "@/lib/types";

import { exitSel, groupSel, userSel, type Selection } from "./selection";

/** The speed above which a link is drawn as flowing and a figure is written next to a name. */
export const BUSY_BPS = 50000;

const HEAD_H = 40;
const USER_H = 30;
const GROUP_GAP = 10;
const OPEN_PAD = 6;
const RIGHT_ROW = 84;
const PAD = 20;
const MIN_HEIGHT = 260;
export const ENTRY_H = 64;
export const EXIT_H = 60;
export const ADD_H = 44;
const MIN_WORLD = 440;
const EDGE = 12;

const COLOR = { live: "#7f8a99", idle: "#b9c0ca", exit: "#414955", down: "#c92f2f" } as const;
const WIDTH = { thin: 1.5, direct: 2, min: 1.5, span: 5.5 } as const;
const DASH = { idle: "3 5", direct: "2 6", down: "6 5" } as const;

export interface MapDims {
  width: number;
  groupLeft: number;
  groupWidth: number;
  entryWidth: number;
  entryLeft: number;
  exitWidth: number;
  exitLeft: number;
}

/** The world is as wide as the viewport (never narrower than a minimum): at scale 1 the text is its normal size. */
export function mapDims(viewportWidth: number): MapDims {
  const worldWidth = Math.max(MIN_WORLD, Math.round(viewportWidth));
  const groupWidth = Math.round(Math.min(300, worldWidth * 0.32));
  const entryWidth = Math.round(Math.min(190, Math.max(120, worldWidth * 0.2)));
  const exitWidth = Math.round(Math.min(300, worldWidth * 0.3));
  return {
    width: worldWidth,
    groupLeft: EDGE,
    groupWidth,
    entryWidth,
    entryLeft: Math.round((worldWidth - entryWidth) / 2),
    exitWidth,
    exitLeft: worldWidth - EDGE - exitWidth,
  };
}

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
 * then by name); empty groups are left out. `listOf` gives the users read for a group's key.
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
  const left = buckets.filter(bucket => bucket.count.users).concat(loose.count.users ? [loose] : []);
  const seen = new Set<string>();
  const clients = [...left.flatMap(bucket => bucket.list), ...more].filter(client => {
    if (seen.has(client.name)) return false;
    seen.add(client.name);
    return true;
  });
  return { clients, left, groupOf: name => where.get(name) ?? LOOSE_KEY };
}

export interface Link {
  key: string;
  path: string;
  stroke: string;
  width: number;
  dash?: string;
  /** What the link belongs to: a selected thing lights its links, the rest fade. */
  rel: Selection[];
  flow: boolean;
}

export interface GroupBox {
  bucket: Bucket;
  id: Selection;
  open: boolean;
  top: number;
  height: number;
  online: number;
  rate: number;
}

export enum RightKind {
  Exit = "exit",
  Direct = "direct",
  Add = "add",
}

export type RightBox =
  | { kind: RightKind.Exit; y: number; exit: Exit; users: number }
  | { kind: RightKind.Direct | RightKind.Add; y: number };

export interface MapLayout {
  height: number;
  centerY: number;
  groups: GroupBox[];
  right: RightBox[];
  links: Link[];
}

const curve = (x1: number, y1: number, x2: number, y2: number): string => {
  const middleX = (x1 + x2) / 2;
  return "M " + x1 + " " + y1 + " C " + middleX + " " + y1 + ", " + middleX + " " + y2 + ", " + x2 + " " + y2;
};

/** What the links of one drawing share: where things are and how thick a rate is drawn. */
interface Drawing {
  dims: MapDims;
  centerY: number;
  widthOf: (rate: number) => number;
  /** Everything a user's link belongs to: the user, the group, the exit while online. */
  relOf: (client: Client) => Selection[];
  groupRel: (client: Client) => Selection;
}

const leftEdge = (drawing: Drawing): number => drawing.dims.groupLeft + drawing.dims.groupWidth;
const entryRight = (drawing: Drawing): number => drawing.dims.entryLeft + drawing.dims.entryWidth;

/** A user of an unfolded group to the entry. */
function userLink(drawing: Drawing, client: Client, y: number): Link {
  return {
    key: userSel(client.name),
    path: curve(leftEdge(drawing), y, drawing.dims.entryLeft, drawing.centerY),
    stroke: client.online ? COLOR.live : COLOR.idle,
    width: client.online ? drawing.widthOf(rateOf(client)) : WIDTH.thin,
    dash: client.online ? undefined : DASH.idle,
    rel: drawing.relOf(client),
    flow: client.online && rateOf(client) > BUSY_BPS,
  };
}

/** A folded group to the entry: one link for all its users. */
function groupLink(drawing: Drawing, box: GroupBox): Link {
  const { bucket, id, online, rate } = box;
  return {
    key: id,
    path: curve(leftEdge(drawing), box.top + HEAD_H / 2, drawing.dims.entryLeft, drawing.centerY),
    stroke: online ? COLOR.live : COLOR.idle,
    width: online ? drawing.widthOf(rate) : WIDTH.thin,
    dash: online ? undefined : DASH.idle,
    rel: [id].concat(...bucket.list.map(client => drawing.relOf(client).filter(item => item !== id))),
    flow: rate > BUSY_BPS,
  };
}

/** The entry to an exit; it belongs to the exit and to everybody who is online through it. */
function exitLink(drawing: Drawing, exit: Exit, y: number, onlineUsers: Client[]): Link {
  const up = exit.state === ExitState.Up;
  const id = exitSel(exit.index);
  return {
    key: id,
    path: curve(entryRight(drawing), drawing.centerY, drawing.dims.exitLeft, y),
    stroke: up ? COLOR.exit : COLOR.down,
    width: up ? drawing.widthOf(rateOf(exit)) : WIDTH.direct,
    dash: up ? undefined : DASH.down,
    rel: [id].concat(...onlineUsers.map(client => [userSel(client.name), drawing.groupRel(client)])),
    flow: up && rateOf(exit) > BUSY_BPS,
  };
}

/** The entry to «Напрямую»: nothing is selected with it. */
function directLink(drawing: Drawing, y: number): Link {
  return {
    key: "direct",
    path: curve(entryRight(drawing), drawing.centerY, drawing.dims.exitLeft, y),
    stroke: COLOR.live,
    width: WIDTH.direct,
    dash: DASH.direct,
    rel: [],
    flow: false,
  };
}

/** How many boxes the right column has besides the exits: «Напрямую» and «Новый выход». */
const EXTRA_RIGHT_BOXES = 2;

export function layoutMap(dims: MapDims, people: People, exits: Exit[], isOpen: (key: string) => boolean): MapLayout {
  const { clients, left, groupOf } = people;
  const heightOf = (bucket: Bucket) => HEAD_H + (isOpen(bucket.key) ? bucket.list.length * USER_H + OPEN_PAD : 0);
  const leftHeight = left.reduce((sum, bucket) => sum + heightOf(bucket), 0) + Math.max(0, left.length - 1) * GROUP_GAP;
  const rightCount = exits.length + EXTRA_RIGHT_BOXES;
  const height = Math.max(MIN_HEIGHT, leftHeight + PAD * 2, rightCount * RIGHT_ROW + PAD * 2);
  const centerY = height / 2;

  const sumRate = (bucket: Bucket) => rateOf(bucket.count);
  const maxRate = Math.max(1, ...left.map(sumRate), ...exits.map(rateOf));
  const groupRel = (client: Client) => groupSel(groupOf(client.name));
  const drawing: Drawing = {
    dims,
    centerY,
    widthOf: rate => WIDTH.min + WIDTH.span * Math.sqrt(Math.min(1, rate / maxRate)),
    groupRel,
    relOf: client =>
      [userSel(client.name), groupRel(client)].concat(
        client.online && client.exit != null ? [exitSel(client.exit)] : [],
      ),
  };

  const links: Link[] = [];
  const groups: GroupBox[] = [];
  let top = (height - leftHeight) / 2;
  for (const bucket of left) {
    const box: GroupBox = {
      bucket,
      id: groupSel(bucket.key),
      open: isOpen(bucket.key),
      top,
      height: heightOf(bucket),
      online: bucket.count.online,
      rate: sumRate(bucket),
    };
    if (box.open) {
      const firstUserY = top + HEAD_H + USER_H / 2;
      bucket.list.forEach((client, index) => links.push(userLink(drawing, client, firstUserY + index * USER_H)));
    } else {
      links.push(groupLink(drawing, box));
    }
    groups.push(box);
    top += box.height + GROUP_GAP;
  }

  const rightTop = centerY - ((rightCount - 1) * RIGHT_ROW) / 2;
  const right: RightBox[] = exits.map((exit, index): RightBox => {
    const y = rightTop + index * RIGHT_ROW;
    const onlineUsers = clients.filter(client => client.exit === exit.index && client.online);
    links.push(exitLink(drawing, exit, y, onlineUsers));
    return { kind: RightKind.Exit, y, exit, users: exit.users ?? 0 };
  });
  const directY = rightTop + exits.length * RIGHT_ROW;
  links.push(directLink(drawing, directY));
  right.push({ kind: RightKind.Direct, y: directY }, { kind: RightKind.Add, y: directY + RIGHT_ROW });
  return { height, centerY, groups, right, links };
}
