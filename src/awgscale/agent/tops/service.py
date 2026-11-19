"""Counting the day's tops, and keeping the count for a few minutes: `state` is asked every few seconds."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping

from ..adguard.api import HOSTLIKE_RE          # names in the query log come from whatever a device asked for: only plain host names are ever counted or shown
from ..commands import RouteMode
from ..files import read_text, write_file
from ..ports import SitesStore
from ..sites.entries import resolver_names
from ..sites.groups import flat_entries
from ..sites.routing import read_mode
from . import querylog
from .models import TOP_HOURS, Tops, top
from .sites import site_of

KEEP_FOR = timedelta(seconds=600)
FILTERED_MARK = b'"IsFiltered":true'
ADDRESS_QUERY_MARK = b'"QT":"A"'             # one lookup = one A query; AAAA/HTTPS would count it again
ADDRESS_QUERY = "A"
SAFE_BROWSING_REASON = 4                     # AdGuard's "Reason" for a name stopped as a dangerous site
SAFE_BROWSING_KEY = "safe"
OWN_RULES_LIST = 0                           # the list id of the admin's own rules
DEFAULT_SUBNET = "172.16.17.0/24"
RUSSIA = "ru"
RUSSIAN_ZONES = ("ru", "su", "xn--p1ai")
ROTATED_SUFFIX = ".1"
JSON_INDENT = 1
# The users' network in the compiled cascade.conf:   CLIENT_SUBNET="172.16.17.0/24"
CLIENT_SUBNET_RE = re.compile(
    r"""
    ^CLIENT_SUBNET=
    "?(?P<subnet>[0-9./]+)"?      # "172.16.17.0/24"
    """,
    re.VERBOSE | re.MULTILINE,
)
# The home country in the compiled cascade.conf, when it is not Russia:   HOME_COUNTRY="kz"
HOME_COUNTRY_RE = re.compile(
    r"""
    ^HOME_COUNTRY=
    "?(?P<country>[a-z]{2})"?     # "kz"
    """,
    re.VERBOSE | re.MULTILINE,
)

Route = Callable[[str], "Way"]
IsUser = Callable[[str], bool]


class Way(StrEnum):
    """Where a user's connection to a name goes."""

    EXIT = "exit"
    DIRECT = "direct"


def users_test(conf_text: str | None) -> IsUser:
    """→ is this the address of a VPN user (and not the server's own address in the tunnel)."""
    try:
        found = CLIENT_SUBNET_RE.search(conf_text or "")
        network = ipaddress.ip_network(found["subnet"] if found else "")
    except ValueError:
        network = ipaddress.ip_network(DEFAULT_SUBNET)
    own = str(network.network_address + 1)

    def is_user(address: str) -> bool:
        try:
            return address != own and ipaddress.ip_address(address) in network
        except ValueError:
            return False
    return is_user


def national_zones(conf_text: str | None) -> tuple[str, ...]:
    """The top-level domains of the home country: its code, and for Russia also .su and .рф."""
    found = HOME_COUNTRY_RE.search(conf_text or "")
    country = found["country"] if found else RUSSIA
    return RUSSIAN_ZONES if country == RUSSIA else (country,)


def route_by(mode: RouteMode, listed_names: set[str], zones: tuple[str, ...] = RUSSIAN_ZONES) -> Route:
    """→ where a VPN user's connection to a name goes, decided the way the server
    decides it (the enabled groups, a listed domain covering its subdomains,
    patterns widened as for the resolver)."""
    if mode is not RouteMode.LIST:
        # "everything foreign goes abroad": names alone cannot tell, so the national zones stand in for "home"
        return lambda host: Way.DIRECT if host.rsplit(".", 1)[-1] in zones else Way.EXIT

    def route(host: str) -> Way:
        labels = host.split(".")
        return Way.EXIT if any(".".join(labels[start:]) in listed_names for start in range(len(labels))) else Way.DIRECT
    return route


def stopped_by(raw: bytes, cutoff: datetime) -> str | None:
    """A line about a request that was stopped → what stopped it: a list's id as text, or "safe"."""
    try:
        result = json.loads(raw).get("Result") or {}
    except ValueError:
        return None
    if not result.get("IsFiltered") or querylog.moment_of(raw) < cutoff:
        return None
    if result.get("Reason") == SAFE_BROWSING_REASON:
        return SAFE_BROWSING_KEY
    rules = result.get("Rules") or [{}]
    return str((rules[0] or {}).get("FilterListID") or OWN_RULES_LIST)


def looked_up(raw: bytes, cutoff: datetime, is_user: IsUser) -> str | None:
    """A line of the log → the host a VPN user looked up, when it counts as a visit."""
    if ADDRESS_QUERY_MARK not in raw:
        return None
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if entry.get("QT") != ADDRESS_QUERY or (entry.get("Result") or {}).get("IsFiltered") or not is_user(str(entry.get("IP", ""))):
        return None
    if querylog.moment_of(raw) < cutoff:
        return None
    host = str(entry.get("QH", "")).rstrip(".").lower()
    return host if HOSTLIKE_RE.match(host) else None


def count(log_file: Path, now: datetime, is_user: IsUser, route: Route) -> Tops:
    """The last day of the query log, counted."""
    cutoff = now - timedelta(hours=TOP_HOURS)
    lines, older = querylog.tail(log_file, cutoff)
    if not older:                            # the log was rotated less than a day ago
        lines = querylog.tail(log_file.with_name(log_file.name + ROTATED_SUFFIX), cutoff)[0] + lines
    visits: dict[str, int] = {}
    by_way: dict[Way, dict[str, int]] = {Way.EXIT: {}, Way.DIRECT: {}}
    blocked: dict[str, int] = {}             # block list id → requests it stopped (every client, as AdGuard counts)
    for raw in lines:
        if FILTERED_MARK in raw:
            stopper = stopped_by(raw, cutoff)
            if stopper is not None:
                blocked[stopper] = blocked.get(stopper, 0) + 1
            continue
        host = looked_up(raw, cutoff, is_user)
        if host is None:
            continue
        name = site_of(host)
        if name:
            visits[name] = visits.get(name, 0) + 1
            side = by_way[route(host)]
            side[name] = side.get(name, 0) + 1
    return Tops(
        at=now.replace(microsecond=0),
        total=sum(visits.values()),
        sites=top(visits),
        blocked_lists=blocked,
        exit=top(by_way[Way.EXIT]),
        exit_total=sum(by_way[Way.EXIT].values()),
        direct=top(by_way[Way.DIRECT]),
        direct_total=sum(by_way[Way.DIRECT].values()),
    )


class TopSites:
    def __init__(self, log_file: Path, kept_file: Path, conf_file: Path, mode_file: Path, sites: SitesStore) -> None:
        self.__log_file = log_file
        self.__kept_file = kept_file
        self.__conf_file = conf_file
        self.__mode_file = mode_file
        self.__sites = sites

    def __kept(self) -> Tops | None:
        """The count kept from an earlier look; None without one."""
        text = read_text(self.__kept_file)
        try:
            record = json.loads(text) if text is not None else None
        except ValueError:
            return None
        return Tops.of(record) if isinstance(record, dict) else None

    def __keep(self, tops: Tops) -> None:
        try:
            self.__kept_file.parent.mkdir(parents=True, exist_ok=True)
            write_file(self.__kept_file, json.dumps(tops.record(), ensure_ascii=False, indent=JSON_INDENT) + "\n")
        except OSError:
            pass

    async def __route(self, conf_text: str | None) -> Route:
        mode = read_mode(self.__mode_file)
        if mode is not RouteMode.LIST:
            return route_by(mode, set(), national_zones(conf_text))
        return route_by(mode, set(resolver_names(flat_entries(await self.__sites.read()))))

    async def __count(self) -> Tops:
        conf_text = await asyncio.to_thread(read_text, self.__conf_file)
        route = await self.__route(conf_text)
        return await asyncio.to_thread(count, self.__log_file, datetime.now(UTC), users_test(conf_text), route)

    async def today(self) -> Tops:
        """The tops, counted anew when the kept count is older than a few minutes.
        Never raises: the page must still render without them."""
        kept = await asyncio.to_thread(self.__kept)
        if kept is not None and kept.whole and kept.at is not None and datetime.now(UTC) - kept.at < KEEP_FOR:
            return kept
        try:
            fresh = await self.__count()
        except Exception:
            return kept if kept is not None else Tops()
        await asyncio.to_thread(self.__keep, fresh)
        return fresh

    async def stopped_by_list(self) -> Mapping[str, Any]:
        """Requests each block list stopped in the last day (ports.StoppedByList)."""
        return (await self.today()).blocked_lists or {}
