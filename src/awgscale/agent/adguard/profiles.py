"""Filtering per group of users: "std" is as everybody, "off" — nothing is
blocked (ads included), "kids" — ads, dangerous sites, adult sites and safe
search; the levels "pro", "proplus", "ultimate" — as everybody, plus one of
HaGeZi's lists that applies to them only. Done with AdGuard's own per-client
settings: one client "grp-<id>" per such group, identified by its members'
tunnel addresses; the members of every group of one level share one client,
"grp-<level>". A group with «Без чужого DNS» (an experiment) also has HaGeZi's
list of the encrypted DNS servers: a children's group in its own client, the
members of every group of one level with it in "grp-<level>-doh", and those of
the groups filtered as everybody with it in "grp-std-doh". The lists of the levels and the children's add-on lists are the
clients' tags (adguard.group_lists: ListTag; the file of the lists it keeps
blocks each name for the clients that carry a tag of a list that has it), set
in the same call as their other settings. Which sites go abroad is
AdGuard's upstream and ipset configuration, the same for everybody — except
for the people who send all their traffic through their exit (chosen in the
app): each client has a twin "<name>-all" filtered the same way whose people
ask the exit's resolvers, and "grp-all" holds such people filtered as
everybody. Their sites are then looked up where their traffic goes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Collection, Iterable, Mapping, Protocol, Sequence

from awgscale.core.clock import utc_now

from ..journal.models import Kind, Who
from ..locks import file_lock
from ..ports import GroupsStore, OwnFilters
from ..sites.routing import THIRD_VIA_EXIT, UPSTREAMS_VIA_EXIT
from ..users.models import LEVELS, GroupFilter, KnownUser, UserGroup
from ..sites.scoped import RouteDns
from .api import PROFILE_PREFIX, foreign_dns_profile, level_profile, route_variant, routed_profile, whole_twin
from .client import AdGuardDown
from .models import REQUEST_BYTES_MAX, Profile
from .group_lists import GroupSource, ListsInForce, foreign_dns_sources, kids_sources, level_sources, sources_for, tags_for
from .moves import (  # noqa: F401 (COMPARED_SWITCHES: read here)
    COMPARED_SWITCHES,
    ClientMoves,
    ProfileClients,
    as_profile,
    host_of,
    request_bytes,
    same,
)

log = logging.getLogger("awgscale.agent")

SAFE_ENGINES = ("bing", "duckduckgo", "ecosia", "google", "pixabay", "yandex", "youtube")
ERRORS_LENGTH = 300
NOTHING_WANTED = "[]"
ASK_AGAIN_SECONDS = 60          # how often the blocker itself is asked whether it still has the clients
AFTER_FAILURE_SECONDS = 30      # the blocker did not answer a moment ago: the state does not wait for it again
COMPLAIN_EVERY_SECONDS = 3600   # a refusal is said in the journal once an hour at most


OWN_PROFILE = PROFILE_PREFIX + "own-"  # the users who chose "kids" or "off" themselves share one client per choice
WHOLE_PROFILE = PROFILE_PREFIX + "all"  # those who send all their traffic through their exit, filtered as everybody
WHOLE_UPSTREAMS = (*UPSTREAMS_VIA_EXIT, THIRD_VIA_EXIT)  # the exit's resolvers, as the whole network asks them in the mode "all"
WHOLE_CACHE_BYTES = 1 << 20
# What one request to the blocker may carry: it refuses one over 65 536 bytes (sites/routing.py UPSTREAM_BYTES, measured).
# A client of a group some rule sends abroad or directly carries the upstreams of its group's own list and its people's
# addresses: one that would not fit is not made, and its people ask as everybody until it fits (`fitting`).
CLIENT_REQUEST_BYTES = 60000
# The filterings whose groups with «Без чужого DNS» share one client per filtering (a children's group has its own).
POOLED_WITHOUT_FOREIGN_DNS = (GroupFilter.STANDARD, *LEVELS)


def effective(group: UserGroup | None, own: str | None) -> str:
    """How a user is filtered: their own choice, unless their group is a children's one (an adult chose
    that; a child does not switch it off in the app); otherwise as their group; outside one, as everybody."""
    if group is not None and group.filter == GroupFilter.KIDS.value:
        return group.filter
    if own is not None:
        return own
    return group.filter if group is not None else GroupFilter.STANDARD.value


def wanted(
    groups: Sequence[UserGroup],
    users: Sequence[KnownUser],
    own: Mapping[str, str] | None = None,
    whole: Collection[str] = (),
    routes: Mapping[str, RouteDns] | None = None,
    owners: dict[str, str] | None = None,
) -> list[Profile] | None:
    """The clients of AdGuard that should exist now; None when the list of users could not be read (then nothing is changed).
    `own`: the filtering single users chose for themselves ({name: level}). It takes them out of their group's client
    and into the one of their choice, unless their group is a children's one (see effective); without it, as before.
    `whole`: who sends all their traffic through their exit; they go to the twins of their clients (see split_people).
    `routes`: how the people of each group some rule sends abroad or directly ask ({group id: RouteDns}); they go to
    the variants of their clients (see split_people); `owners`, when given, is filled with {variant's name: group id}."""
    own = own or {}
    routes = routes or {}
    special = [group for group in groups if (group.filter != GroupFilter.STANDARD.value or group.blocks_foreign_dns) and group.members]
    routed_groups = [group for group in groups if group.id is not None and str(group.id) in routes and group.members]
    if (special or own or whole or routed_groups) and not users:
        return None
    address_of = {user.name: user.ip for user in users}
    in_kids = {member for group in groups if group.filter == GroupFilter.KIDS.value for member in group.members}
    chose = {name: level for name, level in own.items() if name not in in_kids and address_of.get(name)}
    profiles: list[Profile] = []
    by_level: dict[str, set[str]] = {level.value: set() for level in LEVELS}
    without_foreign_dns: dict[str, set[str]] = {level.value: set() for level in POOLED_WITHOUT_FOREIGN_DNS}
    by_own: dict[str, set[str]] = {}
    for name, level in chose.items():
        if level in by_level:
            by_level[level].add(address_of[name])
        elif level != GroupFilter.STANDARD.value:
            by_own.setdefault(level, set()).add(address_of[name])
    for group in special:
        members = [member for member in group.members if member not in chose]
        if group.filter in without_foreign_dns:
            pool = without_foreign_dns if group.blocks_foreign_dns else by_level
            pool[group.filter] |= {address_of[member] for member in members if address_of.get(member)}
            continue
        addresses = sorted({address_of[member] for member in members if address_of.get(member)})
        if not addresses:
            continue
        sources = kids_sources(group.kids_lists) if group.filter == GroupFilter.KIDS.value else frozenset()
        profiles.append(special_profile(PROFILE_PREFIX + str(group.id), addresses, group.filter, sources | foreign_dns_sources(group)))
    for level, addresses in sorted(by_own.items()):
        profiles.append(special_profile(OWN_PROFILE + level, sorted(addresses), level))
    for level in LEVELS:        # as everybody (the global settings), and the tag of the level's list
        if by_level[level.value]:
            profiles.append(level_settings(level_profile(level), sorted(by_level[level.value]), level_sources(level.value)))
    for level in POOLED_WITHOUT_FOREIGN_DNS:    # the same, and the tag of the list of the encrypted DNS servers
        if without_foreign_dns[level.value]:
            profiles.append(foreign_dns_settings(level, sorted(without_foreign_dns[level.value])))
    routed = {address_of[member]: str(group.id) for group in routed_groups for member in group.members if address_of.get(member)}
    return split_people(profiles, {address_of[name] for name in whole if address_of.get(name)}, routed, routes, owners)


@dataclass(frozen=True, slots=True)
class Fitting:
    """The clients that should exist, and the groups whose own clients do not fit one request of the blocker."""

    profiles: list[Profile] | None
    degraded: frozenset[str]
    routes: dict[str, RouteDns]         # the groups whose people ask with their own list


def fitting(
    groups: Sequence[UserGroup],
    users: Sequence[KnownUser],
    own: Mapping[str, str] | None = None,
    whole: Collection[str] = (),
    routes: Mapping[str, RouteDns] | None = None,
    limit: int = CLIENT_REQUEST_BYTES,
) -> Fitting:
    """`wanted`, but a group some rule sends abroad or directly whose client (with its people's addresses) would not
    fit one request is left out of `routes`: its people ask as everybody (the routing of their rules still acts) until
    it fits again — a rule for some groups never stops anything for everybody."""
    kept = dict(routes or {})
    degraded: set[str] = set()
    while True:
        owners: dict[str, str] = {}
        want = wanted(groups, users, own, whole, kept, owners)
        over = {owners[profile.name] for profile in want or [] if profile.name in owners and request_bytes(profile) > limit}
        if not over:
            return Fitting(profiles=want, degraded=frozenset(degraded), routes=kept)
        degraded |= over
        for group in over:
            kept.pop(group, None)


def asks_exit(profile: Profile) -> Profile:
    """The same filtering, the exit's resolvers."""
    return profile.model_copy(
        update={"upstreams": list(WHOLE_UPSTREAMS), "upstreams_cache_enabled": True, "upstreams_cache_size": WHOLE_CACHE_BYTES}
    )


def asks_own(profile: Profile, servers: Sequence[str]) -> Profile:
    """The same filtering, these resolvers (with a cache of the client's own: the global one is not theirs)."""
    return profile.model_copy(update={"upstreams": list(servers), "upstreams_cache_enabled": True, "upstreams_cache_size": WHOLE_CACHE_BYTES})


def split_people(
    profiles: list[Profile],
    whole: set[str],
    routed: Mapping[str, str] | None = None,
    routes: Mapping[str, RouteDns] | None = None,
    owners: dict[str, str] | None = None,
) -> list[Profile]:
    """An address is in one client of AdGuard at most. Each client's addresses are parted by how their people ask:
      as everybody                                    → the client itself;
      in a group some rule sends abroad or directly   → route_variant(client, group): the upstreams of that group's list;
      sending everything through their exit           → whole_twin(client): the exit's resolvers;
      both                                            → whole_twin(route_variant(…)): the exit's resolvers, but the
                                                        names their group's rules send directly asked directly.
    Those in no client (filtered as everybody) go to "grp-all", "grp-r<group>" or "grp-r<group>-all", which take the
    global settings. `owners`, when given, is filled with {variant's name: group id}."""
    routed = routed or {}
    routes = routes or {}
    owners = {} if owners is None else owners
    if not whole and not routed:
        return profiles

    def key(address: str) -> tuple[bool, str | None]:
        group = routed.get(address)
        return address in whole, group if group in routes else None

    def placed_as(profile: Profile, whole_traffic: bool, group: str | None) -> Profile:
        if group is None:
            return asks_exit(profile.model_copy(update={"name": whole_twin(profile.name)})) if whole_traffic else profile
        name = route_variant(profile.name, group)
        if whole_traffic:
            owners[whole_twin(name)] = group
            return asks_own(profile.model_copy(update={"name": whole_twin(name)}), [*WHOLE_UPSTREAMS, *routes[group].direct_lines])
        owners[name] = group
        return asks_own(profile.model_copy(update={"name": name}), routes[group].upstreams)

    result: list[Profile] = []
    placed: set[str] = set()
    for profile in profiles:
        parts: dict[tuple[bool, str | None], list[str]] = {}
        for address in profile.ids:
            parts.setdefault(key(address), []).append(address)
        for (whole_traffic, group), addresses in sorted(parts.items(), key=lambda part: (part[0][0], part[0][1] or "")):
            result.append(placed_as(profile.model_copy(update={"ids": addresses}), whole_traffic, group))
        placed.update(profile.ids)
    left: dict[tuple[bool, str | None], list[str]] = {}
    for address in sorted((whole | set(routed)) - placed):
        left.setdefault(key(address), []).append(address)
    for (whole_traffic, group), addresses in sorted(left.items(), key=lambda part: (part[0][0], part[0][1] or "")):
        if not whole_traffic and group is None:
            continue                # in a group no rule routes now: as everybody, no client
        everybody = Profile(
            name=WHOLE_PROFILE if group is None else routed_profile(group),
            ids=addresses,
            use_global_settings=True,
            filtering_enabled=True,
            parental_enabled=False,
            safebrowsing_enabled=False,
            safe_search={"enabled": False, **{engine: True for engine in SAFE_ENGINES}},
        )
        if group is None:
            result.append(asks_exit(everybody))
        elif whole_traffic:
            owners[whole_twin(routed_profile(group))] = group
            twin = everybody.model_copy(update={"name": whole_twin(routed_profile(group))})
            result.append(asks_own(twin, [*WHOLE_UPSTREAMS, *routes[group].direct_lines]))
        else:
            owners[routed_profile(group)] = group
            result.append(asks_own(everybody, routes[group].upstreams))
    return result


def level_settings(name: str, addresses: list[str], sources: Collection[GroupSource]) -> Profile:
    """A client of a level: as everybody (the global settings), and the tags of its lists."""
    return Profile(
        name=name,
        ids=addresses,
        filtering_enabled=True,
        parental_enabled=False,
        safebrowsing_enabled=True,
        safe_search={"enabled": False, **{engine: True for engine in SAFE_ENGINES}},
        tags=tags_for(sources),
    )


def foreign_dns_settings(level: GroupFilter, addresses: list[str]) -> Profile:
    """The client of the members of the groups of a level, or filtered as everybody, with «Без чужого DNS»: filtered
    as the level's own client — as everybody, the global settings themselves — and the list of the encrypted DNS
    servers too (AdGuard gives a client's tags to the rules also when it takes the global settings:
    tests/stack_adguard.py)."""
    name = foreign_dns_profile(level)
    sources = level_sources(level.value) | {GroupSource.DOH}
    if level != GroupFilter.STANDARD:
        return level_settings(name, addresses, sources)
    return Profile(
        name=name,
        ids=addresses,
        use_global_settings=True,
        filtering_enabled=True,
        parental_enabled=False,
        safebrowsing_enabled=False,
        safe_search={"enabled": False, **{engine: True for engine in SAFE_ENGINES}},
        tags=tags_for(sources),
    )


def special_profile(name: str, addresses: list[str], level: str, sources: Collection[GroupSource] = ()) -> Profile:
    """A client of "kids" or "off" filtering; `sources`: the add-on lists of a children's group."""
    kids = level == GroupFilter.KIDS.value
    return Profile(
        name=name,
        ids=addresses,
        filtering_enabled=level != GroupFilter.OFF.value,
        parental_enabled=kids,
        safebrowsing_enabled=kids,
        safe_search={"enabled": kids, **{engine: True for engine in SAFE_ENGINES}},
        tags=tags_for(sources),
    )


def holders(have: Mapping[str, Mapping[str, Any]]) -> dict[str, set[str]]:
    """{address: the clients that hold it now, as itself or as its network}."""
    held: dict[str, set[str]] = {}
    for name, client in have.items():
        for identifier in client.get("ids") or []:
            held.setdefault(host_of(identifier) or identifier, set()).add(name)
    return held


def kept_tags(want: Sequence[Profile], have: Mapping[str, Mapping[str, Any]], in_force: ListsInForce) -> list[Profile]:
    """The clients wanted, each also with the tags of the lists its people have now, while a list it should have is not
    loaded yet: nobody is left without a list until the one that replaces it blocks — more blocking for a while, never
    less (a children's group that switches NSFW for social networks keeps NSFW until social networks are loaded; a
    child who goes to another group, to the "-all" twin or to a rule variant brings the lists they have to that client,
    and everybody in it has them too meanwhile). What its people have now: the tags of the clients that hold them now
    (itself, and those its newcomers come from) whose lists are loaded (all of them when that is not known), and while
    the older file of names is in force, the lists it names the client with. Once every list it should have is loaded,
    the kept ones go in one change. A person in no client yet brings nothing.
    While AdGuard has not read the file in place, a client is not given a tag it does not carry of a list the file no
    longer has: AdGuard may still apply that list, and would stop at its read."""
    held = holders(have)
    result: list[Profile] = []
    for profile in want:
        asked = sources_for(profile.tags)
        carried = sources_for(have.get(profile.name, {}).get("tags") or [])
        if in_force.unread and in_force.in_file is not None:
            asked = frozenset(source for source in asked if source in in_force.in_file or source in carried)
        loaded = in_force.loaded
        kept: set[GroupSource] = set()
        if loaded is None or not asked <= loaded:
            brought: frozenset[GroupSource] = frozenset()
            if asked:           # a client that is to have no list has none to wait for: its newcomers bring nothing
                coming = {name for address in profile.ids for name in held.get(address, ()) if name != profile.name}
                brought = carried_by(have[name] for name in coming)
            if in_force.unread and in_force.in_file is not None:
                brought = frozenset(source for source in brought if source in in_force.in_file)
            kept = {source for source in carried | brought if loaded is None or source in loaded} | set(in_force.named.get(profile.name, ()))
        tags = tags_for(asked | kept)
        result.append(profile if tags == profile.tags else profile.model_copy(update={"tags": tags}))
    return result


def tagged_in_place(have: Mapping[str, Mapping[str, Any]], named: Mapping[str, Collection[GroupSource]]) -> list[Profile]:
    """While the older file of names is in force: every client as AdGuard holds it, people and all, with the tags of
    the lists that file names it with, and none when it names it with none — nobody moves, and nobody's lists change
    (the file blocks by names), until the file is converted to one of tags (adguard.group_lists.converted: it then
    blocks the same names for the same people by these tags)."""
    return [as_profile(name, client).model_copy(update={"tags": tags_for(named.get(name, ()))}) for name, client in sorted(have.items())]


def carried_by(clients: Iterable[Mapping[str, Any]]) -> frozenset[GroupSource]:
    """The lists these clients carry the tags of."""
    return sources_for(tag for client in clients for tag in client.get("tags") or [])


def sent_for(want: Sequence[Profile], have: Mapping[str, Mapping[str, Any]], in_force: ListsInForce) -> list[Profile]:
    """The clients as they are made now: as wanted (kept_tags), or only tagged where they are while the older file of
    names is in force (tagged_in_place)."""
    if in_force.named:
        return tagged_in_place(have, in_force.named)
    return kept_tags(want, have, in_force)


def signature(profiles: Sequence[Profile], in_force: ListsInForce | None = None) -> str:
    """The clients wanted, and what decides which tags they keep (kept_tags) — not what AdGuard holds. No client
    wanted: NOTHING_WANTED, whatever the lists."""
    if not profiles:
        return NOTHING_WANTED
    lists = {} if in_force is None else {
        "loaded": None if in_force.loaded is None else sorted(source.value for source in in_force.loaded),
        "named": {name: sorted(source.value for source in sources) for name, sources in sorted(in_force.named.items())},
    }
    return json.dumps([[profile.wire() for profile in profiles], lists], sort_keys=True)


@dataclass(slots=True)
class ProfileMemory:
    """What this process last knew to be applied to the blocker. Not kept
    anywhere: the blocker itself is the truth, and a process that has just
    started asks it (a note in a file or in the database could say "applied"
    about a blocker that was reinstalled or restored empty). None is "never"."""

    signature: str | None = None
    asked_at: datetime | None = None
    complained_at: datetime | None = None
    failed_at: datetime | None = None
    degraded: frozenset[str] = frozenset()      # the groups whose people ask as everybody: their own client does not fit
    routes: dict[str, RouteDns] | None = None   # how the people of the routed groups asked when the rules were last read
    routes_unread: bool = False                 # the rules could not be read since: said in the journal once


def _seconds_since(moment: datetime | None) -> float:
    """How long ago; "never" is longer ago than anything."""
    return math.inf if moment is None else (utc_now() - moment).total_seconds()


class GroupListsKeeper(Protocol):
    """The block list made of the groups' own lists (adguard.group_lists.GroupBlockLists)."""

    def nudge(self, groups: Sequence[UserGroup], own: Mapping[str, str] | None = None, carried: frozenset[GroupSource] | None = None) -> None:
        """Puts the list in place, or takes it away, in the background when these groups (and the lists the clients
        carry, when given) need it otherwise than it is now. Never raises."""
        ...

    async def in_force(self) -> ListsInForce:
        """Which lists AdGuard applies now. Never raises."""
        ...

    def settled(self, in_force: ListsInForce) -> None:
        """Every client was seen as wanted, given `in_force`. Never raises."""
        ...

    @property
    def guard(self) -> asyncio.Lock:
        """Held while the clients' tags are decided and made: the file of the lists does not change meanwhile."""
        ...


class GroupDnsSource(Protocol):
    """sites.scoped.GroupDns: how the people of each group some rule sends abroad or directly ask."""

    async def of(self) -> dict[str, RouteDns]: ...


class UsersList(Protocol):
    async def list(self, force: bool = False) -> list[KnownUser]: ...


class WholeRoutes(Protocol):
    """Who sends all their traffic through their exit (users.store.StoredHandPins)."""

    async def whole(self) -> set[str]: ...


class BlockingFollower(Protocol):
    """block.service.BlockService: its rules made again (a rule for some groups blocks their people's addresses)."""

    async def follow(self, users: Sequence[KnownUser] | None = None, force: bool = True) -> None: ...


class JournalWriter(Protocol):
    async def write(self, text: str, bad: bool = False, kind: Kind = Kind.CHANGE, who: Who = Who.SYSTEM) -> None: ...


class AdGuardProfiles:
    """Keeps AdGuard's per-group clients as the groups ask."""

    def __init__(
        self,
        clients: ProfileClients,
        groups: GroupsStore,
        journal: JournalWriter,
        lock_file: Path,
        memory: ProfileMemory,
        group_lists: GroupListsKeeper | None = None,
        own: OwnFilters | None = None,
        routes: WholeRoutes | None = None,
        blocking: BlockingFollower | None = None,
        dns: GroupDnsSource | None = None,
        directory: UsersList | None = None,
    ) -> None:
        self.__dns = dns                        # how the people of the groups some rule sends abroad or directly ask
        self.__directory = directory            # who the users are, for a nudge that does not come with them
        self.__nudged: asyncio.Task[None] | None = None
        self.__blocking = blocking              # the blocker's rules for some groups follow who is in them
        self.__clients = clients
        self.__groups = groups
        self.__journal = journal
        self.__lock_file = lock_file
        self.__memory = memory
        self.__group_lists = group_lists
        self.__own = own
        self.__routes = routes
        self.__guard = asyncio.Lock() if group_lists is None else group_lists.guard

    async def __own_filters(self) -> dict[str, str]:
        return {} if self.__own is None else await self.__own.read()

    async def __whole(self) -> set[str]:
        return set() if self.__routes is None else await self.__routes.whole()

    async def __group_routes(self) -> dict[str, RouteDns]:
        """{group id: how its people ask}. When the rules cannot be read, as they were last read (said in the journal
        once): the other groups' clients do not wait for them. Raises only when they were never read in this process
        (then nothing is changed: a guess could take the routed groups' people out of their clients' variants)."""
        if self.__dns is None:
            return {}
        memory = self.__memory
        try:
            routes = await self.__dns.of()
        except Exception as e:
            if memory.routes is None:
                raise
            if not memory.routes_unread:
                memory.routes_unread = True
                await self.__say(
                    "Правила для групп не прочитаны: клиенты блокировщика пока следуют им такими, какими они были прочитаны в последний раз", True
                )
            log.info("the rules for groups not read, the last ones used: %s", repr(e)[:ERRORS_LENGTH])
            return dict(memory.routes)
        if memory.routes_unread:
            memory.routes_unread = False
            await self.__say("Правила для групп снова прочитаны: клиенты блокировщика следуют им", False)
        memory.routes = dict(routes)
        return routes

    def __nudge_group_lists(self, groups: Sequence[UserGroup], own: Mapping[str, str], carried: frozenset[GroupSource] | None = None) -> None:
        if self.__group_lists is not None:
            self.__group_lists.nudge(groups, own, carried)

    async def __in_force(self) -> ListsInForce:
        """Which lists AdGuard applies; without the keeper of the lists, every client's tags are as wanted."""
        if self.__group_lists is None:
            return ListsInForce(loaded=frozenset(source for source in GroupSource))
        return await self.__group_lists.in_force()

    def __settled(self, in_force: ListsInForce) -> None:
        if self.__group_lists is not None:
            self.__group_lists.settled(in_force)

    def nudge(self) -> None:
        """A rule for some groups, the mode or the sites changed: the clients are looked at again in the background,
        now rather than at the next look of the state. Never raises, never waits: the caller may hold the lock of the
        list, and the clients take the lock of the users."""
        if self.__directory is None or (self.__nudged is not None and not self.__nudged.done()):
            return
        try:
            self.__nudged = asyncio.get_running_loop().create_task(self.__nudge())
        except RuntimeError:
            pass

    async def __nudge(self) -> None:
        try:
            assert self.__directory is not None
            await self.check(await self.__directory.list())
        except Exception as e:
            log.info("the groups' clients not looked at again: %s", repr(e)[:ERRORS_LENGTH])

    async def __follow_blocking(self, users: Sequence[KnownUser]) -> None:
        """Who is in a group may have changed: the blocker's rules for some groups are made again if they come out
        otherwise. Never raises: the filtering of the groups does not wait for it."""
        if self.__blocking is None or not users:
            return
        try:
            await self.__blocking.follow(users, force=False)
        except Exception as e:
            log.info("the blocker's rules for groups not made again: %s", repr(e)[:ERRORS_LENGTH])

    async def __fitting(
        self, groups: Sequence[UserGroup], users: Sequence[KnownUser], own: Mapping[str, str], routes: Mapping[str, RouteDns]
    ) -> Fitting:
        """The clients wanted, a group whose own client does not fit left to ask as everybody — said in the journal
        once when it starts, and once when it fits again."""
        fit = fitting(groups, users, own, await self.__whole(), routes)
        if fit.profiles is None:
            return fit
        names = {str(group.id): group.name for group in groups if group.id is not None}
        started = sorted(fit.degraded - self.__memory.degraded)
        ended = sorted(self.__memory.degraded - fit.degraded)
        self.__memory.degraded = fit.degraded
        for group in started:
            await self.__say(
                f"Группа «{names.get(group, group)}»: её список сайтов не помещается в клиент блокировщика — её люди пока спрашивают DNS "
                "как все (куда идут их сайты, решают правила для неё). Вернётся само, когда поместится",
                True,
            )
        for group in ended:
            if group in fit.routes:
                await self.__say(f"Группа «{names.get(group, group)}»: её люди снова спрашивают DNS по своему списку", False)
        return fit

    async def __say(self, text: str, bad: bool) -> None:
        try:
            await self.__journal.write(text, bad, Kind.CHANGE, Who.SYSTEM)
        except Exception as e:
            log.info("journal not written: %s", repr(e)[:200])

    async def sync(self, groups: Sequence[UserGroup], users: Sequence[KnownUser]) -> str | None:
        """Make AdGuard's per-client settings match the groups (see __sync), while the file of the lists does not
        change (the keeper of the lists' guard). → an error text or None."""
        async with self.__guard:
            return await self.__sync(groups, users)

    async def __sync(self, groups: Sequence[UserGroup], users: Sequence[KnownUser]) -> str | None:
        """Make AdGuard's per-client settings match the groups. → an error text or None.
        The clients change with everybody filtered exactly as before or as wanted at any moment, refusals and outages
        included (adguard.moves: a person changes clients by being held, meanwhile, as their address's network by the
        client they go to, already as wanted).
        A client's lists are its tags: they change with its other settings (kept_tags: the old ones stay until the new
        ones are loaded). The block list made of the groups' own lists is put in place (or taken away) in the
        background: a download never holds up the groups, and no client waits for the file."""
        in_force = await self.__in_force()
        own = await self.__own_filters()
        try:
            routes = await self.__group_routes()
        except Exception as e:
            return "правила для групп не прочитаны: " + repr(e)[:ERRORS_LENGTH]
        fit = await self.__fitting(groups, users, own, routes)
        await self.__follow_blocking(users)
        want = fit.profiles
        if want is None:
            self.__nudge_group_lists(groups, own)
            return None
        try:
            have = await self.__clients.held()
        except AdGuardDown as e:
            self.__nudge_group_lists(groups, own)
            return f"блокировщик не отвечает ({e.kind})"
        sent = sent_for(want, have, in_force)
        # every list a client may carry during the change: those it carries now and those it is given
        self.__nudge_group_lists(groups, own, carried_by(have.values()) | carried_by(profile.wire() for profile in sent))
        if in_force.named:
            # the clients change as wanted once the file is converted (it then says nothing of names)
            error = await self.__tag_in_place(have, sent)
            if error is None:
                self.__settled(in_force)
            return error
        errors = await ClientMoves(clients=self.__clients, want=sent, have=have).run()
        if errors:
            return "; ".join(errors)[:ERRORS_LENGTH]
        self.__nudge_group_lists(groups, own, carried_by(profile.wire() for profile in sent))     # as they all are now: a list nobody carries may go
        self.__memory.signature = signature(want, in_force)
        self.__memory.asked_at = utc_now()
        self.__settled(in_force)
        return None

    async def __tag_in_place(self, have: Mapping[str, Mapping[str, Any]], sent: Sequence[Profile]) -> str | None:
        """Each client whose tags are not as sent is changed in place, nothing else of it: its people stay in it. One
        that cannot fit a request even without its empty fields (only the "-all" twin of a children's group with all
        four lists, within 3 bytes of what the older agent could make: docs/rollback.md) is not sent; the others are."""
        too_large: list[str] = []
        for profile in sent:
            if same(have[profile.name], profile.wire()):
                continue
            if request_bytes(profile) > REQUEST_BYTES_MAX:
                too_large.append(profile.name)
                continue
            done = await self.__clients.update(profile.name, profile)
            if not done.ok:
                return done.said[:ERRORS_LENGTH]
        if too_large:
            return (f"клиент {', '.join(too_large)} с метками списков не помещается в запрос блокировщика — переход на метки ждёт: "
                    "уберите из его группы человека или список (docs/rollback.md)")[:ERRORS_LENGTH]
        return None

    async def __differs_from_blocker(self, want: Sequence[Profile], in_force: ListsInForce) -> bool:
        real = await self.__clients.held()
        sent = sent_for(want, real, in_force)
        if set(real) != {profile.name for profile in sent}:
            return True
        return any(not same(real[profile.name], profile.wire()) for profile in sent)

    async def __complain(self, error: str) -> None:
        try:
            if _seconds_since(self.__memory.complained_at) > COMPLAIN_EVERY_SECONDS:
                self.__memory.complained_at = utc_now()
                await self.__journal.write("Фильтрация групп не применяется: " + error, True, Kind.CHANGE, Who.SYSTEM)
        except Exception as e:
            log.info("journal not written: %s", repr(e)[:200])

    async def check(self, users: Sequence[KnownUser]) -> None:
        """With every look at the state: a user removed by a term must not leave their
        address filtered as a child's (or unfiltered) for whoever gets it next.
        About once a minute the blocker itself is asked too: its clients may have
        been lost (a reinstall, an old config put back, a change by hand)."""
        memory = self.__memory
        groups = (await self.__groups.read()).groups
        own = await self.__own_filters()
        routes = await self.__group_routes()
        in_force = await self.__in_force()
        fit = await self.__fitting(groups, users, own, routes)
        self.__nudge_group_lists(groups, own)
        await self.__follow_blocking(users)
        want = fit.profiles
        if want is None:
            return
        if _seconds_since(memory.failed_at) < AFTER_FAILURE_SECONDS:
            return
        now_wanted = signature(want, in_force)
        known = memory.signature
        differs = known is not None and now_wanted != known
        due = known is None or ((bool(want) or known != NOTHING_WANTED) and _seconds_since(memory.asked_at) > ASK_AGAIN_SECONDS)
        if not differs and due:
            try:
                differs = await self.__differs_from_blocker(want, in_force)
            except Exception:
                memory.failed_at = utc_now()
                return
            memory.asked_at = utc_now()
            if not differs:
                memory.signature = now_wanted
                self.__settled(in_force)
        if not differs:
            return
        async with file_lock(self.__lock_file):
            error = await self.sync((await self.__groups.read()).groups, users)
        if error:
            await self.__complain(error)
