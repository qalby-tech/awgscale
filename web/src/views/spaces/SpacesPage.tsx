"use client";

/** «Пользователи → Пространства»: sets of devices that see each other through the tunnel. */
import { useEffect, useRef, useState } from "react";

import { api, isAuthError, post } from "@/lib/api";
import { clone } from "@/lib/format";
import { type Client } from "@/lib/types";
import { pointerIsDown, typingIn } from "@/lib/pointer";
import { dropList, findClients, showList, useClients } from "@/store/clients";
import { isLocked, Lock, useCore, withLock } from "@/store/core";
import { Chip, GroupHead, NameCard } from "@/ui/groups";
import { Icon, IconName } from "@/ui/Icon";
import { menuIsOpen, type MenuItem } from "@/ui/menu";
import { PageHead } from "@/ui/PageHead";
import { fail } from "@/ui/toast";
import { useHoldReload, useListSync } from "@/views/users/hooks";
import { USER_TABS } from "@/views/tabs";

interface Space {
  id: string | null;
  name: string;
  on: boolean;
  members: string[];
}
interface Spaces {
  v?: string;
  limit?: number;
  spaces?: Space[];
}

const GRID_ID = "spGrid";
const NAME_LENGTH = 40;
const DEFAULT_LIMIT = 30;
const toWire = (space: Space) => ({ id: space.id || null, name: space.name, on: !!space.on, members: space.members });
const read = () => api<Spaces>("/api/spaces");

type Change = (draft: Space[]) => void;

/** The spaces as the server holds them, kept in step with it, and the way to change them. */
function useSpaces(creating: boolean) {
  const version = useCore(state => state.state?.spaces_v);
  const [held, setHeld] = useState<Spaces | null>(null);
  const latest = useRef(held);
  latest.current = held;

  useEffect(() => {
    let alive = true;
    read().then(data => {
      if (alive) setHeld(data);
    }, fail);
    return () => {
      alive = false;
    };
  }, []);
  useListSync<Spaces>({
    version,
    held: held?.v,
    loaded: !!held,
    busy: () => isLocked(Lock.Spaces) || menuIsOpen() || pointerIsDown() || creating || typingIn(GRID_ID),
    read,
    take: setHeld,
  });

  /** After a refused change: the list as the server has it now. */
  const readAgain = async () => {
    try {
      setHeld(await read());
    } catch {
      /* keep what we have */
    }
  };

  /** One change of the spaces, made on a copy of the list. */
  const save = (change: Change) => {
    const current = latest.current;
    if (isLocked(Lock.Spaces) || !current) return;
    const next = clone(current.spaces ?? []);
    change(next);
    void withLock(Lock.Spaces, async () => {
      try {
        setHeld(await post<Spaces>("/api/spaces", { base: current.v || "", spaces: next.map(toWire) }));
      } catch (error) {
        fail(error);
        if (!isAuthError(error)) await readAgain();
      }
    });
  };

  return { held, save };
}

interface SpaceCardProps {
  space: Space;
  index: number;
  /** The address of each member, as the server answered for them. */
  addresses: Record<string, string>;
  /** Whom the «+» offers from: the first users by address. */
  offered: Client[];
  save: (change: Change) => void;
}

const OFFERED_LIST = "spaces:offered";
const NO_CLIENTS: Client[] = [];

/** The addresses of the members of every space, asked of the server by name (again when the members change). */
function useAddresses(spaces: Space[]): Record<string, string> {
  const [addresses, setAddresses] = useState<Record<string, string>>({});
  const tick = useCore(state => state.tick);
  const names = JSON.stringify([...new Set(spaces.flatMap(space => space.members))].sort());
  const asked = useRef("");
  useEffect(() => {
    if (names === asked.current) return;
    asked.current = names;
    findClients(JSON.parse(names)).then(
      found => setAddresses(Object.fromEntries(found.map(client => [client.name, client.ip]))),
      () => {
        asked.current = ""; // asked again with the next poll
      },
    );
  }, [names, tick]);
  return addresses;
}

/** One space: its switch, its name, the users in it. */
function SpaceCard({ space, index, addresses, offered, save }: SpaceCardProps) {
  const addressOf = (name: string) => addresses[name] ?? "";
  const toggle = () =>
    save(draft => {
      draft[index].on = !draft[index].on;
    });
  const rename = (name: string) =>
    save(draft => {
      draft[index].name = name;
    });
  const add = (name: string) =>
    save(draft => {
      draft[index].members.push(name);
    });
  const removeMember = (name: string) =>
    save(draft => {
      draft[index].members = draft[index].members.filter(member => member !== name);
    });
  const remove = () => {
    if (!confirm("Удалить пространство «" + space.name + "»?")) return;
    save(draft => {
      draft.splice(index, 1);
    });
  };
  /** Whom the «+» offers: the first users (by address) who are not in the space yet, as read at the moment of the click. */
  const candidates = (): MenuItem[] =>
    offered
      .filter(client => !space.members.includes(client.name))
      .map(client => ({ label: client.name, sub: client.ip, run: () => add(client.name) }));

  return (
    <section className={"card group" + (space.on ? "" : " off")} aria-label={"Пространство " + space.name}>
      <GroupHead
        name={space.name}
        on={space.on}
        count={space.members.length}
        maxLength={NAME_LENGTH}
        onToggle={toggle}
        plus={{
          label: "Добавить пользователя в «" + space.name + "»",
          emptyText: offered.length ? "Все уже здесь" : "Пользователей нет",
          items: candidates,
        }}
        onRename={rename}
        onDelete={remove}
      />
      <div className="chips">
        {space.members.map(member => (
          <Chip
            key={member}
            text={member}
            sub={addressOf(member)}
            removeLabel="Убрать из пространства"
            onRemove={() => removeMember(member)}
          />
        ))}
      </div>
    </section>
  );
}

export function SpacesPage() {
  const offered = useClients(store => store.lists[OFFERED_LIST]?.clients ?? NO_CLIENTS);
  const [creating, setCreating] = useState(false);
  const newName = useRef<HTMLInputElement>(null);
  const { held, save } = useSpaces(creating);
  useHoldReload(creating);

  const list = held?.spaces ?? [];
  const addresses = useAddresses(list);
  useEffect(() => {
    showList(OFFERED_LIST, {});
    return () => dropList(OFFERED_LIST);
  }, []);
  const full = !held || list.length >= (held.limit || DEFAULT_LIMIT);
  const startNew = () => {
    if (creating) newName.current?.focus();
    else setCreating(true);
  };
  const create = (name: string) => {
    setCreating(false);
    save(draft => {
      draft.unshift({ id: null, name, on: true, members: [] });
    });
  };

  return (
    <>
      <PageHead title="Пользователи" tabs={USER_TABS} />
      <div className="toolbar end">
        <button type="button" className="btn" id="spAdd" disabled={full} onClick={startNew}>
          <Icon name={IconName.Plus} stroke={2} />
          <span>Новое пространство</span>
        </button>
      </div>
      <div className="grid" id={GRID_ID}>
        {creating ? (
          <NameCard
            placeholder="Название пространства"
            maxLength={NAME_LENGTH}
            inputRef={newName}
            onCancel={() => setCreating(false)}
            onCreate={create}
          />
        ) : null}
        {list.map((space, index) => (
          <SpaceCard
            key={space.id ?? "new" + index}
            space={space}
            index={index}
            addresses={addresses}
            offered={offered}
            save={save}
          />
        ))}
        {held && !list.length && !creating ? <div className="card empty">Пространств пока нет</div> : null}
      </div>
    </>
  );
}
