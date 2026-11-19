"""The users, the requests about them and the answers, as objects."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum
from ipaddress import IPv4Address
from typing import Annotated, Any, Mapping

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, SecretStr, SerializerFunctionWrapHandler, Strict, model_serializer

from awgscale.core.patterns import KEY_RE, NAME_RE


# a term:  12h   7d   4w
TERM_RE = re.compile(
    r"""
    ^(?P<count>[1-9][0-9]{0,2})     # 12
    (?P<unit>[hdw])\Z               # h, d or w
    """,
    re.VERBOSE,
)

MAX_NAMES_PER_REQUEST = 60


def _user_name(value: str) -> str:
    if not NAME_RE.match(value):
        raise ValueError("имя: латиница, цифры, _ и -, до 32 символов")
    return value


def _key(value: str) -> str:
    if not KEY_RE.match(value):
        raise ValueError("not a key")
    return value


UserName = Annotated[str, Strict(), AfterValidator(_user_name)]
WgKey = Annotated[str, Strict(), AfterValidator(_key)]


# ── terms ────────────────────────────────────────────────────────────────────

class TermUnit(StrEnum):
    HOUR = "h"
    DAY = "d"
    WEEK = "w"

    @property
    def seconds(self) -> int:
        return _UNIT_SECONDS[self]


_UNIT_SECONDS: Mapping[TermUnit, int] = {TermUnit.HOUR: 3600, TermUnit.DAY: 86400, TermUnit.WEEK: 604800}
NO_TERM = (None, "", "none")


class Term(BaseModel):
    """For how long access is given: "12h", "7d", "4w"."""

    model_config = ConfigDict(frozen=True)

    count: int = Field(ge=1, le=999, description="How many units")
    unit: TermUnit = Field(description="Hours, days or weeks")

    @property
    def duration(self) -> timedelta:
        return timedelta(seconds=self.count * self.unit.seconds)

    @classmethod
    def from_wire(cls, raw: object) -> Term | None:
        """The term a caller wrote; None, "" and "none" are no term."""
        if isinstance(raw, Term) or raw in NO_TERM:
            return raw if isinstance(raw, Term) else None
        match = TERM_RE.match(str(raw))
        if not match:
            raise ValueError("срок: например 12h, 7d, 4w")
        return cls(count=int(match["count"]), unit=TermUnit(match["unit"]))


Expires = Annotated[Term | None, BeforeValidator(Term.from_wire)]


# ── users ────────────────────────────────────────────────────────────────────

class Peer(BaseModel):
    """A user as the tunnel's file holds one."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Name of the user")
    ip: IPv4Address = Field(description="Address of the user inside the tunnel")
    public_key: WgKey = Field(description="Public key of the user's device")
    preshared_key: SecretStr = Field(description="Preshared key of the peer; never logged or shown in repr")


class User(Peer):
    """A user as the database holds one."""

    private_key: SecretStr = Field(description="Private key of the device; never logged or shown in repr")
    expires_at: AwareDatetime | None = Field(default=None, description="When the access ends; None is no term")
    created_at: AwareDatetime = Field(description="When the user was made")


class UserView(BaseModel):
    """What callers are told about a user: never a key."""

    name: str = Field(description="Name of the user")
    ip: IPv4Address = Field(description="Address of the user inside the tunnel")
    expires_at: datetime | None = Field(description="When the access ends; None is no term")
    created_at: datetime = Field(description="When the user was made")

    @classmethod
    def of(cls, user: User) -> UserView:
        return cls(name=user.name, ip=user.ip, expires_at=user.expires_at, created_at=user.created_at)


# ── requests ─────────────────────────────────────────────────────────────────

class AddRequest(BaseModel):
    name: UserName = Field(description="Name of the new user: latin letters, digits, _ and -, up to 32")
    expires: Expires = Field(default=None, description='Term of access counted from now ("12h", "7d", "4w"); None is no term')


class RemoveRequest(BaseModel):
    names: list[UserName] = Field(min_length=1, max_length=MAX_NAMES_PER_REQUEST, description="Whom to remove; each name is answered for separately")


class ExpiryRequest(BaseModel):
    expires: Expires = Field(default=None, description="The new term counted from now; None is no term")
    names: list[UserName] = Field(min_length=1, description="Whose term changes; all of them or nobody")


# ── answers ──────────────────────────────────────────────────────────────────

class Status(StrEnum):
    CREATED = "created"
    REMOVED = "removed"
    NOT_FOUND = "not_found"
    FAILED = "failed"


class RemovalReason(StrEnum):
    """Why a user is removed; written to the log."""

    REMOVED = "removed"
    TERM_OVER = "term over"


class Created(BaseModel):
    name: str = Field(description="Name of the new user")
    status: Status = Field(default=Status.CREATED, description="Always created")
    ip: IPv4Address = Field(description="The address the user was given")
    expires_at: datetime | None = Field(description="When the access ends; None is no term")
    warning: str | None = Field(default=None, description="Set when the user works but the boot file could not be written")

    @model_serializer(mode="wrap")
    def _without_empty_warning(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """As callers get it: the term always, the warning only when there is one."""
        shape: dict[str, Any] = handler(self)
        if not self.warning:
            shape.pop("warning", None)
        return shape

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Removal(BaseModel):
    name: str = Field(description="The name as it was asked for")
    status: Status = Field(description="removed, not_found or failed")
    error: str | None = Field(default=None, description="Why it failed")

    @model_serializer(mode="wrap")
    def _without_empty_error(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """The error only when it failed."""
        shape: dict[str, Any] = handler(self)
        if self.error is None:
            shape.pop("error", None)
        return shape

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class DevicePayload(BaseModel):
    """What a device needs to connect."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    name: str = Field(description="Name of the user")
    conf: str = Field(description="The config for the AmneziaWG app")
    uri: str = Field(description="The vpn:// string for the AmneziaVPN app")
    conf_png: str = Field(description="QR picture of the config, base64 PNG")
    uri_png: str = Field(description="QR picture for the AmneziaVPN app's scanner, base64 PNG")


class UsersAnswer(BaseModel):
    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    users: list[UserView] = Field(description="Every user, in the order they were made")


class CreatedAnswer(BaseModel):
    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    results: list[Created] = Field(description="The one user that was made")


class RemovalsAnswer(BaseModel):
    ok: bool = Field(description="True when every one was removed")
    results: list[Removal] = Field(description="What became of each name")


class ExpiryAnswer(BaseModel):
    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    names: list[str] = Field(description="Whose term changed")
    expires_at: datetime | None = Field(description="When the access ends now; None is no term")


class Health(BaseModel):
    ok: bool = Field(default=True, description="The database and the kernel both answered")
    users: int = Field(description="Users in the database")
    peers: int = Field(description="Peers in the kernel")
