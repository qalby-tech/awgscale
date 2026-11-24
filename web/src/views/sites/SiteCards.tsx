"use client";

import type { ReactNode } from "react";

import { EntryKind, kindOf, sourceOf } from "@/lib/entries";
import { num, plural, whenText } from "@/lib/format";
import { Chip, GroupHead } from "@/ui/groups";
import { Icon, IconName } from "@/ui/Icon";

import { deleteGroup, refreshList, removeItem, renameGroup, toggleAdd, toggleGroup } from "./store";
import type { DraftGroup } from "./types";

/** How many found entries of a list by link are shown before «+N». */
const FOUND_SHOWN = 12;

type Hit = (text: string) => boolean;

/** A list by link: its entries are the file's, shown only when searched for. */
export function ListCard({ group, found }: { group: DraftGroup; found: string[] }) {
  const remove = () => {
    if (!group.id || confirm("Отключить список «" + group.name + "»?")) deleteGroup(group.key);
  };
  const id = group.id;
  return (
    <section className={"card group list" + (group.on ? "" : " off")} aria-label={"Группа " + group.name}>
      <GroupHead
        name={group.name}
        on={group.on}
        link
        count={id ? num(group.items.length) : null}
        onToggle={() => toggleGroup(group.key)}
        onRefresh={id ? () => void refreshList(id) : null}
        onRename={name => renameGroup(group.key, name)}
        onDelete={remove}
      />
      <div className="group-meta">
        <span className="mono" title={group.url ?? ""}>
          {sourceOf(group.url ?? "")}
        </span>
        {group.error ? <span className="bad">{group.error}</span> : null}
        {group.updated ? <span>{whenText(group.updated * 1000)}</span> : null}
      </div>
      <div className="chips">
        {found.slice(0, FOUND_SHOWN).map(text => (
          <Chip key={text} text={text} className="hit" />
        ))}
        {found.length > FOUND_SHOWN ? (
          <span className="chip ro">
            <span className="chip-t">{"+" + num(found.length - FOUND_SHOWN)}</span>
          </span>
        ) : null}
      </div>
    </section>
  );
}

interface GroupCardProps {
  group: DraftGroup;
  /** Networks the server adds to the group by itself. */
  nets: number;
  showNets: boolean;
  adding: boolean;
  hit: Hit;
  /** Entries the server already has: the others are marked new. */
  stored: ReadonlySet<string>;
  covered: ReadonlySet<string>;
  /** Its entries a rule above already names, and by which rule: they do not act here. */
  shadowed: Readonly<Record<string, string>>;
  /** The open field of this card, when it is the one being added to. */
  addRow: ReactNode;
  /** The names of the groups of people, by id: a rule for some of them is marked «только: …». */
  people: Readonly<Record<string, string>>;
}

/** The mark of a group: going directly, and for whom when it is not for everybody. */
export function tagOf(group: DraftGroup, people: Readonly<Record<string, string>>): string | null {
  const only = group.scope.length ? "только: " + group.scope.map(id => people[id] ?? id).join(", ") : "";
  if (group.direct) return only ? "напрямую, " + only : "напрямую";
  return only || null;
}

/** An entry the server has, as the page marks it: in which group. */
export const storedKey = (group: string, item: string): string => group + "\u0000" + item;

/** Why an entry does not act here: the first rule that names a site decides. */
export const shadowedTitle = (by: string): string => "Перекрыто правилом выше: «" + by + "» — срабатывает оно";

const chipClass = (text: string, props: GroupCardProps): string =>
  [
    kindOf(text) === EntryKind.Net ? "net" : "",
    props.stored.has(storedKey(props.group.id ?? "", text)) ? "" : "new",
    props.hit(text) ? "hit" : "",
    props.covered.has(text) ? "covered" : "",
    text in props.shadowed ? "shadowed" : "",
  ]
    .filter(Boolean)
    .join(" ");

/** A group of addresses typed by hand. */
export function GroupCard(props: GroupCardProps) {
  const group = props.group;
  const remove = () => {
    if (!group.items.length || confirm("Удалить группу «" + group.name + "» и её записи (" + group.items.length + ")?"))
      deleteGroup(group.key);
  };
  return (
    <section className={"card group" + (group.on ? "" : " off")} aria-label={"Группа " + group.name}>
      <GroupHead
        name={group.name}
        on={group.on}
        tag={tagOf(group, props.people)}
        count={group.items.length + props.nets}
        onToggle={() => toggleGroup(group.key)}
        plus={{ label: "Добавить в «" + group.name + "»", pressed: props.adding, onClick: () => toggleAdd(group.key) }}
        onRename={name => renameGroup(group.key, name)}
        onDelete={group.auto ? null : remove}
      />
      {props.addRow}
      <div className="chips">
        {group.items.map(text => (
          <Chip
            key={text}
            text={text}
            className={chipClass(text, props)}
            title={text in props.shadowed ? shadowedTitle(props.shadowed[text]) : undefined}
            onRemove={() => removeItem(group.key, text)}
          />
        ))}
        {props.nets && props.showNets ? (
          <span className="chip auto" title="Обновляются сами">
            <Icon name={IconName.Sync} size={14} stroke={2} />
            <span>{props.nets + " " + plural(props.nets, ["сеть", "сети", "сетей"])}</span>
          </span>
        ) : null}
      </div>
    </section>
  );
}
