"""What the commands need of the world around them, named by what it does.

The running agent hands them the real things (the database, the other
services); the checks hand them stand-ins.
"""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from pydantic import SecretStr

from awgscale.watch.models import Status as WatchState

from .adguard.models import Counters
from .app.owners import Owner
from .cascade.models import CascadeSettings
from .clients.exits import Registry
from .commands import RouteMode
from .dns.models import Device
from .invites.models import Invitation
from .journal.models import Kind as JournalKind, Who
from .protocol import Caller
from .sites.models import Seen, SiteGroup
from .spaces.models import StoredSpaces
from .seen.models import ExitSeen
from .state.models import DnsShown, ExitFigures
from .state.system import EntryFacts, ExitSystem
from .store.documents import Record
from .tops.models import Tops
from .tunnel import Peer
from .users.models import KnownUser, Made, Removed, StoredGroups, UserGroup


@dataclass(frozen=True, slots=True)
class Outcome:
    """How a request to another service ended: done or not, and what it said when not."""

    ok: bool
    said: str = ""


@dataclass(frozen=True, slots=True)
class NamesReset:
    """How the watch service's step of a restart of the resolver ended, and the sets of the rules for groups it
    could not make (the resolver's file must not name them: it refuses to start with a set that is not there)."""

    ok: bool
    said: str = ""
    missing: frozenset[str] = frozenset()


class Together(Protocol):
    """The store, for a change that touches several kinds: what is stored inside
    the block is stored whole or not at all, and is read back as written before the block ends."""

    def whole(self) -> AbstractAsyncContextManager[None]: ...


class JournalWriter(Protocol):
    """The journal, for a line a command writes by itself."""

    async def write(self, text: str, bad: bool = False, kind: JournalKind = JournalKind.CHANGE, who: Who = Who.SYSTEM) -> None: ...


class SeenStore(Protocol):
    """Where the notes of the last look at the state are kept, as they are stored."""

    async def load(self) -> Record:
        """What was seen last ({} when nothing yet). Raises StoreDown."""
        ...

    async def save(self, notes: Record) -> None: ...


class WatchGate(Protocol):
    """The watch service: it applies the rules the settings ask for."""

    async def health(self, timeout: float) -> Outcome: ...

    async def apply(self, timeout: float) -> Outcome: ...

    async def pin(self, timeout: float) -> Outcome: ...


PIN_TIMEOUT = 60.0                  # how long the watch service is given to pin the users to their exits


class CascadeStore(Protocol):
    """Where the cascade's settings are kept."""

    async def load(self) -> CascadeSettings:
        """The stored settings. Raises StoreDown — also for a store they were never moved into."""
        ...

    async def save(self, settings: CascadeSettings) -> None: ...


class UsersDirectory(Protocol):
    """Who the users are, as the users service lists them."""

    async def list(self, force: bool = False) -> list[KnownUser]:
        """The users. Asked afresh with `force`; otherwise a list a minute old will do.
        While the service cannot be asked: the users as last known, never "nobody"."""
        ...


class GroupsStore(Protocol):
    """Where the users' groups are kept."""

    async def read(self) -> StoredGroups: ...


class OwnFilters(Protocol):
    """The filtering single users chose for themselves: {name: GroupFilter value}."""

    async def read(self) -> dict[str, str]: ...

    async def save(self, chosen: dict[str, str]) -> None: ...


class HandPins(Protocol):
    """The exits chosen by hand for single users: {name: exit index}."""

    async def read(self) -> dict[str, int]: ...

    async def save(self, pins: dict[str, int]) -> None: ...

    async def whole(self) -> set[str]:
        """Who sends all their traffic through their exit, Russian sites too (chosen in the app)."""
        ...

    async def save_whole(self, names: set[str]) -> None: ...


class UsersGate(Protocol):
    """The users service, for what changes a user or hands out their code."""

    async def set_expiry(self, names: list[str], term: str | None) -> Outcome:
        """A new term from now for these users (None: no term)."""
        ...

    async def config(self, name: str) -> dict[str, Any] | None:
        """What the device needs to connect, as the page reads it; None when there is no such user or the service is away."""
        ...


class AccountsGate(Protocol):
    """The users service, for making a user and removing users."""

    async def add(self, name: str, term: str | None) -> Made | None:
        """→ what the service said; None when it could not be asked."""
        ...

    async def remove(self, names: list[str]) -> Removed | None:
        """→ what became of each name; None when the service could not be asked."""
        ...


class Readiness(Protocol):
    """The store, asked whether it can be written to at all."""

    async def ping(self) -> None:
        """Raises StoreDown now rather than halfway: asked before a command that
        changes something else first (a user made or removed by the users service)."""
        ...


class LeavingNotes(Protocol):
    """The notes of the last look at the state, told what the agent itself does to the users."""

    async def leaving(self, names: Sequence[str]) -> None:
        """These users are being removed by the agent: their going is not news."""
        ...

    async def back(self, name: str) -> None:
        """A user created (again) by the agent is nobody's leftover."""
        ...


class TunnelPeers(Protocol):
    """The users' tunnel: who is in it, when they last spoke, how much went through."""

    async def peers(self) -> list[Peer]: ...


class WatchStatus(Protocol):
    """The watch service, asked how the exits are and what it has loaded."""

    async def status(self, timeout: float) -> WatchState | None:
        """→ its answer; None when it did not answer (the exits are then unknown, not gone)."""
        ...


class ClientCounts(Protocol):
    """The blocker's count of the day for one address. A number, never names."""

    async def asked_by(self, address: str) -> int:
        """How many requests the address made. Raises when the blocker cannot be asked."""
        ...


# ── the exits as servers ─────────────────────────────────────────────────────

class ExitsGate(Protocol):
    """The exits service: it makes a rented server an exit and looks after those it made."""

    async def registry(self) -> Registry:
        """The servers and the service's job. Refused, in the service's words or ours, when it cannot be asked."""
        ...

    async def add(self, index: int, name: str, weight: int, address: str, ssh_port: int, password: SecretStr | None) -> Outcome:
        """Begins the job that makes the server an exit. The password is used once there and kept nowhere."""
        ...

    async def remove(self, index: int, iface: str, name: str) -> Outcome:
        """Takes the entry's side of the link away (and, on a server the service made, cleans up there)."""
        ...

    async def update(self, index: int, reboot: bool, name: str) -> Outcome: ...


class WorkingExits(Protocol):
    """Which exits the watch service calls working right now."""

    def up(self) -> set[int]: ...


class ExitWeights(Protocol):
    """The exits the routing knows (those of the compiled file), with their shares: {number: weight}."""

    def weights(self) -> dict[int, int]: ...


class GroupExits(Protocol):
    """The users' groups, as far as an exit's removal changes them: the stored records."""

    async def records(self) -> list[dict[str, Any]]: ...

    async def save(self, records: list[dict[str, Any]]) -> None: ...


class CountryFinder(Protocol):
    """Where an exit is: by the public address its link points to (the guess its name gives is only shown, never found here)."""

    async def find(self, name: str, iface: str) -> str | None:
        """An ISO 3166-1 alpha-2 code, lower case; None when it cannot be told. Never raises."""
        ...


class KeptPins(Protocol):
    """What is kept on an exit by hand: a group, or single users."""

    async def release(self, index: int) -> None:
        """Everybody kept on this exit goes back to "auto"."""
        ...
# ── the state ────────────────────────────────────────────────────────────────

class ExitsSeen(Protocol):
    """The exits as the last look at the state saw them."""

    async def exit_figures(self) -> dict[int, ExitFigures]:
        """By the exit's number: where its link points, how many users are on it and online."""
        ...


class TunnelFacts(Protocol):
    """The users' tunnel and the links to the exits, as the `awg` tool reports them."""

    async def peers(self) -> list[Peer]: ...

    async def listen_port(self) -> int | None: ...

    async def endpoint_of(self, iface: str) -> str | None: ...


class EntryServer(Protocol):
    """The entry server's own figures."""

    async def facts(self) -> EntryFacts: ...

    async def link_counters(self, iface: str) -> tuple[int, int]:
        """→ (bytes received, bytes sent) on an interface since boot."""
        ...

    async def link_there(self, iface: str) -> bool: ...


class ExitServers(Protocol):
    """How each exit server is doing, asked through its tunnel."""

    async def of(self, iface: str) -> ExitSystem | None: ...


class PinsKeeper(Protocol):
    """Whoever keeps the pins file right between changes."""

    async def check_pins(self, users: Sequence[KnownUser]) -> None: ...


class SeenKeeper(Protocol):
    """The notes of the last look: compared with this one, the difference written into the journal."""

    async def check(self, users: Sequence[KnownUser], exits: Sequence[ExitSeen], now: datetime) -> None:
        """Raises StoreDown: then there is no comparison this time."""
        ...


class DayTops(Protocol):
    """The day's most visited sites, by name. Never raises."""

    async def today(self) -> Tops: ...


class DayCounters(Protocol):
    """The blocker's counters of the day. Raises when it cannot be asked."""

    async def counters(self) -> Counters: ...


class DnsFigures(Protocol):
    """The blocker's day, ready for the state. Never raises."""

    async def stats(self) -> DnsShown: ...


# ── blocking (AdGuard) ───────────────────────────────────────────────────────

class FilterProfiles(Protocol):
    """How each group of users is filtered, as the blocker applies it (AdGuard's
    per-group clients). The real one is adguard.profiles.AdGuardProfiles."""

    async def sync(self, groups: Sequence[UserGroup], users: Sequence[KnownUser]) -> str | None:
        """Make the blocker match these groups. → an error text, or None when it is done
        (also when the list of users could not be read: then nothing is changed)."""
        ...

    async def check(self, users: Sequence[KnownUser]) -> None:
        """With every look at the state: puts the blocker right when the stored groups
        or these users no longer match what it applies. Never raises for a blocker that is away."""
        ...


class BlockGroupsStore(Protocol):
    """Where the admin's own groups of blocked names are kept."""

    async def read(self) -> list[Record]: ...

    def check(self, groups: list[Record]) -> None:
        """Raises StoreRefused for what the store would not keep."""
        ...

    async def save(self, groups: list[Record]) -> None: ...


class BlockCounters(Protocol):
    """The blocker's counters of the day; zeros while it does not answer."""

    async def today(self) -> Counters: ...


class StoppedByList(Protocol):
    """Requests each block list stopped in the last day: {list id as text: count}."""

    async def stopped_by_list(self) -> Mapping[str, Any]: ...


# ── smart-DNS devices ────────────────────────────────────────────────────────

class DevicesStore(Protocol):
    """Where the codes of the devices that use the DNS without the VPN are kept."""

    async def read(self) -> dict[str, Device]: ...

    async def save(self, devices: dict[str, Device]) -> None: ...


# ── spaces and invitations ───────────────────────────────────────────────────


class SpacesStore(Protocol):
    """Where the device spaces are kept."""

    async def read(self) -> StoredSpaces: ...

    async def save(self, records: Sequence[Record]) -> None: ...


class SpaceRules(Protocol):
    """The watch service, for the spaces: it loads the file compiled for it."""

    async def load(self, timeout: float) -> Outcome: ...


class SpacesCompiler(Protocol):
    """Whoever compiles the spaces' file and has it loaded (the spaces' own service)."""

    async def compile(self, spaces: Sequence[Record], groups: Sequence[Record] | None = None) -> str | None:
        """→ why it could not be applied, or None."""
        ...


class InvitesStore(Protocol):
    """Where the invitations are kept."""

    async def read(self) -> list[Invitation]:
        """The invitations that have not expired."""
        ...

    async def save(self, invitations: Sequence[Invitation]) -> None: ...


class GroupMembers(Protocol):
    """The users' groups, for a guest who joins one."""

    async def read(self) -> StoredGroups: ...

    async def save(self, records: Sequence[Record]) -> None: ...


class InviteRecords(Protocol):
    """The invitations as they are stored, every one of them: for whoever removes a group."""

    async def read(self) -> object: ...

    async def save(self, records: Sequence[Record]) -> None: ...


class Newcomers(Protocol):
    """What making a user for a guest takes, beside the invitation itself."""

    async def ready(self) -> None:
        """Raises StoreDown now rather than halfway: no user is made for a change the store could not follow."""
        ...

    async def forget(self, name: str) -> None:
        """Whoever had this name before leaves nothing to the newcomer: spaces, group, pins, pause."""
        ...

    async def add(self, name: str) -> bool:
        """Ask the users service for the user. → did it say yes."""
        ...

    async def pin(self, groups: Sequence[Record], users: Sequence[KnownUser]) -> None:
        """The group the newcomer joined is kept on an exit: recompile who goes where."""
        ...

    async def filter(self, groups: Sequence[Record], users: Sequence[KnownUser]) -> None:
        """The group the newcomer joined is filtered its own way: tell the blocker."""
        ...


class InviteNotes(Protocol):
    """The journal, for what the invitations write into it."""

    async def made(self, text: str, caller: Caller) -> None: ...

    async def used(self, text: str) -> None: ...
# ── sites and routing ────────────────────────────────────────────────────────

class RoutingWatch(Protocol):
    """The watch service, for what the sites ask of it."""

    async def health(self, timeout: float) -> Outcome: ...

    async def apply(self, timeout: float) -> Outcome:
        """Makes the rules anew: a changed mode takes effect."""
        ...

    async def domains(self, timeout: float) -> NamesReset:
        """Loads the listed networks and forgets the addresses learned for the listed names."""
        ...

    async def features(self, timeout: float) -> frozenset[str]:
        """What the running watch service can do (watch/models.FEATURES); empty when it did not answer."""
        ...


class ResolverKeeper(Protocol):
    """Whoever stops and starts the resolver."""

    async def stop(self, timeout: float) -> Outcome: ...

    async def start(self, timeout: float) -> Outcome: ...


class Blocker(Protocol):
    """The resolver's own control API (AdGuard Home)."""

    async def post(self, path: str, payload: dict[str, Any], timeout: float = 10) -> None:
        """Raises when the resolver did not take it."""
        ...

    async def get(self, path: str, timeout: float = 5) -> dict[str, Any]: ...


class ResolverSetup(Protocol):
    """The resolver told where to ask for which names, and asked whether it answers."""

    async def set_upstreams(self, entries: Sequence[str], direct: Sequence[str] = ()) -> str | None:
        """→ why it failed, or None."""
        ...

    async def clear_cache(self) -> None: ...

    async def answers(self) -> bool: ...

    async def prewarm(self, entries: Sequence[str]) -> None: ...


class SitesStore(Protocol):
    """Where the groups of sites are kept."""

    async def read(self) -> list[SiteGroup]: ...

    async def save(self, groups: Sequence[SiteGroup]) -> None: ...


class ModeStore(Protocol):
    """Where the mode the admin chose is kept (nothing stored is the list)."""

    async def read(self) -> RouteMode: ...

    async def save(self, mode: RouteMode) -> None: ...


class BlockGroups(Protocol):
    """The admin's own block groups, as stored."""

    async def read(self) -> Sequence[Record]: ...


class ListSource(Protocol):
    """The lists by link, downloaded."""

    async def read(self, url: str) -> Any:
        """→ the entries and how many lines were skipped (sites.lists.ParsedList). Raises sites.lists.ListFailed."""
        ...


class Prober(Protocol):
    """Opens a site one way and says what a visitor would see."""

    async def probe(self, host: str, address: str, source: str | None, path: str) -> Seen: ...


class OwnersStore(Protocol):
    """Who may run the network from the app."""

    async def read(self) -> list[Owner]: ...

    async def add(self, user: KnownUser) -> None: ...


class RulesView(Protocol):
    """The whole list of rules (rules/store.py): the order decides what a rule covers."""

    async def read(self) -> list[Record]: ...

    async def preview(self, part: Any, records: Sequence[Record]) -> list[Record]: ...


class BlockFollower(Protocol):
    """The blocker's rules made again from the list: a rule that routes, put above a blocked one, unblocks."""

    async def follow(self) -> None: ...


class GroupMembers(Protocol):
    """The addresses of the people of each group (rules/members.py)."""

    async def addresses(self, users: Sequence[Any] | None = None) -> dict[str, list[str]]:
        """{group id: tunnel addresses of its members}; `users` as known now, or read when None."""
        ...


class ProfilesNudge(Protocol):
    """adguard.profiles.AdGuardProfiles: the groups' clients made again in the background when they come out otherwise."""

    def nudge(self) -> None:
        """Never raises; never waits for the lock of the users under the caller's."""
        ...


class ScopedFollower(Protocol):
    """rules.routes.ScopedRoutes: the watch service's chain of the rules for some groups that go abroad or directly."""

    async def follow(self, rules: Sequence[Record] | None = None) -> None:
        """The chain made to follow these rules (None: the stored list). Raises Refused."""
        ...


class ScopedWatch(Protocol):
    """The watch service, as the rules for some groups that go abroad or directly ask it (clients/gates.WatchService)."""

    async def scoped(self, timeout: float) -> Outcome:
        """The chain of those rules loaded again from scoped.rules."""
        ...

    async def features(self, timeout: float) -> frozenset[str]:
        """What the running watch service can do; empty when it did not answer or is of an earlier version."""
        ...


class PinApplier(Protocol):
    """The watch service's pass without probing: the users' sets follow the files (pins, groups)."""

    async def pin(self, timeout: float) -> Outcome: ...


class ScopedNamesKeeper(Protocol):
    """sites.retry.ScopedNamesRetry: the names of the rules for groups given to the resolver again after a failed try."""

    def check(self) -> None:
        """Never raises, never waits."""
        ...


class ScopedMembersKeeper(Protocol):
    """rules.routes.ScopedMembers: who is in each group, handed to the watch service."""

    async def check(self, users: Sequence[KnownUser]) -> None:
        """Never raises."""
        ...
