"""What the page may ask for, field by field. Only the shape and the size are
checked here; what the fields mean is the agent's to judge."""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ModelWrapValidatorHandler, PrivateAttr, StrictBool, StrictInt, StringConstraints, model_validator

from awgscale.agent.state.clients import MAX_PAGE as MAX_CLIENTS_PAGE, ClientSort, ClientStatus
from awgscale.agent.commands import EXIT_COUNTRY_RE, RouteChoice, RuleRoute
from awgscale.agent.users.models import GroupFilter
from awgscale.watch.conf import RouteMode

# The patterns of the constraints below are run by pydantic-core's own engine, not by re:
# they stay plain one-line strings (no re.VERBOSE there), each with an example of what it takes.

# a user or a device:   alice   phone-7   — never starts with "-": a name becomes an argument of a command
Name = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}$")]
# a term:   12h   7d   4w
Term = Annotated[str, StringConstraints(strict=True, pattern=r"^[1-9][0-9]{0,2}[hdw]$")]


def _empty_is_none(value: object) -> object:
    return None if value == "" else value


# the page writes "no term" as an empty string
OptionalTerm = Annotated[Term | None, BeforeValidator(_empty_is_none)]
# the id of a group, a space or a list:   a00001
Country = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z]{2}$")]
ShortId = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z0-9]{6}$")]
StoredId = Annotated[str, StringConstraints(strict=True, max_length=12)]
Title = Annotated[str, StringConstraints(strict=True, max_length=200)]
# The version of a stored list the page started from:   0123456789ab
VERSION_RE = re.compile(
    r"""
    [0-9a-f]{12}
    """,
    re.VERBOSE,
)
# an exit's name:   exit1-nl   nl.2
ExitName = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9._-]{1,32}$")]
ExitIndex = Annotated[StrictInt, Field(ge=1, le=50)]
Names = Annotated[list[Name], Field(min_length=1, max_length=60)]

MAX_USER_GROUPS = 30
MAX_SPACES = 30
MAX_SITE_GROUPS = 60
MAX_BLOCK_LISTS = 50
MAX_SHARES = 8


class Based(BaseModel):
    """A change of a stored list says which version of it the page had: the agent refuses a change based on an older one."""

    base: Any = Field(default=None, description="The version the page showed; anything that is not one counts as none")

    @property
    def version(self) -> str:
        return self.base if isinstance(self.base, str) and VERSION_RE.fullmatch(self.base) else ""


# ── session ──────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: Any = Field(default="", description="What was typed; compared as text")


# ── users ────────────────────────────────────────────────────────────────────

class AddClientRequest(BaseModel):
    name: Name = Field(description="Name of the new user")
    expires: OptionalTerm = Field(default=None, description='Term of access ("12h", "7d", "4w"); none is no term')


class ClientsParams(BaseModel):
    """GET /api/clients: one page of users, searched and filtered on the server."""

    model_config = ConfigDict(extra="forbid")

    q: Annotated[str, StringConstraints(max_length=64)] = Field(default="", description="Part of a name or of an address, any case")
    # a group's id, or "none" for those in no group:   a00001   none
    group: Annotated[str, StringConstraints(pattern=r"^(?:[a-z0-9]{6}|none)$")] | None = Field(default=None, description="Only this group's members")
    exit: Annotated[int, Field(ge=1, le=50)] | None = Field(default=None, description="Only those on this exit now")
    status: ClientStatus = Field(default=ClientStatus.ALL, description="Which users: online, paused, expiring…")
    within: Annotated[int, Field(ge=1, le=400 * 86400)] | None = Field(default=None, description="Seconds: the term ends within (expiring), or not heard from for (stale)")
    sort: ClientSort = Field(default=ClientSort.ADDRESS, description="The order")
    desc: bool = Field(default=False, description="The order backwards")
    offset: Annotated[int, Field(ge=0, le=10_000_000)] = Field(default=0, description="How many to skip")
    limit: Annotated[int, Field(ge=0, le=MAX_CLIENTS_PAGE)] = Field(default=50, description="How many at most; 0 counts only")
    names: Annotated[list[Name], Field(max_length=MAX_CLIENTS_PAGE)] = Field(default_factory=list, description="Exactly these users")

    def for_agent(self) -> dict[str, Any]:
        """The agent's query (state.clients.ClientsQuery): only what was asked for."""
        query = self.model_dump(mode="json", exclude_defaults=True)
        if "names" in query:
            query["names"] = self.names
        return query


class NamesRequest(BaseModel):
    names: Names = Field(description="The users it is about")


class ExpireRequest(NamesRequest):
    expires: OptionalTerm = Field(default=None, description="The new term; none is no term")


class SuspendRequest(NamesRequest):
    on: StrictBool = Field(description="Pause (true) or resume (false)")


class UserExitRequest(NamesRequest):
    exit: ExitIndex | None = Field(default=None, description="The exit to keep them on; none is as the group or the shares decide")


class KidsList(StrEnum):
    NSFW = "nsfw"
    GAMBLING = "gambling"
    BYPASS = "bypass"
    SOCIAL = "social"


class UserGroup(BaseModel):
    id: StoredId | None = Field(default=None, description="The stored group; none is a new one")
    name: Title = Field(description="Name of the group")
    space: Any = Field(default=False, description="The group is a device space; anything true counts")
    exit: Annotated[StrictInt, Field(ge=0, le=99)] | None = Field(default=None, description="The exit the group is kept on")
    filter: GroupFilter = Field(default=GroupFilter.STANDARD, description="How the group is filtered")
    kids_lists: list[KidsList] | None = Field(default=None, max_length=len(KidsList), description="In the children's mode: the add-on lists switched on")
    members: list[Name] = Field(default_factory=list, max_length=253, description="The users in it")

    def for_agent(self) -> dict[str, Any]:
        """As the agent takes it: the exit, the filter and the add-on lists only when the page sent them."""
        group: dict[str, Any] = {"id": self.id, "name": self.name, "space": bool(self.space)}
        if "exit" in self.model_fields_set:
            group["exit"] = self.exit
        if "filter" in self.model_fields_set:
            group["filter"] = self.filter.value
        if self.kids_lists is not None:
            group["kids_lists"] = [kids_list.value for kids_list in self.kids_lists]
        group["members"] = self.members
        return group


class UserGroupsRequest(Based):
    groups: list[UserGroup] = Field(max_length=MAX_USER_GROUPS, description="All the groups, as they should be")


class PassedOn(BaseModel):
    """Something whose shape is checked here and which then goes to the agent exactly as the page sent it, with all its own fields."""

    model_config = ConfigDict(extra="allow")

    # pydantic keeps what is not a field of the model in attributes with one underscore
    _sent: dict[str, Any] = PrivateAttr(default_factory=dict)

    @model_validator(mode="wrap")
    @classmethod
    def remember_what_was_sent(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        checked = handler(data)
        checked._sent = data if isinstance(data, dict) else {}
        return checked

    @property
    def sent(self) -> dict[str, Any]:
        return self._sent


class Space(PassedOn):
    """A device space."""

    id: StoredId | None = Field(default=None, description="The stored space; none is a new one")
    name: Title = Field(description="Name of the space")
    members: list[Name] = Field(default_factory=list, max_length=64, description="The users in it")


class SpacesRequest(Based):
    spaces: list[Space] = Field(max_length=MAX_SPACES, description="All the spaces, as they should be")


# ── sites ────────────────────────────────────────────────────────────────────

class SiteGroup(PassedOn):
    """A group of a list."""

    id: StoredId | None = Field(default=None, description="The stored group; none is a new one")
    name: Title = Field(description="Name of the group")
    url: Annotated[str, StringConstraints(strict=True, max_length=600)] | None = Field(default=None, description="Where a list by link is downloaded from")
    items: list[Annotated[str, StringConstraints(strict=True, max_length=260)]] = Field(default_factory=list, max_length=500, description="Its entries")


SiteGroups = Annotated[list[SiteGroup], Field(max_length=MAX_SITE_GROUPS)]


def as_sent(groups: list[SiteGroup] | list[Space]) -> list[dict[str, Any]]:
    """The groups exactly as the page sent them."""
    return [group.sent for group in groups]


class SitesRequest(Based):
    groups: SiteGroups = Field(description="All the groups of the list, as they should be")


class ModeRequest(BaseModel):
    mode: RouteMode = Field(description="The new mode")


class AppExitRequest(BaseModel):
    exit: ExitIndex | Literal["auto"] = Field(description="The exit to keep this device's user on; auto is as the group or the shares decide")


class AppFilterRequest(BaseModel):
    filter: GroupFilter | Literal["auto"] = Field(description="How this device's user wants their requests filtered; auto is as their group")


class AppRouteRequest(BaseModel):
    route: RouteChoice = Field(description="smart: as the network's mode says; all: everything through the exit, Russian sites too")


class CountryRequest(BaseModel):
    country: Country = Field(description="The home country: its ISO 3166 two-letter code, lower case")


class RuleRouteChange(BaseModel):
    rule: ShortId = Field(description="The rule: a group of sites that go abroad, or of blocked names")
    route: RuleRoute | None = Field(default=None, description="exit: abroad; direct; block: not opened; off: as if it were not there; none: as it is")
    groups: list[ShortId] | None = Field(default=None, max_length=60, description="The groups of people it is for; empty: everybody; none: as it is")

    @model_validator(mode="after")
    def _something(self) -> Self:
        if self.route is None and self.groups is None:
            raise ValueError("nothing to change")
        return self


class RuleRouteRequest(Based):
    changes: list[RuleRouteChange] = Field(default_factory=list, max_length=120, description="Every rule staged on the canvas: applied together")
    order: list[ShortId] | None = Field(default=None, max_length=1000, description="Every rule's id in the new order; none keeps the order")

    @model_validator(mode="after")
    def _something(self) -> Self:
        if not self.changes and self.order is None:
            raise ValueError("nothing to change")
        return self


class RefreshRequest(BaseModel):
    id: ShortId | None = Field(default=None, description="The list by link to download again; none is every list that is on")


class BlockRequest(Based):
    groups: SiteGroups = Field(description="The admin's own groups of blocked names")
    # by the number of the list:   {"17": true}
    lists: dict[Annotated[str, StringConstraints(pattern=r"^\d{1,12}$")], StrictBool] = Field(
        default_factory=dict, max_length=MAX_BLOCK_LISTS, description="Which ready-made lists are on, by their number",
    )
    safebrowsing: Any = Field(default=False, description="Dangerous sites are blocked; anything true counts")


class BlockListOp(StrEnum):
    ADD = "add"
    REMOVE = "remove"
    RENAME = "rename"
    REFRESH = "refresh"


ListNumber = Annotated[StrictInt, Field(gt=0, lt=10 ** 12)]
ListName = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
ListUrl = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=600)]


class BlockListAdd(BaseModel):
    name: ListName = Field(description="How the list is called")
    url: ListUrl = Field(description="Where it is downloaded from")


class BlockListRemove(BaseModel):
    id: ListNumber = Field(description="The list")


class BlockListRename(BaseModel):
    id: ListNumber = Field(description="The list")
    name: ListName = Field(description="Its new name")


class BlockListRequest(BaseModel):
    """One change of the block lists by link; each kind of change needs its own fields."""

    op: BlockListOp = Field(description="What to do")
    id: Any = Field(default=None, description="The list, for remove and rename")
    name: Any = Field(default=None, description="The name, for add and rename")
    url: Any = Field(default=None, description="The link, for add")

    @model_validator(mode="after")
    def has_its_fields(self) -> Self:
        self.for_agent()
        return self

    def for_agent(self) -> dict[str, Any]:
        """The request as the agent takes it: the kind of change and only the fields it needs."""
        if self.op is BlockListOp.ADD:
            fields = BlockListAdd(name=self.name, url=self.url).model_dump()
        elif self.op is BlockListOp.REMOVE:
            fields = BlockListRemove(id=self.id).model_dump()
        elif self.op is BlockListOp.RENAME:
            fields = BlockListRename(id=self.id, name=self.name).model_dump()
        else:
            fields = {}
        return {"op": self.op.value, **fields}


# ── smart DNS ────────────────────────────────────────────────────────────────

class DeviceRequest(BaseModel):
    name: Name = Field(description="Name of the new device")


# ── exits ────────────────────────────────────────────────────────────────────

class AddExitRequest(BaseModel):
    # 203.0.113.10;  the password: one line, any characters
    address: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{1,3}(\.[0-9]{1,3}){3}$")] = Field(description="The server's IPv4 address")
    name: ExitName = Field(description="How the exit is called")
    weight: Annotated[StrictInt, Field(ge=1, le=99)] = Field(default=1, description="Its share of the users, percent")
    ssh_port: Annotated[StrictInt, Field(ge=1, le=65535)] = Field(default=22, description="Where its SSH listens")
    password: Annotated[str, StringConstraints(strict=True, max_length=200, pattern=r"^[^\n]*$")] | None = Field(
        default=None, repr=False, description="The root password, used once; handed to the agent beside the command and never logged",
    )

    def for_agent(self) -> dict[str, Any]:
        return {"address": self.address, "name": self.name, "weight": self.weight, "ssh_port": self.ssh_port}


class RenameExitRequest(BaseModel):
    index: ExitIndex = Field(description="The exit")
    name: ExitName = Field(description="Its new name")


# where an exit is: a country's code, or "auto" (found again by its address) — the agent's own pattern
ExitCountryChoice = Annotated[str, StringConstraints(strict=True, pattern=EXIT_COUNTRY_RE)]


class ExitCountryRequest(BaseModel):
    index: ExitIndex = Field(description="The exit")
    country: ExitCountryChoice = Field(description='Where it is: ISO 3166 two-letter code, lower case, or "auto"')


class RemoveExitRequest(BaseModel):
    index: ExitIndex = Field(description="The exit")
    force: Any = Field(default=False, description="Remove even when the agent asked to confirm; only true counts")


class UpdateExitRequest(BaseModel):
    index: ExitIndex = Field(description="The exit")
    reboot: Any = Field(default=False, description="Reboot it after the update; only true counts")


# by the exit's number:   {"1": 75, "3": 25}
Shares = Annotated[
    dict[Annotated[str, StringConstraints(pattern=r"^[0-9]{1,2}$")], Annotated[StrictInt, Field(ge=1, le=100)]],
    Field(min_length=1, max_length=MAX_SHARES),
]


# The owner's app may switch an exit off: a share of 0
OwnerShares = Annotated[
    dict[Annotated[str, StringConstraints(pattern=r"^[0-9]{1,2}$")], Annotated[StrictInt, Field(ge=0, le=100)]],
    Field(min_length=1, max_length=MAX_SHARES),
]


class OwnerWeightsRequest(BaseModel):
    weights: OwnerShares = Field(description="The share of each exit by its index, percent; 0 switches it off")
    base: OwnerShares | None = Field(default=None, description="The weights the app showed")

    def for_agent(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class WeightsRequest(BaseModel):
    weights: Shares = Field(description="The share of each exit by its index, percent")
    base: Shares | None = Field(default=None, description="The weights the page showed")

    def for_agent(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


# ── invitations ──────────────────────────────────────────────────────────────

class InviteRequest(BaseModel):
    group: ShortId | None = Field(default=None, description="The group the invited users join; none is no group")
    uses: Annotated[StrictInt, Field(ge=1, le=10)] = Field(description="How many devices may use the link")
    days: Annotated[StrictInt, Field(ge=1, le=30)] = Field(description="How many days the link works")


class InviteCodeRequest(BaseModel):
    link: str = Field(min_length=1, max_length=300, description="The invitation's link the page shows, https://<the panel's name>/i/<secret>: drawn as a QR code")


class RedeemRequest(BaseModel):
    name: Name = Field(description="How the person calls their device")


# ── the owner's app: people ──────────────────────────────────────────────────

class PeopleParams(BaseModel):
    """GET /app/api/owner/people: the people of one group (or of none), searched by name."""

    model_config = ConfigDict(extra="forbid")

    # a group's id, or "none" for those in no group:   a00001   none
    group: Annotated[str, StringConstraints(pattern=r"^(?:[a-z0-9]{6}|none)$")] | None = Field(default=None, description="Only this group's members; none given: everybody")
    q: Annotated[str, StringConstraints(max_length=64)] = Field(default="", description="Part of a name, any case")
    offset: Annotated[int, Field(ge=0, le=10_000_000)] = Field(default=0, description="How many to skip")
