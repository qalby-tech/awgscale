"use client";

/**
 * «Сеть»: the network on a canvas. Groups of users, the entry, the exits; a click on a block opens what is
 * known about it at the right. The blocks can be moved; where the admin put them is kept in this browser.
 */
import "@xyflow/react/dist/style.css";

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Dimensions,
  type NodeChange,
  type XYPosition,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { plural } from "@/lib/format";
import {
  ClientSort,
  NO_GROUP_QUERY,
  type ClientsQuery,
  type Exit,
  type RoutingMode,
  type UserGroup,
} from "@/lib/types";
import { sharesOf } from "@/store/exits";
import { useClients, useLists } from "@/store/clients";
import { exitsOf, nowOf, useCore, usersOf } from "@/store/core";
import { loadTraffic, useTraffic } from "@/store/traffic";
import { useUsers } from "@/store/users";
import { IconButton } from "@/ui/controls";
import { IconName } from "@/ui/Icon";
import { PageHead } from "@/ui/PageHead";

import { leaveBlocked, openBlocked, useBlocked } from "../blocked/store";
import type { Block, BlockGroup } from "../blocked/types";
import { leaveSites, modeOf, openSites, useSites } from "../sites/store";
import type { DraftGroup } from "../sites/types";
import { BlockSide, GroupEditor, PoolSide, RestSide, RuleSide } from "./Edit";
import { useGroupsFresh } from "./hooks";
import { buildGraph, type NetEdge, type NetNode } from "./graph";
import { findSelected, MapSideBody } from "./Inspector";
import { NODE_TYPES } from "./nodes";
import { applyPending, discard, forget, pendingCount, usePending, useSitesDirty } from "./pending";
import { gather } from "./people";
import { keyOf, kindOf, mapSelect, SelKind, useSelection, type Selection } from "./selection";

const PLACES_KEY = "netPlaces";
const GROUP_LIST = "net:group";
const EXIT_LIST = "net:exit";
const LIT = "#ff8a3d"; // --accent
const MINI_NODE = "#9aa3ae"; // --muted
const FIT = { padding: 0.15, duration: 250 } as const;
const ZOOM = { min: 0.15, max: 2 } as const;
const NO_CLIENTS: never[] = [];
const NO_GROUPS: never[] = [];

type Places = Record<string, XYPosition>;

const isPlace = (value: unknown): value is XYPosition =>
  !!value &&
  typeof value === "object" &&
  "x" in value &&
  "y" in value &&
  Number.isFinite(value.x) &&
  Number.isFinite(value.y);

/** Where the admin put the blocks, as this browser keeps it; anything that is not a place is dropped. */
function readPlaces(): Places {
  let saved: unknown = {};
  try {
    saved = JSON.parse(localStorage.getItem(PLACES_KEY) || "{}");
  } catch {
    /* no storage: nothing was moved */
  }
  if (!saved || typeof saved !== "object") return {};
  const places: Places = {};
  for (const [id, place] of Object.entries(saved)) if (isPlace(place)) places[id] = { x: place.x, y: place.y };
  return places;
}

function savePlaces(places: Places): void {
  try {
    if (Object.keys(places).length) localStorage.setItem(PLACES_KEY, JSON.stringify(places));
    else localStorage.removeItem(PLACES_KEY);
  } catch {
    /* kept for this visit only */
  }
}

/** The users read for the canvas: those of the group last picked (its inspector lists them), and of the picked exit. */
function listsFor(group: string | null, grouped: boolean, selection: Selection | null): Record<string, ClientsQuery> {
  const lists: Record<string, ClientsQuery> = {};
  if (group !== null)
    lists[GROUP_LIST] = { ...(grouped ? { group: group || NO_GROUP_QUERY } : {}), sort: ClientSort.Seen };
  if (selection && kindOf(selection) === SelKind.Exit)
    lists[EXIT_LIST] = { exit: Number(keyOf(selection)), sort: ClientSort.Seen };
  return lists;
}

function edgeClass(mine: boolean, anySelected: boolean): string {
  if (mine) return "lit";
  return anySelected ? "dim" : "";
}

/** The edges of the selected thing are lit, the others fade; with nothing selected, all are as they are. */
function lighted(edges: NetEdge[], selection: Selection | null): NetEdge[] {
  return edges.map(edge => {
    const rel = edge.data?.rel ?? [];
    const mine = !!selection && rel.includes(selection);
    const style = {
      ...edge.style,
      ...(mine ? { stroke: LIT } : {}),
    };
    return {
      ...edge,
      style,
      className: edgeClass(mine, !!selection),
    };
  });
}

enum SideKind {
  /** A group, an exit or a user: what the map showed. */
  Map = "map",
  Rule = "rule",
  Block = "block",
  Rest = "rest",
  Pool = "pool",
}

const ruleOf = (selection: Selection | null, draft: DraftGroup[] | null): DraftGroup | undefined =>
  selection && kindOf(selection) === SelKind.Rule ? draft?.find(each => each.key === keyOf(selection)) : undefined;
const blockOf = (selection: Selection | null, block: Block | null): BlockGroup | undefined =>
  selection && kindOf(selection) === SelKind.Block
    ? block?.groups.find(each => each.id === keyOf(selection))
    : undefined;

/** The lists a selected rule is in are still being read: it is not gone. */
function waiting(selection: Selection, draft: DraftGroup[] | null, block: Block | null): boolean {
  const kind = kindOf(selection);
  return (kind === SelKind.Rule && draft === null) || (kind === SelKind.Block && block === null);
}

interface SideInput {
  selected: ReturnType<typeof findSelected>;
  groups: UserGroup[];
  draft: DraftGroup[] | null;
  block: Block | null;
  exits: Exit[];
  single: boolean;
  modeNow: RoutingMode;
}

/** What the inspector shows for the selection; null when nothing (or nothing that is still there). */
function sideOf(selection: Selection | null, input: SideInput): SideKind | null {
  if (!selection) return null;
  switch (kindOf(selection)) {
    case SelKind.Rule:
      return ruleOf(selection, input.draft) ? SideKind.Rule : null;
    case SelKind.Block:
      return blockOf(selection, input.block) ? SideKind.Block : null;
    case SelKind.Rest:
      return SideKind.Rest;
    case SelKind.Pool:
      return input.single ? null : SideKind.Pool;
    default:
      return input.selected ? SideKind.Map : null;
  }
}

const CHANGES: readonly [string, string, string] = ["изменение", "изменения", "изменений"];

function changesText(count: number): string {
  return count + " " + plural(count, CHANGES);
}

/** The editor under a group's facts: only a group the server keeps (not «Без группы»). */
function GroupEditorFor({
  groups,
  groupKey,
  exits,
  single,
}: {
  groups: UserGroup[];
  groupKey: string;
  exits: Exit[];
  single: boolean;
}) {
  const group = groups.find(each => each.id != null && each.id === groupKey);
  return group ? <GroupEditor group={group} exits={exits} single={single} /> : null;
}

function Canvas() {
  const state = useCore(store => store.state);
  const groups = useUsers(store => store.groups)?.groups ?? NO_GROUPS;
  const traffic = useTraffic(store => store.data);
  const selection = useSelection(store => store.selection);
  const lists = useClients(store => store.lists);
  const flow = useReactFlow<NetNode, NetEdge>();
  const [places, setPlaces] = useState<Places>(readPlaces);
  /** The sizes the canvas measured: a controlled canvas is told them back (the minimap draws from them). */
  const [sizes, setSizes] = useState<Record<string, Dimensions>>({});
  const [group, setGroup] = useState<string | null>(null);

  useEffect(() => {
    if (selection && kindOf(selection) === SelKind.Group) setGroup(keyOf(selection));
  }, [selection]);
  useLists(listsFor(group, groups.length > 0, selection));

  const exits = exitsOf(state);
  const people = useMemo(
    () =>
      gather(
        usersOf(state),
        groups,
        key => (key === group ? (lists[GROUP_LIST]?.clients ?? NO_CLIENTS) : NO_CLIENTS),
        lists[EXIT_LIST]?.clients ?? NO_CLIENTS,
      ),
    [state, groups, lists, group],
  );
  const sites = useSites(store => store.sites);
  const draft = useSites(store => store.draft);
  const block = useBlocked(store => store.block);
  const pending = usePending();
  const sitesDirty = useSitesDirty();
  const single = !!state?.routing?.single_server;
  const modeNow = modeOf(sites ?? null);
  const sharesNow = useMemo(() => sharesOf(exits), [exits]);
  const sitesChanged = useMemo(() => {
    const saved = new Map((sites?.groups ?? []).map(each => [each.id, !!each.on]));
    return new Set((draft ?? []).filter(each => saved.get(each.key) !== each.on).map(each => each.key));
  }, [sites, draft]);
  const graph = useMemo(
    () =>
      buildGraph({
        people,
        exits,
        state,
        userGroups: groups,
        groupEdits: pending.groups,
        sites: draft,
        sitesChanged,
        blocks: block?.groups ?? null,
        blockEdits: pending.blocks,
        mode: pending.mode ?? modeNow,
        modeStaged: !!pending.mode,
        shares: pending.shares ?? sharesNow,
        sharesStaged: !!pending.shares,
      }),
    [people, exits, state, groups, pending, draft, sitesChanged, block, modeNow, sharesNow],
  );
  const staged = pendingCount(pending, sitesDirty);
  const nodes = useMemo(
    () =>
      graph.nodes.map(node => ({
        ...node,
        ...(places[node.id] ? { position: places[node.id] } : {}),
        ...(sizes[node.id] ? { measured: sizes[node.id] } : {}),
      })),
    [graph, places, sizes],
  );
  const edges = useMemo(() => lighted(graph.edges, selection), [graph, selection]);
  const selected = findSelected(selection, people, exits);
  const side = sideOf(selection, { selected, groups, draft, block, exits, single, modeNow });
  const ruleGroup = ruleOf(selection, draft);
  const blockGroup = blockOf(selection, block);

  // what was selected is gone: draw without it
  const gone = !!selection && side === null && !waiting(selection, draft, block);
  useEffect(() => {
    if (gone) mapSelect(null);
  }, [gone]);

  // the lists of sites and of blocked sites are read for the canvas, and their drafts do not outlive it
  useEffect(() => {
    void openSites();
    void openBlocked();
    return () => {
      leaveSites();
      leaveBlocked();
      forget();
    };
  }, []);

  // staged changes are not lost to a closed tab without a word
  useEffect(() => {
    if (!staged) return;
    const onLeave = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", onLeave);
    return () => window.removeEventListener("beforeunload", onLeave);
  }, [staged]);

  // the inspector takes its room from the canvas, or gives it back: everything is fitted into what is left
  const sideShown = side !== null;
  const firstDraw = useRef(true);
  useEffect(() => {
    if (firstDraw.current) {
      firstDraw.current = false;
      return;
    }
    const timer = window.setTimeout(() => void flow.fitView(FIT), 0);
    return () => window.clearTimeout(timer);
  }, [sideShown, flow]);

  // a window of another size (a phone turned, a window resized): everything is fitted again
  useEffect(() => {
    const onResize = () => void flow.fitView(FIT);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [flow]);

  /** A block moved by the admin stays where they put it. */
  const onNodesChange = useCallback((changes: NodeChange<NetNode>[]) => {
    const measured = changes.flatMap(change =>
      "dimensions" in change && change.dimensions ? [[change.id, change.dimensions] as const] : [],
    );
    if (measured.length) setSizes(current => ({ ...current, ...Object.fromEntries(measured) }));
    setPlaces(current => {
      let next = current;
      for (const change of changes) {
        if (!("position" in change) || !change.position) continue;
        next = { ...next, [change.id]: change.position };
        if (!change.dragging) savePlaces(next);
      }
      return next;
    });
  }, []);

  const arrange = () => {
    savePlaces({});
    setPlaces({});
    window.setTimeout(() => void flow.fitView(FIT), 0);
  };

  return (
    <>
      <PageHead title="Сеть">
        <div className="actions">
          {staged ? (
            <div className="net-pending" role="status">
              <span>{changesText(staged)}</span>
              <button type="button" className="btn" disabled={pending.applying} onClick={discard}>
                Отменить
              </button>
              <button
                type="button"
                className="btn primary"
                id="netApply"
                disabled={pending.applying}
                onClick={() => void applyPending()}
              >
                Применить
              </button>
            </div>
          ) : null}
          <IconButton icon={IconName.Fit} label="Показать всё" onClick={() => void flow.fitView(FIT)} />
          <IconButton icon={IconName.Sync} label="Расставить заново" onClick={arrange} />
        </div>
      </PageHead>
      <div className="net-row">
        <div className="net" id="net">
          <ReactFlow<NetNode, NetEdge>
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onPaneClick={() => mapSelect(null)}
            colorMode="dark"
            fitView
            fitViewOptions={FIT}
            minZoom={ZOOM.min}
            maxZoom={ZOOM.max}
            elementsSelectable={false}
            nodesConnectable={false}
            edgesFocusable={false}
            deleteKeyCode={null}
          >
            <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
            <Controls showInteractive={false} fitViewOptions={FIT} />
            <MiniMap pannable zoomable className="net-mini" nodeColor={MINI_NODE} nodeBorderRadius={6} />
          </ReactFlow>
        </div>
        <aside className="map-side net-side" id="netSide" aria-label="Выбранное на холсте" hidden={side === null}>
          {side === SideKind.Map && selected ? (
            <>
              <MapSideBody
                key={selection}
                selected={selected}
                people={people}
                exits={exits}
                traffic={traffic}
                now={nowOf(state)}
                onClose={() => mapSelect(null)}
              />
              {selected.kind === SelKind.Group ? (
                <GroupEditorFor groups={groups} groupKey={selected.bucket.key} exits={exits} single={single} />
              ) : null}
            </>
          ) : null}
          {ruleGroup ? <RuleSide group={ruleGroup} /> : null}
          {blockGroup ? <BlockSide group={blockGroup} /> : null}
          {side === SideKind.Rest ? <RestSide now={modeNow} /> : null}
          {side === SideKind.Pool ? <PoolSide key={exits.length} exits={exits} /> : null}
        </aside>
      </div>
    </>
  );
}

export function NetworkPage() {
  const state = useCore(store => store.state);
  const tick = useCore(store => store.tick);
  useEffect(() => {
    loadTraffic(true);
  }, []);
  useEffect(() => {
    loadTraffic();
  }, [tick]);
  useGroupsFresh(state);
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  );
}
