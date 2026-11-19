"""The spaces as stored, and the answer of the commands about them."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..store.documents import Record
from ..users.models import version_of

MAX_SPACES = 30
MAX_MEMBERS = 64
MAX_NAME_LENGTH = 40


class Space(BaseModel):
    """A space as stored: a name, whether it is switched on, and who is in it."""

    model_config = ConfigDict(frozen=True)

    id: Any = Field(default=None, description="The space's own id")
    name: Any = Field(default=None, description="Its name")
    on: bool = Field(default=False, description="Switched on: its members reach each other")
    members: tuple[Any, ...] = Field(default=(), description="Names of the users in it")

    @classmethod
    def of(cls, record: Record) -> Space:
        """A stored record, read the forgiving way it always was."""
        members = record.get("members", [])
        return cls(id=record.get("id"), name=record.get("name"), on=bool(record.get("on")), members=tuple(members) if isinstance(members, list) else ())

    def record(self) -> Record:
        return {"id": self.id, "name": self.name, "on": self.on, "members": list(self.members)}


class StoredSpaces(BaseModel):
    """The spaces as the store holds them now."""

    model_config = ConfigDict(frozen=True)

    document: tuple[Record, ...] = Field(description="The records as stored: the fingerprint is taken of these")

    @classmethod
    def of(cls, document: object) -> StoredSpaces:
        records = [record for record in document if isinstance(record, dict)] if isinstance(document, list) else []
        return cls(document=tuple(records))

    @property
    def version(self) -> str:
        return version_of(list(self.document))

    @property
    def spaces(self) -> tuple[Space, ...]:
        return tuple(Space.of(record) for record in self.document)


class SpacesShown(BaseModel):
    """The answer of spaces-get, and of a change of the spaces."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    limit: int = Field(default=MAX_SPACES, description="How many spaces there may be")
    v: str = Field(description="The fingerprint of the stored list")
    spaces: list[dict[str, Any]] = Field(description="The spaces, each with the members that still exist")

    @classmethod
    def of(cls, stored: StoredSpaces, known: set[str]) -> SpacesShown:
        shown = [{**record, "members": [member for member in record.get("members", []) if member in known]} for record in stored.document]
        return cls(v=stored.version, spaces=shown)

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class SpacesApplied(BaseModel):
    """The answer of spaces-sync."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()
