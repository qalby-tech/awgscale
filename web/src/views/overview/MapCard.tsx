"use client";

/**
 * The map of the network: groups of users → the entry → the exits. A click
 * selects a group, a user or an exit: its path is highlighted and the side
 * panel shows what is known about it.
 */
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";

import { ago, fmtBps, hostOf } from "@/lib/format";
import { presenceOf } from "@/lib/presence";
import { PersistedSet } from "@/lib/storage";
import {
  ClientSort,
  ExitState,
  NO_GROUP_QUERY,
  Route,
  type Client,
  type ClientsQuery,
  type Exit,
  type PanelState,
} from "@/lib/types";
import { useClients, useLists } from "@/store/clients";
import { exitsOf, navigate, nowOf, useCore, usersOf } from "@/store/core";
import { openExitForm } from "@/store/exits";
import { useTraffic } from "@/store/traffic";
import { groupsOf, useUsers } from "@/store/users";
import { Dot, DotKind, IconButton } from "@/ui/controls";
import { Icon, IconName } from "@/ui/Icon";
import { PresenceMark } from "@/ui/PresenceMark";

import {
  ADD_H,
  BUSY_BPS,
  ENTRY_H,
  EXIT_H,
  gather,
  layoutMap,
  rateOf,
  RightKind,
  type GroupBox,
  type Link,
  type MapDims,
  type MapLayout,
  type RightBox,
} from "./mapLayout";
import { findSelected, MapSideBody } from "./MapSide";
import { DEFAULT_DIMS, useMapView } from "./mapView";
import { exitSel, mapSelect, userSel, useSelection, type Selection } from "./selection";

const ZOOM_STEP = 1.25;
/** How far above the middle line the note «пользователей нет» stands. */
const EMPTY_NOTE_RISE = 10;
const LIT = "#ff8a3d"; // --accent
const FADED = 0.3;
const FLOW_SHARE = 0.45;

/** Which groups on the map are unfolded ("" is «Без группы»); remembered in this browser. */
const unfolded = new PersistedSet("mapOpen");

const NO_CLIENTS: Client[] = [];
/** The users of a group are read only while it is unfolded; those of an exit, while it is selected. */
const groupList = (key: string): string => "map:g:" + key;
const exitList = (selection: Selection | null): string => "map:" + (selection ?? "");
const EXIT_SELECTION = /^x:\d+$/;

function mapLists(open: string[], grouped: boolean, selection: Selection | null): Record<string, ClientsQuery> {
  const lists: Record<string, ClientsQuery> = {};
  open.forEach(key => {
    lists[groupList(key)] = { ...(grouped ? { group: key || NO_GROUP_QUERY } : {}), sort: ClientSort.Seen };
  });
  if (selection && EXIT_SELECTION.test(selection))
    lists[exitList(selection)] = { exit: Number(selection.slice(2)), sort: ClientSort.Seen };
  return lists;
}

const placed = (left: number, top: number, width: number, height?: number): CSSProperties => ({
  left,
  top,
  width,
  height,
});
const current = (on: boolean) => (on ? ("true" as const) : undefined);

function Links({
  links,
  dims,
  height,
  selection,
}: {
  links: Link[];
  dims: MapDims;
  height: number;
  selection: Selection | null;
}) {
  return (
    <svg
      width={dims.width}
      height={height}
      viewBox={"0 0 " + dims.width + " " + height}
      className="map-links"
      aria-hidden="true"
    >
      {links.flatMap(link => {
        const mine = !!selection && link.rel.includes(selection);
        const faded = !!selection && !mine;
        return [
          <path
            key={link.key}
            d={link.path}
            stroke={mine ? LIT : link.stroke}
            strokeWidth={link.width}
            strokeDasharray={link.dash}
            opacity={faded ? FADED : undefined}
          />,
          link.flow && !faded ? (
            <path
              key={link.key + ":flow"}
              d={link.path}
              className="flow"
              strokeWidth={Math.max(1, link.width * FLOW_SHARE)}
            />
          ) : null,
        ];
      })}
    </svg>
  );
}

interface GroupNodeProps {
  box: GroupBox;
  dims: MapDims;
  selection: Selection | null;
  exitName: (index: number | null) => string;
  onHead: (box: GroupBox) => void;
}

function UserNode({
  client,
  selection,
  exitName,
}: {
  client: Client;
  selection: Selection | null;
  exitName: (index: number | null) => string;
}) {
  const id = userSel(client.name);
  const mine = selection === id;
  const rate = rateOf(client);
  return (
    <button
      type="button"
      className={"node-user" + (client.online ? "" : " off") + (mine ? " sel" : "")}
      data-k={id}
      aria-current={current(mine)}
      title={
        client.name +
        " · " +
        client.ip +
        "\nвыход: " +
        exitName(client.exit) +
        "\n↓ " +
        fmtBps(client.down_bps) +
        "  ↑ " +
        fmtBps(client.up_bps)
      }
      onClick={() => mapSelect(mine ? null : id)}
    >
      <PresenceMark presence={presenceOf(client)} />
      <span>{client.name}</span>
      {rate > BUSY_BPS ? <small>{fmtBps(rate)}</small> : null}
    </button>
  );
}

function GroupNode({ box, dims, selection, exitName, onHead }: GroupNodeProps) {
  const bucket = box.bucket;
  const mine = selection === box.id;
  const count = box.online + " из " + bucket.count.users;
  return (
    <div
      className={"node group" + (box.online ? "" : " off") + (mine ? " sel" : "")}
      style={placed(dims.groupLeft, box.top, dims.groupWidth, box.height)}
    >
      <button
        type="button"
        className="node-head"
        aria-expanded={box.open}
        aria-current={current(mine)}
        data-k={box.id}
        title={bucket.name + "\n" + count + " онлайн" + (box.rate ? "\n" + fmtBps(box.rate) : "")}
        onClick={() => onHead(box)}
      >
        <Icon name={IconName.Chevron} size={14} stroke={2.2} />
        <Dot kind={box.online ? DotKind.Good : DotKind.Off} />
        <b>{bucket.name}</b>
        {box.rate > BUSY_BPS ? <small className="rate">{fmtBps(box.rate)}</small> : null}
        <small className="cnt">{count}</small>
      </button>
      {box.open
        ? bucket.list.map(client => (
            <UserNode key={client.name} client={client} selection={selection} exitName={exitName} />
          ))
        : null}
    </div>
  );
}

function ExitNode({
  exit,
  users,
  y,
  dims,
  selection,
  now,
}: {
  exit: Exit;
  users: number;
  y: number;
  dims: MapDims;
  selection: Selection | null;
  now: number;
}) {
  const id = exitSel(exit.index);
  const mine = selection === id;
  const up = exit.state === ExitState.Up;
  const rate = rateOf(exit);
  return (
    <button
      type="button"
      className={"node exit" + (up ? "" : " down") + (mine ? " sel" : "")}
      data-k={id}
      aria-current={current(mine)}
      style={placed(dims.exitLeft, y - EXIT_H / 2, dims.exitWidth)}
      title={
        exit.name +
        "\n" +
        hostOf(exit.endpoint) +
        "\n↓ " +
        fmtBps(exit.down_bps) +
        "  ↑ " +
        fmtBps(exit.up_bps) +
        "\nсвязь: " +
        ago(exit.handshake, now)
      }
      onClick={() => mapSelect(mine ? null : id)}
    >
      <Dot kind={up ? DotKind.Good : DotKind.Crit} />
      <span>
        <b>{exit.name}</b>
        <br />
        <small>{up ? users + " польз." + (rate > BUSY_BPS ? " · " + fmtBps(rate) : "") : "недоступен"}</small>
      </span>
    </button>
  );
}

function RightNode({
  box,
  dims,
  selection,
  now,
}: {
  box: RightBox;
  dims: MapDims;
  selection: Selection | null;
  now: number;
}) {
  if (box.kind === RightKind.Exit)
    return <ExitNode exit={box.exit} users={box.users} y={box.y} dims={dims} selection={selection} now={now} />;
  if (box.kind === RightKind.Direct)
    return (
      <div className="node exit direct" style={placed(dims.exitLeft, box.y - EXIT_H / 2, dims.exitWidth)}>
        <Icon name={IconName.Home} size={16} stroke={1.8} />
        <span>
          <b>Напрямую</b>
          <br />
          <small>сайты своей страны</small>
        </span>
      </div>
    );
  return (
    <button
      type="button"
      className="node add-exit"
      data-k="x:add"
      title="Подключить арендованный сервер как новый выход"
      style={placed(dims.exitLeft, box.y - ADD_H / 2, dims.exitWidth)}
      onClick={startNewExit}
    >
      <Icon name={IconName.Plus} size={16} stroke={2} />
      <span>Новый выход</span>
    </button>
  );
}

function entryNote(state: PanelState | null): string {
  if (!state) return "нет данных";
  const totals = state.totals ?? {};
  return (totals.online || 0) + " онлайн · " + fmtBps((totals.down_bps || 0) + (totals.up_bps || 0));
}

/**
 * Closing the side panel from inside it: the focus goes back to the node the panel was about.
 * → the function that closes the panel.
 */
function useFocusBack(
  selection: Selection | null,
  world: RefObject<HTMLDivElement | null>,
  side: RefObject<HTMLElement | null>,
): () => void {
  const focusBack = useRef<Selection | null>(null);

  useEffect(() => {
    const key = focusBack.current;
    if (selection || !key) return;
    focusBack.current = null;
    const nodes = [...(world.current?.querySelectorAll<HTMLElement>("[data-k]") ?? [])];
    nodes.find(node => node.dataset.k === key)?.focus({ preventScroll: true });
  }, [selection, world]);

  return () => {
    if (selection && side.current?.contains(document.activeElement)) focusBack.current = selection;
    mapSelect(null);
  };
}

interface MapWorldProps {
  layout: MapLayout;
  dims: MapDims;
  selection: Selection | null;
  now: number;
  exitName: (index: number | null) => string;
  onHead: (box: GroupBox) => void;
  /** What the entry's node says under its name. */
  entryText: string;
  /** There are no users at all. */
  empty: boolean;
}

/** Everything drawn on the canvas: the links, the groups at the left, the entry in the middle, the exits at the right. */
function MapWorld({ layout, dims, selection, now, exitName, onHead, entryText, empty }: MapWorldProps) {
  return (
    <>
      <Links links={layout.links} dims={dims} height={layout.height} selection={selection} />
      {layout.groups.map(box => (
        <GroupNode key={box.id} box={box} dims={dims} selection={selection} exitName={exitName} onHead={onHead} />
      ))}
      {empty ? (
        <div className="node-more" style={placed(dims.groupLeft, layout.centerY - EMPTY_NOTE_RISE, dims.groupWidth)}>
          пользователей нет
        </div>
      ) : null}
      <div className="node entry" style={placed(dims.entryLeft, layout.centerY - ENTRY_H / 2, dims.entryWidth)}>
        <b>Вход</b>
        <small>{entryText}</small>
      </div>
      {layout.right.map(box => (
        <RightNode
          key={box.kind === RightKind.Exit ? exitSel(box.exit.index) : box.kind}
          box={box}
          dims={dims}
          selection={selection}
          now={now}
        />
      ))}
    </>
  );
}

/** The map is where the network is put together: a new exit starts here, on the page of the servers. */
function startNewExit(): void {
  openExitForm(true);
  navigate(Route.Servers);
}

/** A click on the empty canvas. */
function clearSelection(): void {
  if (useSelection.getState().selection) mapSelect(null);
}

export function MapCard() {
  const state = useCore(state => state.state);
  const groups = groupsOf(useUsers(state => state.groups));
  const traffic = useTraffic(state => state.data);
  const selection = useSelection(state => state.selection);
  const [open, setOpen] = useState(() => unfolded.values());
  const [dims, setDims] = useState(DEFAULT_DIMS);
  const [drawn, redraw] = useReducer((count: number) => count + 1, 0);
  const box = useRef<HTMLDivElement>(null);
  const world = useRef<HTMLDivElement>(null);
  const side = useRef<HTMLElement>(null);

  const exits = exitsOf(state);
  const now = nowOf(state);
  const lists = useClients(store => store.lists);
  const people = useMemo(
    () =>
      gather(
        usersOf(state),
        groups,
        key => lists[groupList(key)]?.clients ?? NO_CLIENTS,
        lists[exitList(selection)]?.clients ?? NO_CLIENTS,
      ),
    [state, groups, lists, selection],
  );
  useLists(mapLists(open, groups.length > 0, selection));
  const layout = useMemo(() => layoutMap(dims, people, exits, key => open.includes(key)), [dims, people, exits, open]);
  const selected = findSelected(selection, people, exits);
  const exitName = (index: number | null) => exits.find(exit => exit.index === index)?.name ?? "—";

  const view = useMapView(box, world, clearSelection);
  const { settle, isFitted } = view;
  const sideShown = !!selected;

  // The panel takes its room first; then the canvas is measured and the world fitted into it — again
  // whenever the world, the room left by the panel or the window changes, or a redraw is asked for.
  useLayoutEffect(() => {
    const other = settle(dims, layout.height);
    if (other) setDims(other);
  }, [settle, dims, layout.height, sideShown, drawn]);

  useEffect(() => {
    const onResize = () => {
      if (isFitted()) redraw();
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [isFitted]);

  const closeSide = useFocusBack(selection, world, side);
  const showAll = () => {
    view.fitAgain();
    redraw();
  };

  // what was selected is gone: draw without it
  useEffect(() => {
    if (selection && !selected) mapSelect(null);
  }, [selection, selected]);

  /** The first click selects and unfolds; a click on the selected one folds it. */
  const onHead = (box: GroupBox) => {
    if (selection === box.id || !box.open) {
      unfolded.toggle(box.bucket.key);
      setOpen(unfolded.values());
    }
    mapSelect(box.id);
  };

  return (
    <section className="card pad map-card" aria-label="Карта сети">
      <div className="map-top">
        <h2 className="card-title">Карта сети</h2>
        <div className="map-tools">
          <IconButton icon={IconName.Minus} label="Мельче" onClick={() => view.zoom(1 / ZOOM_STEP)} />
          <IconButton icon={IconName.Plus} label="Крупнее" onClick={() => view.zoom(ZOOM_STEP)} />
          <IconButton icon={IconName.Fit} label="Показать всё" onClick={showAll} />
        </div>
      </div>
      <div className="map-row">
        <div className="map" id="map" ref={box} {...view.pointer}>
          <div className="map-world" id="mapWorld" ref={world} style={{ width: dims.width, height: layout.height }}>
            <MapWorld
              layout={layout}
              dims={dims}
              selection={selection}
              now={now}
              exitName={exitName}
              onHead={onHead}
              entryText={entryNote(state)}
              empty={!usersOf(state).users}
            />
          </div>
        </div>
        <aside
          className="map-side"
          id="mapSide"
          ref={side}
          aria-label="Выбранное на карте"
          hidden={!selected}
          data-sel={selection ?? ""}
        >
          {selected ? (
            <MapSideBody
              key={selection}
              selected={selected}
              people={people}
              exits={exits}
              traffic={traffic}
              now={now}
              onClose={closeSide}
            />
          ) : null}
        </aside>
      </div>
    </section>
  );
}
