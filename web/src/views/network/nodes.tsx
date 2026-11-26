"use client";

/** The blocks of the canvas. A click on one selects it (the inspector opens); the canvas itself moves them. */
import { Handle, Position, type NodeProps, type NodeTypes } from "@xyflow/react";
import type { ReactNode } from "react";

import { fmtBps } from "@/lib/format";
import { ExitState, Route } from "@/lib/types";
import { navigate } from "@/store/core";
import { openExitForm } from "@/store/exits";
import { Dot, DotKind } from "@/ui/controls";
import { Icon, IconName } from "@/ui/Icon";

import {
  NodeKind,
  Target,
  type EntryNode,
  type ExitNode,
  type GroupNode,
  type RuleNode,
  type TargetNode,
} from "./graph";
import { BUSY_BPS } from "./people";
import { mapSelect, useSelection } from "./selection";

/** The points edges come in and go out of: drawn, never dragged (the edges are what the network is, not drawings). */
function Ports({ left, right }: { left?: boolean; right?: boolean }) {
  return (
    <>
      {left ? <Handle type="target" position={Position.Left} isConnectable={false} /> : null}
      {right ? <Handle type="source" position={Position.Right} isConnectable={false} /> : null}
    </>
  );
}

/** A block that is selected by a click; a click on the selected one closes the inspector. */
function Pickable({ id, className, children }: { id: string; className: string; children: ReactNode }) {
  const mine = useSelection(state => state.selection === id);
  return (
    <button
      type="button"
      className={className + (mine ? " sel" : "")}
      data-k={id}
      aria-current={mine ? "true" : undefined}
      onClick={() => mapSelect(mine ? null : id)}
    >
      {children}
    </button>
  );
}

function GroupBlock({ id, data }: NodeProps<GroupNode>) {
  const { bucket, rate, pinned, staged } = data;
  const count = bucket.count;
  return (
    <>
      <Pickable id={id} className={"node ugroup" + (count.online ? "" : " off") + (staged ? " staged" : "")}>
        <Dot kind={count.online ? DotKind.Good : DotKind.Off} />
        <b>{bucket.name}</b>
        {pinned ? <small className="pin">→ {pinned}</small> : null}
        {rate > BUSY_BPS ? <small className="rate">{fmtBps(rate)}</small> : null}
        <small className="cnt">{count.online + " из " + count.users}</small>
      </Pickable>
      <Ports right />
    </>
  );
}

function EntryBlock({ data }: NodeProps<EntryNode>) {
  return (
    <>
      <Ports left right />
      <div className="node entry">
        <span>Вход</span>
        <small>{data.note}</small>
      </div>
    </>
  );
}

const TARGET_NAMES: Record<Target, string> = {
  [Target.Abroad]: "За границу",
  [Target.Direct]: "Напрямую",
  [Target.Block]: "Блок",
};

function RuleBlock({ id, data }: NodeProps<RuleNode>) {
  return (
    <>
      <Ports left right />
      <Pickable
        id={id}
        className={"node rule to-" + data.target + (data.on ? "" : " off") + (data.staged ? " staged" : "")}
      >
        <i className="rule-mark" aria-hidden="true" />
        <span>
          <b>{data.name}</b>
          <small>{data.on ? data.note : "выключено · " + data.note}</small>
        </span>
      </Pickable>
    </>
  );
}

function TargetBlock({ id, data }: NodeProps<TargetNode>) {
  const pool = data.target === Target.Abroad;
  const body = (
    <>
      <i className="rule-mark" aria-hidden="true" />
      <span>
        <b>{TARGET_NAMES[data.target]}</b>
        <small>{data.note}</small>
      </span>
    </>
  );
  return (
    <>
      <Ports left right={pool} />
      {pool ? (
        <Pickable id={id} className={"node target to-" + data.target}>
          {body}
        </Pickable>
      ) : (
        <div className={"node target to-" + data.target}>{body}</div>
      )}
    </>
  );
}

function ExitBlock({ id, data }: NodeProps<ExitNode>) {
  const exit = data.exit;
  const up = exit.state === ExitState.Up;
  return (
    <>
      <Ports left />
      <Pickable id={id} className={"node exit" + (up ? "" : " down")}>
        <Dot kind={up ? DotKind.Good : DotKind.Crit} />
        <span>
          <b>{exit.name}</b>
          <small>{up ? (exit.users ?? 0) + " польз. · " + fmtBps(exit.down_bps) : "недоступен"}</small>
        </span>
        {data.share == null ? null : (
          <small className={"share" + (data.staged ? " staged" : "")}>{data.share ? data.share + "%" : "выкл."}</small>
        )}
      </Pickable>
    </>
  );
}

/** A new exit starts on the page of the servers. */
function AddExitBlock() {
  return (
    <button
      type="button"
      className="node add-exit"
      onClick={() => {
        openExitForm(true);
        navigate(Route.Servers);
      }}
    >
      <Icon name={IconName.Plus} size={16} stroke={2} />
      <span>Новый выход</span>
    </button>
  );
}

export const NODE_TYPES: NodeTypes = {
  [NodeKind.Group]: GroupBlock,
  [NodeKind.Entry]: EntryBlock,
  [NodeKind.Rule]: RuleBlock,
  [NodeKind.Target]: TargetBlock,
  [NodeKind.Exit]: ExitBlock,
  [NodeKind.AddExit]: AddExitBlock,
};
