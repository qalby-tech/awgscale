/**
 * The network as a graph for the canvas, left to right: the groups of users, the entry, the rules (the groups of
 * sites that go abroad, the blocked ones, and «Остальное» — what the network's mode decides), where they lead
 * («За границу», «Напрямую», «Блок»), and the exits behind «За границу». Pure: where a node stands is worked out
 * here, unless the admin moved it (then their place is kept). What is staged is drawn as it will be.
 */
import type { Edge, Node } from "@xyflow/react";

import { ExitState, RoutingMode, type Client, type Exit, type PanelState, type UserGroup } from "@/lib/types";

import type { BlockGroup } from "../blocked/types";
import type { SiteGroup } from "../sites/types";
import { RuleRoute, type GroupEdit } from "./pending";
import { BUSY_BPS, rateOf, type Bucket, type People } from "./people";
import { blockSel, exitSel, groupSel, POOL_SEL, REST_SEL, ruleSel, userSel, type Selection } from "./selection";

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
}
export interface EntryData extends Record<string, unknown> {
  note: string;
}
export interface RuleData extends Record<string, unknown> {
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
const COLUMN = { group: 0, entry: 340, rule: 620, target: 940, exit: 1260 } as const;
const ROW = { group: 56, rule: 56, target: 90, exit: 80 } as const;
const ENTRY_HEIGHT = 64;

// the panel is dark (app/globals.css): links are light on --sunken
const COLOR = {
  live: "#7f8a99",
  idle: "#4a525e",
  exit: "#9aa3ae",
  down: "#f0506e",
  abroad: "#c7773f",
  block: "#f0506e",
  off: "#353c47",
} as const;
const TARGET_COLORS: Record<Target, string> = {
  [Target.Abroad]: COLOR.abroad,
  [Target.Direct]: COLOR.live,
  [Target.Block]: COLOR.block,
};
const WIDTH = { thin: 1.5, direct: 2, min: 1.5, span: 5.5 } as const;
const DASH = { idle: "3 5", direct: "2 6", down: "6 5" } as const;

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
  /** The groups of sites as the server keeps them; null while they are read. */
  sites: SiteGroup[] | null;
  /** Ids of the site groups whose «on» differs from the server's. */
  blocks: BlockGroup[] | null;
  /** Rules staged to lead elsewhere, by their id. */
  routes: Record<string, RuleRoute>;
  mode: RoutingMode;
  modeStaged: boolean;
  shares: number[];
  sharesStaged: boolean;
}

/** Where a rule leads; one that is off keeps the colour of the list it is kept in. */
function targetOf(route: RuleRoute, blockList: boolean): Target {
  if (route === RuleRoute.Block) return Target.Block;
  if (route === RuleRoute.Exit) return Target.Abroad;
  return blockList ? Target.Block : Target.Abroad;
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
  /** `blockList`: the rule is kept among the blocked groups (its id says which list it is in). */
  const ruleOf = (id: string, name: string, note: string, now: RuleRoute, blockList: boolean): RuleNode => {
    const route = input.routes[id] ?? now;
    return {
      id: blockList ? blockSel(id) : ruleSel(id),
      type: NodeKind.Rule,
      position: { x: 0, y: 0 },
      data: {
        name,
        note,
        on: route !== RuleRoute.Off,
        target: targetOf(route, blockList),
        staged: id in input.routes,
      },
    };
  };
  if (!single) {
    for (const group of input.sites ?? []) {
      const note = group.url ? "список по ссылке" : entriesText(group.items.length);
      rules.push(ruleOf(group.id, group.name, note, group.on ? RuleRoute.Exit : RuleRoute.Off, false));
    }
  }
  for (const group of input.blocks ?? []) {
    if (!group.id) continue;
    const note = entriesText(group.items.length);
    rules.push(ruleOf(group.id, group.name, note, group.on ? RuleRoute.Block : RuleRoute.Off, true));
  }
  const foreign = input.mode === RoutingMode.Foreign && !single;
  rules.push({
    id: REST_SEL,
    type: NodeKind.Rule,
    position: { x: 0, y: 0 },
    data: {
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
      data: { bucket, rate, pinned: single ? null : exitName(pin), staged: !!edit },
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
        strokeDasharray: rule.data.target === Target.Direct ? DASH.direct : undefined,
      },
      data: { rel: [rule.id, TARGET_IDS[rule.data.target]], flow: false },
    });
    // in the mode «всё зарубежное» what is of the home country still goes direct
    if (rule.id === REST_SEL && foreign)
      edges.push({
        id: "e:home:" + rule.id,
        source: rule.id,
        target: TARGET_IDS[Target.Direct],
        style: { stroke: COLOR.live, strokeWidth: WIDTH.direct, strokeDasharray: DASH.direct },
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
          stroke: up ? COLOR.exit : COLOR.down,
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
