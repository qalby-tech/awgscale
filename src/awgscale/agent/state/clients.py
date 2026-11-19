"""The users as one look at the tunnel found them: a row each, the counters over
all of them, and one page of them for a query.

A look is taken by the sampling job, not by whoever asks: the rows, their sort
orders and the counters are made once per look, and a question is answered by
one pass over an order that is already there."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from ipaddress import IPv4Address
from typing import Any, Callable, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from awgscale.core.patterns import NAME_RE

from ..tunnel import Peer
from ..users.models import KnownUser
from ..wire import unix, unix_or_zero

BITS = 8
MIN_RATE_SECONDS = 1.0          # two looks closer than this say nothing about speed: the one before keeps its figures
MAX_PAGE = 100
MAX_NAMES = 100
MAX_QUERY_LENGTH = 64
DEFAULT_WITHIN = timedelta(days=1)
MAX_WITHIN_SECONDS = 400 * 86400
UNGROUPED = "none"              # the query's group of those who are in none
NO_ADDRESS = IPv4Address("0.0.0.0")


class ClientStatus(StrEnum):
    """Which users a page is of."""

    ALL = "all"
    ONLINE = "online"
    OFFLINE = "offline"
    PAUSED = "paused"
    EXPIRING = "expiring"       # the term ends within `within`
    STALE = "stale"             # not heard from for `within`, or never
    NEVER = "never"             # never heard from


class ClientSort(StrEnum):
    ADDRESS = "address"
    NAME = "name"
    SEEN = "seen"               # the latest handshake first
    TRAFFIC = "traffic"         # the most bytes first
    SPEED = "speed"             # the fastest now first
    EXPIRES = "expires"         # the nearest end of term first


@dataclass(frozen=True, slots=True)
class ClientRow:
    """A user as one look found them."""

    name: str
    ip: str
    address: int                 # the address as a number: the order by address
    expires_at: datetime | None
    endpoint: str | None
    handshake: datetime | None
    rx: int                      # bytes received FROM the user (their upload)
    tx: int                      # bytes sent TO the user (their download)
    live: bool                   # the tunnel reported counters for this user
    exit: int | None             # the exit the user is on now
    pin: int | None              # the exit chosen by hand
    group: str | None            # the id of the user's group
    suspended: bool
    online: bool
    down_bps: float
    up_bps: float
    haystack: str                # what the search looks in: the name in lower case and the address

    def wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "suspended": self.suspended,
            "ip": self.ip,
            "expires_at": unix(self.expires_at),
            "endpoint": self.endpoint,
            "handshake": unix_or_zero(self.handshake),
            "rx": self.rx,
            "tx": self.tx,
            "live": self.live,
            "exit": self.exit,
            "pin": self.pin,
            "group": self.group,
            "online": self.online,
            "down_bps": self.down_bps,
            "up_bps": self.up_bps,
        }


def address_number(ip: str) -> int:
    try:
        return int(IPv4Address(ip))
    except ValueError:
        return int(NO_ADDRESS)


def _rate(now: int, before: int, seconds: float) -> float:
    """Bits per second a counter of bytes grew by between two looks."""
    return max(now - before, 0) / seconds * BITS if seconds > 0 else 0.0


def _speed(now: int, before: int, speed_before: float, seconds: float, measured: bool) -> float:
    return _rate(now, before, seconds) if measured else speed_before


@dataclass(frozen=True, slots=True)
class LookInput:
    """What a look gathered about the users, before the rows are made of it."""

    now: datetime
    users: Sequence[KnownUser]
    peers: Mapping[str, Peer]                # by address
    on_exit: Mapping[str, int]               # address → the exit the user is on
    paused: frozenset[str]
    pins: Mapping[str, int]
    group_of: Mapping[str, str]              # name → the id of their group
    group_ids: tuple[str, ...]               # every group, in the stored order
    online_for: timedelta


def make_rows(look: LookInput, before: Mapping[str, ClientRow], seconds: float) -> list[ClientRow]:
    """The rows of a look; speeds against the look before (by address) over the seconds really between them,
    0 for a user it did not have. A look right after another (a change asked for a new one) keeps the speeds."""
    rows: list[ClientRow] = []
    measured = seconds >= MIN_RATE_SECONDS
    for user in look.users:
        peer = look.peers.get(user.ip)
        was = before.get(user.ip)
        handshake = peer.handshake if peer is not None else None
        rx = peer.received if peer is not None else 0
        tx = peer.sent if peer is not None else 0
        rows.append(
            ClientRow(
                name=user.name,
                ip=user.ip,
                address=address_number(user.ip),
                expires_at=user.expires_at,
                endpoint=peer.endpoint if peer is not None else None,
                handshake=handshake,
                rx=rx,
                tx=tx,
                live=peer is not None,
                exit=look.on_exit.get(user.ip),
                pin=look.pins.get(user.name),
                group=look.group_of.get(user.name),
                suspended=user.name in look.paused,
                online=handshake is not None and look.now - handshake < look.online_for,
                down_bps=_speed(tx, was.tx, was.down_bps, seconds, measured) if was is not None else 0.0,      # tx on the server = the user's download
                up_bps=_speed(rx, was.rx, was.up_bps, seconds, measured) if was is not None else 0.0,
                haystack=user.name.lower() + " " + user.ip,
            )
        )
    return rows


# ── the counters ─────────────────────────────────────────────────────────────

class Count(BaseModel):
    """How many users, how many of them are online, how fast they go and how much they took."""

    users: int = Field(default=0, description="Users")
    online: int = Field(default=0, description="…of them online")
    paused: int = Field(default=0, description="…of them paused")
    down_bps: float = Field(default=0.0, description="Their download, bits per second")
    up_bps: float = Field(default=0.0, description="Their upload, bits per second")
    rx: int = Field(default=0, description="Bytes received from them, as the tunnel counts since it started")
    tx: int = Field(default=0, description="Bytes sent to them")

    def add(self, row: ClientRow) -> None:
        self.users += 1
        self.online += row.online
        self.paused += row.suspended
        self.down_bps += row.down_bps
        self.up_bps += row.up_bps
        self.rx += row.rx
        self.tx += row.tx


class GroupCount(Count):
    id: str = Field(description="The group's id")


class ExitCount(Count):
    index: int = Field(description="The exit's number")


class UserCounters(BaseModel):
    """What all users together are, counted once per look."""

    model_config = ConfigDict(frozen=True)

    users: int = Field(default=0, description="Users in all")
    online: int = Field(default=0, description="Online now")
    paused: int = Field(default=0, description="Paused")
    live: int = Field(default=0, description="Reported by the tunnel")
    never: int = Field(default=0, description="Never connected")
    expiring: int = Field(default=0, description="Whose term ends within a day")
    rx: int = Field(default=0, description="Bytes received from all users, as the tunnel counts since it started")
    tx: int = Field(default=0, description="Bytes sent to all users")
    down_bps: float = Field(default=0.0, description="Download of all users, bits per second")
    up_bps: float = Field(default=0.0, description="Upload of all users, bits per second")
    groups: tuple[GroupCount, ...] = Field(default=(), description="Per group, in the stored order")
    ungrouped: Count = Field(default_factory=Count, description="Those in no group")
    exits: tuple[ExitCount, ...] = Field(default=(), description="Per exit the users are on now")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def count_rows(rows: Sequence[ClientRow], group_ids: Sequence[str], now: datetime) -> UserCounters:
    groups = {group_id: Count() for group_id in group_ids}
    ungrouped = Count()
    exits: dict[int, Count] = {}
    online = 0
    paused = 0
    live = 0
    never = 0
    expiring = 0
    rx = 0
    tx = 0
    down = 0.0
    up = 0.0
    soon = now + DEFAULT_WITHIN
    for row in rows:
        online += row.online
        paused += row.suspended
        live += row.live
        never += row.handshake is None
        expiring += row.expires_at is not None and now < row.expires_at <= soon
        rx += row.rx
        tx += row.tx
        down += row.down_bps
        up += row.up_bps
        groups.get(row.group or "", ungrouped).add(row)
        if row.exit is not None:
            exits.setdefault(row.exit, Count()).add(row)
    return UserCounters(
        users=len(rows),
        online=online,
        paused=paused,
        live=live,
        never=never,
        expiring=expiring,
        rx=rx,
        tx=tx,
        down_bps=down,
        up_bps=up,
        groups=tuple(GroupCount(id=group_id, **count.model_dump()) for group_id, count in groups.items()),
        ungrouped=ungrouped,
        exits=tuple(ExitCount(index=index, **count.model_dump()) for index, count in sorted(exits.items())),
    )


# ── the orders ───────────────────────────────────────────────────────────────

FAR_PAST = float("-inf")
FAR_FUTURE = float("inf")


def _seen_key(row: ClientRow) -> tuple[float, int]:
    return (-(row.handshake.timestamp() if row.handshake is not None else FAR_PAST), row.address)


def _expires_key(row: ClientRow) -> tuple[float, int]:
    return (row.expires_at.timestamp() if row.expires_at is not None else FAR_FUTURE, row.address)


SORT_KEYS: Mapping[ClientSort, Callable[[ClientRow], Any]] = {
    ClientSort.ADDRESS: lambda row: row.address,
    ClientSort.NAME: lambda row: (row.name.lower(), row.address),
    ClientSort.SEEN: _seen_key,
    ClientSort.TRAFFIC: lambda row: (-(row.rx + row.tx), row.address),
    ClientSort.SPEED: lambda row: (-(row.down_bps + row.up_bps), row.address),
    ClientSort.EXPIRES: _expires_key,
}


def orders_of(rows: Sequence[ClientRow]) -> dict[ClientSort, tuple[int, ...]]:
    """Every order a page can be asked in, as positions of the rows."""
    positions = range(len(rows))
    return {sort: tuple(sorted(positions, key=lambda position: key(rows[position]))) for sort, key in SORT_KEYS.items()}


# ── a query ──────────────────────────────────────────────────────────────────

class ClientsQuery(BaseModel):
    """One page of users: who, in which order, from where."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    q: StrictStr = Field(default="", max_length=MAX_QUERY_LENGTH, description="Part of a name or of an address, any case")
    group: StrictStr | None = Field(default=None, max_length=12, description='Only this group\'s members; "none": those in no group')
    exit: StrictInt | None = Field(default=None, ge=1, le=99, description="Only those on this exit now")
    status: ClientStatus = Field(default=ClientStatus.ALL, description="Which users")
    within: StrictInt = Field(default=int(DEFAULT_WITHIN.total_seconds()), ge=1, le=MAX_WITHIN_SECONDS, description="Seconds: the term ends within (expiring), or not heard from for (stale)")
    sort: ClientSort = Field(default=ClientSort.ADDRESS, description="The order")
    desc: StrictBool = Field(default=False, description="The order backwards")
    offset: StrictInt = Field(default=0, ge=0, description="How many to skip")
    limit: StrictInt = Field(default=50, ge=0, le=MAX_PAGE, description="How many at most; 0 counts only")
    names: tuple[StrictStr, ...] | None = Field(default=None, max_length=MAX_NAMES, description="Exactly these users (the rest of the query still applies)")

    @field_validator("names")
    @classmethod
    def names_fit(cls, names: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if names is not None and not all(NAME_RE.match(name) for name in names):
            raise ValueError("does not fit")
        return names


class ClientsPage(BaseModel):
    """The answer of `clients`."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    time: datetime = Field(description="When the look the page is of was taken")
    total: int = Field(description="How many users fit the query in all")
    offset: int = Field(description="Where the page begins among them")
    rows: tuple[ClientRow, ...] = Field(description="The page")

    def wire(self) -> dict[str, Any]:
        return {"ok": True, "time": unix_or_zero(self.time), "total": self.total, "offset": self.offset, "clients": [row.wire() for row in self.rows]}


def _test(query: ClientsQuery, now: datetime) -> Callable[[ClientRow], bool] | None:
    """The query's conditions as one test of a row; None when every row fits."""
    tests: list[Callable[[ClientRow], bool]] = []
    needle = query.q.strip().lower()
    if needle:
        tests.append(lambda row: needle in row.haystack)
    if query.group == UNGROUPED:
        tests.append(lambda row: row.group is None)
    elif query.group is not None:
        group = query.group
        tests.append(lambda row: row.group == group)
    if query.exit is not None:
        index = query.exit
        tests.append(lambda row: row.exit == index)
    within = timedelta(seconds=query.within)
    match query.status:
        case ClientStatus.ONLINE:
            tests.append(lambda row: row.online)
        case ClientStatus.OFFLINE:
            tests.append(lambda row: not row.online)
        case ClientStatus.PAUSED:
            tests.append(lambda row: row.suspended)
        case ClientStatus.EXPIRING:
            soon = now + within
            tests.append(lambda row: row.expires_at is not None and now < row.expires_at <= soon)
        case ClientStatus.STALE:
            since = now - within
            tests.append(lambda row: row.handshake is None or row.handshake < since)
        case ClientStatus.NEVER:
            tests.append(lambda row: row.handshake is None)
    if not tests:
        return None
    if len(tests) == 1:
        return tests[0]
    return lambda row: all(test(row) for test in tests)


class ClientsIndex:
    """The rows of one look, ready to be asked."""

    def __init__(self, rows: Sequence[ClientRow], now: datetime) -> None:
        self.__rows = tuple(rows)
        self.__now = now
        self.__orders = orders_of(self.__rows)
        self.__by_name = {row.name: position for position, row in enumerate(self.__rows)}

    @property
    def rows(self) -> tuple[ClientRow, ...]:
        return self.__rows

    def by_address(self) -> dict[str, ClientRow]:
        return {row.ip: row for row in self.__rows}

    def by_name(self) -> dict[str, ClientRow]:
        return {row.name: row for row in self.__rows}

    def page(self, query: ClientsQuery) -> ClientsPage:
        if query.names is not None:
            wanted = {self.__by_name[name] for name in query.names if name in self.__by_name}
            order: Iterable[int] = [position for position in self.__orders[query.sort] if position in wanted]
        else:
            order = self.__orders[query.sort]
        if query.desc:
            order = reversed(tuple(order))
        test = _test(query, self.__now)
        rows = self.__rows
        if test is None and query.names is None:
            positions = order if isinstance(order, tuple) else tuple(order)
            chosen = positions[query.offset:query.offset + query.limit]
            return ClientsPage(time=self.__now, total=len(positions), offset=query.offset, rows=tuple(rows[position] for position in chosen))
        end = query.offset + query.limit
        total = 0
        page: list[ClientRow] = []
        for position in order:
            row = rows[position]
            if test is not None and not test(row):
                continue
            if query.offset <= total < end:
                page.append(row)
            total += 1
        return ClientsPage(time=self.__now, total=total, offset=query.offset, rows=tuple(page))


@dataclass(frozen=True, slots=True)
class Made:
    """The computing part of a look: the rows ready to be asked, the counters, and the rows before it by name."""

    index: ClientsIndex
    counters: UserCounters
    before_by_name: dict[str, ClientRow]


def made_of(look: LookInput, before: ClientsIndex | None, seconds: float) -> Made:
    """Run beside the loop: nothing here waits for anything. `seconds`: since the look before, by this process's clock."""
    rows = make_rows(look, before.by_address() if before is not None else {}, seconds)
    index = ClientsIndex(rows, look.now)
    return Made(index=index, counters=count_rows(index.rows, look.group_ids, look.now), before_by_name=before.by_name() if before is not None else {})
