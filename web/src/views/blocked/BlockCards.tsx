"use client";

import { shadowedTitle } from "../sites/SiteCards";
import type { ReactNode } from "react";

import { sourceOf } from "@/lib/entries";
import { num, whenText } from "@/lib/format";
import { Chip, GroupHead } from "@/ui/groups";

import {
  changeLists,
  deleteGroup,
  removeItem,
  renameGroup,
  toggleAdd,
  toggleGroup,
  toggleList,
  toggleSafe,
} from "./store";
import { ListOp, type Blocked, type BlockGroup, type BlockList, type TopBlocked } from "./types";

const LIST_NAME_LENGTH = 40;
export const SAFE_NAME = "Опасные сайты";

const perDay = (on: boolean | undefined, day: number | undefined): string | null =>
  on ? num(day || 0) + " за сутки" : null;
/** The host a blocker's rule is about: «||ads.example^» → ads.example. */
const ruleName = (rule: string | undefined): string => /^\|\|([^\^$/]+)\^/.exec(rule || "")?.[1] ?? (rule || "");

export const listTitle = (list: BlockList): string => list.title || list.name;

interface ListCardProps {
  list: BlockList;
  index: number;
  /** The rule of this list that blocks the address in the search. */
  found: Blocked | null;
  /** What was blocked most often: shown in the main list. */
  top: TopBlocked[];
}

/** A ready-made list of the blocker, or one connected by a link. */
export function ListCard({ list, index, found, top }: ListCardProps) {
  const title = listTitle(list);
  const meta: ReactNode[] = [];
  if (list.custom && list.url)
    meta.push(
      <span key="src" className="mono" title={list.url}>
        {sourceOf(list.url)}
      </span>,
    );
  if (list.updated) meta.push(<span key="when">{whenText(list.updated)}</span>);
  const body: ReactNode[] = [];
  if (found)
    body.push(
      <div key="hit" className="chips">
        <Chip text={ruleName(found.rule)} className="hit" title={found.rule} />
      </div>,
    );
  else if (list.on) {
    if (meta.length)
      body.push(
        <div key="meta" className="group-meta">
          {meta}
        </div>,
      );
    if (top.length) {
      body.push(
        <div key="label" className="sub-label">
          Чаще всего за сутки
        </div>,
        <div key="top" className="chips">
          {top.map(site => (
            <Chip key={site.domain} text={site.domain} sub={num(site.count)} />
          ))}
        </div>,
      );
    }
  }
  const remove = () => {
    if (confirm("Отключить список «" + title + "»?"))
      void changeLists({ op: ListOp.Remove, id: list.id }, "Список отключён");
  };
  return (
    <section
      className={"card group list" + (body.length ? "" : " bare") + (list.on ? "" : " off")}
      aria-label={"Группа " + title}
    >
      <GroupHead
        name={title}
        on={list.on}
        link={!!list.custom}
        count={list.rules ? num(list.rules) : null}
        day={perDay(list.on, list.day)}
        maxLength={LIST_NAME_LENGTH}
        onToggle={() => void toggleList(index)}
        onRefresh={() => void changeLists({ op: ListOp.Refresh }, "Списки обновлены")}
        onRename={list.custom ? name => void changeLists({ op: ListOp.Rename, id: list.id, name }) : null}
        onDelete={list.custom ? remove : null}
      />
      {body}
    </section>
  );
}

/** «Опасные сайты»: the blocker's own check of every address. */
export function SafeCard({ safe, found }: { safe: { on?: boolean; day?: number }; found: string | null }) {
  return (
    <section
      className={"card group" + (found ? "" : " bare") + (safe.on ? "" : " off")}
      aria-label={"Группа " + SAFE_NAME}
    >
      <GroupHead name={SAFE_NAME} on={!!safe.on} day={perDay(safe.on, safe.day)} onToggle={() => void toggleSafe()} />
      {found ? (
        <div className="chips">
          <Chip text={found} className="hit" />
        </div>
      ) : null}
    </section>
  );
}

interface OwnCardProps {
  group: BlockGroup;
  index: number;
  groupKey: string;
  adding: boolean;
  hit: (text: string) => boolean;
  addRow: ReactNode;
  /** Its entries a rule above already names, and by which rule: they are not blocked. */
  shadowed: Readonly<Record<string, string>>;
  /** The names of the groups of people it is for; empty: everybody. */
  forWhom: string[];
}

/** A group of addresses the admin blocks. */
export function OwnCard({ group, index, groupKey, adding, hit, addRow, shadowed, forWhom }: OwnCardProps) {
  const remove = () => {
    if (!group.items.length || confirm("Удалить группу «" + group.name + "» и её записи (" + group.items.length + ")?"))
      void deleteGroup(index);
  };
  return (
    <section className={"card group" + (group.on ? "" : " off")} aria-label={"Группа " + group.name}>
      <GroupHead
        name={group.name}
        on={group.on}
        count={group.items.length}
        tag={forWhom.length ? "только: " + forWhom.join(", ") : null}
        onToggle={() => void toggleGroup(index)}
        plus={{ label: "Добавить в «" + group.name + "»", pressed: adding, onClick: () => toggleAdd(groupKey) }}
        onRename={name => void renameGroup(index, name)}
        onDelete={remove}
      />
      {addRow}
      <div className="chips">
        {group.items.map(text => (
          <Chip
            key={text}
            text={text}
            className={(hit(text) ? "hit" : "") + (text in shadowed ? " shadowed" : "")}
            title={text in shadowed ? shadowedTitle(shadowed[text]) : undefined}
            onRemove={() => void removeItem(index, text)}
          />
        ))}
      </div>
    </section>
  );
}
