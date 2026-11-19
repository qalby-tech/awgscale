"""The state of the cascade: the agent's answer to `state` as the panel reads it,
and what the panel adds to it before the page receives it.

The answer is the agent's own. The panel reads a few of its fields, adds what
the agent cannot know (who is online, how fast everything goes, the speed
chart) and passes everything else on as it came. So every part here

* keeps the fields it was not told about (extra="allow"), and gives back only
  the fields it was given or that were set on it (dumped with exclude_unset):
  an absent key stays absent, a null stays null;
* gives its keys back in the order they came, the ones set later after them;
* takes its fields strictly: an int stays an int and a float a float, and an
  answer of the wrong kind is refused, never quietly converted.

The moments the panel computes with are datetimes here; on the wire they are
the Unix seconds the page has always read. The moments it only passes on (an
exit's `changed`, the watch service's ticks) stay the numbers they came as: a
float of seconds would not come back from a datetime to its last digit."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, ModelWrapValidatorHandler, PlainSerializer, PrivateAttr, SerializerFunctionWrapHandler,
    model_serializer, model_validator,
)

NEVER = 0                       # how the wire says "never"

Number = int | float


def _moment_or_never(value: object) -> object:
    """Whole Unix seconds of the wire → a moment; 0 is never (None)."""
    if type(value) in (int, float):
        return None if value == NEVER else datetime.fromtimestamp(value, UTC)
    return value


def _moment(value: object) -> object:
    """Unix seconds → a moment; null stays None."""
    if type(value) in (int, float):
        return datetime.fromtimestamp(value, UTC)
    return value


def _whole_seconds_or_never(moment: datetime | None) -> int:
    return NEVER if moment is None else int(moment.timestamp())


def _whole_seconds(moment: datetime | None) -> int | None:
    return None if moment is None else int(moment.timestamp())


def _exact_seconds(moment: datetime | None) -> float | None:
    return None if moment is None else moment.timestamp()


# the agent's clock and a handshake: whole seconds, 0 for never
AgentMoment = Annotated[datetime | None, BeforeValidator(_moment_or_never), PlainSerializer(_whole_seconds_or_never, when_used="json")]
# the end of a user's term: whole seconds, null for no term
Term = Annotated[datetime | None, BeforeValidator(_moment), PlainSerializer(_whole_seconds, when_used="json")]
# a moment of the panel's own clock: seconds with their fraction
PanelMoment = Annotated[datetime | None, BeforeValidator(_moment), PlainSerializer(_exact_seconds, when_used="json")]


class Relayed(BaseModel):
    """A part of an answer passed on to the page (see the module's notes)."""

    model_config = ConfigDict(extra="allow", strict=True)

    _arrival: tuple[str, ...] = PrivateAttr(default=())

    @model_validator(mode="wrap")
    @classmethod
    def remember_order(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        part = handler(data)
        if isinstance(data, dict):
            part._arrival = tuple(data)
        return part

    @model_serializer(mode="wrap")
    def in_arrival_order(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        shown = handler(self)
        ordered = {key: shown[key] for key in self._arrival if key in shown}
        ordered.update(shown)
        return ordered

    def wire(self) -> dict[str, Any]:
        """As the page receives it."""
        return self.model_dump(mode="json", exclude_unset=True)


# ── what the agent says ──────────────────────────────────────────────────────

class ServerFacts(Relayed):
    """Load, memory and disk of a server, when it reports them."""

    load1: Number | None = Field(default=None, description="Load average of the last minute")
    cpus: Number | None = Field(default=None, description="Processors")
    mem_total: Number | None = Field(default=None, description="Memory, bytes")
    mem_used: Number | None = Field(default=None, description="Memory in use, bytes")
    uptime: Number | None = Field(default=None, description="Seconds since boot")
    disk_pct: Number | None = Field(default=None, description="How full the root disk is, percent")


class EntryLook(ServerFacts):
    """The entry server itself."""

    hostname: str | None = Field(default=None, description="The server's name")
    wan_if: str | None = Field(default=None, description="The interface of the default route")
    wan_rx: Number | None = Field(default=None, description="Bytes received on it since boot")
    wan_tx: Number | None = Field(default=None, description="Bytes sent on it since boot")
    listen_port: int | None = Field(default=None, description="The port the users' tunnel listens on")
    client_if_up: bool | None = Field(default=None, description="The users' tunnel interface is there")


class ExitLook(Relayed):
    """An exit: what the watch service says of it, what the entry sees of the link, and (added here) how fast it goes."""

    index: int | None = Field(default=None, description="The exit's number in cascade.conf")
    name: str | None = Field(default=None, description="The exit's name")
    iface: str | None = Field(default=None, description="The link's interface")
    state: str | None = Field(default=None, description="An ExitState value: up, down or unknown")
    handshake: AgentMoment = Field(default=None, description="When the link last spoke; None when never")
    rx: Number | None = Field(default=None, description="Bytes received through the link")
    tx: Number | None = Field(default=None, description="Bytes sent through the link")
    probe_ms: Number | None = Field(default=None, description="How long the exit took to answer its probe, milliseconds")
    changed: Number | None = Field(default=None, description="When its state last changed, Unix seconds as the watch service gave them (passed on)")
    weight: Number | None = Field(default=None, description="Its share of new users")
    endpoint: str | None = Field(default=None, description="Where the link points")
    iface_rx: Number | None = Field(default=None, description="Bytes received on the link since boot")
    iface_tx: Number | None = Field(default=None, description="Bytes sent on the link since boot")
    sys: ServerFacts | None = Field(default=None, description="The exit server itself, when it is up and said so")
    users: int | None = Field(default=None, description="How many users are on it now")
    online: int | None = Field(default=None, description="…of them online")
    down_bps: float = Field(default=0.0, description="Added by the panel: what comes in through the exit, bits per second")
    up_bps: float = Field(default=0.0, description="Added by the panel: what goes out through it, bits per second")


class ClientLook(Relayed):
    """A user on a page of `clients`: who, where from, when last heard, how much traffic, whether online and how fast."""

    name: str | None = Field(default=None, description="Name of the user")
    suspended: bool | None = Field(default=None, description="Paused: nothing passes")
    ip: str | None = Field(default=None, description="Address inside the tunnel")
    expires_at: Term = Field(default=None, description="When the access ends; None is no term")
    endpoint: str | None = Field(default=None, description="Where the device connects from")
    handshake: AgentMoment = Field(default=None, description="When the device last spoke; None when never or not reported")
    rx: Number | None = Field(default=None, description="Bytes received FROM the user (their upload)")
    tx: Number | None = Field(default=None, description="Bytes sent TO the user (their download)")
    live: bool | None = Field(default=None, description="The tunnel reported counters for this user")
    exit: int | None = Field(default=None, description="The exit the user is on now")
    pin: int | None = Field(default=None, description="The exit chosen for this user by hand, if any")
    group: str | None = Field(default=None, description="The id of the user's group")
    online: bool | None = Field(default=None, description="The tunnel heard from them recently")
    down_bps: Number | None = Field(default=None, description="The user's download, bits per second")
    up_bps: Number | None = Field(default=None, description="The user's upload, bits per second")


class ClientsLook(Relayed):
    """The agent's answer to `clients`: one page of users and how many fit the query in all."""

    ok: bool | None = Field(default=None, description="The agent answered with a page")
    error: str | None = Field(default=None, description="Why it did not")
    time: AgentMoment = Field(default=None, description="When the look the page is of was taken")
    total: int | None = Field(default=None, description="How many users fit the query in all")
    offset: int | None = Field(default=None, description="Where the page begins")
    clients: list[ClientLook] | None = Field(default=None, description="The page")


class TrafficLook(BaseModel):
    """The agent's answer to `traffic`: every user's byte counters, for the metrics."""

    ok: bool = Field(default=False, description="The agent answered with the counters")
    time: AgentMoment = Field(default=None, description="When the look they are of was taken")
    users: list[tuple[str, Number, Number]] = Field(default_factory=list, description="Per user: name, bytes received from them, bytes sent to them")


class UsersLook(Relayed):
    """The users, counted by the agent at its last look: never listed in the state."""

    users: int = Field(default=0, description="Users in all")
    online: int = Field(default=0, description="Online now")
    down_bps: Number = Field(default=0.0, description="Download of all users, bits per second")
    up_bps: Number = Field(default=0.0, description="Upload of all users, bits per second")


class WatchLook(Relayed):
    """The watch service's own work. Its moments are passed on as the numbers they came as."""

    started: Number | None = Field(default=None, description="When it started, Unix seconds")
    applied: Number | None = Field(default=None, description="When the rules were last put in, Unix seconds; 0 never")
    last_tick: Number | None = Field(default=None, description="When the last pass ended, Unix seconds; 0 never")
    tick_seconds: Number | None = Field(default=None, description="How long the last pass took")
    ticks: Number | None = Field(default=None, description="Passes made")
    tick_failed: Number | None = Field(default=None, description="Passes that failed")
    ok: bool | None = Field(default=None, description="The watch service answered")


class RoutingLook(Relayed):
    """How much is routed which way: counts only."""

    mode: str | None = Field(default=None, description="A RouteMode value: what goes abroad")
    udp_via_exit: bool | None = Field(default=None, description="Calls go through an exit")
    exit_nets: Number | None = Field(default=None, description="Listed networks loaded")
    exit_domains: Number | None = Field(default=None, description="Listed names that go abroad")
    exit_patterns: Number | None = Field(default=None, description="…of them, patterns")
    direct_domains: Number | None = Field(default=None, description="Names forced direct")
    exit_ips: Number | None = Field(default=None, description="Addresses learned for the listed names")
    direct_ips: Number | None = Field(default=None, description="Addresses learned for the names forced direct")
    single_server: bool | None = Field(default=None, description="A single server: no exits, the pages of the exits are hidden")


class TopSite(Relayed):
    name: JsonValue = Field(default=None, description="A site, by the name of its group, or its own domain")
    count: JsonValue = Field(default=None, description="Lookups in the period")


class TopBlocked(Relayed):
    domain: str | None = Field(default=None, description="A name the blocker stopped")
    count: JsonValue = Field(default=None, description="How many times")


class TopClient(Relayed):
    ip: str | None = Field(default=None, description="An address in the tunnel")
    count: JsonValue = Field(default=None, description="Requests it made")


class DnsLook(Relayed):
    """The blocker's day. Its counters are AdGuard's and are passed on whatever they are."""

    ok: bool | None = Field(default=None, description="The blocker answered")
    error: str | None = Field(default=None, description="Why it did not")
    queries: JsonValue = Field(default=None, description="Requests in all")
    blocked: JsonValue = Field(default=None, description="Requests stopped")
    safe: JsonValue = Field(default=None, description="Requests stopped as dangerous sites")
    avg_ms: JsonValue = Field(default=None, description="How long an answer takes on average, milliseconds")
    top_blocked: list[TopBlocked] | None = Field(default=None, description="The names stopped most often")
    top_clients: list[TopClient] | None = Field(default=None, description="The most active addresses")
    top_sites: list[TopSite] | None = Field(default=None, description="The day's most visited")
    top_sites_total: JsonValue = Field(default=None, description="Lookups of the day in all")
    top_exit: list[TopSite] | None = Field(default=None, description="…of them, through the exits")
    top_exit_total: JsonValue = Field(default=None, description="Lookups through the exits in all")
    top_direct: list[TopSite] | None = Field(default=None, description="…and directly")
    top_direct_total: JsonValue = Field(default=None, description="Lookups directly in all")
    top_sites_hours: JsonValue = Field(default=None, description="Over how many hours")


# ── what the panel adds ──────────────────────────────────────────────────────

class Totals(Relayed):
    """What all users together do right now."""

    down_bps: float = Field(default=0.0, description="Download of all users, bits per second")
    up_bps: float = Field(default=0.0, description="Upload of all users, bits per second")
    via_exits_down_bps: float = Field(default=0.0, description="What comes in through the exits, bits per second")
    online: int = Field(default=0, description="Users online")
    clients: int = Field(default=0, description="Users in all")


class HistoryPoint(Relayed):
    """One point of the speed chart."""

    t: AgentMoment = Field(default=None, description="The agent's clock at that look")
    down: float = Field(default=0.0, description="Download of all users, bits per second")
    up: float = Field(default=0.0, description="Upload of all users, bits per second")


class PanelState(Relayed):
    """The agent's answer to `state` (a refusal is one too: ok false and an error), with what the panel adds after it."""

    ok: bool | None = Field(default=None, description="The agent answered with the state")
    error: str | None = Field(default=None, description="Why it did not")
    time: AgentMoment = Field(default=None, description="The agent's clock at this look")
    entry: EntryLook | None = Field(default=None, description="The entry server")
    exits: list[ExitLook] | None = Field(default=None, description="The exits, in the order of cascade.conf")
    watch: WatchLook | None = Field(default=None, description="The watch service's own work")
    ru_networks: Number | None = Field(default=None, description="Russian networks loaded")
    routing: RoutingLook | None = Field(default=None, description="How much is routed which way")
    users: UsersLook | None = Field(default=None, description="The users, counted")
    dns: DnsLook | None = Field(default=None, description="The blocker's day")
    ugroups_v: str | None = Field(default=None, description="Fingerprint of the users' groups; None with the store down")
    sites_v: str | None = Field(default=None, description="Fingerprint of the sites' groups")
    spaces_v: str | None = Field(default=None, description="Fingerprint of the spaces")
    store_ok: bool | None = Field(default=None, description="The store answered")
    # added by the panel, in the order the page has always had them after the agent's fields
    fetched_at: PanelMoment = Field(default=None, description="When the panel asked for this look")
    totals: Totals | None = Field(default=None, description="All users together")
    stale: bool | None = Field(default=None, description="A later look failed: this one is the last good")
    poll_error: str | None = Field(default=None, description="Why the later look failed")
    history: list[HistoryPoint] | None = Field(default=None, description="The speed chart, oldest first")
    server_time: PanelMoment = Field(default=None, description="The panel's clock when it answered the page")
    ui: str | None = Field(default=None, description="The id of the page build the panel hands out now")
