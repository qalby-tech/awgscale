"""The answer of `state`. Inside: moments and typed parts; on the wire: the
numbers the panel and the bot have always read (Unix seconds, byte counters)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from awgscale.watch.conf import DEFAULT_HOME_COUNTRY
from awgscale.watch.models import ExitView, Work

from ..adguard.models import Counters
from ..commands import RouteMode
from ..tops.models import Tops
from ..wire import exit_shape, unix_or_zero, work_shape
from .clients import UserCounters
from .system import EntryFacts, ExitSystem


class ExitShown(BaseModel):
    """An exit in the state: what the watch service says of it, and what the entry sees of the link."""

    model_config = ConfigDict(frozen=True)

    view: ExitView = Field(description="The watch service's own answer")
    weight: int = Field(description="Its share, as the compiled settings have it")
    endpoint: str | None = Field(description="Where the link points")
    iface_rx: int = Field(description="Bytes received on the link since boot")
    iface_tx: int = Field(description="Bytes sent on the link since boot")
    system: ExitSystem | None = Field(default=None, description="Load, memory and disk of the server, when it is up and said so")
    users: int = Field(default=0, description="How many users are on it now")
    online: int = Field(default=0, description="…of them online")

    def wire(self) -> dict[str, Any]:
        """The watch service's view without its list of addresses: how many users, not who."""
        shown = exit_shape(self.view.model_copy(update={"clients": []}))      # the addresses are not shaped only to be dropped
        del shown["clients"]
        shown["users"] = self.users
        shown["online"] = self.online
        shown["weight"] = self.weight
        shown["endpoint"] = self.endpoint
        shown["iface_rx"] = self.iface_rx
        shown["iface_tx"] = self.iface_tx
        if self.system is not None:
            shown["sys"] = self.system.wire()
        return shown


class RoutingShown(BaseModel):
    """How much is routed which way: counts only."""

    model_config = ConfigDict(frozen=True)

    mode: RouteMode = Field(description="What goes abroad")
    udp_via_exit: bool = Field(description="Calls go through an exit")
    exit_nets: int = Field(description="Listed networks loaded")
    exit_domains: int = Field(description="Listed names that go abroad")
    exit_patterns: int = Field(description="…of them, patterns")
    direct_domains: int = Field(description="Names forced direct")
    exit_ips: int = Field(description="Addresses learned for the listed names")
    direct_ips: int = Field(description="Addresses learned for the names forced direct")
    single_server: bool = Field(default=False, description="A single server: no exits; said only when it is on")
    home_country: str = Field(default=DEFAULT_HOME_COUNTRY, description="Whose networks go direct; said only when it is not Russia")

    def wire(self) -> dict[str, Any]:
        hidden: set[str] = set()
        if not self.single_server:
            hidden.add("single_server")
        if self.home_country == DEFAULT_HOME_COUNTRY:
            hidden.add("home_country")
        return self.model_dump(mode="json", exclude=hidden or None)


class DnsShown(BaseModel):
    """The blocker's day: totals, the names stopped most, the most active addresses, the tops by name."""

    model_config = ConfigDict(frozen=True)

    counters: Counters | None = Field(description="AdGuard's counters; None when it did not answer")
    average_ms: Any = Field(default=0, description="How long an answer takes on average, milliseconds")
    error: str | None = Field(default=None, description="Why it did not")
    tops: Tops = Field(description="The day's most visited, by name")

    def wire(self) -> dict[str, Any]:
        if self.counters is None:
            return {"ok": False, "error": self.error, **self.tops.wire()}
        return {
            "ok": True,
            "queries": self.counters.queries,
            "blocked": self.counters.blocked,
            "safe": self.counters.safe,
            "avg_ms": self.average_ms,
            "top_blocked": [stopped.model_dump() for stopped in self.counters.top_blocked],
            "top_clients": [client.model_dump() for client in self.counters.top_clients],
            **self.tops.wire(),
        }


class Versions(BaseModel):
    """Which groups and spaces are stored now: an open page re-reads them when these change."""

    model_config = ConfigDict(frozen=True)

    ugroups_v: str | None = Field(default=None, description="Fingerprint of the users' groups; None with the store down")
    sites_v: str | None = Field(default=None, description="Fingerprint of the sites' groups")
    spaces_v: str | None = Field(default=None, description="Fingerprint of the spaces")


class StateShown(BaseModel):
    """The answer of state."""

    model_config = ConfigDict(frozen=True)

    time: datetime = Field(description="When the look was taken")
    entry: EntryFacts = Field(description="The entry server itself")
    listen_port: int | None = Field(description="The port the users' tunnel listens on")
    client_if_up: bool = Field(description="The users' tunnel interface is there")
    exits: tuple[ExitShown, ...] = Field(description="The exits, in the order of cascade.conf")
    watch: Work | None = Field(description="The watch service's own work; None when it did not answer (the exits are then unknown, not gone)")
    ru_networks: int = Field(description="Russian networks loaded")
    routing: RoutingShown = Field(description="How much is routed which way")
    counters: UserCounters = Field(description="The users, counted: never listed here (see `clients`)")
    dns: DnsShown = Field(description="The blocker's day")
    versions: Versions = Field(description="The fingerprints of the stored lists")
    store_ok: bool = Field(description="The store answered")

    def wire(self) -> dict[str, Any]:
        watch: dict[str, Any] = {} if self.watch is None else work_shape(self.watch)
        return {
            "ok": True,
            "time": unix_or_zero(self.time),
            "entry": {**self.entry.wire(), "listen_port": self.listen_port, "client_if_up": self.client_if_up},
            "exits": [exit_.wire() for exit_ in self.exits],
            "watch": {**watch, "ok": self.watch is not None},
            "ru_networks": self.ru_networks,
            "routing": self.routing.wire(),
            "users": self.counters.wire(),
            "dns": self.dns.wire(),
            **self.versions.model_dump(),
            "store_ok": self.store_ok,
        }


class TrafficShown(BaseModel):
    """The answer of `traffic`: the byte counters of every user the tunnel reports, and nothing else."""

    model_config = ConfigDict(frozen=True)

    time: datetime = Field(description="When the look was taken")
    counters: tuple[tuple[str, int, int], ...] = Field(description="Per user: name, bytes received from them, bytes sent to them")

    def wire(self) -> dict[str, Any]:
        return {"ok": True, "time": unix_or_zero(self.time), "users": [list(counters) for counters in self.counters]}
