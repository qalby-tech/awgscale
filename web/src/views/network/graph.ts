/**
 * The network as a graph for the canvas, left to right: the groups of users, the entry, the rules (the groups of
 * sites that go abroad, the blocked ones, and «Остальное» — what the network's mode decides), where they lead
 * («За границу», «Напрямую», «Блок»), and the exits behind «За границу». Pure: where a node stands is worked out
 * here, unless the admin moved it (then their place is kept). What is staged is drawn as it will be.
 */
import type { Edge, Node } from "@xyflow/react";

import { ExitState, RoutingMode, type Client, type Exit, type PanelState, type UserGroup } from "@/lib/types";

import { RuleRoute, type GroupEdit } from "./pending";
import { BUSY_BPS, rateOf, type Bucket, type People } from "./people";
import { RuleWay, type Covered, type Rule } from "./rules";
import { exitSel, groupSel, POOL_SEL, REST_SEL, ruleSel, userSel, type Selection } from "./selection";

export enum NodeKind {
  Group = "group",
  Entry = "entry",
  Rule = "rule",
  Target = "target",
  Exit = "exit",
  AddExit = "add",
}

/** Where a rule leads. */
export enum Target {
  Abroad = "abroad",
  Direct = "direct",
  Block = "block",
}

export interface GroupData extends Record<string, unknown> {
  bucket: Bucket;
  rate: number;
  /** The exit the group is kept on (staged or saved), by name; null when the server decides. */
  pinned: string | null;
  staged: boolean;
  /** Its point may be pulled onto an exit: a group the server keeps, on a server with exits. */
  pull: boolean;
}
export interface EntryData extends Record<string, unknown> {
  note: string;
}
export interface RuleData extends Record<string, unknown> {
  /** Its number in the order the rules are tried, from 1. */
  place: number;
  name: string;
  note: string;
  on: boolean;
  target: Target;
  staged: boolean;
}
export interface TargetData extends Record<string, unknown> {
  target: Target;
  note: string;
}
export interface ExitData extends Record<string, unknown> {
  exit: Exit;
  share: number | null;
  staged: boolean;
}
export type EmptyData = Record<string, unknown>;

export type GroupNode = Node<GroupData, NodeKind.Group>;
export type EntryNode = Node<EntryData, NodeKind.Entry>;
export type RuleNode = Node<RuleData, NodeKind.Rule>;
export type TargetNode = Node<TargetData, NodeKind.Target>;
export type ExitNode = Node<ExitData, NodeKind.Exit>;
export type AddNode = Node<EmptyData, NodeKind.AddExit>;
export type NetNode = GroupNode | EntryNode | RuleNode | TargetNode | ExitNode | AddNode;

/** What an edge belongs to: a selected thing lights its edges, the rest fade. */
export interface LinkData extends Record<string, unknown> {
  rel: Selection[];
  flow: boolean;
}
export type NetEdge = Edge<LinkData>;

export const ENTRY_ID = "entry";
export const ADD_EXIT_ID = "add";
const TARGET_IDS: Record<Target, string> = {
  [Target.Abroad]: POOL_SEL,
  [Target.Direct]: "t:direct",
  [Target.Block]: "t:block",
};

/** The columns and rows of the automatic places, in the canvas's own units. */
const COLUMN = { group: 0, entry: 300, rule: 580, target: 900, exit: 1200 } as const;
const ROW = { group: 80, rule: 76, target: 96, exit: 96 } as const;
const ENTRY_HEIGHT = 72;

// the panel is dark (app/globals.css): links are light on --sunken; abroad is --abroad, direct is --direct
const COLOR = {
  live: "#5b6273",
  idle: "#343a48",
  down: "#f87171",
  abroad: "#ff9f43",
  direct: "#60a5fa",
  block: "#f87171",
  off: "#262a35",
  pin: "#8b9cff",
} as const;
const TARGET_COLORS: Record<Target, string> = {
  [Target.Abroad]: COLOR.abroad,
  [Target.Direct]: COLOR.direct,
  [Target.Block]: COLOR.block,
};
const WIDTH = { thin: 1.5, direct: 2, min: 1.5, span: 5.5 } as const;
const DASH = { idle: "3 5", down: "6 5", pin: "4 5" } as const;

function column(count: number, row: number, center: number): number[] {
  const top = center - ((count - 1) * row) / 2;
  return Array.from({ length: count }, (_, index) => top + index * row);
}

const rateText = (bps: number): string =>
  bps < 1e6 ? Math.round(bps / 1e3) + " Кбит/с" : (bps / 1e6).toFixed(bps < 1e7 ? 1 : 0).replace(".", ",") + " Мбит/с";

/** The entry's line: how many online and how fast all of them go. */
export function entryNote(state: PanelState | null): string {
  if (!state) return "нет данных";
  const totals = state.totals ?? {};
  return (totals.online || 0) + " онлайн · " + rateText(rateOf(totals));
}

const ENTRIES: readonly [string, string, string] = ["запись", "записи", "записей"];
function entriesText(count: number): string {
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return count + " " + ENTRIES[2];
  if (ones === 1) return count + " " + ENTRIES[0];
  if (ones >= 2 && ones <= 4) return count + " " + ENTRIES[1];
  return count + " " + ENTRIES[2];
}

/** Everything the graph is made of: what the state says, and what is staged on top of what the server keeps. */
export interface GraphInput {
  people: People;
  exits: Exit[];
  state: PanelState | null;
  userGroups: UserGroup[];
  groupEdits: Record<string, GroupEdit>;
  /** Every rule, in the order the server keeps; null while it is read. */
  rules: Rule[] | null;
  /** The order staged (every rule's id), or null. */
  order: string[] | null;
  /** Entries a rule above covers, by rule. */
  covered: Covered;
  /** Rules staged to lead elsewhere, by their id. */
  routes: Record<string, RuleRoute>;
  /** Rules staged for other groups of people, by their id. */
  scopes: Record<string, string[]>;
  mode: RoutingMode;
  modeStaged: boolean;
  shares: number[];
  sharesStaged: boolean;
}

const ROUTE_TARGETS: Record<RuleRoute, Target | null> = {
  [RuleRoute.Exit]: Target.Abroad,
  [RuleRoute.Direct]: Target.Direct,
  [RuleRoute.Block]: Target.Block,
  [RuleRoute.Off]: null,
};

/** Where a rule leads; one that is off keeps the colour of where it would lead (`home`). */
function targetOf(route: RuleRoute, home: Target): Target {
  return ROUTE_TARGETS[route] ?? home;
}

/** A rule's way as the route the canvas stages. */
export function wayRoute(way: RuleWay): RuleRoute {
  if (way === RuleWay.Block) return RuleRoute.Block;
  return way === RuleWay.Direct ? RuleRoute.Direct : RuleRoute.Exit;
}

/** Where a rule leads now, on the server. */
export function routeNow(rule: Rule): RuleRoute {
  return rule.on ? wayRoute(rule.route) : RuleRoute.Off;
}

/** The rules in the staged order (or as kept). */
export function orderedRules(rules: Rule[], order: string[] | null): Rule[] {
  if (!order) return rules;
  const byId = new Map(rules.map(rule => [rule.id, rule]));
  return order.flatMap(id => byId.get(id) ?? []);
}

export interface Graph {
  nodes: NetNode[];
  edges: NetEdge[];
}

export function buildGraph(input: GraphInput): Graph {
  const { people, exits, state } = input;
  const single = !!state?.routing?.single_server;
  const left = people.left;
  const exitName = (index: number | null | undefined) => exits.find(exit => exit.index === index)?.name ?? null;

  const rules: RuleNode[] = [];
  for (const rule of orderedRules(input.rules ?? [], input.order)) {
    // on a server without exits nothing goes abroad or is routed: only the blocked rules mean something
    if (single && rule.route !== RuleWay.Block) continue;
    const now = routeNow(rule);
    const route = input.routes[rule.id] ?? now;
    const home = ROUTE_TARGETS[wayRoute(rule.route)] ?? Target.Abroad;
    const covered = Object.keys(input.covered[rule.id] ?? {}).length;
    const scope = input.scopes[rule.id] ?? rule.groups ?? [];
    const scopeNames = scope.map(id => input.userGroups.find(group => group.id === id)?.name ?? "…");
    const note =
      (scope.length ? "только: " + scopeNames.join(", ") + " · " : "") +
      (rule.url ? "список по ссылке" : entriesText(rule.items.length)) +
      (covered ? " · " + covered + " перекрыто" : "");
    rules.push({
      id: ruleSel(rule.id),
      type: NodeKind.Rule,
      position: { x: 0, y: 0 },
      data: {
        place: rules.length + 1,
        name: rule.name,
        note,
        on: route !== RuleRoute.Off,
        target: targetOf(route, home),
        staged:
          rule.id in input.routes ||
          rule.id in input.scopes ||
          (!!input.order && input.order.indexOf(rule.id) !== (input.rules ?? []).indexOf(rule)),
      },
    });
  }
  const foreign = input.mode === RoutingMode.Foreign && !single;
  rules.push({
    id: REST_SEL,
    type: NodeKind.Rule,
    position: { x: 0, y: 0 },
    data: {
      place: rules.length + 1,
      name: "Остальное",
      note: foreign ? "зарубежное — за границу, своё — напрямую" : "напрямую",
      on: true,
      target: foreign ? Target.Abroad : Target.Direct,
      staged: input.modeStaged,
    },
  });

  const targets: Target[] = single ? [Target.Direct, Target.Block] : [Target.Abroad, Target.Direct, Target.Block];
  const exitCount = single ? 0 : exits.length;
  const height = Math.max(
    left.length * ROW.group,
    rules.length * ROW.rule,
    targets.length * ROW.target,
    (exitCount + 1) * ROW.exit,
  );
  const center = (height - ENTRY_HEIGHT) / 2;

  const maxRate = Math.max(1, ...left.map(bucket => rateOf(bucket.count)), ...exits.map(rateOf));
  const widthOf = (rate: number) => WIDTH.min + WIDTH.span * Math.sqrt(Math.min(1, rate / maxRate));
  const groupRel = (client: Client) => groupSel(people.groupOf(client.name));

  const nodes: NetNode[] = [];
  const edges: NetEdge[] = [];

  const groupTops = column(left.length, ROW.group, center);
  left.forEach((bucket, index) => {
    const id = groupSel(bucket.key);
    const rate = rateOf(bucket.count);
    const online = bucket.count.online > 0;
    const saved = input.userGroups.find(group => (group.id ?? "") === bucket.key);
    const edit = input.groupEdits[bucket.key];
    const pin = edit?.exit !== undefined ? edit.exit : (saved?.exit ?? null);
    nodes.push({
      id,
      type: NodeKind.Group,
      position: { x: COLUMN.group, y: groupTops[index] },
      data: {
        bucket,
        rate,
        pinned: single ? null : exitName(pin),
        staged: !!edit,
        pull: !single && !!saved?.id && exits.length > 0,
      },
    });
    // a group kept on an exit: a thin line straight to it, over everything the rules decide
    if (!single && pin != null && exitName(pin) !== null)
      edges.push({
        id: "e:pin:" + id,
        source: id,
        target: exitSel(pin),
        className: "pin",
        style: { stroke: COLOR.pin, strokeWidth: WIDTH.thin, strokeDasharray: DASH.pin },
        data: { rel: [id, exitSel(pin)], flow: false },
      });
    edges.push({
      id: "e:" + id,
      source: id,
      target: ENTRY_ID,
      style: {
        stroke: online ? COLOR.live : COLOR.idle,
        strokeWidth: online ? widthOf(rate) : WIDTH.thin,
        strokeDasharray: online ? undefined : DASH.idle,
      },
      data: {
        rel: [
          id,
          ...bucket.list
            .filter(client => client.online && client.exit != null)
            .map(client => exitSel(client.exit ?? 0)),
        ],
        flow: rate > BUSY_BPS,
      },
    });
  });

  nodes.push({
    id: ENTRY_ID,
    type: NodeKind.Entry,
    position: { x: COLUMN.entry, y: center },
    data: { note: entryNote(state) },
  });

  const ruleTops = column(rules.length, ROW.rule, center);
  rules.forEach((rule, index) => {
    nodes.push({ ...rule, position: { x: COLUMN.rule, y: ruleTops[index] } });
    edges.push({
      id: "e:in:" + rule.id,
      source: ENTRY_ID,
      target: rule.id,
      style: { stroke: rule.data.on ? COLOR.idle : COLOR.off, strokeWidth: WIDTH.thin },
      data: { rel: [rule.id], flow: false },
    });
    if (!rule.data.on) return;
    edges.push({
      id: "e:out:" + rule.id,
      source: rule.id,
      target: TARGET_IDS[rule.data.target],
      style: {
        stroke: TARGET_COLORS[rule.data.target],
        strokeWidth: WIDTH.direct,
      },
      data: { rel: [rule.id, TARGET_IDS[rule.data.target]], flow: false },
    });
    // in the mode «всё зарубежное» what is of the home country still goes direct
    if (rule.id === REST_SEL && foreign)
      edges.push({
        id: "e:home:" + rule.id,
        source: rule.id,
        target: TARGET_IDS[Target.Direct],
        style: { stroke: COLOR.direct, strokeWidth: WIDTH.direct },
        data: { rel: [rule.id, TARGET_IDS[Target.Direct]], flow: false },
      });
  });

  const day = state?.dns;
  const blockedShare = day?.ok && day.queries ? Math.round((1000 * (day.blocked ?? 0)) / day.queries) / 10 : null;
  const targetNotes: Record<Target, string> = {
    [Target.Abroad]: exits.length ? "выход по доле · закрыто при сбое" : "выходов нет",
    [Target.Direct]: "со входа · сети своей страны",
    [Target.Block]:
      blockedShare == null ? "блокировщик" : String(blockedShare).replace(".", ",") + "% запросов за сутки",
  };
  const targetTops = column(targets.length, ROW.target, center);
  targets.forEach((target, index) => {
    nodes.push({
      id: TARGET_IDS[target],
      type: NodeKind.Target,
      position: { x: COLUMN.target, y: targetTops[index] },
      data: { target, note: targetNotes[target] },
    });
  });

  const exitTops = column(exitCount + 1, ROW.exit, center);
  if (!single) {
    exits.forEach((exit, index) => {
      const id = exitSel(exit.index);
      const up = exit.state === ExitState.Up;
      const riders = people.clients.filter(client => client.exit === exit.index && client.online);
      nodes.push({
        id,
        type: NodeKind.Exit,
        position: { x: COLUMN.exit, y: exitTops[index] },
        data: { exit, share: input.shares[index] ?? null, staged: input.sharesStaged },
      });
      edges.push({
        id: "e:" + id,
        source: POOL_SEL,
        target: id,
        style: {
          stroke: up ? COLOR.abroad : COLOR.down,
          strokeWidth: up ? widthOf(rateOf(exit)) : WIDTH.direct,
          strokeDasharray: up ? undefined : DASH.down,
        },
        data: {
          rel: [id, POOL_SEL, ...riders.flatMap(client => [userSel(client.name), groupRel(client)])],
          flow: up && rateOf(exit) > BUSY_BPS,
        },
      });
    });
  }
  nodes.push({
    id: ADD_EXIT_ID,
    type: NodeKind.AddExit,
    position: { x: single ? COLUMN.target : COLUMN.exit, y: single ? center + ROW.target * 2 : exitTops[exitCount] },
    data: {},
  });
  return { nodes, edges };
}
