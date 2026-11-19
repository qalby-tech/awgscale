"""What the routing and the resolver are given: the flat list of entries, the
resolver's own file and upstream rules, the mode — and the order in which they
are made to follow a change."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from awgscale.core.ashell import AsyncRunner

from ..commands import RouteMode
from ..files import write_file
from ..ports import Blocker, ResolverKeeper, ResolverSetup, RoutingWatch
from .entries import is_network, resolver_names
from .groups import automatic_on, flat_entries
from .models import SiteGroup

UPSTREAMS_VIA_EXIT = ["https://1.1.1.1/dns-query", "https://9.9.9.9/dns-query"]
UPSTREAMS_DIRECT = ["https://8.8.8.8/dns-query", "https://8.8.4.4/dns-query"]
UPSTREAMS_FALLBACK = ["77.88.8.8", "77.88.8.1"]
THIRD_VIA_EXIT = "https://1.0.0.1/dns-query"
NAMES_PER_RULE = 40
# What bounds the routed set in all: the resolver takes its per-name upstream
# rules in one API request, and refuses a request over 65 536 bytes (measured on
# a throwaway copy of it: "cannot read more than 65536 bytes"). Its own start
# with 6 000 names was fine.
UPSTREAM_BYTES = 60000
PUBLIC_MODE = 0o644
MODE_FILE_READ = 16
# a pattern in the flat list, cut down to what the resolver understands:  api.*.cdn.example.net → cdn.example.net
UP_TO_LAST_STAR_RE = re.compile(
    r"""
    ^.*\*        # api.*   everything up to the last star
    [^.]*\.      # .       and the rest of its label
    """,
    re.VERBOSE,
)
# the line of the resolver's settings that names its file of sets:   "  ipset_file: /old/place"
IPSET_FILE_LINE_RE = re.compile(
    r"""
    ^\ \ ipset_file:\        # "  ipset_file: " — two spaces in front
    (?P<path>.*)$             # /old/place
    """,
    re.VERBOSE | re.MULTILINE,
)
# an address in dig's short answer:   142.250.74.110
ADDRESS_LINE_RE = re.compile(
    r"""
    ^(?P<address>\d+\.\d+\.\d+\.\d+)$
    """,
    re.VERBOSE | re.MULTILINE,
)
FORCE_EXIT_SET = "force_exit"
FORCE_DIRECT_SET = "force_direct"
COMMENT = "#"

HEALTH_TIMEOUT = 10.0
DOMAINS_TIMEOUT = 90.0
APPLY_TIMEOUT = 150.0
KEEPER_TIMEOUT = 60.0
SHOWN_REASON = 200
SHOWN_KEEPER_REASON = 160
UPSTREAM_TRIES = 20                 # the resolver has just been restarted
ANSWER_TRIES = 15
RETRY_SECONDS = 1.0
DIG_TIMEOUT = 10.0
PREWARM_TIMEOUT = 5.0
PREWARM_AT_ONCE = 24
LOOPBACK = "127.0.0.1"
ORDINARY_NAME = "ya.ru"


class NotApplied(Exception):
    """The routing or the resolver did not take a change; the text is for the admin."""


# ── what has no world around it ──────────────────────────────────────────────

def upstreams(entries: Sequence[str], mode: RouteMode) -> dict[str, Any]:
    """The resolver's upstream settings. By the list: listed domains via the exit,
    the rest directly. Everything foreign: all via the exit. Each group of names
    is one rule naming both exit resolvers (not one rule per resolver: every name
    would be sent twice, and the request has a size limit — see UPSTREAM_BYTES)."""
    if mode is not RouteMode.LIST:
        servers = [*UPSTREAMS_VIA_EXIT, THIRD_VIA_EXIT]
    else:
        servers = list(UPSTREAMS_DIRECT)
        names = resolver_names(list(entries))
        for start in range(0, len(names), NAMES_PER_RULE):
            servers.append("[/" + "/".join(names[start:start + NAMES_PER_RULE]) + "/]" + " ".join(UPSTREAMS_VIA_EXIT))
    return {"upstream_dns": servers, "fallback_dns": UPSTREAMS_FALLBACK, "upstream_mode": "parallel"}


def upstreams_fit(entries: Sequence[str], mode: RouteMode, limit: int = UPSTREAM_BYTES) -> bool:
    """Does the resolver take these settings in one request."""
    return len(json.dumps(upstreams(entries, mode)).encode()) <= limit


def ipset_text(exit_text: str, direct_text: str) -> str:
    """The text of the resolver's file of sets: "name,name/set" — it adds what it
    resolves to the set. A pattern (with a star) is widened to the literal part
    after its last starred label; addresses and networks are not names."""
    names: list[str] = []
    seen: set[str] = set()
    for line in exit_text.splitlines():
        if not line.strip() or line.lstrip().startswith(COMMENT) or is_network(line):
            continue
        name = UP_TO_LAST_STAR_RE.sub("", line).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    direct = [line for line in direct_text.splitlines() if line.strip() and not line.lstrip().startswith(COMMENT)]
    exit_line = ",".join(names) + f"/{FORCE_EXIT_SET}\n" if names else ""
    direct_line = ",".join(direct) + f"/{FORCE_DIRECT_SET}\n" if direct else ""
    return exit_line + direct_line


def read_mode(mode_file: Path) -> RouteMode:
    """The mode the routing runs in: the compiled file, the same one the rules
    are made from at boot. Anything but a plain "geo" is the default, the list."""
    try:
        with open(mode_file) as handle:
            written = handle.read(MODE_FILE_READ).strip()
    except OSError:
        return RouteMode.LIST
    return RouteMode.FOREIGN if written == RouteMode.FOREIGN.value else RouteMode.LIST


def listed_lines(path: Path) -> list[str]:
    """The lines of a compiled list: no empty ones, no comments."""
    try:
        with open(path) as handle:
            return [line.strip() for line in handle if line.strip() and not line.startswith(COMMENT)]
    except OSError:
        return []


def text_or_empty(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


# ── the resolver's own settings ──────────────────────────────────────────────

class ResolverTuning:
    """The resolver told where to ask for which names, and asked whether it answers."""

    def __init__(self, blocker: Blocker, run: AsyncRunner, client_iface: str, mode_file: Path) -> None:
        self.__blocker = blocker
        self.__run = run
        self.__client_iface = client_iface
        self.__mode_file = mode_file

    async def __server(self) -> str:
        """The address the users ask their names at: the entry's own on their tunnel."""
        shown = (await self.__run(["ip", "-4", "-o", "addr", "show", self.__client_iface])).out.split()
        return shown[3].split("/")[0] if len(shown) > 3 else LOOPBACK

    async def set_upstreams(self, entries: Sequence[str]) -> str | None:
        """→ why it failed, or None. Asked again for a while: the resolver has just been restarted."""
        payload = upstreams(entries, read_mode(self.__mode_file))
        last = ""
        for _ in range(UPSTREAM_TRIES):
            try:
                await self.__blocker.post("dns_config", payload)
                return None
            except Exception as e:
                last = str(e)
                await asyncio.sleep(RETRY_SECONDS)
        return last

    async def clear_cache(self) -> None:
        try:
            await self.__blocker.post("cache_clear", {})
        except Exception:
            pass

    async def answers(self, tries: int = ANSWER_TRIES) -> bool:
        """The resolver answers an ordinary name on the users' address."""
        server = await self.__server()
        for _ in range(tries):
            done = await self.__run(["dig", "+short", "+time=2", "+tries=1", f"@{server}", ORDINARY_NAME, "A"], None, DIG_TIMEOUT)
            if ADDRESS_LINE_RE.search(done.out):
                return True
            await asyncio.sleep(RETRY_SECONDS)
        return False

    async def prewarm(self, entries: Sequence[str]) -> None:
        """Resolves every listed name once through the resolver, so their addresses
        are learned right away, instead of when a user happens to open the site."""
        names = resolver_names(list(entries))
        names += ["www." + name for name in names]
        server = await self.__server()
        at_once = asyncio.Semaphore(PREWARM_AT_ONCE)

        async def ask(name: str) -> None:
            async with at_once:
                await self.__run(["dig", "+short", "+time=3", "+tries=1", f"@{server}", name, "A"], None, PREWARM_TIMEOUT)

        await asyncio.gather(*(ask(name) for name in names))


# ── the routing following the groups ─────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class RoutingFiles:
    """The files compiled for the routing and the resolver."""

    domains_exit: Path              # the flat list: what goes abroad
    domains_direct: Path            # what never goes abroad
    nets_auto: Path                 # networks kept up to date by the watch service
    nets_auto_off: Path             # present while the group that owns them is switched off
    domains_ipset: Path             # compiled for the resolver: names → sets
    domains_shared: Path            # the flat list again, for the DNS-without-VPN container
    resolver_yaml: Path             # the resolver's own settings: told where its file of sets is
    route_mode: Path                # the mode, as the rules read it at boot


class Routing:
    def __init__(self, files: RoutingFiles, watch: RoutingWatch, keeper: ResolverKeeper, resolver: ResolverSetup) -> None:
        self.__files = files
        self.__watch = watch
        self.__keeper = keeper
        self.__resolver = resolver

    def mode(self) -> RouteMode:
        return read_mode(self.__files.route_mode)

    def automatic_networks(self) -> int:
        return len([line for line in listed_lines(self.__files.nets_auto) if is_network(line)])

    def changed(self, groups: Sequence[SiteGroup]) -> bool:
        """Routing changes only if the set of active entries (or the automatic
        networks) changes; renaming or regrouping must not restart the resolver."""
        different = sorted(flat_entries(groups)) != sorted(listed_lines(self.__files.domains_exit))
        return different or automatic_on(groups) == self.__files.nets_auto_off.exists()

    def __point_resolver_at_sets(self) -> None:
        settings = text_or_empty(self.__files.resolver_yaml)
        pointed = IPSET_FILE_LINE_RE.sub(lambda _: "  ipset_file: " + str(self.__files.domains_ipset), settings)
        if pointed != settings:
            with open(self.__files.resolver_yaml, "w") as handle:       # in place: its owner and mode stay
                handle.write(pointed)

    async def relearn(self) -> None:
        """Makes the resolver learn the listed domains anew. It reads its file of
        sets only at start (and rewrites its settings on exit), so: ask its keeper
        to stop it → point the settings at the file → the watch service loads the
        listed networks and forgets the learned addresses → start it. A restart
        also empties its cache; users are without DNS for about a second.
        Raises NotApplied."""
        exit_text = text_or_empty(self.__files.domains_exit)
        direct_text = text_or_empty(self.__files.domains_direct)
        write_file(self.__files.domains_ipset, ipset_text(exit_text, direct_text), PUBLIC_MODE)
        self.__files.domains_shared.parent.mkdir(parents=True, exist_ok=True)
        write_file(self.__files.domains_shared, exit_text, PUBLIC_MODE)
        health = await self.__watch.health(HEALTH_TIMEOUT)      # asked first: the resolver is not stopped for a step that cannot be made
        if not health.ok:
            raise NotApplied((health.said or "сторож не отвечает").strip()[-SHOWN_REASON:])
        stopped = await self.__keeper.stop(KEEPER_TIMEOUT)
        if not stopped.ok:
            await self.__keeper.start(KEEPER_TIMEOUT)
            raise NotApplied("блокировщик не остановился: " + stopped.said.strip()[-SHOWN_KEEPER_REASON:])
        try:
            await asyncio.to_thread(self.__point_resolver_at_sets)
            reset = await self.__watch.domains(DOMAINS_TIMEOUT)
        finally:
            started = await self.__keeper.start(KEEPER_TIMEOUT)
        if not started.ok:
            raise NotApplied("блокировщик не запустился: " + started.said.strip()[-SHOWN_KEEPER_REASON:])
        if not reset.ok:
            raise NotApplied(reset.said.strip()[-SHOWN_REASON:] or "ошибка")

    async def follow(self, groups: Sequence[SiteGroup]) -> None:
        """Makes the routing follow these groups: the flat list, the switch of the
        automatic networks, the resolver's sets and its upstreams. Raises NotApplied."""
        entries = flat_entries(groups)
        write_file(self.__files.domains_exit, "".join(entry + "\n" for entry in entries), PUBLIC_MODE)
        if automatic_on(groups):
            self.__files.nets_auto_off.unlink(missing_ok=True)
        else:
            write_file(self.__files.nets_auto_off, "", PUBLIC_MODE)
        try:
            await self.relearn()
        except NotApplied as e:
            raise NotApplied("не удалось применить: " + str(e)[-SHOWN_REASON:]) from None
        failed = await self.__resolver.set_upstreams(entries)
        if failed:
            raise NotApplied("списки применены, но не удалось настроить DNS: " + failed[:SHOWN_REASON])
        if not await self.__resolver.answers():
            raise NotApplied("после применения DNS не отвечает")

    async def switch(self, mode: RouteMode, entries: Sequence[str]) -> None:
        """Switches the routing and the resolver to this mode. Raises NotApplied."""
        write_file(self.__files.route_mode, mode.value + "\n", PUBLIC_MODE)
        applied = await self.__watch.apply(APPLY_TIMEOUT)
        if not applied.ok:
            raise NotApplied("не удалось применить: " + applied.said[-SHOWN_REASON:])
        failed = await self.__resolver.set_upstreams(entries)
        if failed:
            raise NotApplied("маршруты переключены, но не удалось настроить DNS: " + failed[:SHOWN_REASON])
        await self.__resolver.clear_cache()      # answers found the other mode's way would live on until their TTL
        if not await self.__resolver.answers():
            raise NotApplied("после переключения DNS не отвечает")

    async def prewarm(self, entries: Sequence[str]) -> None:
        await self.__resolver.prewarm(entries)
