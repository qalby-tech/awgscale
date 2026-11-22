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
  type NodeChange,
  type XYPosition,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ClientSort, NO_GROUP_QUERY, type ClientsQuery } from "@/lib/types";
import { useClients, useLists } from "@/store/clients";
import { exitsOf, nowOf, useCore, usersOf } from "@/store/core";
import { loadTraffic, useTraffic } from "@/store/traffic";
import { useUsers } from "@/store/users";
import { IconButton } from "@/ui/controls";
import { IconName } from "@/ui/Icon";
import { PageHead } from "@/ui/PageHead";

import { useGroupsFresh } from "./hooks";
import { buildGraph, type NetEdge, type NetNode } from "./graph";
import { findSelected, MapSideBody } from "./Inspector";
import { NODE_TYPES } from "./nodes";
import { gather } from "./people";
import { keyOf, kindOf, mapSelect, SelKind, useSelection, type Selection } from "./selection";

const PLACES_KEY = "netPlaces";
const GROUP_LIST = "net:group";
const EXIT_LIST = "net:exit";
const FADED = 0.25;
const LIT = "#ff8a3d"; // --accent
const MINI_NODE = "#9aa3ae"; // --muted
const FIT = { padding: 0.15, duration: 250 } as const;
const ZOOM = { min: 0.3, max: 2 } as const;
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
      ...(selection && !mine ? { opacity: FADED } : {}),
    };
    return {
      ...edge,
      style,
      className: edgeClass(mine, !!selection),
    };
  });
}

function Canvas() {
  const state = useCore(store => store.state);
  const groups = useUsers(store => store.groups)?.groups ?? NO_GROUPS;
  const traffic = useTraffic(store => store.data);
  const selection = useSelection(store => store.selection);
  const lists = useClients(store => store.lists);
  const flow = useReactFlow<NetNode, NetEdge>();
  const [places, setPlaces] = useState<Places>(readPlaces);
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
  const graph = useMemo(() => buildGraph(people, exits, state), [people, exits, state]);
  const nodes = useMemo(
    () => graph.nodes.map(node => (places[node.id] ? { ...node, position: places[node.id] } : node)),
    [graph, places],
  );
  const edges = useMemo(() => lighted(graph.edges, selection), [graph, selection]);
  const selected = findSelected(selection, people, exits);

  // what was selected is gone: draw without it
  useEffect(() => {
    if (selection && !selected) mapSelect(null);
  }, [selection, selected]);

  // the inspector takes its room from the canvas, or gives it back: everything is fitted into what is left
  const sideShown = !!selected;
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
        <aside className="map-side net-side" id="netSide" aria-label="Выбранное на холсте" hidden={!selected}>
          {selected ? (
            <MapSideBody
              key={selection}
              selected={selected}
              people={people}
              exits={exits}
              traffic={traffic}
              now={nowOf(state)}
              onClose={() => mapSelect(null)}
            />
          ) : null}
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
