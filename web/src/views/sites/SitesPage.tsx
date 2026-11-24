"use client";

import { useEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";

import { COUNTRIES, DEFAULT_COUNTRY } from "@/lib/countries";
import { hitFn, routeMatches } from "@/lib/entries";
import { plural } from "@/lib/format";
import { RoutingMode } from "@/lib/types";
import { holdReload, Lock, setLeaveGuard, setUnsavedWork, useCore } from "@/store/core";
import { groupsOf, useUsers } from "@/store/users";
import { useGroupsFresh } from "@/views/network/hooks";
import { SITE_TABS } from "@/views/tabs";
import { AddRow, LinkButton, LinkForm, NewGroupButton, NoCards, SearchBox, type LinkFields } from "@/ui/entries";
import { flashChip, NameCard } from "@/ui/groups";
import { PageHead } from "@/ui/PageHead";

import { ProbeCard } from "./ProbeCard";
import { GroupCard, ListCard, storedKey } from "./SiteCards";
import {
  addEntry,
  addLink,
  AddResult,
  addTyped,
  applySites,
  cancelChanges,
  closeAdd,
  closeLinkForm,
  createGroup,
  dirtyCount,
  followState,
  leaveSites,
  LinkRefusal,
  modeOf,
  openSites,
  probeHost,
  setCountry,
  setMode,
  setSearch,
  sitesDirty,
  startNaming,
  toggleLinkForm,
  useSites,
} from "./store";
import type { DraftGroup, Probe, Sites } from "./types";

const GROUP_NAME_LENGTH = 60;
const LEAVE_QUESTION = "Изменения не применены. Уйти со страницы?";
const MODES: ReadonlyArray<readonly [RoutingMode, string]> = [
  [RoutingMode.List, "По списку"],
  [RoutingMode.Foreign, "Всё зарубежное"],
];
const EMPTY: ReadonlySet<string> = new Set();
const NONE_SHADOWED: Readonly<Record<string, string>> = {};

/** What goes abroad: only the list, or everything foreign. */
function ModeSwitch({ sites, busy }: { sites: Sites | null; busy: boolean }) {
  return (
    <div className="seg mode" id="siteMode" role="group" aria-label="Что идёт через заграницу">
      {sites
        ? MODES.map(([mode, text]) => (
            <button
              key={mode}
              type="button"
              aria-pressed={mode === modeOf(sites)}
              disabled={busy}
              onClick={() => void setMode(mode)}
            >
              {text}
            </button>
          ))
        : null}
    </div>
  );
}

/** The home country: whose networks go direct. Not shown on a single server, where nothing is routed by country. */
function CountrySelect({ busy }: { busy: boolean }) {
  const routing = useCore(core => core.state?.routing);
  if (!routing || routing.single_server) return null;
  const current = routing.home_country ?? DEFAULT_COUNTRY;
  const offered = COUNTRIES.some(([code]) => code === current)
    ? COUNTRIES
    : [...COUNTRIES, [current, current.toUpperCase()] as const];
  return (
    <select
      className="select"
      id="siteCountry"
      aria-label="Своя страна: её сети идут напрямую"
      title="Своя страна: её сети идут напрямую"
      value={current}
      disabled={busy}
      onChange={event => void setCountry(event.target.value, current)}
    >
      {offered.map(([code, name]) => (
        <option key={code} value={code}>
          {name}
        </option>
      ))}
    </select>
  );
}

/** «Отменить / Применить N»: shown while something is staged or being sent. */
function ApplyBar({ staged, sending }: { staged: number; sending: boolean }) {
  return (
    <div className="actions" id="siteActions" hidden={!staged && !sending}>
      <button type="button" className="btn" id="siteCancel" disabled={sending} onClick={cancelChanges}>
        Отменить
      </button>
      <button
        type="button"
        className="btn primary"
        id="siteApply"
        disabled={sending || !staged}
        onClick={() => void applySites()}
      >
        <span>{sending ? "Применяю…" : "Применить"}</span>
        {!sending && staged ? <span className="count">{staged}</span> : null}
      </button>
    </div>
  );
}

function submitLink({ name, url }: LinkFields): void {
  const refused = addLink(name.value.trim(), url.value.trim());
  if (refused === LinkRefusal.NoName) name.focus();
  else if (refused === LinkRefusal.BadUrl) url.focus();
}

/** Opens the section and closes it: the lists are read, unsaved changes guard the way out, an open form holds a reload. */
function useSitesSection(formOpen: boolean): void {
  const state = useCore(core => core.state);
  useGroupsFresh(state);

  useEffect(() => {
    void openSites();
    setLeaveGuard(() => !sitesDirty() || confirm(LEAVE_QUESTION));
    setUnsavedWork(sitesDirty);
    return () => {
      leaveSites();
      setLeaveGuard(null);
      setUnsavedWork(null);
    };
  }, []);

  useEffect(() => followState(state), [state]);

  // an open form is not reloaded away by a newer build of the page
  useEffect(() => (formOpen ? holdReload() : undefined), [formOpen]);
}

interface SiteGridProps {
  sites: Sites;
  draft: DraftGroup[];
  query: string;
  addKey: string | null;
  naming: boolean;
  probe: Probe | null;
  cover: string[];
  /** The field that is open in one of the cards. */
  addRow: ReactNode;
  nameInput: RefObject<HTMLInputElement | null>;
  people: Readonly<Record<string, string>>;
}

/** The cards of the groups a search leaves on the page, the card that checks a whole address, the card that names a new group. */
function SiteGrid({ sites, draft, query, addKey, naming, probe, cover, addRow, nameInput, people }: SiteGridProps) {
  // what the server has, group by group: an entry added to one more group is new there
  const stored = useMemo(
    () => new Set(sites.groups.flatMap(group => (group.url ? [] : group.items.map(item => storedKey(group.id, item))))),
    [sites],
  );
  const covered = useMemo(() => (cover.length ? new Set(cover) : EMPTY), [cover]);
  const hit = hitFn(query, routeMatches);
  const host = probeHost(query);
  const nameHit = (group: DraftGroup) => !!query && group.name.toLowerCase().includes(query);
  const netsOf = (group: DraftGroup) => (group.auto && sites.auto?.[group.auto]?.nets) || 0;
  const isAdding = (group: DraftGroup) => !group.url && addKey === group.key;
  const visible = draft.filter(group => !query || nameHit(group) || isAdding(group) || group.items.some(hit));
  return (
    <>
      {naming ? (
        <NameCard
          placeholder="Название группы"
          maxLength={GROUP_NAME_LENGTH}
          inputRef={nameInput}
          onCreate={createGroup}
          onCancel={() => startNaming(false)}
        />
      ) : null}
      {host ? <ProbeCard host={host} sites={sites} draft={draft} probe={probe} /> : null}
      {visible.map(group =>
        group.url ? (
          <ListCard key={group.key} group={group} found={query ? group.items.filter(hit) : []} />
        ) : (
          <GroupCard
            key={group.key}
            group={group}
            nets={netsOf(group)}
            showNets={!query || nameHit(group)}
            adding={addKey === group.key}
            hit={hit}
            stored={stored}
            covered={covered}
            shadowed={(group.id && sites.covered?.[group.id]) || NONE_SHADOWED}
            addRow={addKey === group.key ? addRow : null}
            people={people}
          />
        ),
      )}
      {!visible.length && !naming && !host ? <NoCards text={query ? "Нет в списках" : "Список пуст"} /> : null}
    </>
  );
}

export function SitesPage() {
  const current = useSites();
  const sending = useCore(core => core.locks.includes(Lock.Sites));
  const grid = useRef<HTMLDivElement>(null);
  const addInput = useRef<HTMLInputElement>(null);
  const replaceCheck = useRef<HTMLInputElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const { sites, draft } = current;
  useSitesSection(current.naming || !!current.addKey || current.linkOpen);
  const groups = useUsers(store => store.groups);
  const people = useMemo(
    () => Object.fromEntries(groupsOf(groups).flatMap(group => (group.id ? [[group.id, group.name]] : []))),
    [groups],
  );

  useEffect(() => {
    if (current.flash) flashChip(grid.current, current.flash.text);
  }, [current.flash]);

  const add = () => {
    const field = addInput.current;
    if (!field) return;
    if (addEntry(field.value, !!replaceCheck.current?.checked) === AddResult.Added) {
      field.value = "";
      if (replaceCheck.current) replaceCheck.current.checked = true;
    }
    field.focus();
  };
  const newGroup = () => {
    if (current.naming) nameInput.current?.focus();
    else startNaming(true);
  };
  const replaced = current.cover.length;

  const addRow = (
    <AddRow
      id="siteInput"
      allowNet
      label="Домен, шаблон или сеть"
      error={current.addError}
      inputRef={addInput}
      onInput={() => addTyped(addInput.current?.value ?? "")}
      onAdd={add}
      onClose={closeAdd}
      replace={{
        shown: replaced > 0,
        checkRef: replaceCheck,
        text: "Заменить " + replaced + " " + plural(replaced, ["запись", "записи", "записей"]),
      }}
    />
  );

  return (
    <>
      <PageHead title="Сайты" tabs={SITE_TABS}>
        <ApplyBar staged={dirtyCount(sites, draft)} sending={sending} />
      </PageHead>
      <div className="toolbar">
        <SearchBox id="siteSearch" onSearch={setSearch} />
        <div className="tools">
          <ModeSwitch sites={sites} busy={current.modeBusy} />
          <CountrySelect busy={current.countryBusy} />
          <NewGroupButton onClick={newGroup} />
          <LinkButton id="siteLinkBtn" open={current.linkOpen} onToggle={toggleLinkForm} />
        </div>
      </div>
      <div className="slot" id="siteLinkSlot">
        {current.linkOpen ? (
          <LinkForm id="siteLink" error={current.linkError} onAdd={submitLink} onClose={closeLinkForm} />
        ) : null}
      </div>
      <div className="grid" id="siteGrid" ref={grid}>
        {draft && sites ? (
          <SiteGrid
            sites={sites}
            draft={draft}
            query={current.query}
            addKey={current.addKey}
            naming={current.naming}
            probe={current.probe}
            cover={current.cover}
            addRow={addRow}
            nameInput={nameInput}
            people={people}
          />
        ) : null}
      </div>
    </>
  );
}
