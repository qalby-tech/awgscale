"""The groups of sites as stored, and the answers of the commands about them."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_serializer

from awgscale.core.clock import moment_of

from ..commands import RouteMode
from ..store.documents import Record

MAX_ENTRIES = 500                   # entries the admin types, in all the groups together
MAX_GROUPS = 60
MAX_SITE_LISTS = 4                  # lists by link among the sites that go abroad
MAX_LIST_ENTRIES = 3000             # entries taken from one list


Moment = Annotated[datetime | None, BeforeValidator(moment_of)]


class SiteGroup(BaseModel):
    """A group of sites as stored. Either its entries were typed by the admin, or
    — a list by link — they are the file behind `url`, downloaded through an exit.
    What a record does not have is not made up: it comes out as it went in."""

    model_config = ConfigDict(frozen=True, extra="allow")

    id: str = Field(description="The group's own id")
    name: str = Field(description="Its name, as the admin typed it")
    on: bool = Field(default=False, description="Whether its entries go abroad now")
    items: tuple[str, ...] = Field(default=(), description="The entries: domains, patterns, networks")
    auto: str | None = Field(default=None, description="Set for the group whose networks are kept up to date by themselves (telegram)")
    kind: str | None = Field(default=None, description="Only among the block groups: what the group does")
    url: str | None = Field(default=None, description="The link of a list by link")
    updated: Moment = Field(default=None, description="When the list by link was last downloaded")
    error: str = Field(default="", description="Why the last download of the list failed; empty when it did not")
    skipped: int = Field(default=0, description="Lines of the list's file that were not understood")

    @field_serializer("updated")
    def _unix_seconds(self, moment: datetime | None) -> int | None:
        return None if moment is None else int(moment.timestamp())

    @classmethod
    def of(cls, record: Record) -> SiteGroup:
        return cls.model_validate(record)

    @property
    def by_link(self) -> bool:
        return bool(self.url)

    def wire(self) -> Record:
        """As stored and as the page gets it: the keys the record has, moments as Unix seconds."""
        return self.model_dump(mode="json", exclude_unset=True)


class SitesShown(BaseModel):
    """The answer of sites-get, and of every change of the sites."""

    mode: RouteMode = Field(description="What goes abroad now, as the routing runs")
    groups: list[SiteGroup] = Field(description="The groups, in the admin's order")
    version: str = Field(description="The fingerprint of what the admin edits")
    automatic_networks: int = Field(description="How many networks the automatic group has now")
    applied: bool | None = Field(default=None, description="After a change: whether the routing had to be applied anew")
    already: bool | None = Field(default=None, description="After mode-set: the mode asked for was running already")

    def shape(self) -> dict[str, Any]:
        """Everything but the verdict: what a refusal of a stale change carries too."""
        return {
            "mode": self.mode.value,
            "limit": MAX_ENTRIES,
            "max_lists": MAX_SITE_LISTS,
            "groups": [group.wire() for group in self.groups],
            "v": self.version,
            "auto": {"telegram": {"nets": self.automatic_networks}},
        }

    def wire(self) -> dict[str, Any]:
        answer: dict[str, Any] = {"ok": True, **self.shape()}
        if self.applied is not None:
            answer["applied"] = self.applied
        if self.already is not None:
            answer["already"] = self.already
        return answer


# ── check: where a name would go ─────────────────────────────────────────────

class Via(StrEnum):
    DIRECT = "direct"
    EXIT = "exit"


class Route(BaseModel):
    """Which way a name goes, and by which entry of which group."""

    via: Via = Field(description="Directly, or through an exit")
    group: str | None = Field(default=None, description="The group whose entry sends it abroad")
    rule: str | None = Field(default=None, description="That entry (for the VPN side: as the resolver understands it)")

    def wire(self) -> dict[str, Any]:
        """A direct route says only that; a route abroad always names its group and rule (None in the "everything foreign" mode)."""
        if self.via is Via.DIRECT:
            return {"via": self.via.value}
        return {"via": self.via.value, "group": self.group, "rule": self.rule}


class BlockedKind(StrEnum):
    OWN = "own"             # a group of the admin's
    LIST = "list"           # a block list
    SAFE = "safe"           # the dangerous-sites check


class Blocked(BaseModel):
    """What blocks a name."""

    kind: BlockedKind = Field(default=BlockedKind.OWN, description="What sort of thing blocks it")
    id: int | None = Field(default=None, description="The block list's id, for a list")
    gid: str | None = Field(default=None, description="The admin's group, for an own rule")
    list: str | None = Field(default="свои правила", description="How the page names the blocker")
    rule: str = Field(default="", description="The rule that matched")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Checked(BaseModel):
    """The answer of check."""

    host: str = Field(description="The name asked about, put in order")
    vpn: Route = Field(description="Where the VPN side sends it")
    dns: Route = Field(description="Where the DNS without VPN sends it")
    blocked: Blocked | None = Field(default=None, description="What blocks it, if anything")

    def wire(self) -> dict[str, Any]:
        return {"ok": True, "host": self.host, "vpn": self.vpn.wire(), "dns": self.dns.wire(), "blocked": self.blocked.wire() if self.blocked else None}


# ── probe: does a site open ──────────────────────────────────────────────────

class ProbeState(StrEnum):
    OK = "ok"
    REFUSED = "refused"
    ERROR = "error"
    DEAD = "dead"


class Seen(BaseModel):
    """What a visitor would see, opening the site one way."""

    state: ProbeState = Field(description="The verdict")
    code: int = Field(description="The HTTP status; 0 when there was no answer")
    ms: int = Field(description="How long it took, milliseconds")
    text: str = Field(description="The verdict in words for the admin")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Way(BaseModel):
    """One way to a site: directly from this server, or through one exit."""

    via: Via = Field(description="Directly, or through an exit")
    name: str = Field(description="How the page names the way")
    source: str | None = Field(default=None, description="The entry's address on the exit's tunnel; never shown")


class WaySeen(BaseModel):
    way: Way = Field(description="The way")
    seen: Seen = Field(description="What was seen that way")

    def wire(self) -> dict[str, Any]:
        return {**self.seen.wire(), "via": self.way.via.value, "name": self.way.name}


class Probed(BaseModel):
    """The answer of probe."""

    host: str = Field(description="The site, put in order")
    ways: list[WaySeen] = Field(description="Every way and what was seen")

    def wire(self) -> dict[str, Any]:
        return {"ok": True, "host": self.host, "ways": [way.wire() for way in self.ways]}
