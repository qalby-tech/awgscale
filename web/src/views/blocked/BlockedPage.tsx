"use client";

import { useEffect, useRef } from "react";

import { entryMatches, hitFn, isHost } from "@/lib/entries";
import { holdReload } from "@/store/core";
import { SITE_TABS } from "@/views/tabs";
import { AddRow, LinkButton, LinkForm, NewGroupButton, NoCards, SearchBox, type LinkFields } from "@/ui/entries";
import { flashChip, NameCard } from "@/ui/groups";
import { PageHead } from "@/ui/PageHead";

import { ListCard, listTitle, OwnCard, SAFE_NAME, SafeCard } from "./BlockCards";
import {
  addEntry,
  addLink,
  AddResult,
  addTyped,
  closeAdd,
  closeLinkForm,
  createGroup,
  groupKey,
  leaveBlocked,
  LinkRefusal,
  openBlocked,
  setSearch,
  startNaming,
  toggleLinkForm,
  useBlocked,
} from "./store";
import { BlockedBy, type Block, type Blocked, type BlockGroup, type BlockHit, type BlockList } from "./types";

const GROUP_NAME_LENGTH = 60;
const NONE_SHADOWED: Readonly<Record<string, string>> = {};

async function submitLink({ name, url }: LinkFields): Promise<void> {
  const refused = await addLink(name.value.trim(), url.value.trim());
  if (refused === LinkRefusal.NoName) name.focus();
  else if (refused === LinkRefusal.BadUrl) url.focus();
}

function emptyText(query: string, hit: BlockHit | null): string {
  if (!query) return "Список пуст";
  return isHost(query) && hit && !hit.failed ? "Не блокируется" : "Ничего не найдено";
}

interface OwnRow {
  group: BlockGroup;
  index: number;
  key: string;
  adding: boolean;
  shown: boolean;
}

interface ShownCards {
  /** For every ready-made list, by its place: is its card shown. */
  lists: boolean[];
  safe: boolean;
  /** The address searched for is blocked as a dangerous site. */
  safeHit: boolean;
  own: OwnRow[];
  any: boolean;
}

/**
 * Which cards a search leaves on the page: those whose name has the text, those
 * holding an entry that has it, the one the blocker named for a whole address —
 * and the card a field is open in. Without a search, all of them.
 */
function shownCards(
  block: Block | null,
  query: string,
  found: Blocked | null,
  addKey: string | null,
  hit: (text: string) => boolean,
): ShownCards {
  const nameHit = (name: string) => !!query && name.toLowerCase().includes(query);
  const foundInList = (list: BlockList) => found?.kind === BlockedBy.List && found.id === list.id;
  const lists = (block?.lists ?? []).map(list => !query || nameHit(listTitle(list)) || foundInList(list));
  const safeHit = found?.kind === BlockedBy.Safe;
  const safe = !query || nameHit(SAFE_NAME) || safeHit;
  const own = (block?.groups ?? []).map((group, index) => {
    const key = groupKey(group, index);
    const adding = addKey === key;
    const mine = found?.kind === BlockedBy.Own && !!found.gid && found.gid === group.id;
    const shown = !query || nameHit(group.name) || adding || mine || group.items.some(hit);
    return { group, index, key, adding, shown };
  });
  return { lists, safe, safeHit, own, any: lists.some(Boolean) || safe || own.some(row => row.shown) };
}

export function BlockedPage() {
  const state = useBlocked();
  const grid = useRef<HTMLDivElement>(null);
  const addInput = useRef<HTMLInputElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const { block, query } = state;

  useEffect(() => {
    void openBlocked();
    return leaveBlocked;
  }, []);

  const formOpen = state.naming || state.addKey != null || state.linkOpen;
  useEffect(() => (formOpen ? holdReload() : undefined), [formOpen]);

  useEffect(() => {
    if (state.flash) flashChip(grid.current, state.flash.text);
  }, [state.flash]);

  const add = async () => {
    const field = addInput.current;
    if (!field) return;
    const text = field.value;
    const result = await addEntry(text, () => {
      field.value = "";
    });
    const again = addInput.current;
    if (!again) return;
    if (result === AddResult.Failed) again.value = text;
    again.focus();
  };
  const newGroup = () => {
    if (state.naming) nameInput.current?.focus();
    else startNaming(true);
  };

  const hit = hitFn(query, entryMatches);
  const found = state.hit && !state.hit.pending && state.hit.host === query ? (state.hit.blocked ?? null) : null;
  const shown = shownCards(block, query, found, state.addKey, hit);
  const lists = block?.lists ?? [];
  const listCard = (index: number) =>
    shown.lists[index] ? (
      <ListCard
        key={"list" + lists[index].id}
        list={lists[index]}
        index={index}
        top={index === 0 ? (block?.top ?? []) : []}
        found={found?.kind === BlockedBy.List && found.id === lists[index].id ? found : null}
      />
    ) : null;

  const addRow = (
    <AddRow
      id="blInput"
      allowNet={false}
      label="Домен или шаблон"
      error={state.addError}
      inputRef={addInput}
      onInput={addTyped}
      onAdd={() => void add()}
      onClose={closeAdd}
    />
  );

  return (
    <>
      <PageHead title="Сайты" tabs={SITE_TABS} />
      <div className="toolbar">
        <SearchBox id="blSearch" onSearch={setSearch} />
        <div className="tools">
          <NewGroupButton onClick={newGroup} />
          <LinkButton id="blLinkBtn" open={state.linkOpen} onToggle={toggleLinkForm} />
        </div>
      </div>
      <div className="slot" id="blLinkSlot">
        {state.linkOpen ? (
          <LinkForm
            id="blLink"
            error={state.linkError}
            busy={state.linkBusy}
            onAdd={fields => void submitLink(fields)}
            onClose={closeLinkForm}
          />
        ) : null}
      </div>
      <div className="grid" id="blGrid" ref={grid}>
        {block ? (
          <>
            {state.naming ? (
              <NameCard
                placeholder="Название группы"
                maxLength={GROUP_NAME_LENGTH}
                inputRef={nameInput}
                onCreate={name => void createGroup(name)}
                onCancel={() => startNaming(false)}
              />
            ) : null}
            {lists.length ? listCard(0) : null}
            {shown.safe ? (
              <SafeCard key="safe" safe={block.safebrowsing ?? {}} found={shown.safeHit ? query : null} />
            ) : null}
            {lists.map((_, index) => (index > 0 ? listCard(index) : null))}
            {shown.own.map(row =>
              row.shown ? (
                <OwnCard
                  key={row.key}
                  group={row.group}
                  index={row.index}
                  groupKey={row.key}
                  adding={row.adding}
                  hit={hit}
                  addRow={row.adding ? addRow : null}
                  shadowed={(row.group.id && block?.covered?.[row.group.id]) || NONE_SHADOWED}
                />
              ) : null,
            )}
            {!shown.any && !state.naming && !state.hit?.pending ? <NoCards text={emptyText(query, state.hit)} /> : null}
          </>
        ) : null}
      </div>
    </>
  );
}
