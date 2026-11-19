"""The users and their groups as the agent sees them, and the answers of the commands about them."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..store.documents import Document, Record

MAX_USER_GROUPS = 30
VERSION_LENGTH = 12


class GroupFilter(StrEnum):
    """How a group's requests are filtered. `OFF` is kept but no longer offered.
    The levels (`PRO`, `PROPLUS`, `ULTIMATE`) are the usual filtering plus one of
    HaGeZi's lists for their members only."""

    STANDARD = "std"
    OFF = "off"
    KIDS = "kids"
    PRO = "pro"
    PROPLUS = "proplus"
    ULTIMATE = "ultimate"


LEVELS = (GroupFilter.PRO, GroupFilter.PROPLUS, GroupFilter.ULTIMATE)


class KidsList(StrEnum):
    """HaGeZi's add-on lists the admin may switch on for a group in the children's mode."""

    NSFW = "nsfw"
    GAMBLING = "gambling"
    BYPASS = "bypass"
    SOCIAL = "social"


DEFAULT_KIDS_LISTS = (KidsList.NSFW, KidsList.GAMBLING, KidsList.BYPASS)      # a group just switched to the children's mode


def kids_lists_of(value: object) -> tuple[str, ...] | None:
    """A list of the add-on lists → those known, once each, in their own order; None for what is not a list."""
    if not isinstance(value, list):
        return None
    return tuple(kids_list.value for kids_list in KidsList if kids_list.value in value)


class KnownUser(BaseModel):
    """A user as the users service lists one — or, while it cannot be asked, as last known."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Name of the user")
    ip: str = Field(default="", description="Address inside the tunnel")
    expires_at: datetime | None = Field(default=None, description="When the access ends; None is no term")
    created_at: datetime | None = Field(default=None, description="When the user was made; None when only the tunnel's file knows them")


class UserGroup(BaseModel):
    """A group as stored: the admin's own order among the users, each user in one group at most."""

    model_config = ConfigDict(frozen=True)

    id: str | None = Field(default=None, description="The group's own id")
    name: str | None = Field(default=None, description="Its name")
    space: bool = Field(default=False, description="The group is also a device space of exactly its members")
    exit: Any = Field(default=None, description="The exit its members are kept on; None is as the shares decide")
    filter: str = Field(default=GroupFilter.STANDARD.value, description="How its members' requests are filtered")
    kids_lists: tuple[str, ...] | None = Field(default=None, description="In the children's mode: HaGeZi's add-on lists switched on (KidsList values); None for a record from before them, which has none")
    members: tuple[str, ...] = Field(default=(), description="Names of the users in it")

    @classmethod
    def of(cls, record: Record) -> UserGroup:
        """A stored record, read the forgiving way it always was."""
        members = record.get("members", [])
        return cls(
            id=record.get("id"),
            name=record.get("name"),
            space=bool(record.get("space")),
            exit=record.get("exit"),
            filter=record.get("filter") or GroupFilter.STANDARD.value,
            kids_lists=kids_lists_of(record.get("kids_lists")),
            members=tuple(member for member in members if isinstance(member, str)) if isinstance(members, list) else (),
        )

    def shown(self, known: set[str]) -> dict[str, Any]:
        """As the page gets it: without the members that are no more; a group in the children's mode with its add-on lists, when it has them."""
        shown: dict[str, Any] = {"id": self.id, "name": self.name, "space": self.space, "exit": self.exit, "filter": self.filter}
        if self.filter == GroupFilter.KIDS.value and self.kids_lists is not None:      # a record from before them is shown as it always was
            shown["kids_lists"] = list(self.kids_lists)
        shown["members"] = [m for m in self.members if m in known]
        return shown


def version_of(document: Document) -> str:
    """A short fingerprint of a stored list (the groups or the spaces). A page
    says which one its change is based on, and a change based on an older one is
    refused — otherwise a tab left open elsewhere would silently undo what was
    done since it was loaded."""
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:VERSION_LENGTH]


class StoredGroups(BaseModel):
    """The groups as the store holds them now."""

    model_config = ConfigDict(frozen=True)

    document: tuple[Record, ...] = Field(description="The records as stored: the fingerprint is taken of these")
    groups: tuple[UserGroup, ...] = Field(description="The same, typed")

    @classmethod
    def of(cls, document: object) -> StoredGroups:
        records = [record for record in document if isinstance(record, dict)] if isinstance(document, list) else []
        return cls(document=tuple(records), groups=tuple(UserGroup.of(record) for record in records))

    @property
    def version(self) -> str:
        return version_of(list(self.document))


class GroupsShown(BaseModel):
    """The answer of ugroups-get, and of a change of the groups."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    limit: int = Field(default=MAX_USER_GROUPS, description="How many groups there may be")
    v: str = Field(description="The fingerprint of the stored list")
    groups: list[dict[str, Any]] = Field(description="The groups, each with the members that still exist")

    @classmethod
    def of(cls, stored: StoredGroups, known: set[str]) -> GroupsShown:
        return cls(v=stored.version, groups=[group.shown(known) for group in stored.groups])

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class TermSet(BaseModel):
    """The answer of expire."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    names: list[str] = Field(description="Whose term changed")
    expires: str | None = Field(description='The term that was asked for, like "7d"; None is no term')

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class DeviceCode(BaseModel):
    """The answer of qr: what the users service handed out, as it is."""

    payload: dict[str, Any] = Field(description="The config, the vpn:// string and both QR pictures")

    def wire(self) -> dict[str, Any]:
        return dict(self.payload)


class Paused(BaseModel):
    """The answer of suspend."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    mode: str = Field(description='"on": paused, "off": let through again')
    names: list[str] = Field(description="Whom it was asked for")
    paused: list[str] = Field(description="Everybody who is paused now")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class PausedNow(BaseModel):
    """The answer of suspend-sync."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    paused: list[str] = Field(description="Everybody who is paused now")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class ExitChosen(BaseModel):
    """The answer of user-exit."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    names: list[str] = Field(description="Whose exit was chosen")
    exit: int | None = Field(description="The exit's number; None is as the group or the shares decide")
    exit_name: str | None = Field(description="How that exit is called")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


# ── a user made, users removed ───────────────────────────────────────────────

class Made(BaseModel):
    """What the users service said to "make this user"."""

    model_config = ConfigDict(frozen=True)

    ok: bool = Field(description="The user was made")
    error: object = Field(default=None, description="Why not, in the service's words")
    answer: dict[str, Any] = Field(description="The service's answer as the page reads it: the term as Unix seconds")


class RemovedOne(BaseModel):
    """What became of one name that was to be removed."""

    model_config = ConfigDict(frozen=True)

    name: object = Field(default=None, description="The name as it was asked for")
    status: object = Field(default=None, description="removed, not_found or failed")
    error: object = Field(default=None, description="Why it failed")


class Removed(BaseModel):
    """What the users service said to "remove these users"."""

    model_config = ConfigDict(frozen=True)

    results: tuple[RemovedOne, ...] = Field(default=(), description="What became of each name")
    answer: dict[str, Any] = Field(description="The service's answer as the page reads it")


class UserAdded(BaseModel):
    """The answer of add."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    result: dict[str, Any] = Field(description="What the users service answered")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class UsersRemoved(BaseModel):
    """The answer of remove."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    removed: list[Any] = Field(description="Who was removed")
    result: dict[str, Any] = Field(description="What the users service answered")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


# ── a user's check ───────────────────────────────────────────────────────────

class FindingState(StrEnum):
    OK = "ok"
    WARN = "warn"
    BAD = "bad"


class Finding(BaseModel):
    """One thing the entry server knows about a user."""

    model_config = ConfigDict(frozen=True)

    state: FindingState = Field(description="Good, worth a look, or the reason it does not work")
    text: str = Field(description="The finding, in words for the admin")


class Diagnosis(BaseModel):
    """The answer of diag."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    name: str = Field(description="Whom it is about")
    endpoint: str | None = Field(default=None, description="The address the device connects from, without the port")
    with_endpoint: bool = Field(default=False, description="The tunnel knows the user's key: the answer says where the device connects from")
    items: list[Finding] = Field(description="The findings, in the order they were made")

    def wire(self) -> dict[str, Any]:
        """Without the address when the tunnel has no key for the user."""
        shown: dict[str, Any] = {"ok": self.ok, "name": self.name}
        if self.with_endpoint:
            shown["endpoint"] = self.endpoint
        shown["items"] = [{"state": finding.state.value, "text": finding.text} for finding in self.items]
        return shown
