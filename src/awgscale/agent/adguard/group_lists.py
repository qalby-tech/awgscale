"""The groups' own block lists, made into one file AdGuard reads, and «Угрозы» for everybody.

A group of a level (Pro, Pro++, Ultimate) is blocked HaGeZi's list of that
level; a group in the children's mode, the add-on lists the admin switched on
for it (NSFW, gambling, the ways around the filter, social networks). Only the
lists some group with members needs are downloaded; every plain rule of them
(`||name^`) is written once, for the AdGuard clients that should have it:

    ||tracker.example^$client='grp-pro'|'grp-pro-all'|'grp-proplus'|…

(the levels nest, so most names are in all of them). The clients are those of
adguard.profiles: "grp-<level>" for the members of every group of a level and
the users who chose that level themselves, "grp-<id>" for one group in the
children's mode, and the twin "…-all" of each, for its people who send all
their traffic through their exit (always named: who does is no reason to
download anything again). The file is a local path AdGuard
reads as one of its block lists (inside its safe_fs_patterns; the agent and the
resolver see the same /opt/AdGuardHome). When no group needs a list, the list is
taken out of AdGuard and the file is deleted: it costs AdGuard tens of megabytes.

Done in the background (a download never holds up the groups' save): at the
start and then every few hours by the scheduler, and whenever the groups need
other lists than the file has (adguard.profiles nudges with every sync and every
look at the state). The file says in its header what it was made for.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Protocol, Sequence

import httpx

from ..clients.tls import tls_context
from ..journal.models import Kind, Who
from ..ports import GroupsStore, OwnFilters
from ..users.models import GroupFilter, KidsList, UserGroup
from .api import group_profile, level_profile, whole_twin
from .client import AdGuardDown
from .models import GROUP_LISTS_TITLE, THREATS_LIST_TITLE, FilterList

log = logging.getLogger("awgscale.agent")

DOWNLOAD_TIMEOUT = 60.0
SAID_LENGTH = 160
HEADER_LINES_READ = 4
FILE_MODE = 0o644                   # the resolver's container reads it
RETRY_AFTER_SECONDS = 300           # a failed attempt is not repeated by a nudge sooner than this
COMPLAIN_EVERY_SECONDS = 3600       # a failure is said in the journal once an hour at most
TITLE_LINE = f"! Title: {GROUP_LISTS_TITLE}"
NEEDS_MARK = "! Needs: "            # the header line that says which clients get which lists

# A plain blocking rule of an AdBlock-syntax list, and nothing else: no
# modifiers, no exceptions, no wildcards, no comments. Looked for line by line
# in the whole downloaded text (lower-cased).
#     ||tracker.example.com^
RULE_RE = re.compile(
    r"""
    ^[ \t]*\|\|                                         # the rule covers the name and every name under it
    (?P<name>[a-z0-9_](?:[a-z0-9_.-]{0,251}[a-z0-9_])?) # tracker.example.com
    \^[ \t]*\r?$                                        # the end of the name, and nothing after it
    """,
    re.VERBOSE | re.MULTILINE,
)


class Scheme(StrEnum):
    HTTPS = "https"


class GroupSource(StrEnum):
    """HaGeZi's lists the groups may need; the value is the file's name without ".txt"."""

    PRO = "pro"
    PROPLUS = "pro.plus"
    ULTIMATE = "ultimate"
    NSFW = "nsfw"
    GAMBLING = "gambling.mini"
    BYPASS = "doh-vpn-proxy-bypass"
    SOCIAL = "social"


LEVEL_SOURCES: Mapping[str, GroupSource] = {
    GroupFilter.PRO.value: GroupSource.PRO,
    GroupFilter.PROPLUS.value: GroupSource.PROPLUS,
    GroupFilter.ULTIMATE.value: GroupSource.ULTIMATE,
}
KIDS_SOURCES: Mapping[str, GroupSource] = {
    KidsList.NSFW.value: GroupSource.NSFW,
    KidsList.GAMBLING.value: GroupSource.GAMBLING,
    KidsList.BYPASS.value: GroupSource.BYPASS,
    KidsList.SOCIAL.value: GroupSource.SOCIAL,
}

Needs = dict[str, tuple[GroupSource, ...]]          # AdGuard's client → the lists it gets


class ListRefused(Exception):
    """A download that is not a list that can be used; the file in use stays."""


class NothingToFree(Exception):
    """No group needs a list, there is no file, and the blocker cannot be asked whether it still lists it: not worth a line in the journal."""


def needs_of(groups: Sequence[UserGroup], own: Mapping[str, str] | None = None) -> Needs:
    """Which client gets which lists. Decided from the stored groups (those with members) and the levels users chose
    themselves (`own`: {name: level}): a list of users that cannot be read never takes a list away. Every client is
    named with its twin (see whole_twin)."""
    needs: dict[str, set[GroupSource]] = {}
    for level in set((own or {}).values()):
        if level in LEVEL_SOURCES:
            needs.setdefault(level_profile(GroupFilter(level)), set()).add(LEVEL_SOURCES[level])
    for group in groups:
        if not group.members:
            continue
        if group.filter in LEVEL_SOURCES:
            needs.setdefault(level_profile(GroupFilter(group.filter)), set()).add(LEVEL_SOURCES[group.filter])
        elif group.filter == GroupFilter.KIDS.value and group.id is not None and group.kids_lists:
            needs.setdefault(group_profile(group.id), set()).update(KIDS_SOURCES[kids_list] for kids_list in group.kids_lists if kids_list in KIDS_SOURCES)
    for client, sources in list(needs.items()):
        needs[whole_twin(client)] = sources
    order = list(GroupSource)
    return {client: tuple(sorted(sources, key=order.index)) for client, sources in sorted(needs.items()) if sources}


def signature(needs: Needs) -> str:
    """The needs as one line: "grp-3=nsfw,social grp-pro=pro"; empty when nothing is needed."""
    return " ".join(f"{client}={','.join(source.value for source in sources)}" for client, sources in needs.items())


def sources_of(needs: Needs) -> list[GroupSource]:
    """Every list some client needs, each once, in their own order."""
    wanted = {source for sources in needs.values() for source in sources}
    return [source for source in GroupSource if source in wanted]


def rule_names(text: str) -> Iterator[str]:
    """The names of a downloaded list's plain rules, in their order."""
    return (found.group("name") for found in RULE_RE.finditer(text.lower()))


def client_bits(needs: Needs, source: GroupSource) -> int:
    """The clients that get a list, as bits: client number i of the needs (in their order) is bit i."""
    return sum(1 << index for index, sources in enumerate(needs.values()) if source in sources)


def merged_into(blocked: dict[str, int], text: str, bits: int, min_rules: int) -> int:
    """A downloaded list's names join the names blocked so far, for these clients. → how many rules it had.
    Refuses a list with fewer than min_rules (what it added is then of no use: the whole file is not made)."""
    count = 0
    for name in rule_names(text):
        blocked[name] = blocked.get(name, 0) | bits
        count += 1
    if count < min_rules:
        raise ListRefused(f"в списке {count} правил, меньше {min_rules}")
    return count


def file_lines(needs: Needs, blocked: Mapping[str, int]) -> Iterator[str]:
    """The file AdGuard is given, line by line: a header that says what it was made for, then every
    name once, for every client that should have it blocked."""
    clients = list(needs)
    modifiers: dict[int, str] = {}
    yield TITLE_LINE + "\n"
    yield NEEDS_MARK + signature(needs) + "\n"
    for name, bits in blocked.items():
        if bits not in modifiers:
            modifiers[bits] = "$client=" + "|".join(f"'{client}'" for index, client in enumerate(clients) if bits >> index & 1)
        yield f"||{name}^{modifiers[bits]}\n"


def made_for(path: Path) -> str | None:
    """What the file in place was made for (its "! Needs:" line); None when there is no file, or it says nothing."""
    try:
        with open(path, errors="replace") as handle:
            for _ in range(HEADER_LINES_READ):
                line = handle.readline()
                if line.startswith(NEEDS_MARK):
                    return line[len(NEEDS_MARK):].rstrip("\n")
    except FileNotFoundError:
        return None
    return None


def digest_of(path: Path) -> bytes | None:
    """The file's SHA-256, read in chunks; None when there is no file."""
    try:
        with open(path, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").digest()
    except FileNotFoundError:
        return None


def written_if_changed(path: Path, lines: Iterable[str]) -> bool:
    """Puts the lines into the file whole (written beside it as they come, then a rename; the old file
    is left as it is when the new one is the same). → whether it changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    digest = hashlib.sha256()
    try:
        with os.fdopen(handle, "wb") as file:
            for line in lines:
                data = line.encode()
                digest.update(data)
                file.write(data)
        if digest.digest() == digest_of(path):
            Path(temporary).unlink()
            return False
        os.chmod(temporary, FILE_MODE)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


class ListDownloader:
    """Downloads HaGeZi's lists: https only, no more than max_bytes each."""

    def __init__(self, base_url: str, max_bytes: int, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.__base_url = base_url
        self.__max_bytes = max_bytes
        self.__transport = transport

    async def text(self, source: GroupSource) -> str:
        """Raises ListRefused, or httpx.HTTPError when the list cannot be reached."""
        url = self.__base_url + source.value + ".txt"
        if httpx.URL(url).scheme != Scheme.HTTPS.value:
            raise ListRefused("ссылка на список не https")
        chunks: list[bytes] = []
        size = 0
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, verify=tls_context(), transport=self.__transport, follow_redirects=False) as http:
            async with http.stream("GET", url) as response:
                if response.status_code != httpx.codes.OK:
                    raise ListRefused(f"{source.value}: HTTP {response.status_code}")
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.__max_bytes:
                        raise ListRefused(f"{source.value}: больше {self.__max_bytes} байт")
                    chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")


class Downloader(Protocol):
    async def text(self, source: GroupSource) -> str: ...


class BlockLists(Protocol):
    """AdGuard's block lists (adguard.api.AdGuard)."""

    async def filters(self) -> list[FilterList]: ...

    async def add_filter(self, name: str, url: str) -> None: ...

    async def set_filter(self, filter_list: FilterList, name: object, enabled: bool, timeout: float | None = None) -> None: ...

    async def remove_filter(self, filter_list: FilterList) -> None: ...

    async def refresh(self) -> None: ...


class JournalWriter(Protocol):
    async def write(self, text: str, bad: bool = False, kind: Kind = Kind.CHANGE, who: Who = Who.SYSTEM) -> None: ...


@dataclass(slots=True)
class GroupListsMemory:
    """What this process last made of the file. Not kept anywhere: a process that has just started looks again.
    The moments are time.time() of this process, 0 for "never"."""

    applied: str | None = None          # the signature of the needs put in place ("" is nothing); None: not known yet
    failed_at: float = 0.0
    failed_wanting: str | None = None   # what the groups needed when it failed: a change of theirs is tried at once
    refresh_owed: bool = False          # the file changed and AdGuard has not been told yet (its call failed after the write)
    complained_at: float = 0.0


class GroupBlockLists:
    """Keeps the file of the groups' own lists in AdGuard as the groups ask."""

    def __init__(
        self,
        adguard: BlockLists,
        groups: GroupsStore,
        journal: JournalWriter,
        downloader: Downloader,
        file: Path,
        min_rules: int,
        memory: GroupListsMemory,
        own: OwnFilters | None = None,
    ) -> None:
        self.__own = own
        self.__adguard = adguard
        self.__groups = groups
        self.__journal = journal
        self.__downloader = downloader
        self.__file = file
        self.__min_rules = min_rules
        self.__memory = memory
        self.__lock = asyncio.Lock()
        self.__task: asyncio.Task[str | None] | None = None

    def nudge(self, groups: Sequence[UserGroup], own: Mapping[str, str] | None = None) -> None:
        """The groups (and the levels users chose themselves) as they are now: when they need other lists than were
        last put in place, the file is put right in the background. Never raises."""
        memory = self.__memory
        wanting = signature(needs_of(groups, own))
        if wanting == memory.applied:
            return
        if self.__task is not None and not self.__task.done():
            return
        if wanting == memory.failed_wanting and time.time() - memory.failed_at < RETRY_AFTER_SECONDS:
            return
        try:
            self.__task = asyncio.get_running_loop().create_task(self.keep())
        except RuntimeError:
            pass

    async def keep(self, refresh: bool = False) -> str | None:
        """Puts the file in place for what the groups need (downloaded again when refresh, or when the file was made for other needs),
        takes it away when nothing is needed. → an error text or None. Never raises: a failure is said in the journal, once an hour at most."""
        async with self.__lock:
            memory = self.__memory
            wanting: str | None = None
            try:
                own = {} if self.__own is None else await self.__own.read()
                needs = needs_of((await self.__groups.read()).groups, own)
                wanting = signature(needs)
                if needs:
                    error = await self.__provide(needs, refresh)
                else:
                    await self.__withdraw()
                    error = None
            except NothingToFree as e:
                memory.failed_at = time.time()
                memory.failed_wanting = wanting
                log.info("group lists: the blocker is away (%s), and there is nothing to take away", e)
                return None
            except Exception as e:
                error = reason(e)
            if error is None:
                memory.applied = wanting
                memory.failed_at = 0.0
                memory.failed_wanting = None
                return None
            memory.failed_at = time.time()
            memory.failed_wanting = wanting
            log.warning("group lists: %s", error)
            await self.__complain(error)
            return error

    def __held(self, filters: Sequence[FilterList]) -> FilterList | None:
        return next((filter_list for filter_list in filters if filter_list.url == str(self.__file)), None)

    async def __written(self, needs: Needs) -> bool:
        """Downloads every list the needs name, one after the other (of each, only its names are kept, merged
        with the others'), and writes the file. → whether it changed."""
        blocked: dict[str, int] = {}
        for source in sources_of(needs):
            text = await self.__downloader.text(source)
            await asyncio.to_thread(merged_into, blocked, text, client_bits(needs, source), self.__min_rules)
            del text
        return await asyncio.to_thread(written_if_changed, self.__file, file_lines(needs, blocked))

    async def __provide(self, needs: Needs, refresh: bool) -> str | None:
        """Makes the file anew when asked, or when it was made for other needs; a download that fails keeps the file in use,
        and AdGuard is still made to have it."""
        was_made_for = await asyncio.to_thread(made_for, self.__file)
        failed: str | None = None
        if refresh or was_made_for != signature(needs):
            try:
                if await self.__written(needs):
                    self.__memory.refresh_owed = True
            except (ListRefused, httpx.HTTPError, OSError) as e:
                if was_made_for is None:
                    raise
                failed = reason(e)
        held = self.__held(await self.__adguard.filters())
        if held is None:
            await self.__adguard.add_filter(GROUP_LISTS_TITLE, str(self.__file))       # AdGuard reads the file at once
        elif not held.enabled:
            await self.__adguard.set_filter(held, held.name, True)
            await self.__adguard.refresh()                                              # a list that was off has no rules loaded
        elif self.__memory.refresh_owed:
            await self.__adguard.refresh()
        self.__memory.refresh_owed = False
        return failed

    async def __withdraw(self) -> None:
        """Out of AdGuard first, then the file."""
        has_file = await asyncio.to_thread(self.__file.is_file)
        try:
            held = self.__held(await self.__adguard.filters())
        except AdGuardDown as e:
            if has_file:
                raise
            raise NothingToFree(e.kind) from None
        if held is not None:
            await self.__adguard.remove_filter(held)
        await asyncio.to_thread(self.__file.unlink, True)

    async def __complain(self, error: str) -> None:
        try:
            if time.time() - self.__memory.complained_at > COMPLAIN_EVERY_SECONDS:
                self.__memory.complained_at = time.time()
                await self.__journal.write("Списки групп не применяются: " + error, True, Kind.CHANGE, Who.SYSTEM)
        except Exception as e:
            log.info("journal not written: %s", repr(e)[:200])


def reason(error: Exception) -> str:
    """A failure in words for the journal."""
    if isinstance(error, AdGuardDown):
        return f"блокировщик не отвечает ({error.kind})"
    if isinstance(error, httpx.HTTPError):
        return "список не скачан: " + (str(error)[:SAID_LENGTH] or type(error).__name__)
    return (str(error) or type(error).__name__)[:SAID_LENGTH]


class ThreatsList:
    """«Угрозы» (HaGeZi's Threat Intelligence Feeds, mini) for everybody: an ordinary list AdGuard downloads by its link.
    Put in when it is missing; switched off by the admin, it stays off."""

    def __init__(self, adguard: BlockLists, url: str) -> None:
        self.__adguard = adguard
        self.__url = url

    async def ensure(self) -> bool:
        """→ whether it was missing and has been put in. Raises AdGuardDown."""
        if any(filter_list.url == self.__url for filter_list in await self.__adguard.filters()):
            return False
        await self.__adguard.add_filter(THREATS_LIST_TITLE, self.__url)
        return True
