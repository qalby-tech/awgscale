"""The invitations as stored, and the answers of the commands about them."""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Collection

from pydantic import BaseModel, ConfigDict, Field

from ..store.documents import Record

MAX_INVITES = 40
MAX_CLIENTS = 240                       # the tunnel's /24 holds 253
MIN_USES = 1
MAX_USES = 10
MIN_DAYS = 1
MAX_DAYS = 30
TOKEN_BYTES = 16                        # 128 random bits
HASH_PREFIX = "invite:"


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_of(token: str) -> str:
    """What is stored of a link's secret: a fingerprint nobody can turn back into the link."""
    return hashlib.sha256((HASH_PREFIX + token).encode()).hexdigest()


def moment_of(seconds: object) -> datetime | None:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    return datetime.fromtimestamp(seconds, UTC)


class Invitation(BaseModel):
    """An invitation. The store holds its moments as Unix seconds; here they are datetime.
    The record it was read from is kept, so that writing it back changes only what was changed."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="The invitation's own id")
    hash: str = Field(default="", description="The fingerprint of the link's secret")
    group: Any = Field(default=None, description="The id of the group a guest joins; None is no group")
    left: int = Field(default=0, description="How many more devices may use it")
    total: int = Field(default=1, description="For how many devices it was made")
    expires_at: datetime = Field(description="Until when it works")
    created: datetime | None = Field(default=None, description="When it was made")
    used: tuple[str, ...] = Field(default=(), description="Names of the users made with it")
    stored: Record = Field(default_factory=dict, repr=False, description="The record as the store held it")

    @classmethod
    def of(cls, record: Record) -> Invitation | None:
        """A stored record; None for one that is not an invitation."""
        expires_at = moment_of(record.get("expires_at", 0))
        if expires_at is None or not isinstance(record.get("id"), str):
            return None
        used = record.get("used", [])
        return cls(
            id=record["id"],
            hash=str(record.get("hash", "")),
            group=record.get("group"),
            left=record.get("left", 0),
            total=record.get("total", 1),
            expires_at=expires_at,
            created=moment_of(record.get("created")),
            used=tuple(used) if isinstance(used, list) else (),
            stored=dict(record),
        )

    @classmethod
    def new(cls, invite_id: str, token: str, group: str | None, uses: int, now: datetime, days: int) -> Invitation:
        return cls(id=invite_id, hash=hash_of(token), group=group, left=uses, total=uses, expires_at=now + timedelta(days=days), created=now)

    def record(self) -> Record:
        """As the store holds it."""
        if self.stored:
            return {**self.stored, "left": self.left, "used": list(self.used)}
        return {
            "id": self.id,
            "hash": self.hash,
            "group": self.group,
            "left": self.left,
            "total": self.total,
            "expires_at": int(self.expires_at.timestamp()),
            "created": None if self.created is None else int(self.created.timestamp()),
            "used": list(self.used),
        }

    def opens_with(self, token: str) -> bool:
        return secrets.compare_digest(self.hash, hash_of(token))

    def usable_in(self, group_ids: Collection[object]) -> bool:
        """Does a link still work for it: uses left, and the group it was made for still there."""
        return self.left > 0 and (self.group is None or self.group in group_ids)

    def taken_by(self, name: str) -> Invitation:
        """One use less, and whose it was."""
        return self.model_copy(update={"left": self.left - 1, "used": (*self.used, name)})

    def given_back_by(self, name: str) -> Invitation:
        """The use of somebody who was not made after all comes back."""
        if name not in self.used:
            return self
        remaining = list(self.used)
        remaining.remove(name)
        return self.model_copy(update={"left": min(self.total, self.left + 1), "used": tuple(remaining)})

    def shown(self, group_name: object) -> dict[str, Any]:
        """As the admin's page gets it: never the fingerprint."""
        record = self.record()
        return {
            "id": self.id,
            "group": self.group,
            "group_name": group_name,
            "left": self.left,
            "total": self.total,
            "expires_at": record["expires_at"],
            "created": record.get("created"),
            "used": list(self.used),
        }


class InvitesShown(BaseModel):
    """The answer of invite-list."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    limit: int = Field(default=MAX_INVITES, description="How many invitations may work at once")
    invites: list[dict[str, Any]] = Field(description="The invitations a link still works for")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class InviteMade(BaseModel):
    """The answer of invite-add: the only time the link's secret is ever shown."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    id: str = Field(description="The new invitation's id")
    token: str = Field(description="The secret of the link")
    invites: list[dict[str, Any]] = Field(description="The invitations a link still works for")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class InviteRevoked(BaseModel):
    """The answer of invite-remove."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    invites: list[dict[str, Any]] = Field(description="The invitations a link still works for")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class InviteInfoAnswer(BaseModel):
    """The answer of invite-info, for the public page: is the link good, and for what. Says nothing else."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    group: Any = Field(description="The name of the group a guest joins; None is no group")
    left: int = Field(description="How many more devices may use the link")
    total: int = Field(description="For how many devices it was made")
    expires_at: Any = Field(description="Until when it works, Unix seconds")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class Redeemed(BaseModel):
    """The answer of invite-redeem: what the guest's device needs to connect."""

    payload: dict[str, Any] = Field(description="The config, the vpn:// string and both QR pictures, as the users service handed them out")
    left: int = Field(description="How many more devices may use the link")
    group: Any = Field(description="The name of the group the guest joined; None when none")

    def wire(self) -> dict[str, Any]:
        return dict(self.payload, left=self.left, group=self.group)
