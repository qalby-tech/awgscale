"""How AdGuard's clients go from what it holds to what is wanted with nobody out of a client between two calls
(AdGuardProfiles.sync). A person who is in a client stays in one at every moment, filtered as before or as after —
also when AdGuard refuses a call, answers nothing, or goes down halfway: every call is safe on its own, and the first
one that does not go through stops the change (the next look starts from what AdGuard then holds).

AdGuard refuses an address another client holds, so an address cannot go from one client to another in one call.
It holds an address and a network apart, though, and asks the client of the address first: a client that also holds
"10.0.0.2/32" takes that person over the moment the one holding "10.0.0.2" lets them go (tests/stack_adguard.py,
v0.107.80, also across a restart). So a person changes clients in three calls, each of which they are in a client:

  1. the client they go to also takes them as "<address>/32" (it already has its new settings);
  2. the client they leave lets them go: from now on the one they go to filters them;
  3. the client they went to holds them as the address itself.

A client whose filtering itself changes (a group's filter) while some of its people leave would filter those
people its new way for a moment: they are first held by a temporary client "grp-move-…" with its old settings
(or the client itself becomes that temporary one, when nobody stays in it), and taken from there. A client is never
removed while someone in it is wanted elsewhere and not yet held there; a new client takes over, by renaming, a client
nobody wants any more of the same filtering. The temporary clients are removed at the end; any left behind by a stop
are taken apart by the next change."""
from __future__ import annotations

from ipaddress import ip_address
from typing import Any, Mapping, Protocol, Sequence

from ..ports import Outcome
from .api import PROFILE_PREFIX
from .models import Profile

COMPARED_SWITCHES = ("use_global_settings", "filtering_enabled", "parental_enabled", "safebrowsing_enabled")
MOVE_PROFILE = PROFILE_PREFIX + "move-"     # a temporary client: never a group's (group ids are hexadecimal) nor a level's
HOST_BITS = {4: 32, 6: 128}


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


def same(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Do two clients of AdGuard filter the same people the same way. Fields that are absent count as switched off."""
    if sorted(first.get("ids") or []) != sorted(second.get("ids") or []):
        return False
    if any(bool(first.get(switch)) != bool(second.get(switch)) for switch in COMPARED_SWITCHES):
        return False
    if list(first.get("upstreams") or []) != list(second.get("upstreams") or []):
        return False
    if bool(first.get("upstreams_cache_enabled")) != bool(second.get("upstreams_cache_enabled")):
        return False
    return bool((first.get("safe_search") or {}).get("enabled")) == bool((second.get("safe_search") or {}).get("enabled"))


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


def as_profile(name: str, client: Mapping[str, Any]) -> Profile:
    """A client as AdGuard reports it, as the agent sends it."""
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


class Stopped(Exception):
    """AdGuard did not take a call: the change stops here, safe as it is."""

    def __init__(self, said: str) -> None:
        super().__init__(said)
        self.said = said


class ClientMoves:
    """One change of the clients (see the module). `run` → what AdGuard refused (empty: everything is as wanted)."""

    def __init__(self, clients: ProfileClients, want: Sequence[Profile], have: Mapping[str, Mapping[str, Any]]) -> None:
        self.__clients = clients
        self.__want = want
        self.__have = have
        self.__state: dict[str, Profile] = {}               # what AdGuard holds, as far as its answers tell
        self.__wanted: dict[str, Profile] = {}
        self.__final: dict[str, str] = {}                   # {address: the client it ends in}
        self.__base: dict[str, str | None] = {}             # {wanted client: the client it is made of now}
        self.__changing: set[str] = set()                   # the clients whose filtering itself changes
        self.__via_temporary: set[str] = set()              # leaving such a client: held by a temporary one meanwhile
        self.__temporary: set[str] = set()

    async def run(self) -> list[str]:
        self.__state = {name: as_profile(name, client) for name, client in self.__have.items()}
        self.__wanted = {profile.name: profile for profile in self.__want}
        self.__final = {address: profile.name for profile in self.__want for address in profile.ids}
        try:
            await self.__tidy()
            self.__plan()
            await self.__cover()
            await self.__prepare()
            await self.__release()
            await self.__settle()
            await self.__clear()
        except Stopped as e:
            return [e.said]
        return []

    # ── what AdGuard holds ────────────────────────────────────────────────

    def __exact(self, name: str) -> set[str]:
        return {identifier for identifier in self.__state[name].ids if host_of(identifier) is None}

    def __covers(self, name: str) -> set[str]:
        return {address for address in map(host_of, self.__state[name].ids) if address is not None}

    def __exact_holder(self, address: str) -> str | None:
        return next((name for name in self.__state if address in self.__exact(name)), None)

    def __covered_elsewhere(self, address: str, name: str) -> bool:
        """Is this address held by another client: as itself, or as its network."""
        return any(address in self.__exact(other) or address in self.__covers(other) for other in self.__state if other != name)

    def __outgoing(self, name: str, identity: str | None) -> set[str]:
        """The people of this client wanted in another one."""
        return {address for address in self.__exact(name) if self.__final.get(address) not in (None, identity)}

    def __identity(self, name: str) -> str | None:
        """The wanted client this one becomes (itself, or the one it is renamed into); None: removed at the end."""
        return next((wanted for wanted, made_of in self.__base.items() if made_of == name), None)

    def __free(self, profile: Profile) -> set[str]:
        """Its addresses no client holds as themselves."""
        return {address for address in profile.ids if self.__exact_holder(address) is None}

    def __temporary_name(self) -> str:
        number = 1
        while f"{MOVE_PROFILE}{number}" in self.__state or f"{MOVE_PROFILE}{number}" in self.__wanted:
            number += 1
        return f"{MOVE_PROFILE}{number}"

    # ── the calls ─────────────────────────────────────────────────────────

    async def __add(self, profile: Profile) -> None:
        done = await self.__clients.add(profile)
        if not done.ok:
            raise Stopped(done.said)
        self.__state[profile.name] = profile

    async def __update(self, name: str, profile: Profile) -> None:
        if name == profile.name and same(self.__state[name].wire(), profile.wire()):
            return
        done = await self.__clients.update(name, profile)
        if not done.ok:
            raise Stopped(done.said)
        del self.__state[name]
        self.__state[profile.name] = profile

    async def __delete(self, name: str) -> None:
        done = await self.__clients.delete(name)
        if not done.ok:
            raise Stopped(done.said)
        del self.__state[name]

    # ── the steps ─────────────────────────────────────────────────────────

    async def __tidy(self) -> None:
        """What a stopped change left: a network nobody is served by is dropped, unless its client is where that person
        goes; one that serves its person is held as the address itself, in the same client (nobody changes clients).
        A client left with nothing is removed only when it is not wanted (a wanted one is made as wanted later)."""
        for name in sorted(self.__state):
            covers = self.__covers(name)
            if not covers:
                continue
            serving = {address for address in covers if self.__exact_holder(address) is None}
            waiting = {address for address in covers - serving if self.__final.get(address) == name}
            ids = sorted(self.__exact(name) | serving | {host_id(address) for address in waiting})
            if ids:
                await self.__update(name, self.__state[name].model_copy(update={"ids": ids}))
            elif name not in self.__wanted:
                await self.__delete(name)

    def __plan(self) -> None:
        """Which client each wanted one is made of: itself, or one nobody wants any more of the same filtering."""
        self.__base = {name: name if name in self.__state else None for name in self.__wanted}
        for profile in self.__want:
            if self.__base[profile.name] is not None:
                continue
            spare = [
                name
                for name in sorted(self.__state)
                if name not in self.__wanted and self.__identity(name) is None and switches(self.__state[name].wire()) == switches(profile.wire())
            ]
            if spare:
                self.__base[profile.name] = max(spare, key=lambda name: len(self.__exact(name) & set(profile.ids)))
        self.__changing = {name for name in self.__wanted if name in self.__state and switches(self.__state[name].wire()) != switches(self.__wanted[name].wire())}

    async def __cover(self) -> None:
        """Step 1, for a client whose filtering changes: those leaving it are held meanwhile by a temporary client
        filtered as it is now — the client itself, renamed, when nobody stays in it."""
        for name in sorted(self.__changing):
            leaving = self.__outgoing(name, name)
            if not leaving:
                continue
            temporary = self.__temporary_name()
            moved = self.__state[name].model_copy(update={"name": temporary, "ids": sorted(host_id(address) for address in leaving)})
            if any(self.__final.get(address) == name for address in self.__exact(name)):
                await self.__add(moved)
                self.__via_temporary |= leaving
            else:
                await self.__update(name, moved)
                self.__base[name] = None
                self.__changing.discard(name)
            self.__temporary.add(temporary)

    async def __prepare(self) -> None:
        """Step 1: each wanted client takes, as networks, the people it gets from others, and as themselves those no
        client holds; it has its new settings — unless its filtering changes while some of its people still leave."""
        def stale_first(profile: Profile) -> bool:
            """A client holding a network that is someone else's now lets it go first: the one they go to takes it."""
            made_of = self.__base[profile.name]
            return made_of is None or all(self.__final.get(address) == profile.name for address in self.__covers(made_of))

        for profile in sorted(self.__want, key=stale_first):
            made_of = self.__base[profile.name]
            waits = made_of in self.__changing and bool(self.__outgoing(profile.name, profile.name))
            ids: set[str] = set()
            if made_of is not None:
                ids = {
                    identifier
                    for identifier in self.__state[made_of].ids
                    if identifier in self.__final or self.__final.get(host_of(identifier) or "") == profile.name
                }
            for address in profile.ids:
                holder = self.__exact_holder(address)
                if made_of is not None and holder == made_of:
                    continue
                if holder is None:
                    if not waits:
                        ids.add(address)
                elif address not in self.__via_temporary:
                    ids.add(host_id(address))
            if not ids:
                continue
            settings = self.__state[made_of] if waits and made_of is not None else profile
            made = settings.model_copy(update={"name": profile.name, "ids": sorted(ids)})
            if made_of is None:
                await self.__add(made)
            else:
                await self.__update(made_of, made)
            self.__base[profile.name] = profile.name

    async def __release(self) -> None:
        """Step 2: the clients let go of the people who leave them (each now held where they go, or by a temporary
        client); those whose filtering changes first, so that whoever comes to them meets their new settings; then the
        clients nobody wants any more are removed."""
        order = sorted(self.__changing) + [profile.name for profile in self.__want if profile.name not in self.__changing]
        for wanted in order:
            made_of = self.__base[wanted]
            if made_of is None:
                continue
            # only those held elsewhere meanwhile (all of them, unless a call above was not answered as made)
            leaving = {address for address in self.__outgoing(made_of, wanted) if self.__covered_elsewhere(address, made_of)}
            if not leaving:
                continue
            kept = {identifier for identifier in self.__state[made_of].ids if identifier not in leaving and (host_of(identifier) is not None or identifier in self.__final)}
            ids = sorted(kept | self.__free(self.__wanted[wanted]))
            if not ids:
                continue
            await self.__update(made_of, self.__wanted[wanted].model_copy(update={"ids": ids}))
            self.__base[wanted] = wanted
        for name in sorted(self.__state):
            if name in self.__temporary or self.__identity(name) is not None:
                continue
            if all(self.__covered_elsewhere(address, name) for address in self.__exact(name) if address in self.__final):
                await self.__delete(name)

    async def __settle(self) -> None:
        """Step 3: every wanted client exactly as wanted — its people held as themselves."""
        for profile in self.__want:
            made_of = self.__base[profile.name]
            if made_of is None:
                await self.__add(profile)
            else:
                await self.__update(made_of, profile)
            self.__base[profile.name] = profile.name

    async def __clear(self) -> None:
        """Step 4: the temporary clients, and whatever else nobody wants, once everybody in them is held elsewhere."""
        for name in sorted(self.__state):
            if name in self.__wanted:
                continue
            people = [address for address in self.__exact(name) | self.__covers(name) if address in self.__final]
            if all(self.__exact_holder(address) not in (None, name) for address in people):
                await self.__delete(name)

