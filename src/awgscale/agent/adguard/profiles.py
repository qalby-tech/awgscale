"""Filtering per group of users: "std" is as everybody, "off" — nothing is
blocked (ads included), "kids" — ads, dangerous sites, adult sites and safe
search; the levels "pro", "proplus", "ultimate" — as everybody, plus one of
HaGeZi's lists that applies to them only. Done with AdGuard's own per-client
settings: one client "grp-<id>" per such group, identified by its members'
tunnel addresses; the members of every group of one level share one client,
"grp-<level>". The lists of the levels and the children's add-on lists name
these clients (adguard.group_lists keeps them). Which sites go abroad is
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
from typing import Any, Collection, Mapping, Protocol, Sequence

from awgscale.core.clock import utc_now

from ..journal.models import Kind, Who
from ..locks import file_lock
from ..ports import GroupsStore, Outcome, OwnFilters
from ..sites.routing import THIRD_VIA_EXIT, UPSTREAMS_VIA_EXIT
from ..users.models import LEVELS, GroupFilter, KnownUser, UserGroup
from ..sites.scoped import RouteDns
from .api import PROFILE_PREFIX, level_profile, route_variant, routed_profile, whole_twin
from .client import AdGuardDown
from .models import Profile

log = logging.getLogger("awgscale.agent")

SAFE_ENGINES = ("bing", "duckduckgo", "ecosia", "google", "pixabay", "yandex", "youtube")
COMPARED_SWITCHES = ("use_global_settings", "filtering_enabled", "parental_enabled", "safebrowsing_enabled")
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
    special = [group for group in groups if group.filter != GroupFilter.STANDARD.value and group.members]
    routed_groups = [group for group in groups if group.id is not None and str(group.id) in routes and group.members]
    if (special or own or whole or routed_groups) and not users:
        return None
    address_of = {user.name: user.ip for user in users}
    in_kids = {member for group in groups if group.filter == GroupFilter.KIDS.value for member in group.members}
    chose = {name: level for name, level in own.items() if name not in in_kids and address_of.get(name)}
    profiles: list[Profile] = []
    by_level: dict[str, set[str]] = {level.value: set() for level in LEVELS}
    by_own: dict[str, set[str]] = {}
    for name, level in chose.items():
        if level in by_level:
            by_level[level].add(address_of[name])
        elif level != GroupFilter.STANDARD.value:
            by_own.setdefault(level, set()).add(address_of[name])
    for group in special:
        members = [member for member in group.members if member not in chose]
        if group.filter in by_level:
            by_level[group.filter] |= {address_of[member] for member in members if address_of.get(member)}
            continue
        addresses = sorted({address_of[member] for member in members if address_of.get(member)})
        if not addresses:
            continue
        profiles.append(special_profile(PROFILE_PREFIX + str(group.id), addresses, group.filter))
    for level, addresses in sorted(by_own.items()):
        profiles.append(special_profile(OWN_PROFILE + level, sorted(addresses), level))
    for level in LEVELS:        # as everybody (the global settings), and the level's list names this client
        if by_level[level.value]:
            profiles.append(
                Profile(
                    name=level_profile(level),
                    ids=sorted(by_level[level.value]),
                    filtering_enabled=True,
                    parental_enabled=False,
                    safebrowsing_enabled=True,
                    safe_search={"enabled": False, **{engine: True for engine in SAFE_ENGINES}},
                )
            )
    routed = {address_of[member]: str(group.id) for group in routed_groups for member in group.members if address_of.get(member)}
    return split_people(profiles, {address_of[name] for name in whole if address_of.get(name)}, routed, routes, owners)


def request_bytes(profile: Profile) -> int:
    """The size of the blocker's request for this client, as it is sent (clients/update: the largest of the two)."""
    body = {"name": profile.name, "data": profile.wire()}
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())


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
            result.append(asks_own(everybody.model_copy(update={"name": whole_twin(routed_profile(group))}), [*WHOLE_UPSTREAMS, *routes[group].direct_lines]))
        else:
            owners[routed_profile(group)] = group
            result.append(asks_own(everybody, routes[group].upstreams))
    return result


def special_profile(name: str, addresses: list[str], level: str) -> Profile:
    """A client of "kids" or "off" filtering."""
    kids = level == GroupFilter.KIDS.value
    return Profile(
        name=name,
        ids=addresses,
        filtering_enabled=level != GroupFilter.OFF.value,
        parental_enabled=kids,
        safebrowsing_enabled=kids,
        safe_search={"enabled": kids, **{engine: True for engine in SAFE_ENGINES}},
    )


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


def signature(profiles: Sequence[Profile]) -> str:
    return json.dumps([profile.wire() for profile in profiles], sort_keys=True)


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


def _seconds_since(moment: datetime | None) -> float:
    """How long ago; "never" is longer ago than anything."""
    return math.inf if moment is None else (utc_now() - moment).total_seconds()


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


class GroupListsKeeper(Protocol):
    """The block list made of the groups' own lists (adguard.group_lists.GroupBlockLists)."""

    def nudge(self, groups: Sequence[UserGroup], own: Mapping[str, str] | None = None, routed: Collection[str] = ()) -> None:
        """Puts the list in place, or takes it away, in the background when these groups need it otherwise than it is now. Never raises."""
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

    async def __own_filters(self) -> dict[str, str]:
        return {} if self.__own is None else await self.__own.read()

    async def __whole(self) -> set[str]:
        return set() if self.__routes is None else await self.__routes.whole()

    async def __group_routes(self) -> dict[str, RouteDns]:
        """{group id: how its people ask}; raises when it cannot be made (then nothing is changed)."""
        return {} if self.__dns is None else await self.__dns.of()

    def __nudge_group_lists(self, groups: Sequence[UserGroup], own: Mapping[str, str], routed: Collection[str] = ()) -> None:
        if self.__group_lists is not None:
            self.__group_lists.nudge(groups, own, routed)

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

    async def __fitting(self, groups: Sequence[UserGroup], users: Sequence[KnownUser], own: Mapping[str, str], routes: Mapping[str, RouteDns]) -> Fitting:
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
        """Make AdGuard's per-client settings match the groups. → an error text or None.
        A client that stays is changed in place (its people stay in it meanwhile; a refused change leaves it as it
        was). AdGuard refuses an address another of its clients still holds, so the clients that give addresses away
        are changed first, then the others and the new ones take what is free (a new one that takes the people of a
        client nobody wants any more is that client, renamed in place); only when all of that was taken are the
        clients nobody wants any more removed, and the addresses they held given last. The block list made of the
        groups' own lists is put in place (or taken away) in the background: a download never holds up the groups."""
        own = await self.__own_filters()
        try:
            routes = await self.__group_routes()
        except Exception as e:
            return "правила для групп не прочитаны: " + repr(e)[:ERRORS_LENGTH]
        fit = await self.__fitting(groups, users, own, routes)
        self.__nudge_group_lists(groups, own, fit.routes)
        await self.__follow_blocking(users)
        want = fit.profiles
        if want is None:
            return None
        try:
            have = await self.__clients.held()
        except AdGuardDown as e:
            return f"блокировщик не отвечает ({e.kind})"
        errors = await self.__apply(want, have)
        if errors:
            return "; ".join(errors)[:ERRORS_LENGTH]
        self.__memory.signature = signature(want)
        self.__memory.asked_at = utc_now()
        return None

    async def __apply(self, want: Sequence[Profile], have: Mapping[str, Mapping[str, Any]]) -> list[str]:
        """The clients made as wanted (see sync). → what the blocker refused."""
        by_name = {profile.name: profile for profile in want}
        holder: dict[str, str] = {address: name for name, client in have.items() for address in client.get("ids") or []}
        errors: list[str] = []
        partial: list[Profile] = []         # given only the addresses that were free: the rest comes last
        replaced: list[str] = []            # nothing it keeps is free: removed with the unwanted ones, then made again

        renamed: set[str] = set()          # unwanted clients made into a new one in place (their people never left a client)

        def donor_for(profile: Profile) -> str | None:
            """An unwanted client holding some of this new one's people: it becomes this one, in place."""
            wanted_ids = set(profile.ids)
            overlaps = [
                (len({address for address, name in holder.items() if name == held_name} & wanted_ids), held_name)
                for held_name in have
                if held_name not in by_name and held_name not in renamed
            ]
            best = max(overlaps, default=(0, None))
            return best[1] if best[0] else None

        async def place(profile: Profile, held: bool) -> None:
            current = profile.name
            if not held:
                donor = donor_for(profile)
                if donor is not None:
                    current = donor
                    held = True
            ids = [address for address in profile.ids if holder.get(address) in (None, profile.name, current)]
            if not ids:
                if held:
                    replaced.append(profile.name)
                else:
                    partial.append(profile)
                return
            placed = profile.model_copy(update={"ids": ids})
            done = await self.__clients.update(current, placed) if held else await self.__clients.add(placed)
            if not done.ok:
                errors.append(done.said)
                return
            if current != profile.name:
                renamed.add(current)
            for address in [address for address, name in holder.items() if name in (profile.name, current) and address not in ids]:
                del holder[address]
            holder.update({address: profile.name for address in ids})
            if len(ids) < len(profile.ids):
                partial.append(profile)

        changing = [name for name, client in have.items() if name in by_name and not same(client, by_name[name].wire())]
        # those that give addresses away first: what they give is then free for the others
        changing.sort(key=lambda name: not (set(have[name].get("ids") or []) - set(by_name[name].ids)))
        for name in changing:
            await place(by_name[name], held=True)
        for profile in want:
            if profile.name not in have:
                await place(profile, held=False)
        if errors:
            return errors               # nothing is removed before what replaces it was taken
        for name in [name for name in have if name not in by_name and name not in renamed] + replaced:
            removed = await self.__clients.delete(name)
            if not removed.ok:
                errors.append(removed.said)
                continue
            for address in [address for address, holding in holder.items() if holding == name]:
                del holder[address]
        for name in replaced:
            partial.append(by_name[name])
        for profile in partial:
            has = {address for address, name in holder.items() if name == profile.name}
            ids = [address for address in profile.ids if holder.get(address) in (None, profile.name)]
            if len(ids) < len(profile.ids):
                errors.append(f"{profile.name}: адрес ещё у другого клиента")
            if not ids or set(ids) == has:
                continue
            held = bool(has)
            placed = profile.model_copy(update={"ids": ids})
            done = await self.__clients.update(profile.name, placed) if held else await self.__clients.add(placed)
            if not done.ok:
                errors.append(done.said)
                continue
            holder.update({address: profile.name for address in ids})
        return errors

    async def __differs_from_blocker(self, want: Sequence[Profile]) -> bool:
        real = await self.__clients.held()
        if set(real) != {profile.name for profile in want}:
            return True
        return any(not same(real[profile.name], profile.wire()) for profile in want)

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
        fit = await self.__fitting(groups, users, own, routes)
        self.__nudge_group_lists(groups, own, fit.routes)
        await self.__follow_blocking(users)
        want = fit.profiles
        if want is None:
            return
        if _seconds_since(memory.failed_at) < AFTER_FAILURE_SECONDS:
            return
        now_wanted = signature(want)
        known = memory.signature
        differs = known is not None and now_wanted != known
        due = known is None or ((bool(want) or known != NOTHING_WANTED) and _seconds_since(memory.asked_at) > ASK_AGAIN_SECONDS)
        if not differs and due:
            try:
                differs = await self.__differs_from_blocker(want)
            except Exception:
                memory.failed_at = utc_now()
                return
            memory.asked_at = utc_now()
            if not differs:
                memory.signature = now_wanted
        if not differs:
            return
        async with file_lock(self.__lock_file):
            error = await self.sync((await self.__groups.read()).groups, users)
        if error:
            await self.__complain(error)
