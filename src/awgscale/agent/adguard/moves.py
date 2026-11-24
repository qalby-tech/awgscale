"""How AdGuard's clients go from what it holds to what is wanted (AdGuardProfiles.sync), so that after every single call
each person is filtered exactly as before the change or exactly as wanted — never a third way, and never by nobody —
also when AdGuard refuses a call, answers nothing, or goes down halfway: every call is safe on its own, and the first
one that does not go through stops the change. The next change starts from what AdGuard then holds, whatever it is.

What a person gets is everything of their client: its switches, upstreams, safe search, services, and its tags — the
groups' block lists (adguard.group_lists: `$ctag=…`), which go with the client's settings, not with its name. So a
person may only ever be held by a client with the settings of the one they were in, or of the one they are wanted in
(a temporary client is a copy of the one its people leave, tags and all).

AdGuard refuses an address another client holds, so an address cannot go from one client to another in one call. It
holds an address and its network ("10.0.0.2/32") apart, though, and asks the client of the address first: a client
that also holds "10.0.0.2/32" takes that person over the moment the one holding "10.0.0.2" lets them go
(tests/stack_adguard.py, v0.107.80, also across a restart and when the holder is deleted).

A change is made in rounds, each from a state where every client holds addresses only (no networks):

  0. tidy: whatever a stopped change left is made plain — a network whose address another client holds is dropped (it
     filters nobody), a network that filters its person becomes the address, in the same client; a client that then
     holds nobody is removed (it filtered nobody). Nobody changes clients.
  1. the clients that are wanted and missing are added, and those whose settings are already as wanted take, as their
     networks, the people they get from others (and as themselves those no client holds);
  2. a client whose settings change does so in the same call that lets go of everybody who leaves it — once each of
     them is held, as a network, by the client they go to, already as wanted (so first those whose leavers go to
     clients that are ready). Two such clients waiting for each other: the leavers of one are first held by a temporary
     client "grp-move-N" with its settings (its tags too), so that this is exactly what they had;
  3. every client lets go of those who leave it and are held where they go (an unwanted one that is left empty is
     removed), and
  4. every wanted client holds its people as their addresses.

A request in the middle of a change is held to MOVE_REQUEST_BYTES: when the networks a client takes would not fit,
it takes as many as fit, and the next round takes the rest; when two full clients wait for each other's room, the
people leaving one of them are held by a stand-in meanwhile, as in step 2."""
from __future__ import annotations

import json
from ipaddress import ip_address
from typing import Any, Iterable, Mapping, Protocol, Sequence

from ..ports import Outcome
from .api import PROFILE_PREFIX
from .models import REQUEST_BYTES_MAX, Profile, update_bytes

COMPARED_SWITCHES = ("use_global_settings", "filtering_enabled", "parental_enabled", "safebrowsing_enabled")
MOVE_PROFILE = PROFILE_PREFIX + "move-"     # a temporary client: never a group's (group ids are hexadecimal) nor a level's
HOST_BITS = {4: 32, 6: 128}
# A request in the middle of a change: at least this, and never below the largest client as wanted plus MOVE_ROOM_BYTES
# (room for a few hundred networks), so that a client as wanted always has room for the networks of its newcomers —
# but never over REQUEST_BYTES_MAX (move_limit).
MOVE_REQUEST_BYTES = 64000
MOVE_ROOM_BYTES = 1536
ROUNDS_AT_MOST = 1000           # a guard only: each round moves someone (the next change goes on from where it stopped)


class ProfileClients(Protocol):
    """AdGuard's per-group clients."""

    async def held(self) -> dict[str, dict[str, Any]]:
        """The clients that stand for groups, by name. Raises AdGuardDown."""
        ...

    async def add(self, profile: Profile) -> Outcome: ...

    async def update(self, name: str, profile: Profile) -> Outcome:
        """The client `name` made as `profile`, in place: its people stay in it meanwhile. A refused change leaves it as it was."""
        ...

    async def delete(self, name: str) -> Outcome: ...


def switches(client: Mapping[str, Any]) -> tuple[bool, ...]:
    """How a client filters, apart from where it asks."""
    return (*(bool(client.get(switch)) for switch in COMPARED_SWITCHES), bool((client.get("safe_search") or {}).get("enabled")))


def request_bytes(profile: Profile) -> int:
    """The size of the blocker's request for this client, as it is sent (clients/update: the largest of the two)."""
    return update_bytes(profile.name, profile.wire())


def move_limit(want: Iterable[Profile]) -> int:
    """How big a request in the middle of this change may be (see MOVE_REQUEST_BYTES)."""
    largest = max((request_bytes(profile) for profile in want), default=0)
    return min(REQUEST_BYTES_MAX, max(MOVE_REQUEST_BYTES, largest + MOVE_ROOM_BYTES))


def as_profile(name: str, client: Mapping[str, Any]) -> Profile:
    """A client as AdGuard reports it, as the agent sends it. Fields that are absent count as switched off or empty."""
    fields = {key: client[key] for key in Profile.model_fields if key in client and client[key] is not None}
    fields.update(
        name=name,
        ids=list(client.get("ids") or []),
        filtering_enabled=bool(client.get("filtering_enabled")),
        parental_enabled=bool(client.get("parental_enabled")),
        safebrowsing_enabled=bool(client.get("safebrowsing_enabled")),
        safe_search=dict(client.get("safe_search") or {}),
    )
    return Profile(**fields)


def settings_of(profile: Profile) -> str:
    """Everything a client gives its people but its name: all the agent sets (its tags, so its lists, too), as one line —
    whether it is sent whole or without its empty fields (Profile.wire)."""
    fields = profile.full()
    del fields["name"]
    del fields["ids"]
    return json.dumps(fields, sort_keys=True)


def same(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Do two clients of AdGuard filter the same people exactly the same way (the name aside)."""
    if sorted(first.get("ids") or []) != sorted(second.get("ids") or []):
        return False
    return settings_of(as_profile("", first)) == settings_of(as_profile("", second))


def host_id(address: str) -> str:
    """The network of this one address, as a client of AdGuard holds it ("10.0.0.2" → "10.0.0.2/32")."""
    return f"{address}/{HOST_BITS[ip_address(address).version]}"


def host_of(identifier: str) -> str | None:
    """The address of a one-address network ("10.0.0.2/32" → "10.0.0.2"); None for anything else."""
    address, slash, bits = identifier.partition("/")
    if not slash:
        return None
    try:
        version = ip_address(address).version
    except ValueError:
        return None
    return address if bits == str(HOST_BITS[version]) else None


class Stopped(Exception):
    """AdGuard did not take a call, or nothing more can be moved: the change stops here, safe as it is."""

    def __init__(self, said: str) -> None:
        super().__init__(said)
        self.said = said


class ClientMoves:
    """One change of the clients (see the module). `run` → what AdGuard refused (empty: everything is as wanted)."""

    def __init__(
        self,
        clients: ProfileClients,
        want: Sequence[Profile],
        have: Mapping[str, Mapping[str, Any]],
        limit: int | None = None,
    ) -> None:
        self.__clients = clients
        self.__have = have
        self.__limit = move_limit(want) if limit is None else limit
        self.__wanted = {profile.name: profile for profile in want}
        self.__final = {address: profile.name for profile in want for address in profile.ids}
        self.__state: dict[str, Profile] = {}       # what AdGuard holds, as far as its answers tell
        self.__holders: dict[str, str] | None = None  # {address: the client holding it as itself}, made again after each call
        self.__ready_now: dict[str, bool] = {}      # {wanted client: is it there as wanted}, made again after each call
        self.__networks_now: dict[str, set[str]] = {}   # {client: those it holds as networks}, made again after each call
        self.__calls = 0                            # calls made: a round that makes none moves nobody any more

    async def run(self) -> list[str]:
        self.__state = {name: as_profile(name, client) for name, client in self.__have.items()}
        try:
            await self.__tidy()
            rounds = 0
            while not self.__done():
                rounds += 1
                if rounds > ROUNDS_AT_MOST:
                    raise Stopped("клиенты блокировщика не сошлись за " + str(ROUNDS_AT_MOST) + " кругов: " + ", ".join(sorted(self.__unsettled())))
                calls = self.__calls
                await self.__round()
                if self.__calls == calls:
                    await self.__make_room()
                if self.__calls == calls:
                    raise Stopped("клиенты блокировщика не меняются дальше: " + ", ".join(sorted(self.__unsettled())))
        except Stopped as e:
            return [e.said]
        return []

    # ── what AdGuard holds ────────────────────────────────────────────────

    def __people(self, name: str) -> set[str]:
        """Those this client holds as themselves."""
        return {identifier for identifier in self.__state[name].ids if host_of(identifier) is None}

    def __networks(self, name: str) -> set[str]:
        """Those this client holds as their networks."""
        if name not in self.__networks_now:
            self.__networks_now[name] = {address for address in map(host_of, self.__state[name].ids) if address is not None}
        return self.__networks_now[name]

    def __holder(self, address: str) -> str | None:
        if self.__holders is None:
            self.__holders = {address: name for name in self.__state for address in self.__people(name)}
        return self.__holders.get(address)

    def __covered(self, address: str) -> bool:
        """Held, as a network, by the client they go to (ready as wanted)? Someone wanted in no client always is."""
        target = self.__final.get(address)
        return target is None or (self.__ready(target) and address in self.__networks(target))

    def __ready(self, name: str) -> bool:
        """Is this client there with the settings it is wanted with?"""
        if name not in self.__state or name not in self.__wanted:
            return False
        if name not in self.__ready_now:
            self.__ready_now[name] = settings_of(self.__state[name]) == settings_of(self.__wanted[name])
        return self.__ready_now[name]

    def __leavers(self, name: str) -> set[str]:
        return {address for address in self.__people(name) if self.__final.get(address) != name}

    def __incoming(self, name: str) -> list[str]:
        """The people wanted in this client it does not hold yet in any way, as their addresses (those no client holds)
        or their networks (those another one holds), in a steady order."""
        profile = self.__wanted[name]
        held = self.__people(name) | self.__networks(name) if name in self.__state else set()
        identifiers = []
        for address in profile.ids:
            if address in held:
                continue
            identifiers.append(address if self.__holder(address) is None else host_id(address))
        return identifiers

    def __fitting(self, profile: Profile, kept: Iterable[str], offered: Sequence[str]) -> list[str]:
        """`kept` and as many of `offered` as fit one request (at least one of them, when `kept` is empty)."""
        ids = sorted(kept)
        taken: list[str] = []
        for identifier in offered:
            if (ids or taken) and request_bytes(profile.model_copy(update={"ids": [*ids, *taken, identifier]})) > self.__limit:
                break
            taken.append(identifier)
        return sorted([*ids, *taken])

    def __done(self) -> bool:
        return not self.__unsettled()

    def __unsettled(self) -> set[str]:
        """The clients that are not yet exactly as wanted, or not wanted."""
        names = {name for name in self.__state if name not in self.__wanted}
        for name, profile in self.__wanted.items():
            if not self.__ready(name) or sorted(self.__state[name].ids) != sorted(profile.ids):
                names.add(name)
        return names

    def __temporary_name(self) -> str:
        number = 1
        while f"{MOVE_PROFILE}{number}" in self.__state or f"{MOVE_PROFILE}{number}" in self.__wanted:
            number += 1
        return f"{MOVE_PROFILE}{number}"

    # ── the calls ─────────────────────────────────────────────────────────

    async def __add(self, profile: Profile) -> None:
        self.__calls += 1
        done = await self.__clients.add(profile)
        if not done.ok:
            raise Stopped(done.said)
        self.__state[profile.name] = profile
        self.__holders = None
        self.__ready_now = {}
        self.__networks_now = {}

    async def __update(self, profile: Profile) -> None:
        """The client of this name, made as `profile` (its name stays)."""
        now = self.__state[profile.name]
        if settings_of(now) == settings_of(profile) and sorted(now.ids) == sorted(profile.ids):
            return
        self.__calls += 1
        done = await self.__clients.update(profile.name, profile)
        if not done.ok:
            raise Stopped(done.said)
        self.__state[profile.name] = profile
        self.__holders = None
        self.__ready_now = {}
        self.__networks_now = {}

    async def __delete(self, name: str) -> None:
        self.__calls += 1
        done = await self.__clients.delete(name)
        if not done.ok:
            raise Stopped(done.said)
        del self.__state[name]
        self.__holders = None
        self.__ready_now = {}
        self.__networks_now = {}

    async def __hold(self, name: str, ids: Iterable[str]) -> None:
        """The client keeps its settings and holds these; removed when that is nobody."""
        ids = sorted(ids)
        if ids:
            await self.__update(self.__state[name].model_copy(update={"ids": ids}))
        else:
            await self.__delete(name)

    # ── the steps ─────────────────────────────────────────────────────────

    async def __tidy(self) -> None:
        """Step 0 (see the module): no networks left; nobody changes clients."""
        for name in sorted(self.__state):
            networks = self.__networks(name)
            if not networks:
                continue
            serving = {address for address in networks if self.__holder(address) is None}
            others = {identifier for identifier in self.__state[name].ids if host_of(identifier) is None}
            await self.__hold(name, others | serving)

    async def __round(self) -> None:
        await self.__receive()
        await self.__change()
        await self.__release()
        await self.__settle()

    async def __receive(self) -> None:
        """Step 1: the missing clients added and the ready ones given their newcomers, all as wanted."""
        for name, profile in self.__wanted.items():
            if name in self.__state and not self.__ready(name):
                continue
            incoming = self.__incoming(name)
            if not incoming:
                continue
            if name in self.__state:
                await self.__update(profile.model_copy(update={"ids": self.__fitting(profile, self.__state[name].ids, incoming)}))
            else:
                await self.__add(profile.model_copy(update={"ids": self.__fitting(profile, (), incoming)}))

    async def __change(self) -> None:
        """Step 2: the clients whose settings change, each once all who leave it are held where they go."""
        while True:
            changing = sorted(name for name in self.__wanted if name in self.__state and not self.__ready(name))
            due = [name for name in changing if all(self.__covered(address) for address in self.__leavers(name))]
            if due:
                await self.__become(due[0])
                continue
            # waiting for each other: the leavers of a client in such a cycle, going to a client that changes too, are
            # held meanwhile; a client that only waits for one short of room waits for the next round
            stuck = {name: {address for address in self.__leavers(name) if self.__final.get(address) in changing} for name in changing}
            waits = {name: {self.__final[address] for address in leavers} for name, leavers in stuck.items()}
            cycle = [name for name in changing if name in self.__reached(waits, name)]
            if not cycle:
                return
            name = min(cycle)
            await self.__stand_in(name, stuck[name])

    @staticmethod
    def __reached(waits: Mapping[str, set[str]], start: str) -> set[str]:
        """The clients `start` waits for, directly or through others."""
        reached: set[str] = set()
        ahead = list(waits.get(start, ()))
        while ahead:
            name = ahead.pop()
            if name not in reached:
                reached.add(name)
                ahead.extend(waits.get(name, ()))
        return reached

    async def __make_room(self) -> None:
        """Nobody could be moved: the clients are too full to take the networks of those they get, and those who leave
        them wait for room where they go. The people who leave one of them are held by a stand-in meanwhile."""
        waiting = {
            name: {address for address in self.__leavers(name) if not self.__covered(address)}
            for name in sorted(self.__wanted)
            if name in self.__state
        }
        full = [name for name, leavers in waiting.items() if leavers]
        if full:
            name = min(full)
            await self.__stand_in(name, waiting[name])

    async def __become(self, name: str) -> None:
        """The client as wanted, in one call: those who stay, those it gets (as networks, or as themselves when no
        client holds them); those who leave it are held where they go (or by a stand-in), or wanted in no client."""
        profile = self.__wanted[name]
        staying = (self.__people(name) - self.__leavers(name)) | {host_id(address) for address in self.__networks(name)}
        await self.__update(profile.model_copy(update={"ids": self.__fitting(profile, staying, self.__incoming(name))}))
        for temporary in sorted(self.__state):
            if temporary.startswith(MOVE_PROFILE) and temporary not in self.__wanted:
                await self.__plain(temporary)

    async def __stand_in(self, name: str, leavers: set[str]) -> None:
        """A temporary client with this one's settings holds (as networks) these of its people, who go to a client
        that changes too; the client lets go of them (changing at once when nobody else waits), and the temporary one
        holds them as themselves."""
        current = self.__state[name]
        temporary = current.model_copy(update={"name": self.__temporary_name(), "ids": []})
        ids = self.__fitting(temporary, (), [host_id(address) for address in sorted(leavers)])
        await self.__add(temporary.model_copy(update={"ids": ids}))
        moved = {host_of(identifier) for identifier in ids}
        if all(address in moved or self.__covered(address) for address in self.__leavers(name)):
            await self.__become(name)
            return
        await self.__hold(name, set(current.ids) - moved)
        await self.__plain(temporary.name)

    async def __plain(self, name: str) -> None:
        """The client holds as themselves those it holds as networks whose address no client holds."""
        networks = {address for address in self.__networks(name) if self.__holder(address) is None}
        if networks:
            others = {identifier for identifier in self.__state[name].ids if host_of(identifier) not in networks}
            await self.__hold(name, others | networks)

    async def __release(self) -> None:
        """Step 3: every client lets go of those who leave it and are held where they go; an unwanted one left with
        nobody is removed, a wanted one as wanted takes its newcomers in the same call (it may be too full to take them
        before). A client whose settings change and still waits keeps them, and its people with them."""
        for name in sorted(self.__state):
            if name not in self.__state:
                continue
            going = {address for address in self.__leavers(name) if self.__covered(address)}
            if not going:
                continue
            kept = set(self.__state[name].ids) - going
            if not kept and name in self.__wanted:
                if not self.__ready(name):
                    continue        # it changes in the call that lets them go (step 2)
                kept = set(self.__fitting(self.__wanted[name], (), self.__incoming(name)))
            await self.__hold(name, kept)

    async def __settle(self) -> None:
        """Step 4: every client holds its people as themselves (each network whose address no other client holds any more)."""
        for name in sorted(self.__state):
            await self.__plain(name)
