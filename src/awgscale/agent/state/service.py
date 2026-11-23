"""Putting the state together. One thing after another, in the order it has
always been asked: a later step may depend on what an earlier one put right.

The look is taken by the sampling job (and, right after a change, by the first
question that comes): `awg show dump` once, the users once, the rows and the
counters made once. `state` and `clients` answer from the last look."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Sequence

from awgscale.watch.conf import DEFAULT_HOME_COUNTRY
from awgscale.watch.health import ExitState
from awgscale.watch.models import Status

from ..commands import Clients, DayTraffic, RouteMode, State, Traffic
from ..errors import Refused, StoreDown
from ..exits.working import DEFAULT_WEIGHT
from ..locks import file_lock
from ..ports import (
    DnsFigures, EntryServer, ExitServers, ExitWeights, FilterProfiles, GroupsStore, HandPins, PinsKeeper, ScopedMembersKeeper, SeenKeeper, SitesStore, SpacesStore,
    TunnelFacts, UsersDirectory, WatchStatus,
)
from ..seen.models import ExitSeen
from ..tunnel import Peer, endpoint_host
from ..sites.entries import WILDCARD, is_network
from ..sites.groups import version_of_sites
from ..sites.routing import listed_lines
from ..payloads import decode
from ..users.models import KnownUser, StoredGroups, version_of
from ..users.pauses import Pauses, PauseStore
from .clients import ClientsIndex, ClientsPage, ClientsQuery, LookInput, made_of
from .ledger import DayAnswer, Ledger
from .models import ExitFigures, ExitShown, RoutingShown, StateShown, TrafficShown, Versions

log = logging.getLogger("awgscale.agent")

STATUS_TIMEOUT = 20.0
ERROR_LENGTH = 300
STALE_AFTER_LOOKS = 3                  # a look older than this many intervals is not answered from: the job is not running


def on_exits(exits: Sequence[ExitShown]) -> dict[str, int]:
    """Address → the exit its user is on (the watch service lists every address): made beside the loop."""
    return {str(address): exit_.view.index for exit_ in exits for address in exit_.view.clients}


def by_address(peers: Sequence[Peer]) -> dict[str, Peer]:
    return {peer.address: peer for peer in peers}


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One look: the summary, the users ready to be asked, and when it was taken (by this process's clock)."""

    summary: StateShown
    clients: ClientsIndex
    taken: float


@dataclass(frozen=True, slots=True)
class Sampling:
    """How often a look is taken, and what it counts as online."""

    every: timedelta
    online_for: timedelta
    keep_day_every: timedelta


class StateService:
    def __init__(
        self,
        watch: WatchStatus,
        exits: ExitWeights,
        tunnel: TunnelFacts,
        entry: EntryServer,
        directory: UsersDirectory,
        pins: PinsKeeper,
        seen: SeenKeeper,
        profiles: FilterProfiles,
        exit_servers: ExitServers,
        pause_store: PauseStore,
        pauses: Pauses,
        hand_pins: HandPins,
        groups: GroupsStore,
        sites: SitesStore,
        spaces: SpacesStore,
        dns: DnsFigures,
        client_iface: str,
        domains_exit: Path,
        domains_direct: Path,
        lock_file: Path,
        sampling: Sampling,
        ledger: Ledger,
        scoped: ScopedMembersKeeper | None = None,
    ) -> None:
        self.__scoped = scoped                 # who is in each group, for the watch service's rules for some groups
        self.__watch = watch
        self.__exits = exits
        self.__tunnel = tunnel
        self.__entry = entry
        self.__directory = directory
        self.__pins = pins
        self.__seen = seen
        self.__profiles = profiles
        self.__exit_servers = exit_servers
        self.__pause_store = pause_store
        self.__pauses = pauses
        self.__hand_pins = hand_pins
        self.__groups = groups
        self.__sites = sites
        self.__spaces = spaces
        self.__dns = dns
        self.__client_iface = client_iface
        self.__domains_exit = domains_exit
        self.__domains_direct = domains_direct
        self.__lock_file = lock_file
        self.__sampling = sampling
        self.__ledger = ledger
        self.__snapshot: Snapshot | None = None
        self.__changed = True                  # a change was made since the last look: the next question waits for a new one
        self.__one_look = asyncio.Lock()
        self.__day_kept = time.monotonic()

    async def __exits_now(self, status: Status | None) -> list[ExitShown]:
        """The exits as the watch service has them, with what the entry sees of each link."""
        weights = self.__exits.weights()
        shown: list[ExitShown] = []
        for view in status.exits if status is not None else []:
            endpoint = await self.__tunnel.endpoint_of(view.iface)
            received, sent = await self.__entry.link_counters(view.iface)
            shown.append(ExitShown(view=view, weight=weights.get(view.index, DEFAULT_WEIGHT), endpoint=endpoint, iface_rx=received, iface_tx=sent))
        return shown

    async def __keep_right(self, users: list[KnownUser], status: Status | None, now: datetime) -> None:
        """What a look at the state puts right on the way: the pins, the journal's notes, the groups' filtering."""
        try:
            await self.__pins.check_pins(users)
        except Exception:
            pass
        try:
            if status is not None:                 # no answer about the exits is not "the exits are gone"
                await self.__seen.check(users, [ExitSeen(index=view.index, name=view.name, state=view.state) for view in status.exits], now)
        except Exception:
            pass
        try:
            await self.__profiles.check(users)
        except Exception:
            pass
        try:
            if self.__scoped is not None:          # no lock: it writes only its own file
                await self.__scoped.check(users)
        except Exception:
            pass

    async def __with_systems(self, exits: list[ExitShown]) -> list[ExitShown]:
        """After the journal's look at the exits: these figures change all the time."""
        shown: list[ExitShown] = []
        for exit_ in exits:
            system = await self.__exit_servers.of(exit_.view.iface) if exit_.view.state is ExitState.UP else None
            shown.append(exit_.model_copy(update={"system": system}) if system is not None else exit_)
        return shown

    async def __versions(self, stored_groups: StoredGroups) -> Versions:
        groups = version_of(list(stored_groups.document))
        sites = version_of_sites(await self.__sites.read())
        spaces = (await self.__spaces.read()).version
        return Versions(ugroups_v=groups, sites_v=sites, spaces_v=spaces)

    async def __routing(self, status: Status | None) -> RoutingShown:
        listed = await asyncio.to_thread(listed_lines, self.__domains_exit)
        direct = await asyncio.to_thread(listed_lines, self.__domains_direct)
        return RoutingShown(
            mode=RouteMode(status.route_mode.value) if status is not None else RouteMode.LIST,
            udp_via_exit=status.udp_via_exit if status is not None else False,
            exit_nets=status.force_exit_nets if status is not None else 0,
            exit_domains=len([entry for entry in listed if not is_network(entry)]),
            exit_patterns=len([entry for entry in listed if WILDCARD in entry]),
            direct_domains=len(direct),
            exit_ips=status.force_exit_ips if status is not None else 0,
            direct_ips=status.force_direct_ips if status is not None else 0,
            single_server=status.single_server if status is not None else False,
            home_country=status.home_country if status is not None else DEFAULT_HOME_COUNTRY,
        )

    async def __look(self, before: Snapshot | None) -> Snapshot:
        now = datetime.now(UTC).replace(microsecond=0)
        taken = time.monotonic()
        status = await self.__watch.status(STATUS_TIMEOUT)
        exits = await self.__exits_now(status)
        on_exit = await asyncio.to_thread(on_exits, exits)
        live = await asyncio.to_thread(by_address, await self.__tunnel.peers())
        users = await self.__directory.list()
        await self.__keep_right(users, status, now)
        exits = await self.__with_systems(exits)
        # The state is answered with the store down as well: the tunnel, the exits and
        # the counters do not depend on it. Only what is stored there is left out, and
        # nothing is recompiled from an answer that did not come.
        paused: set[str] = set()
        store_ok = True
        try:
            stored_paused = set(await self.__pause_store.read())
            by_hand = await self.__hand_pins.read()
        except StoreDown:
            stored_paused = set()
            by_hand = {}
            store_ok = False
            try:
                await self.__pauses.restore()
            except Exception:
                pass
        if stored_paused:
            try:
                async with file_lock(self.__lock_file):
                    paused = set(await self.__pauses.sync(users))     # also puts the rule back after a reboot or a firewall reload
            except Exception:
                paused = stored_paused
        try:
            stored_groups = await self.__groups.read()
            versions = await self.__versions(stored_groups)
        except StoreDown:
            stored_groups = StoredGroups.of([])
            versions = Versions()
            store_ok = False
        group_of = {member: group.id for group in stored_groups.groups if group.id for member in group.members}
        look = LookInput(
            now=now,
            users=users,
            peers=live,
            on_exit=on_exit,
            paused=frozenset(paused),
            pins=by_hand,
            group_of=group_of,
            group_ids=tuple(group.id for group in stored_groups.groups if group.id),
            online_for=self.__sampling.online_for,
        )
        made = await asyncio.to_thread(made_of, look, before.clients if before is not None else None, taken - before.taken if before is not None else 0.0)
        await asyncio.to_thread(self.__ledger.fold, made.index.rows, made.before_by_name)
        counters = made.counters
        per_exit = {count.index: count for count in counters.exits}
        exits = [
            exit_.model_copy(update={"users": per_exit[exit_.view.index].users, "online": per_exit[exit_.view.index].online}) if exit_.view.index in per_exit else exit_
            for exit_ in exits
        ]
        listen_port = await self.__tunnel.listen_port()
        entry = await self.__entry.facts()
        client_if_up = await self.__entry.link_there(self.__client_iface)
        routing = await self.__routing(status)
        summary = StateShown(
            time=now,
            entry=entry,
            listen_port=listen_port,
            client_if_up=client_if_up,
            exits=tuple(exits),
            watch=status.watch if status is not None else None,
            ru_networks=status.ru_networks if status is not None else 0,
            routing=routing,
            counters=counters,
            dns=await self.__dns.stats(),
            versions=versions,
            store_ok=store_ok,
        )
        return Snapshot(summary=summary, clients=made.index, taken=taken)

    # ── the look, taken ──────────────────────────────────────────────────────

    async def sample(self) -> None:
        """What the sampling job does: one look. Never raises (a failed look leaves the last one)."""
        try:
            async with self.__one_look:
                await self.__take()
        except Exception as e:
            log.error("the look at the state failed: %s", repr(e)[:ERROR_LENGTH])

    async def __take(self) -> None:
        self.__changed = False                 # a change made while the look runs asks for another one
        self.__snapshot = await self.__look(self.__snapshot)
        if time.monotonic() - self.__day_kept >= self.__sampling.keep_day_every.total_seconds():
            self.__day_kept = time.monotonic()
            await asyncio.to_thread(self.__ledger.save)

    def changed(self) -> None:
        """Something about the users was changed: the next question is answered from a new look."""
        self.__changed = True

    def keep_day(self) -> None:
        """On the way out: the day's traffic into its file."""
        self.__ledger.save()

    async def __current(self) -> Snapshot:
        """The last look, when it is fresh and nothing was changed since; otherwise a new one
        (questions that come together wait for the same look)."""
        asked = time.monotonic()
        snapshot = self.__snapshot
        too_old = self.__sampling.every.total_seconds() * STALE_AFTER_LOOKS
        if snapshot is not None and not self.__changed and asked - snapshot.taken < too_old:
            return snapshot
        async with self.__one_look:
            snapshot = self.__snapshot
            if snapshot is None or snapshot.taken < asked:
                await self.__take()
        if self.__snapshot is None:
            raise Refused("состояние ещё не получено")
        return self.__snapshot

    # ── the commands ─────────────────────────────────────────────────────────

    async def show(self, _request: State) -> StateShown:
        return (await self.__current()).summary

    async def clients(self, request: Clients) -> ClientsPage:
        try:
            query = ClientsQuery.model_validate(decode(request.payload))
        except ValueError:
            raise Refused("некорректный запрос") from None
        return (await self.__current()).clients.page(query)

    async def day(self, request: DayTraffic) -> DayAnswer:
        snapshot = await self.__current()
        since = datetime.fromtimestamp(int(request.since), UTC)
        return self.__ledger.answer(since, snapshot.summary.time)

    async def exit_figures(self) -> dict[int, ExitFigures]:
        """The exits of the last look the sampling job took, by number: the host their link points to, their users and those online.
        Never waits for a new look (the app's question must stay quick); before the first one there is nothing."""
        snapshot = self.__snapshot
        if snapshot is None:
            return {}
        return {exit_.view.index: ExitFigures(address=endpoint_host(exit_.endpoint), users=exit_.users, online=exit_.online) for exit_ in snapshot.summary.exits}

    async def traffic(self, _request: Traffic) -> TrafficShown:
        """Every user's byte counters, as the tunnel reports them (for the metrics): names and numbers only."""
        snapshot = await self.__current()
        return TrafficShown(time=snapshot.summary.time, counters=tuple((row.name, row.rx, row.tx) for row in snapshot.clients.rows if row.live))

