"""AdGuard's settings by name: its block lists, the dangerous-sites check, the admin's own rules, the per-group clients, the counters."""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from ..ports import Outcome
from ..rules.scoped import set_id
from .client import AdGuardClient, AdGuardDown
from .models import ClientCount, Counters, FilterList, Profile, TopBlocked

REFRESH_TIMEOUT = 60.0
ADD_LIST_TIMEOUT = 90.0
CHANGE_LIST_TIMEOUT = 60.0
STATS_TIMEOUT = 3.0
PROFILE_TIMEOUT = 15.0
PROFILE_PREFIX = "grp-"
ROUTE_INFIX = "-r"              # the variant of a client for the people of a group some rule sends abroad or directly
WHOLE_SUFFIX = "-all"           # the twin of a client for its people who send all their traffic through their exit


def level_profile(level: StrEnum) -> str:
    """The one client of the members of every group of a level ("grp-pro"…); group ids are hexadecimal, never a level's name."""
    return PROFILE_PREFIX + level.value


def group_profile(group_id: object) -> str:
    """The client of one group filtered in its own way ("grp-<id>")."""
    return PROFILE_PREFIX + str(group_id)


def whole_twin(name: str) -> str:
    """The client filtered as `name` whose people send all their traffic through their exit, and so ask their exit's
    resolvers ("grp-pro" → "grp-pro-all"); the lists that name a client name its twin too."""
    return name + WHOLE_SUFFIX


def route_variant(name: str, group_id: str) -> str:
    """The client filtered as `name` whose people are in a group some rule sends abroad or directly: they ask with the
    upstreams of their group's own list ("grp-pro" → "grp-pro-rg00001"; for nobody's client, "grp-rg00001")."""
    return name + ROUTE_INFIX + set_id(group_id)


def routed_profile(group_id: str) -> str:
    """The client of such a group's people filtered as everybody: "grp-r" and six characters, never a group's own (six)."""
    return PROFILE_PREFIX + ROUTE_INFIX.lstrip("-") + set_id(group_id)


TOP_BLOCKED_LOOKED_AT = 12
TOP_BLOCKED_KEPT = 6
TOP_CLIENTS_LOOKED_AT = 10
# names in AdGuard's counters come from whatever a device asked for; only plain host names are ever shown
#     ads.example.com   _dmarc.example.org
HOSTLIKE_RE = re.compile(
    r"""
    ^[a-z0-9_]                          # a
    (?:[a-z0-9_.-]{0,251}[a-z0-9_])?    # ds.example.com — never ends in a dot or a hyphen
    \Z
    """,
    re.VERBOSE,
)


class AdGuard:
    def __init__(self, client: AdGuardClient) -> None:
        self.__client = client

    # ── block lists ──────────────────────────────────────────────────────────

    async def filters(self) -> list[FilterList]:
        status = await self.__client.get("filtering/status")
        return [FilterList.of(record) for record in status.get("filters") or []]

    async def set_filter(self, filter_list: FilterList, name: object, enabled: bool, timeout: float | None = None) -> None:
        """A list's name and switch; the list is told from the others by its link."""
        payload = {"url": filter_list.url, "whitelist": False, "data": {"name": name, "url": filter_list.url, "enabled": enabled}}
        if timeout is None:
            await self.__client.post("filtering/set_url", payload)
        else:
            await self.__client.post("filtering/set_url", payload, timeout)

    async def add_filter(self, name: str, url: str) -> None:
        await self.__client.post("filtering/add_url", {"name": name, "url": url, "whitelist": False}, ADD_LIST_TIMEOUT)

    async def remove_filter(self, filter_list: FilterList) -> None:
        await self.__client.post("filtering/remove_url", {"url": filter_list.url, "whitelist": False}, CHANGE_LIST_TIMEOUT)

    async def refresh(self, timeout: float = REFRESH_TIMEOUT) -> None:
        """Downloads the lists again."""
        await self.__client.post("filtering/refresh", {"whitelist": False}, timeout)

    async def set_rules(self, rules: list[str]) -> None:
        """AdGuard's custom rules, all of them: the agent owns that list."""
        await self.__client.post("filtering/set_rules", {"rules": rules})

    # ── dangerous sites ──────────────────────────────────────────────────────

    async def safebrowsing(self) -> bool:
        return bool((await self.__client.get("safebrowsing/status")).get("enabled"))

    async def set_safebrowsing(self, enabled: bool) -> None:
        await self.__client.post("safebrowsing/" + ("enable" if enabled else "disable"), {})

    # ── the counters of the day ──────────────────────────────────────────────

    async def counters(self) -> Counters:
        stats = await self.__client.get("stats", STATS_TIMEOUT)
        top = [
            TopBlocked(domain=name, count=count)
            for entry in stats.get("top_blocked_domains", [])[:TOP_BLOCKED_LOOKED_AT]
            for name, count in entry.items()
            if HOSTLIKE_RE.match(str(name))
        ]
        clients = [
            ClientCount(ip=address, count=count) for entry in stats.get("top_clients", [])[:TOP_CLIENTS_LOOKED_AT] for address, count in entry.items()
        ]
        return Counters(
            queries=stats.get("num_dns_queries", 0),
            blocked=stats.get("num_blocked_filtering", 0),
            safe=stats.get("num_replaced_safebrowsing", 0),
            top_blocked=tuple(top[:TOP_BLOCKED_KEPT]),
            average_seconds=stats.get("avg_processing_time", 0),
            top_clients=tuple(clients),
        )

    async def asked_by(self, address: str) -> int:
        """How many requests one address made today, as AdGuard counts its most active clients."""
        stats = await self.__client.get("stats")
        return sum(int(entry.get(address, 0)) for entry in stats.get("top_clients", []))

    # ── per-group clients ────────────────────────────────────────────────────

    async def held(self) -> dict[str, dict[str, Any]]:
        """The clients of AdGuard that stand for groups, by name."""
        return {name: client for name, client in (await self.__clients()).items() if name.startswith(PROFILE_PREFIX)}

    async def others(self) -> dict[str, dict[str, Any]]:
        """The admin's own clients of AdGuard (not the groups'), by name."""
        return {name: client for name, client in (await self.__clients()).items() if not name.startswith(PROFILE_PREFIX)}

    async def __clients(self) -> dict[str, dict[str, Any]]:
        clients = (await self.__client.get("clients")).get("clients") or []
        return {str(client.get("name", "")): client for client in clients}

    async def __change(self, path: str, payload: object) -> Outcome:
        try:
            await self.__client.post(path, payload, PROFILE_TIMEOUT)
        except AdGuardDown as e:
            return Outcome(False, e.said)
        return Outcome(True)

    async def add(self, profile: Profile) -> Outcome:
        return await self.__change("clients/add", profile.wire())

    async def update(self, name: str, profile: Profile) -> Outcome:
        """In place: AdGuard changes the client as one step, or refuses and leaves it as it was."""
        return await self.__change("clients/update", {"name": name, "data": profile.wire()})

    async def delete(self, name: str) -> Outcome:
        return await self.__change("clients/delete", {"name": name})
