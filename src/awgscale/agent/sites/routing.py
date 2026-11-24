"""What the routing and the resolver are given: the flat list of entries, the
resolver's own file and upstream rules, the mode — and the order in which they
are made to follow a change."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Sequence

from awgscale.core.ashell import AsyncRunner

from ..commands import RouteMode
from ..files import write_file
from ..ports import Blocker, NamesReset, ResolverKeeper, ResolverSetup, RoutingWatch
from ..rules.scoped import ScopedName
from .entries import is_network, resolver_names
from .groups import automatic_on, direct_entries, flat_entries
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
SCOPED_LINE_WORDS = 2              # a line of domains-scoped.txt: "rs_ab12cd m.youtube.com"
FORCE_EXIT_SET = "force_exit"
FORCE_DIRECT_SET = "force_direct"
COMMENT = "#"

HEALTH_TIMEOUT = 10.0
SCOPED_NAMES_FEATURE = "scoped-names"      # the watch service makes the sets of domains-scoped.txt and says which it could not (watch/models.FEATURES)
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

def upstreams(entries: Sequence[str], mode: RouteMode, direct: Sequence[str] = ()) -> dict[str, Any]:
    """The resolver's upstream settings. By the list: listed domains via the exit,
    the rest directly. Everything foreign: all via the exit, but the names sent
    directly (`direct`) are asked directly too — their traffic leaves from here, so
    an answer for here is wanted. Each group of names is one rule naming both
    resolvers of its way (not one rule per resolver: every name would be sent
    twice, and the request has a size limit — see UPSTREAM_BYTES)."""
    if mode is not RouteMode.LIST:
        servers = [*UPSTREAMS_VIA_EXIT, THIRD_VIA_EXIT]
    else:
        servers = list(UPSTREAMS_DIRECT)
        names = resolver_names(list(entries))
        for start in range(0, len(names), NAMES_PER_RULE):
            servers.append("[/" + "/".join(names[start:start + NAMES_PER_RULE]) + "/]" + " ".join(UPSTREAMS_VIA_EXIT))
    # in both modes: a name sent directly under one that goes abroad (sub.example.com under example.com) is asked
    # directly — the resolver takes the most specific rule
    names = resolver_names(list(direct))
    for start in range(0, len(names), NAMES_PER_RULE):
        servers.append("[/" + "/".join(names[start:start + NAMES_PER_RULE]) + "/]" + " ".join(UPSTREAMS_DIRECT))
    return {"upstream_dns": servers, "fallback_dns": UPSTREAMS_FALLBACK, "upstream_mode": "parallel"}


def upstreams_fit(entries: Sequence[str], mode: RouteMode, limit: int = UPSTREAM_BYTES, direct: Sequence[str] = ()) -> bool:
    """Does the resolver take these settings in one request."""
    return len(json.dumps(upstreams(entries, mode, direct)).encode()) <= limit


def _listed_names(text: str) -> list[str]:
    """The names of a compiled list as the resolver takes them: a pattern (with a star) widened to the literal part
    after its last starred label; addresses, networks and comments are not names; each once."""
    names: dict[str, None] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(COMMENT) or is_network(line):
            continue
        name = UP_TO_LAST_STAR_RE.sub("", line).strip()
        if name:
            names[name] = None
    return list(names)


def _under(name: str) -> list[str]:
    """The domains a name is under, the nearest first: m.youtube.com → youtube.com, com."""
    labels = name.split(".")
    return [".".join(labels[start:]) for start in range(1, len(labels))]


def ipset_text(exit_text: str, direct_text: str, scoped: Sequence[ScopedName] = (), hand: Collection[str] = ()) -> str:
    """The text of the resolver's file of sets: "name,name/set" — it adds what it
    resolves to the set. A pattern (with a star) is widened to the literal part
    after its last starred label; addresses and networks are not names.

    With rules for some groups of people (`scoped`: a name and the set of the rule that decides it for them), a name
    is in all of its sets: "m.youtube.com/force_exit,rs_ab12cd". The resolver takes only the most specific name, so a
    name of such a rule that nobody else names carries the sets of the name for everybody it is under. A name under
    one sent directly by hand (`hand`, domains-direct.txt) stays with that file. Without them the text is as it always was."""
    names = _listed_names(exit_text)
    direct = _listed_names(direct_text)
    if not scoped:
        exit_line = ",".join(names) + f"/{FORCE_EXIT_SET}\n" if names else ""
        direct_line = ",".join(direct) + f"/{FORCE_DIRECT_SET}\n" if direct else ""
        return exit_line + direct_line
    by_hand = {UP_TO_LAST_STAR_RE.sub("", name).strip() for name in hand if not is_network(name)}
    fate: dict[str, list[str]] = {}
    for name in names:
        fate.setdefault(name, []).append(FORCE_EXIT_SET)
    for name in direct:
        fate.setdefault(name, []).append(FORCE_DIRECT_SET)
    rule_sets: dict[str, set[str]] = {}
    for pair in scoped:
        if pair.name in by_hand or any(domain in by_hand for domain in _under(pair.name)):
            continue
        rule_sets.setdefault(pair.name, set()).add(pair.set_name)
    order = list(dict.fromkeys([*names, *direct, *rule_sets]))
    lines: dict[tuple[str, ...], list[str]] = {}
    for name in order:
        own = fate.get(name)
        if own is None:         # the resolver asks this name now, not the one for everybody it is under: its sets come along
            own = next((fate[domain] for domain in _under(name) if domain in fate), [])
        sets = (*own, *sorted(rule_sets.get(name, ())))
        if sets:
            lines.setdefault(sets, []).append(name)
    return "".join(",".join(listed) + "/" + ",".join(sets) + "\n" for sets, listed in lines.items())


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

    async def set_upstreams(self, entries: Sequence[str], direct: Sequence[str] = ()) -> str | None:
        """→ why it failed, or None. Asked again for a while: the resolver has just been restarted."""
        payload = upstreams(entries, read_mode(self.__mode_file), direct)
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
    domains_direct: Path            # what never goes abroad: written by hand on the server
    domains_direct_rules: Path      # what never goes abroad: the groups of sites sent directly
    nets_auto: Path                 # networks kept up to date by the watch service
    nets_auto_off: Path             # present while the group that owns them is switched off
    domains_ipset: Path             # compiled for the resolver: names → sets
    domains_scoped: Path            # the names each rule for some groups decides: "rs_<rule> name" (rules/scoped.py)
    scoped_retry: Path              # present while those names could not be given to the resolver: tried again (ScopedNamesRetry)
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

    def changed(self, groups: Sequence[SiteGroup], scoped: Sequence[ScopedName] = ()) -> bool:
        """Routing changes only if the set of active entries (abroad or direct, or the
        automatic networks, or the names the rules for some groups decide) changes;
        renaming or regrouping must not restart the resolver."""
        different = sorted(flat_entries(groups)) != sorted(listed_lines(self.__files.domains_exit))
        different = different or sorted(direct_entries(groups)) != sorted(listed_lines(self.__files.domains_direct_rules))
        different = different or sorted(pair.line() for pair in scoped) != sorted(listed_lines(self.__files.domains_scoped))
        return different or automatic_on(groups) == self.__files.nets_auto_off.exists()

    def hand_names(self) -> list[str]:
        """The names sent directly by hand (domains-direct.txt): above every rule, for everybody."""
        return [line for line in listed_lines(self.__files.domains_direct) if not is_network(line)]

    def direct_names(self) -> list[str]:
        """Everything sent directly: by hand and by the groups."""
        lines = listed_lines(self.__files.domains_direct) + listed_lines(self.__files.domains_direct_rules)
        return [line for line in dict.fromkeys(lines) if not is_network(line)]

    def __point_resolver_at_sets(self) -> None:
        settings = text_or_empty(self.__files.resolver_yaml)
        pointed = IPSET_FILE_LINE_RE.sub(lambda _: "  ipset_file: " + str(self.__files.domains_ipset), settings)
        if pointed != settings:
            with open(self.__files.resolver_yaml, "w") as handle:       # in place: its owner and mode stay
                handle.write(pointed)

    def __scoped_names(self) -> list[ScopedName]:
        lines = (line.split() for line in listed_lines(self.__files.domains_scoped))
        return [ScopedName(set_name=parts[0], name=parts[1]) for parts in lines if len(parts) == SCOPED_LINE_WORDS]

    def scoped_pending(self) -> bool:
        """The names of the rules for groups could not be given to the resolver (their people go by the rules for
        everybody for now): tried again with the next change or by ScopedNamesRetry."""
        return self.__files.scoped_retry.exists()

    async def names_can_be_made(self) -> bool:
        """Does the running watch service make the sets of the rules for groups ("scoped-names")? False also when it
        does not answer: a restart of the resolver to give it those names could not succeed (ScopedNamesRetry)."""
        return SCOPED_NAMES_FEATURE in await self.__watch.features(HEALTH_TIMEOUT)

    def scoped_settled(self) -> None:
        """Nothing waits to be given any more (no rule for groups is on now)."""
        self.__files.scoped_retry.unlink(missing_ok=True)

    def __scoped_failed(self, legacy: str | None) -> None:
        """The resolver is given the file of the sets that always exist (`legacy`; None: it was given a file without
        just the sets that are missing), and the names of the rules for groups are forgotten, so that the next change,
        or the retry, puts them back."""
        if legacy is not None:
            write_file(self.__files.domains_ipset, legacy, PUBLIC_MODE)
        write_file(self.__files.domains_scoped, "", PUBLIC_MODE)
        write_file(self.__files.scoped_retry, "", PUBLIC_MODE)

    async def relearn(self) -> None:
        """Makes the resolver learn the listed domains anew. It reads its file of
        sets only at start (and rewrites its settings on exit), so: ask its keeper
        to stop it → point the settings at the file → the watch service loads the
        listed networks and forgets the learned addresses → start it. A restart
        also empties its cache; users are without DNS for about a second.

        The resolver refuses to start with a set that is not there, and the sets of the rules for groups are made by
        the watch service's step (from domains-scoped.txt): until it has said so, the file names only the sets that
        always exist; the whole file is written only after it, without the sets it could not make. A watch service of
        a version that does not make them (no "scoped-names") is given none of those names. Either way the change for
        everybody goes through, and the names of the rules for groups wait for the retry (scoped_pending). Raises
        NotApplied — for what everybody's routing needs only."""
        exit_text = text_or_empty(self.__files.domains_exit)
        direct_text = "".join(name + "\n" for name in self.direct_names())
        scoped = self.__scoped_names()
        legacy = ipset_text(exit_text, direct_text)
        write_file(self.__files.domains_ipset, legacy, PUBLIC_MODE)
        self.__files.domains_shared.parent.mkdir(parents=True, exist_ok=True)
        write_file(self.__files.domains_shared, exit_text, PUBLIC_MODE)
        reset = NamesReset(False, "сторож не ответил")
        given: list[ScopedName] = []
        try:
            health = await self.__watch.health(HEALTH_TIMEOUT)      # asked first: the resolver is not stopped for a step that cannot be made
            if not health.ok:
                raise NotApplied((health.said or "сторож не отвечает").strip()[-SHOWN_REASON:])
            makes_names = not scoped or SCOPED_NAMES_FEATURE in await self.__watch.features(HEALTH_TIMEOUT)
            stopped = await self.__keeper.stop(KEEPER_TIMEOUT)
            if not stopped.ok:
                await self.__keeper.start(KEEPER_TIMEOUT)
                raise NotApplied("блокировщик не остановился: " + stopped.said.strip()[-SHOWN_KEEPER_REASON:])
            try:
                await asyncio.to_thread(self.__point_resolver_at_sets)
                reset = await self.__watch.domains(DOMAINS_TIMEOUT)
            finally:
                if reset.ok and makes_names:
                    given = [pair for pair in scoped if pair.set_name not in reset.missing]
                if given:
                    write_file(self.__files.domains_ipset, ipset_text(exit_text, direct_text, given, listed_lines(self.__files.domains_direct)), PUBLIC_MODE)
                started = await self.__keeper.start(KEEPER_TIMEOUT)
        finally:
            if len(given) < len(scoped):
                self.__scoped_failed(None if given else legacy)
        if reset.ok and len(given) == len(scoped):
            self.__files.scoped_retry.unlink(missing_ok=True)
        if not started.ok:
            raise NotApplied("блокировщик не запустился: " + started.said.strip()[-SHOWN_KEEPER_REASON:])
        if not reset.ok:
            raise NotApplied(reset.said.strip()[-SHOWN_REASON:] or "ошибка")

    async def follow(self, groups: Sequence[SiteGroup], scoped: Sequence[ScopedName] = ()) -> None:
        """Makes the routing follow these groups (and the names the rules for some groups decide): the flat list, the
        switch of the automatic networks, the resolver's sets and its upstreams. Raises NotApplied."""
        entries = flat_entries(groups)
        write_file(self.__files.domains_scoped, "".join(line + "\n" for line in sorted(pair.line() for pair in scoped)), PUBLIC_MODE)
        write_file(self.__files.domains_exit, "".join(entry + "\n" for entry in entries), PUBLIC_MODE)
        write_file(self.__files.domains_direct_rules, "".join(entry + "\n" for entry in direct_entries(groups)), PUBLIC_MODE)
        if automatic_on(groups):
            self.__files.nets_auto_off.unlink(missing_ok=True)
        else:
            write_file(self.__files.nets_auto_off, "", PUBLIC_MODE)
        try:
            await self.relearn()
        except NotApplied as e:
            raise NotApplied("не удалось применить: " + str(e)[-SHOWN_REASON:]) from None
        failed = await self.__resolver.set_upstreams(entries, self.direct_names())
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
        failed = await self.__resolver.set_upstreams(entries, self.direct_names())
        if failed:
            raise NotApplied("маршруты переключены, но не удалось настроить DNS: " + failed[:SHOWN_REASON])
        await self.__resolver.clear_cache()      # answers found the other mode's way would live on until their TTL
        if not await self.__resolver.answers():
            raise NotApplied("после переключения DNS не отвечает")

    async def prewarm(self, entries: Sequence[str]) -> None:
        await self.__resolver.prewarm(entries)
