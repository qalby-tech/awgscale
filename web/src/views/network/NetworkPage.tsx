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
  type FinalConnectionState,
  type NodeChange,
  type XYPosition,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EntryKind, kindOf as entryKind } from "@/lib/entries";
import { plural } from "@/lib/format";
import {
  ClientSort,
  NO_GROUP_QUERY,
  type ClientsQuery,
  type Exit,
  Route,
  type RoutingMode,
  type UserGroup,
} from "@/lib/types";
import { loadExits, sharesOf } from "@/store/exits";
import { useClients, useLists } from "@/store/clients";
import { exitsOf, navigate, nowOf, setLeaveGuard, setUnsavedWork, useCore, usersOf } from "@/store/core";
import { loadTraffic, useTraffic } from "@/store/traffic";
import { saveUserGroups, useUsers } from "@/store/users";
import { NameCard } from "@/ui/groups";
import { IconButton } from "@/ui/controls";
import { Icon, IconName } from "@/ui/Icon";
import { PageHead } from "@/ui/PageHead";

import { followState, leaveSites, modeOf, openSites, openSitesNaming, useSites } from "../sites/store";
import { GroupEditor, PoolSide, RestSide, RuleSide, type RuleView } from "./Edit";
import { useGroupsFresh } from "./hooks";
import { findSelected, MapSideBody } from "./Inspector";
import { addExit, NODE_TYPES } from "./nodes";
import { buildGraph, orderedRules, routeNow, type NetEdge, type NetNode } from "./graph";
import {
  anythingStaged,
  applyPending,
  discard,
  forget,
  pendingCount,
  stageGroup,
  stageOrder,
  usePending,
} from "./pending";
import { forgetRules, loadRules, RuleWay, useRules, type Covered, type Rule } from "./rules";
import { gather } from "./people";
import { groupSel, keyOf, kindOf, mapSelect, SelKind, useSelection, type Selection } from "./selection";

const PLACES_KEY = "netPlaces";
const LEAVE_QUESTION = "Изменения на холсте не применены. Уйти со страницы?";
const GROUP_LIST = "net:group";
const EXIT_LIST = "net:exit";
const LIT = "#8b9cff"; // --accent
const MINI_NODE = "#4a5162"; // --off
const MINI_SIZE = { width: 168, height: 104 } as const;
const PULLED_LINE = { stroke: LIT, strokeWidth: 2, strokeDasharray: "4 5" } as const;
const FIT = { padding: 0.15, duration: 250 } as const;
const ZOOM = { min: 0.15, max: 2 } as const;
const NO_CLIENTS: never[] = [];
const GROUP_NAME_LENGTH = 60;
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
      className: [edge.className, edgeClass(mine, !!selection)].filter(Boolean).join(" "),
    };
  });
}

enum SideKind {
  /** A group, an exit or a user: what the map showed. */
  Map = "map",
  Rule = "rule",
  Rest = "rest",
  Pool = "pool",
}

const NAMES_ONLY_LINK =
  "Список по ссылке и группу, чьи сети обновляются сами, не направить напрямую и не заблокировать.";

/** The rule a selection names, as the inspector edits it, with its place in the (staged) order. */
function ruleOf(
  selection: Selection | null,
  rules: Rule[] | null,
  order: string[] | null,
  covered: Covered,
): RuleView | undefined {
  if (!selection || kindOf(selection) !== SelKind.Rule || !rules) return undefined;
  const key = keyOf(selection);
  const ordered = orderedRules(rules, order);
  const place = ordered.findIndex(each => each.id === key);
  if (place < 0) return undefined;
  const rule = ordered[place];
  const network = rule.items.find(item => entryKind(item) === EntryKind.Net);
  let namesOnly: string | null = null;
  if (rule.url || rule.auto) namesOnly = NAMES_ONLY_LINK;
  else if (network) namesOnly = "Напрямую и в блок — только сайты, а здесь есть сеть " + network + ".";
  return {
    id: rule.id,
    name: rule.name,
    items: rule.items,
    now: routeNow(rule),
    namesOnly,
    blockList: rule.route === RuleWay.Block,
    byLink: !!rule.url,
    covered: covered[rule.id] ?? {},
    scope: rule.groups ?? [],
    place,
    count: ordered.length,
  };
}

interface SideInput {
  selected: ReturnType<typeof findSelected>;
  groups: UserGroup[];
  rule: RuleView | undefined;
  exits: Exit[];
  single: boolean;
  modeNow: RoutingMode;
}

/** What the inspector shows for the selection; null when nothing (or nothing that is still there). */
function sideOf(selection: Selection | null, input: SideInput): SideKind | null {
  if (!selection) return null;
  switch (kindOf(selection)) {
    case SelKind.Rule:
      return input.rule ? SideKind.Rule : null;
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

/** Where a pulled line was let go: the block under the pointer, by what it selects. */
function droppedOn(event: MouseEvent | TouchEvent): Selection | null {
  const point = "changedTouches" in event ? event.changedTouches[0] : event;
  if (!point) return null;
  const under = document.elementFromPoint(point.clientX, point.clientY);
  const block = under instanceof HTMLElement ? under.closest<HTMLElement>("[data-k]") : null;
  return block?.dataset.k ?? null;
}

/**
 * A group's point let go on an exit: the group is staged onto that exit; on «За границу»: back to the shares (the
 * server decides). Anywhere else nothing changes.
 */
function pulled(groups: UserGroup[], from: string | null | undefined, to: Selection | null): void {
  if (!from || !to || kindOf(from) !== SelKind.Group) return;
  const group = groups.find(each => each.id != null && each.id === keyOf(from));
  if (!group) return;
  if (kindOf(to) === SelKind.Exit) stageGroup(group, { exit: Number(keyOf(to)) });
  else if (kindOf(to) === SelKind.Pool) stageGroup(group, { exit: null });
}

/** A new group of people, made at once (as on «Пользователи»), and selected on the canvas. */
async function newGroup(name: string): Promise<void> {
  const saved = await saveUserGroups(draft => {
    draft.unshift({ id: null, name, space: false, members: [] });
  });
  const made = saved ? useUsers.getState().groups?.groups.find(group => group.id != null && group.name === name) : null;
  if (made?.id) mapSelect(groupSel(made.id));
}

/** A new group of sites is named on «Сайты», where its sites are added. */
function newRule(): void {
  openSitesNaming();
  navigate(Route.Sites);
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
  const [naming, setNaming] = useState(false);

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
  const rulesList = useRules(store => store.rules);
  const covered = useRules(store => store.covered);
  const pending = usePending();
  const single = !!state?.routing?.single_server;
  const modeNow = state?.routing?.mode ?? modeOf(sites ?? null);
  const sharesNow = useMemo(() => sharesOf(exits), [exits]);
  const graph = useMemo(
    () =>
      buildGraph({
        people,
        exits,
        state,
        userGroups: groups,
        groupEdits: pending.groups,
        rules: rulesList,
        order: pending.order,
        covered,
        routes: pending.routes,
        scopes: pending.scopes,
        mode: pending.mode ?? modeNow,
        modeStaged: !!pending.mode,
        shares: pending.shares ?? sharesNow,
        sharesStaged: !!pending.shares,
      }),
    [people, exits, state, groups, pending, rulesList, covered, modeNow, sharesNow],
  );
  const staged = pendingCount(pending);
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
  const rule = ruleOf(selection, rulesList, pending.order, covered);
  const side = sideOf(selection, { selected, groups, rule, exits, single, modeNow });
  const move = (id: string, step: number) => {
    const now = (rulesList ?? []).map(each => each.id);
    const ids = orderedRules(rulesList ?? [], pending.order).map(each => each.id);
    const from = ids.indexOf(id);
    const to = from + step;
    if (from < 0 || to < 0 || to >= ids.length) return;
    [ids[from], ids[to]] = [ids[to], ids[from]];
    stageOrder(ids, now);
  };

  // what was selected is gone: draw without it
  const gone = !!selection && side === null && !(kindOf(selection) === SelKind.Rule && rulesList === null);
  useEffect(() => {
    if (gone) mapSelect(null);
  }, [gone]);

  // the lists of sites and of blocked sites are read for the canvas, their drafts do not outlive it, and what is
  // staged guards the way out of the section
  useEffect(() => {
    void openSites(); // the mode, and its switch with its question
    void loadRules();
    setLeaveGuard(() => !anythingStaged() || confirm(LEAVE_QUESTION));
    setUnsavedWork(anythingStaged);
    return () => {
      setLeaveGuard(null);
      setUnsavedWork(null);
      leaveSites();
      forgetRules();
      forget();
    };
  }, []);

  // the list of sites changed in another window: an idle canvas takes it, as «Сайты» does
  useEffect(() => followState(state), [state]);

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

  // a window of another width (a phone turned, a window resized): everything is fitted again; a phone's address bar
  // that hides changes only the height, and keeps the admin's zoom
  useEffect(() => {
    let width = window.innerWidth;
    const onResize = () => {
      if (window.innerWidth === width) return;
      width = window.innerWidth;
      void flow.fitView(FIT);
    };
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

  const onConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent, connection: FinalConnectionState) =>
      pulled(groups, connection.fromNode?.id, droppedOn(event)),
    [groups],
  );

  const arrange = () => {
    savePlaces({});
    setPlaces({});
    window.setTimeout(() => void flow.fitView(FIT), 0);
  };

  return (
    <>
      <PageHead title="Сеть" />
      <div className="net-row">
        <div className="net" id="net">
          <div className="net-tools" role="toolbar" aria-label="Холст">
            <button type="button" className="btn net-tool" id="netNewGroup" onClick={() => setNaming(true)}>
              <Icon name={IconName.Plus} size={16} stroke={2} />
              <span>Группа</span>
            </button>
            <button type="button" className="btn net-tool" id="netNewRule" onClick={newRule}>
              <Icon name={IconName.Plus} size={16} stroke={2} />
              <span>Правило</span>
            </button>
            {single ? null : (
              <button type="button" className="btn net-tool" onClick={addExit}>
                <Icon name={IconName.Plus} size={16} stroke={2} />
                <span>Выход</span>
              </button>
            )}
            <IconButton icon={IconName.Fit} label="Показать всё" onClick={() => void flow.fitView(FIT)} />
            <IconButton icon={IconName.Sync} label="Расставить заново" onClick={arrange} />
          </div>
          {naming ? (
            <div className="net-name">
              <NameCard
                placeholder="Название группы"
                maxLength={GROUP_NAME_LENGTH}
                onCreate={name => {
                  setNaming(false);
                  void newGroup(name);
                }}
                onCancel={() => setNaming(false)}
              />
            </div>
          ) : null}
          {staged ? (
            <div className="net-pending" role="status">
              <span>{changesText(staged)}</span>
              <small>не применены</small>
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
            nodesConnectable={!single}
            onConnectEnd={onConnectEnd}
            connectionLineStyle={PULLED_LINE}
            edgesFocusable={false}
            deleteKeyCode={null}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1} />
            <Controls showInteractive={false} fitViewOptions={FIT} />
            <MiniMap
              pannable
              zoomable
              className="net-mini"
              nodeColor={MINI_NODE}
              nodeBorderRadius={6}
              style={MINI_SIZE}
            />
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
          {rule ? <RuleSide key={rule.id} rule={rule} single={single} move={move} people={groups} /> : null}
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
    void loadExits(); // the countries of the exits
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
