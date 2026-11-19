"""Filtering per group of users: "std" is as everybody, "off" — nothing is
blocked (ads included), "kids" — ads, dangerous sites, adult sites and safe
search; the levels "pro", "proplus", "ultimate" — as everybody, plus one of
HaGeZi's lists that applies to them only. Done with AdGuard's own per-client
settings: one client "grp-<id>" per such group, identified by its members'
tunnel addresses; the members of every group of one level share one client,
"grp-<level>". The lists of the levels and the children's add-on lists name
these clients (adguard.group_lists keeps them). Which sites go abroad
is not touched: that is AdGuard's upstream and ipset configuration, the same
for everybody.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from awgscale.core.clock import utc_now

from ..journal.models import Kind, Who
from ..locks import file_lock
from ..ports import GroupsStore, Outcome
from ..users.models import LEVELS, GroupFilter, KnownUser, UserGroup
from .api import PROFILE_PREFIX, level_profile
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


def wanted(groups: Sequence[UserGroup], users: Sequence[KnownUser]) -> list[Profile] | None:
    """The clients of AdGuard that should exist now; None when the list of users could not be read (then nothing is changed)."""
    special = [group for group in groups if group.filter != GroupFilter.STANDARD.value and group.members]
    if special and not users:
        return None
    address_of = {user.name: user.ip for user in users}
    profiles: list[Profile] = []
    by_level: dict[str, set[str]] = {level.value: set() for level in LEVELS}
    for group in special:
        if group.filter in by_level:
            by_level[group.filter] |= {address_of[member] for member in group.members if address_of.get(member)}
            continue
        addresses = sorted({address_of[member] for member in group.members if address_of.get(member)})
        if not addresses:
            continue
        kids = group.filter == GroupFilter.KIDS.value
        profiles.append(
            Profile(
                name=PROFILE_PREFIX + str(group.id),
                ids=addresses,
                filtering_enabled=group.filter != GroupFilter.OFF.value,
                parental_enabled=kids,
                safebrowsing_enabled=kids,
                safe_search={"enabled": kids, **{engine: True for engine in SAFE_ENGINES}},
            )
        )
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
    return profiles


def same(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Do two clients of AdGuard filter the same people the same way. Fields that are absent count as switched off."""
    if sorted(first.get("ids") or []) != sorted(second.get("ids") or []):
        return False
    if any(bool(first.get(switch)) != bool(second.get(switch)) for switch in COMPARED_SWITCHES):
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


def _seconds_since(moment: datetime | None) -> float:
    """How long ago; "never" is longer ago than anything."""
    return math.inf if moment is None else (utc_now() - moment).total_seconds()


class ProfileClients(Protocol):
    """AdGuard's per-group clients."""

    async def held(self) -> dict[str, dict[str, Any]]:
        """The clients that stand for groups, by name. Raises AdGuardDown."""
        ...

    async def add(self, profile: Profile) -> Outcome: ...

    async def delete(self, name: str) -> Outcome: ...


class GroupListsKeeper(Protocol):
    """The block list made of the groups' own lists (adguard.group_lists.GroupBlockLists)."""

    def nudge(self, groups: Sequence[UserGroup]) -> None:
        """Puts the list in place, or takes it away, in the background when these groups need it otherwise than it is now. Never raises."""
        ...


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
    ) -> None:
        self.__clients = clients
        self.__groups = groups
        self.__journal = journal
        self.__lock_file = lock_file
        self.__memory = memory
        self.__group_lists = group_lists

    def __nudge_group_lists(self, groups: Sequence[UserGroup]) -> None:
        if self.__group_lists is not None:
            self.__group_lists.nudge(groups)

    async def sync(self, groups: Sequence[UserGroup], users: Sequence[KnownUser]) -> str | None:
        """Make AdGuard's per-client settings match the groups. → an error text or None.
        In two steps, because AdGuard refuses an address that another of its clients
        still holds: first every client that is not exactly as wanted is removed
        (that frees its addresses), then the missing ones are added. For the moment
        in between such a group is filtered as everybody. The block list made of the
        groups' own lists is put in place (or taken away) in the background: a download
        never holds up the groups."""
        self.__nudge_group_lists(groups)
        want = wanted(groups, users)
        if want is None:
            return None
        try:
            have = await self.__clients.held()
        except AdGuardDown as e:
            return f"блокировщик не отвечает ({e.kind})"
        by_name = {profile.name: profile for profile in want}
        errors: list[str] = []
        for name, held in list(have.items()):
            if name in by_name and same(held, by_name[name].wire()):
                continue
            removed = await self.__clients.delete(name)
            if removed.ok:
                have = {other: client for other, client in have.items() if other != name}
            else:
                errors.append(removed.said)
        for profile in want:
            if profile.name in have:
                continue
            added = await self.__clients.add(profile)
            if not added.ok:
                errors.append(added.said)
        if errors:
            return "; ".join(errors)[:ERRORS_LENGTH]
        self.__memory.signature = signature(want)
        self.__memory.asked_at = utc_now()
        return None

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
        self.__nudge_group_lists(groups)
        want = wanted(groups, users)
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
