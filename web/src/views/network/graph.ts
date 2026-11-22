/**
 * The network as a graph for the canvas: groups of users at the left, the entry in the middle, the exits and
 * «Напрямую» at the right, the links between them. Pure: where a node stands is worked out here, unless the
 * admin moved it (then their place is kept, see positions.ts).
 */
import type { Edge, Node } from "@xyflow/react";

import { ExitState, type Client, type Exit, type PanelState } from "@/lib/types";

import { BUSY_BPS, rateOf, type Bucket, type People } from "./people";
import { exitSel, groupSel, userSel, type Selection } from "./selection";

export enum NodeKind {
  Group = "group",
  Entry = "entry",
  Exit = "exit",
  Direct = "direct",
  AddExit = "add",
}

export interface GroupData extends Record<string, unknown> {
  bucket: Bucket;
  rate: number;
}
export interface EntryData extends Record<string, unknown> {
  note: string;
}
export interface ExitData extends Record<string, unknown> {
  exit: Exit;
}
export type EmptyData = Record<string, unknown>;

export type GroupNode = Node<GroupData, NodeKind.Group>;
export type EntryNode = Node<EntryData, NodeKind.Entry>;
export type ExitNode = Node<ExitData, NodeKind.Exit>;
export type PlainNode = Node<EmptyData, NodeKind.Direct | NodeKind.AddExit>;
export type NetNode = GroupNode | EntryNode | ExitNode | PlainNode;

/** What an edge belongs to: a selected thing lights its edges, the rest fade. */
export interface LinkData extends Record<string, unknown> {
  rel: Selection[];
  flow: boolean;
}
export type NetEdge = Edge<LinkData>;

export const ENTRY_ID = "entry";
export const DIRECT_ID = "direct";
export const ADD_EXIT_ID = "add";

/** The columns and rows of the automatic places, in the canvas's own units. */
const COLUMN = { group: 0, entry: 360, right: 680 } as const;
const ROW = { group: 56, right: 80 } as const;
const SIZE = { group: 260, entry: 200, right: 260, entryHeight: 64 } as const;

// the panel is dark (app/globals.css): links are light on --sunken
const COLOR = { live: "#7f8a99", idle: "#4a525e", exit: "#9aa3ae", down: "#f0506e" } as const;
const WIDTH = { thin: 1.5, direct: 2, min: 1.5, span: 5.5 } as const;
const DASH = { idle: "3 5", direct: "2 6", down: "6 5" } as const;

export const SIZES = SIZE;

function column(count: number, row: number, center: number): number[] {
  const top = center - ((count - 1) * row) / 2;
  return Array.from({ length: count }, (_, index) => top + index * row);
}

/** The entry's line: how many online and how fast all of them go. */
export function entryNote(state: PanelState | null): string {
  if (!state) return "нет данных";
  const totals = state.totals ?? {};
  return (totals.online || 0) + " онлайн · " + rateText(rateOf(totals));
}

const rateText = (bps: number): string =>
  bps < 1e6 ? Math.round(bps / 1e3) + " Кбит/с" : (bps / 1e6).toFixed(bps < 1e7 ? 1 : 0).replace(".", ",") + " Мбит/с";

export interface Graph {
  nodes: NetNode[];
  edges: NetEdge[];
}

/** Every node and edge of the network as it is now. `single`: a server without exits draws no right column but «Новый выход». */
export function buildGraph(people: People, exits: Exit[], state: PanelState | null): Graph {
  const single = !!state?.routing?.single_server;
  const left = people.left;
  const rightCount = exits.length + (single ? 1 : 2);
  const center = (Math.max(left.length * ROW.group, rightCount * ROW.right) - SIZE.entryHeight) / 2;
  const groupTops = column(left.length, ROW.group, center);
  const rightTops = column(rightCount, ROW.right, center);

  const maxRate = Math.max(1, ...left.map(bucket => rateOf(bucket.count)), ...exits.map(rateOf));
  const widthOf = (rate: number) => WIDTH.min + WIDTH.span * Math.sqrt(Math.min(1, rate / maxRate));
  const groupRel = (client: Client) => groupSel(people.groupOf(client.name));

  const nodes: NetNode[] = [];
  const edges: NetEdge[] = [];
  left.forEach((bucket, index) => {
    const id = groupSel(bucket.key);
    const rate = rateOf(bucket.count);
    const online = bucket.count.online > 0;
    nodes.push({
      id,
      type: NodeKind.Group,
      position: { x: COLUMN.group, y: groupTops[index] },
      data: { bucket, rate },
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
  exits.forEach((exit, index) => {
    const id = exitSel(exit.index);
    const up = exit.state === ExitState.Up;
    const riders = people.clients.filter(client => client.exit === exit.index && client.online);
    nodes.push({ id, type: NodeKind.Exit, position: { x: COLUMN.right, y: rightTops[index] }, data: { exit } });
    edges.push({
      id: "e:" + id,
      source: ENTRY_ID,
      target: id,
      style: {
        stroke: up ? COLOR.exit : COLOR.down,
        strokeWidth: up ? widthOf(rateOf(exit)) : WIDTH.direct,
        strokeDasharray: up ? undefined : DASH.down,
      },
      data: {
        rel: [id, ...riders.flatMap(client => [userSel(client.name), groupRel(client)])],
        flow: up && rateOf(exit) > BUSY_BPS,
      },
    });
  });
  if (!single) {
    nodes.push({
      id: DIRECT_ID,
      type: NodeKind.Direct,
      position: { x: COLUMN.right, y: rightTops[exits.length] },
      data: {},
    });
    edges.push({
      id: "e:" + DIRECT_ID,
      source: ENTRY_ID,
      target: DIRECT_ID,
      style: { stroke: COLOR.live, strokeWidth: WIDTH.direct, strokeDasharray: DASH.direct },
      data: { rel: [], flow: false },
    });
  }
  nodes.push({
    id: ADD_EXIT_ID,
    type: NodeKind.AddExit,
    position: { x: COLUMN.right, y: rightTops[rightCount - 1] },
    data: {},
  });
  return { nodes, edges };
}
