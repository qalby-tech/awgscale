"""The groups' own block lists, made into one file AdGuard reads, and «Угрозы» for everybody.

A group of a level (Pro, Pro++, Ultimate) is blocked HaGeZi's list of that
level; a group in the children's mode, the add-on lists the admin switched on
for it (NSFW, gambling, the ways around the filter, social networks).

Which lists a person has is part of their AdGuard client's settings: the client
carries one tag per list (ListTag: AdGuard knows a fixed set of tags, and a rule
`||name^$ctag=a|b` blocks the name only for the clients that carry a or b —
tests/stack_adguard.py). adguard.profiles gives every client its tags in the
same call as its switches and upstreams, so people change lists the way they
change any setting, and nothing here has to follow them.

The file holds every list it keeps once, each name on one line with every tag
of the lists that have it (sorted):

    ||tracker.example^$ctag=device_laptop|device_nas|device_pc

(one line per list and name would double the rules, and AdGuard's memory with
them: docs/rollback.md, "The groups' lists"). It does not depend on who is in
which client: it changes only when the set of lists changes or a list is
downloaded again. A list is kept while a group wants it or any client in
AdGuard carries its tag, so nobody's list is taken out from under them: a client
that changes its lists keeps the old tags until the new lists are loaded
(adguard.profiles.kept_tags). A tag whose list is not in the file yet blocks
nothing.

The file is a local path AdGuard reads as one of its block lists (inside its
safe_fs_patterns; the agent and the resolver see the same /opt/AdGuardHome).
When nothing needs a list, it is taken out of AdGuard and the file deleted.

The older agent wrote the same file naming clients (`$client='grp-pro'|…`, and a
header line "! Needs: grp-pro=pro …"). While such a file is in force,
adguard.profiles moves nobody: it only gives every client the tags of the lists
that file names it with (and none to a client it does not name). Once it has
seen them so (settled), the file is turned into one of tags in place, without
downloading anything: each name gets the tags that block it for exactly the
clients the older file named (converted), so nobody's blocking changes; one
write and one refresh. Then every list is downloaded again, and the clients
move as wanted (docs/rollback.md, "The groups' lists").

Done in the background (a download never holds up the groups' save): at the
start and then every few hours by the scheduler (every list downloaded again),
and whenever the lists needed are not those in the file (adguard.profiles
nudges with every sync and every look at the state).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Iterator, Mapping, Protocol, Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..clients.tls import tls_context
from ..journal.models import Kind, Who
from ..ports import GroupsStore, OwnFilters
from ..users.models import GroupFilter, KidsList, UserGroup
from .client import AdGuardDown
from .models import GROUP_LISTS_TITLE, THREATS_LIST_TITLE, FilterList

log = logging.getLogger("awgscale.agent")

DOWNLOAD_TIMEOUT = 60.0             # between two pieces of a download
DOWNLOAD_DEADLINE_SECONDS = 600.0   # a whole download: one that trickles never holds up the lists after it
SAID_LENGTH = 160
HEADER_LINES_READ = 4
FILE_MODE = 0o644                   # the resolver's container reads it
RETRY_AFTER_SECONDS = 300           # a failed attempt is not repeated by a nudge sooner than this
COMPLAIN_EVERY_SECONDS = 3600       # a failure is said in the journal once an hour at most
TITLE_LINE = f"! Title: {GROUP_LISTS_TITLE}"
SOURCES_MARK = "! Sources: "        # the header line that says which lists the file has
RULES_MARK = "! Rules: "            # …and how many rules (AdGuard says how many it has loaded: they are compared)
CONVERTED_LINE = "! Converted: from the older file of names"  # not downloaded: every list is downloaded again soon
UNREAD_SUFFIX = ".unread"           # the mark beside the file while AdGuard has not read it (what it may have instead)
UNREAD_TAGS = "tags"                # …an earlier file of tags
NEEDS_MARK = "! Needs: "            # the older file's header line: which clients it names with which lists

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
# A rule of the file this module writes.
#     ||tracker.example.com^$ctag=device_nas|device_pc
TAGGED_RULE_RE = re.compile(
    r"""
    ^\|\|(?P<name>[a-z0-9_.-]+)     # ||tracker.example.com
    \^\$ctag=(?P<tags>[a-z_|]+)$    # ^$ctag=device_nas|device_pc
    """,
    re.VERBOSE | re.MULTILINE,
)
# A rule of the older agent's file of names.
#     ||tracker.example.com^$client='grp-pro'|'grp-pro-all'
NAMED_RULE_RE = re.compile(
    r"""
    ^\|\|(?P<name>[a-z0-9_.-]+)         # ||tracker.example.com
    \^\$client=(?P<clients>[^\n]+)$    # ^$client='grp-pro'|'grp-pro-all'
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


class ListTag(StrEnum):
    """The tag of AdGuard's clients that stands for each list: the member of the same name as its GroupSource.
    AdGuard takes only its own fixed tags (an unknown one: clients/add refuses it, a rule never matches it); these are
    tags of kinds of devices that no HaGeZi list uses (tests/stack_adguard.py checks each is one AdGuard knows). The only
    place where a list and its tag are matched."""

    PRO = "device_pc"
    PROPLUS = "device_laptop"
    ULTIMATE = "device_nas"
    NSFW = "device_tv"
    GAMBLING = "device_gameconsole"
    BYPASS = "device_securityalarm"
    SOCIAL = "device_phone"


class FileKind(StrEnum):
    """What the file in place is."""

    MISSING = "missing"
    TAGS = "tags"           # made by this module: rules by the clients' tags
    NAMES = "names"         # made by the older agent: rules by the clients' names ("! Needs:")
    OTHER = "other"         # something else: replaced as if missing


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
ORDER: tuple[GroupSource, ...] = tuple(GroupSource)
SOURCE_VALUES = frozenset(source.value for source in GroupSource)


class ListRefused(Exception):
    """A download that is not a list that can be used; the list in use stays."""


class NothingToFree(Exception):
    """Nothing needs a list, there is no file, and the blocker cannot be asked whether it still lists it: not worth a line in the journal."""


def tag_of(source: GroupSource) -> ListTag:
    return ListTag[source.name]


def source_of(tag: object) -> GroupSource | None:
    """The list a tag stands for; None for any other tag (a client may carry tags of its own)."""
    for member in ListTag:
        if member.value == tag:
            return GroupSource[member.name]
    return None


def tags_for(sources: Iterable[GroupSource]) -> list[str]:
    """The tags of these lists as a client is sent them: sorted, each once (AdGuard reads them back sorted, and keeps
    a tag sent twice twice)."""
    return sorted({tag_of(source).value for source in sources})


def sources_for(tags: Iterable[object]) -> frozenset[GroupSource]:
    """The lists these tags stand for (other tags left out)."""
    return frozenset(source for source in map(source_of, tags) if source is not None)


def level_sources(level: str) -> frozenset[GroupSource]:
    """The list of a level ("pro"…); none for any other filtering."""
    return frozenset({LEVEL_SOURCES[level]}) if level in LEVEL_SOURCES else frozenset()


def kids_sources(kids_lists: Iterable[str] | None) -> frozenset[GroupSource]:
    """The add-on lists a children's group has switched on."""
    return frozenset(KIDS_SOURCES[kids_list] for kids_list in kids_lists or () if kids_list in KIDS_SOURCES)


def wanted_sources(groups: Sequence[UserGroup], own: Mapping[str, str] | None = None) -> frozenset[GroupSource]:
    """Every list some client should carry: the levels of the groups with members and of the users who chose one
    themselves, the add-on lists of the children's groups with members. (A little more than adguard.profiles gives
    out, never less: a list costs only its download.)"""
    wanted: set[GroupSource] = set()
    for level in (own or {}).values():
        wanted |= level_sources(level)
    for group in groups:
        if not group.members:
            continue
        wanted |= level_sources(group.filter)
        if group.filter == GroupFilter.KIDS.value:
            wanted |= kids_sources(group.kids_lists)
    return frozenset(wanted)


def in_order(sources: Iterable[GroupSource]) -> list[GroupSource]:
    chosen = set(sources)
    return [source for source in ORDER if source in chosen]


def sources_signature(sources: Iterable[GroupSource]) -> str:
    """The lists as one line: "pro nsfw"; empty for none."""
    return " ".join(source.value for source in in_order(sources))


def bit_of(source: GroupSource) -> int:
    return 1 << ORDER.index(source)


def sources_of_bits(bits: int) -> list[GroupSource]:
    return [source for index, source in enumerate(ORDER) if bits >> index & 1]


def rule_names(text: str) -> Iterator[str]:
    """The names of a downloaded list's plain rules, in their order."""
    return (found.group("name") for found in RULE_RE.finditer(text.lower()))


def accepted_names(text: str, min_rules: int) -> list[str]:
    """The names of a downloaded list; refuses a list with fewer than min_rules (the one in use then stays)."""
    names = list(rule_names(text))
    if len(names) < min_rules:
        raise ListRefused(f"в списке {len(names)} правил, меньше {min_rules}")
    return names


def replaced_in(blocked: dict[str, int], source: GroupSource, names: Sequence[str]) -> None:
    """The names of one list, as downloaded now, take the place of those it had in `blocked` ({name: the bits of its lists})."""
    bit = bit_of(source)
    for name in [name for name, bits in blocked.items() if bits & bit]:
        bits = blocked[name] & ~bit
        if bits:
            blocked[name] = bits
        else:
            del blocked[name]
    for name in names:
        blocked[name] = blocked.get(name, 0) | bit


def kept_only(blocked: dict[str, int], sources: Collection[GroupSource]) -> None:
    """Every list but these taken out of `blocked`."""
    mask = sum(bit_of(source) for source in sources)
    for name in list(blocked):
        bits = blocked[name] & mask
        if bits:
            blocked[name] = bits
        else:
            del blocked[name]


def read_tagged(path: Path) -> dict[str, int]:
    """The names of a file of tags, with the bits of their lists ({} when there is none)."""
    blocked: dict[str, int] = {}
    try:
        handle = open(path, errors="replace")
    except FileNotFoundError:
        return blocked
    with handle:
        for line in handle:                 # line by line: the agent never holds the whole file (tens of megabytes)
            found = TAGGED_RULE_RE.match(line)
            if found is None:
                continue
            bits = 0
            for source in sources_for(found.group("tags").split("|")):
                bits |= bit_of(source)
            if bits:
                blocked[found.group("name")] = bits
    return blocked


def converted(path: Path, named: Mapping[str, frozenset[GroupSource]]) -> dict[str, int]:
    """The older file of names as names with the bits of lists, so that the clients carrying the tags of the lists it
    names them with (`named`, its header) have exactly the names it blocked for them, and the other clients none: a
    name gets every list all of whose clients it was blocked for. (The names of a list a client would get by its tag
    alone, not being one the file named, are all there — and maybe some of the other lists of the clients that have
    it, until the lists are downloaded again.)"""
    holders = {source: {client for client, sources in named.items() if source in sources} for source in ORDER}
    bits_of: dict[frozenset[str], int] = {}
    blocked: dict[str, int] = {}
    with open(path, errors="replace") as handle:
        for line in handle:                 # line by line: the agent never holds the whole file (tens of megabytes)
            found = NAMED_RULE_RE.match(line)
            if found is None:
                continue
            clients = frozenset(client.strip("'") for client in found.group("clients").split("|"))
            if clients not in bits_of:
                bits_of[clients] = sum(bit_of(source) for source, having in holders.items() if having and having <= clients)
            if bits_of[clients]:
                blocked[found.group("name")] = bits_of[clients]
    return blocked


def needs_line(named: Mapping[str, Collection[GroupSource]]) -> str:
    """Clients and their lists as the older file's header says them: "grp-k=nsfw,social grp-pro=pro"."""
    return " ".join(f"{client}={','.join(source.value for source in in_order(sources))}" for client, sources in sorted(named.items()))


def named_of(line: str) -> dict[str, frozenset[GroupSource]]:
    """The other way round (needs_line)."""
    named: dict[str, frozenset[GroupSource]] = {}
    for pair in line.split():
        client, _, values = pair.partition("=")
        named[client] = frozenset(GroupSource(value) for value in values.split(",") if value in SOURCE_VALUES)
    return named


def file_lines(blocked: Mapping[str, int], made_of_names: bool = False) -> Iterator[str]:
    """The file AdGuard is given, line by line: a header that says which lists it has and how many rules (and when it
    was converted from the older file of names), then every name once, with the tags of every list that has it, in the
    order of the names (the same lists make the same file, whatever order they were downloaded in: it is not written,
    nor AdGuard refreshed, for nothing)."""
    present = 0
    for bits in blocked.values():
        present |= bits
    modifiers: dict[int, str] = {}
    yield TITLE_LINE + "\n"
    yield SOURCES_MARK + sources_signature(sources_of_bits(present)) + "\n"
    yield RULES_MARK + str(len(blocked)) + "\n"
    if made_of_names:
        yield CONVERTED_LINE + "\n"
    for name in sorted(blocked):
        bits = blocked[name]
        if bits not in modifiers:
            modifiers[bits] = "$ctag=" + "|".join(tags_for(sources_of_bits(bits)))
        yield f"||{name}^{modifiers[bits]}\n"


class FileHead(BaseModel):
    """What a file of the groups' lists says in its header."""

    model_config = ConfigDict(frozen=True)

    kind: FileKind = Field(default=FileKind.MISSING, description="What the file is")
    sources: frozenset[GroupSource] = Field(default=frozenset(), description="A file of tags: its lists")
    rules: int | None = Field(default=None, description="A file of tags: how many rules it has")
    converted: bool = Field(default=False, description="A file of tags converted from the older file of names, not all downloaded again yet")
    named: dict[str, frozenset[GroupSource]] = Field(default_factory=dict, description="A file of names: {client: the lists it names it with}")


def head_of(path: Path) -> FileHead:
    """The header of the file in place. Raises OSError when it cannot be read."""
    try:
        with open(path, errors="replace") as handle:
            lines = [handle.readline().rstrip("\n") for _ in range(HEADER_LINES_READ)]
    except FileNotFoundError:
        return FileHead()
    sources: frozenset[GroupSource] | None = None
    rules: int | None = None
    for line in lines:
        if line.startswith(NEEDS_MARK):
            return FileHead(kind=FileKind.NAMES, named=named_of(line[len(NEEDS_MARK):]))
        if line.startswith(SOURCES_MARK):
            sources = frozenset(GroupSource(value) for value in line[len(SOURCES_MARK):].split() if value in SOURCE_VALUES)
        if line.startswith(RULES_MARK) and line[len(RULES_MARK):].isdigit():
            rules = int(line[len(RULES_MARK):])
    if sources is None or rules is None:
        return FileHead(kind=FileKind.OTHER)
    return FileHead(kind=FileKind.TAGS, sources=sources, rules=rules, converted=CONVERTED_LINE in lines)


class Unread(BaseModel):
    """The mark beside the file while AdGuard has not read it: what it may still have instead, and how AdGuard showed
    the list just before the file changed (a read that ended after the agent stopped waiting is told by the list
    changing since, to the file's number of rules)."""

    model_config = ConfigDict(frozen=True)

    instead: str = Field(description="UNREAD_TAGS: an earlier file of tags; otherwise the older file of names' clients (needs_line)")
    seen: bool = Field(default=False, description="AdGuard's list was seen just before the change (otherwise only a read it answers tells)")
    last_updated: str | None = Field(default=None, description="When AdGuard said it had last read the list, before the change")
    rules_count: int | None = Field(default=None, description="How many rules AdGuard said the list had, before the change")


def unread_of(path: Path) -> Unread | None:
    """The mark beside the file (Unread); None when AdGuard has read the file in place."""
    try:
        return Unread.model_validate_json(path.read_text())
    except FileNotFoundError:
        return None
    except ValueError:
        return Unread(instead=UNREAD_TAGS)


def named_signature(named: Mapping[str, frozenset[GroupSource]]) -> str:
    return json.dumps({client: sources_signature(sources) for client, sources in sorted(named.items())})


def digest_of(path: Path) -> bytes | None:
    """The file's SHA-256, read in chunks; None when there is no file."""
    try:
        with open(path, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").digest()
    except FileNotFoundError:
        return None


def written_if_changed(path: Path, lines: Iterable[str], changing: Callable[[], None] | None = None) -> bool:
    """Puts the lines into the file whole (written beside it as they come, then a rename; the old file
    is left as it is when the new one is the same; `changing` is called just before it is replaced). → whether it changed."""
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
        if changing is not None:
            changing()
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


def marked(path: Path, unread: Unread) -> None:
    """The mark beside the file (unread_of), written whole."""
    written_if_changed(path, [unread.model_dump_json() + "\n"])


def read_since(unread: Unread, held: FilterList | None, head: FileHead) -> bool:
    """Has AdGuard read the file in place since the mark: its list on, with the file's number of rules, and changed since?"""
    if not unread.seen or held is None or not held.enabled or head.rules is None or held.rules_count != head.rules:
        return False
    return str(held.last_updated) != str(unread.last_updated) or held.rules_count != unread.rules_count


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
    """AdGuard's block lists and the clients that stand for groups (adguard.api.AdGuard)."""

    async def filters(self) -> list[FilterList]: ...

    async def add_filter(self, name: str, url: str) -> None: ...

    async def set_filter(self, filter_list: FilterList, name: object, enabled: bool, timeout: float | None = None) -> None: ...

    async def remove_filter(self, filter_list: FilterList) -> None: ...

    async def refresh(self) -> None: ...

    async def held(self) -> dict[str, dict[str, Any]]: ...


class JournalWriter(Protocol):
    async def write(self, text: str, bad: bool = False, kind: Kind = Kind.CHANGE, who: Who = Who.SYSTEM) -> None: ...


class ListsInForce(BaseModel):
    """Which lists AdGuard applies, as adguard.profiles needs to know it to give the clients their tags."""

    model_config = ConfigDict(frozen=True)

    loaded: frozenset[GroupSource] | None = Field(
        default=None, description="The lists of the file of tags AdGuard has loaded; None: not known (a client then keeps every tag it has)"
    )
    named: dict[str, frozenset[GroupSource]] = Field(
        default_factory=dict, description="While AdGuard may apply the older file of names: {client: the lists it names it with}"
    )
    in_file: frozenset[GroupSource] | None = Field(default=None, description="The lists of the file in place; None: not known")
    unread: bool = Field(default=False, description="AdGuard has not read the file in place yet (it may still apply lists the file no longer has)")


@dataclass(slots=True)
class GroupListsMemory:
    """What this process last made of the file. Not kept anywhere (what AdGuard has read is the mark beside the file):
    a process that has just started looks again. The moments are time.time() of this process, 0 for "never"."""

    applied: str | None = None          # the lists of the file put in place (sources_signature; an older file of names: what it was kept for); None: not yet
    carried: frozenset[GroupSource] = frozenset()   # the lists the clients in AdGuard carried, as last seen
    settled_for: str | None = None      # the older file's clients (named_signature) adguard.profiles last saw as wanted
    convert_waits: bool = False         # an older file of names waits for adguard.profiles to see the clients as it names them…
    named_for: str | None = None        # …so (named_signature)
    failed_at: float = 0.0
    failed_wanting: str | None = None   # what was needed when it failed: a change of it is tried at once
    complained_at: float = 0.0


class GroupBlockLists:
    """Keeps the file of the groups' own lists in AdGuard as the groups and the clients ask."""

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
        deadline: float = DOWNLOAD_DEADLINE_SECONDS,
    ) -> None:
        self.__own = own
        self.__adguard = adguard
        self.__groups = groups
        self.__journal = journal
        self.__downloader = downloader
        self.__file = file
        self.__unread = file.with_name(file.name + UNREAD_SUFFIX)
        self.__min_rules = min_rules
        self.__memory = memory
        self.__deadline = deadline
        self.__lock = asyncio.Lock()
        self.__guard = asyncio.Lock()           # the file changes, or the clients' tags do: never both at once
        self.__task: asyncio.Task[str | None] | None = None

    def guard(self) -> asyncio.Lock:
        """Held by adguard.profiles while it decides and makes the clients' tags, and here while it decides which lists
        stay in the file and writes it (never while downloading or waiting for AdGuard to read it): a list is never
        taken out of the file while a client is being given its tag."""
        return self.__guard

    @property
    def task(self) -> asyncio.Task[str | None] | None:
        """The work started by the last nudge (tests wait for it)."""
        return self.__task

    def __wanting(self, wanted: frozenset[GroupSource]) -> str:
        return sources_signature(wanted | self.__memory.carried)

    def nudge(self, groups: Sequence[UserGroup], own: Mapping[str, str] | None = None, carried: frozenset[GroupSource] | None = None) -> None:
        """The groups (and the levels users chose themselves) as they are now, and the lists the clients carry when it
        is known: when other lists are needed than were last put in place, or AdGuard has not read the file yet, or an
        older file of names waits to be converted, the file is put right in the background. Never raises."""
        memory = self.__memory
        if carried is not None:
            memory.carried = carried
        wanting = self.__wanting(wanted_sources(groups, own))
        convert = memory.convert_waits and memory.settled_for == memory.named_for
        if wanting == memory.applied and not convert and not self.__unread.exists():
            return
        if wanting == memory.failed_wanting and time.time() - memory.failed_at < RETRY_AFTER_SECONDS:
            return
        self.__start()

    def __start(self) -> None:
        if self.__task is not None and not self.__task.done():
            return
        try:
            self.__task = asyncio.get_running_loop().create_task(self.keep())
        except RuntimeError:
            pass

    def settled(self, in_force: ListsInForce) -> None:
        """adguard.profiles has seen every client as wanted, given `in_force`: an older file of names that still names
        the clients so may be converted. Never raises."""
        memory = self.__memory
        memory.settled_for = named_signature(in_force.named)
        if time.time() - memory.failed_at < RETRY_AFTER_SECONDS:
            return
        if in_force.named and (not memory.convert_waits or memory.settled_for == memory.named_for):
            self.__start()          # the older file of names (first seen by adguard.profiles, or waiting for this)

    async def in_force(self) -> ListsInForce:
        """Which lists AdGuard applies now (see ListsInForce): those of the file in place once AdGuard has read it,
        not known while it may still have an earlier file of tags, and the clients the older file names while it may
        still have that one. Never raises: what cannot be read is not known."""
        try:
            head = await asyncio.to_thread(head_of, self.__file)
            unread = await asyncio.to_thread(unread_of, self.__unread)
        except OSError:
            return ListsInForce()
        if head.kind == FileKind.NAMES:
            return ListsInForce(loaded=frozenset(), named=head.named)
        in_file = head.sources if head.kind == FileKind.TAGS else frozenset()
        if unread is not None:
            try:
                held = self.__held(await self.__adguard.filters())
            except AdGuardDown:
                held = None
            if read_since(unread, held, head):
                unread = None
                try:
                    await asyncio.to_thread(self.__unread.unlink, True)
                except OSError:
                    pass                # it is known all the same; the keeper takes the mark away later
        if unread is not None and unread.instead != UNREAD_TAGS:
            return ListsInForce(loaded=frozenset(), named=named_of(unread.instead), in_file=in_file, unread=True)
        if unread is not None:
            return ListsInForce(in_file=in_file, unread=True)
        return ListsInForce(loaded=in_file, in_file=in_file)

    async def keep(self, refresh: bool = False) -> str | None:
        """Puts the file in place for what is needed (every list downloaded again when refresh, only the missing ones
        otherwise), takes it away when nothing is needed. → an error text or None. Never raises: a failure is said in
        the journal, once an hour at most."""
        async with self.__lock:
            memory = self.__memory
            wanting: str | None = None
            try:
                own = {} if self.__own is None else await self.__own.read()
                wanted = wanted_sources((await self.__groups.read()).groups, own)
                wanting = self.__wanting(wanted)
                error = await self.__keep(wanted, refresh)
                wanting = self.__wanting(wanted)
                head = await asyncio.to_thread(head_of, self.__file)
                if head.kind != FileKind.NAMES:
                    # what the file has: a client that took or dropped a list's tag while it was made has it made again at the next nudge
                    wanting = sources_signature(head.sources)
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

    async def __carried(self) -> frozenset[GroupSource] | None:
        """The lists the clients in AdGuard carry now; None when it cannot be asked."""
        try:
            clients = await self.__adguard.held()
        except AdGuardDown:
            return None
        carried = sources_for(tag for client in clients.values() for tag in client.get("tags") or [])
        self.__memory.carried = carried
        return carried

    async def __keep(self, wanted: frozenset[GroupSource], refresh: bool) -> str | None:
        memory = self.__memory
        head = await asyncio.to_thread(head_of, self.__file)
        named = frozenset(source for sources in head.named.values() for source in sources)
        carried = await self.__carried()
        needed = wanted | named | (memory.carried if carried is None else carried)
        if not needed:
            await self.__withdraw()
            return None
        if head.kind == FileKind.NAMES:
            if not await self.__convert(head):
                return None
            head = await asyncio.to_thread(head_of, self.__file)
        refresh = refresh or head.converted         # what was converted is downloaded again at once (until it all is)
        blocked = await asyncio.to_thread(read_tagged, self.__file) if head.kind == FileKind.TAGS else {}
        present = head.sources if head.kind == FileKind.TAGS else frozenset()
        failures: list[str] = []
        fetched: set[GroupSource] = set()
        for source in in_order(needed if refresh else needed - present):
            try:
                text = await asyncio.wait_for(self.__downloader.text(source), self.__deadline)
                names = await asyncio.to_thread(accepted_names, text, self.__min_rules)
                del text
            except (ListRefused, httpx.HTTPError, OSError, asyncio.TimeoutError) as e:
                failures.append(f"{source.value}: {reason(e)}")
                continue
            await asyncio.to_thread(replaced_in, blocked, source, names)
            fetched.add(source)
        async with self.__guard:
            # read again right before the write, while no client is being given a tag: a list leaves only when no group
            # wants it and no client carries it
            wanted = wanted | await self.__wanted()
            carried = await self.__carried()
            kept = present | fetched if carried is None else wanted | carried
            if carried is not None:
                await asyncio.to_thread(kept_only, blocked, kept)
            if not blocked:
                return "; ".join(failures)[:SAID_LENGTH] or None
            still_converted = head.converted and not (present & kept) <= fetched
            await self.__write(file_lines(blocked, still_converted), UNREAD_TAGS)
        del blocked
        await self.__in_adguard()
        return "; ".join(failures)[:SAID_LENGTH] or None

    async def __wanted(self) -> frozenset[GroupSource]:
        own = {} if self.__own is None else await self.__own.read()
        return wanted_sources((await self.__groups.read()).groups, own)

    async def __write(self, lines: Iterable[str], instead: str) -> None:
        """The file made anew; when it changes, AdGuard has not read it yet: the mark beside it says what it may still
        have (`instead`, see Unread) — unless an earlier mark says the older file of names — and how its list looked."""
        path = self.__unread
        seen = True
        held: FilterList | None = None
        try:
            held = self.__held(await self.__adguard.filters())
        except AdGuardDown:
            seen = False

        def changing() -> None:
            earlier = unread_of(path)
            kept = instead if earlier is None or earlier.instead == UNREAD_TAGS else earlier.instead
            if not seen:
                marked(path, Unread(instead=kept) if earlier is None else earlier.model_copy(update={"instead": kept}))
                return
            last_updated = None if held is None else str(held.last_updated)
            rules_count = None if held is None else held.rules_count
            marked(path, Unread(instead=kept, seen=True, last_updated=last_updated, rules_count=rules_count))
        await asyncio.to_thread(written_if_changed, self.__file, lines, changing)

    async def __convert(self, head: FileHead) -> bool:
        """The older file of names turned into one of tags in place, once adguard.profiles has seen the clients carry
        the tags of the lists it names them with (and the others none). → whether it was."""
        memory = self.__memory
        memory.named_for = named_signature(head.named)
        memory.convert_waits = memory.settled_for != memory.named_for
        if memory.convert_waits:
            return False
        async with self.__guard:
            blocked = await asyncio.to_thread(converted, self.__file, head.named)
            await self.__write(file_lines(blocked, True), needs_line(head.named))
        del blocked
        await self.__in_adguard()
        log.info("group lists: the older file of names was converted to one of tags")
        return True

    async def __in_adguard(self) -> None:
        """AdGuard has the file in place and has read it: the mark beside it goes."""
        head = await asyncio.to_thread(head_of, self.__file)
        unread = await asyncio.to_thread(unread_of, self.__unread)
        filters = await self.__adguard.filters()
        held = self.__held(filters)
        if held is None:
            await self.__adguard.add_filter(GROUP_LISTS_TITLE, str(self.__file))       # AdGuard reads the file at once
        elif not held.enabled:
            await self.__adguard.set_filter(held, held.name, True)
            await self.__adguard.refresh()                                              # a list that was off has no rules loaded
        elif unread is not None and read_since(unread, held, head):
            pass                                                                        # a read that ended after the agent stopped waiting
        elif unread is not None or held.rules_count != head.rules:
            await self.__adguard.refresh()                                              # also a copy that is not the file's (a config put back)
        await asyncio.to_thread(self.__unread.unlink, True)

    def __held(self, filters: Sequence[FilterList]) -> FilterList | None:
        return next((filter_list for filter_list in filters if filter_list.url == str(self.__file)), None)

    async def __withdraw(self) -> None:
        """Out of AdGuard first, then the file (and its mark)."""
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
        await asyncio.to_thread(self.__unread.unlink, True)
        self.__memory.convert_waits = False

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
    if isinstance(error, asyncio.TimeoutError):
        return "список не скачан: не уложился во время"
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
