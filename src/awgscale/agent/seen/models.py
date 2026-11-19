"""What was seen last, and what a look at the state sees now."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from awgscale.watch.health import ExitState

from ..journal.models import Kind
from ..store.documents import Record

NOTES_VERSION = 1
COMPARED = ("clients", "exits", "leaving")       # what of the notes says something; the moment of the look does not
STATE_AT = 0                                     # an exit's stored note is a list: its state, its name, since when
NAME_AT = 1
SINCE_AT = 2


def moment(seconds: object) -> datetime | None:
    """A stored moment (whole Unix seconds); None for what is not one, and for "never" (0)."""
    if type(seconds) is not int or not seconds:
        return None
    return datetime.fromtimestamp(seconds, UTC)


def seconds_of(when: datetime) -> int:
    return int(when.timestamp())


class ExitSeen(BaseModel):
    """An exit as a look at the state sees it."""

    model_config = ConfigDict(frozen=True)

    index: object = Field(description="The exit's number")
    name: object = Field(description="Its name")
    state: ExitState = Field(description="up, down, or unknown: just booted, a half-written file")


class ExitNote(BaseModel):
    """What was last noted about an exit."""

    model_config = ConfigDict(frozen=True)

    state: object = Field(description="The state it was last seen in")
    name: object = Field(description="Its name then")
    since: datetime | None = Field(default=None, description="Since when it is in that state; None when that was not noted")
    stored: tuple[Any, ...] = Field(description="The note as it is stored")

    @classmethod
    def of(cls, stored: object) -> ExitNote | None:
        """A stored note; None for what is not one."""
        if not isinstance(stored, (list, tuple)) or not stored:
            return None
        name = stored[NAME_AT] if len(stored) > NAME_AT else None
        since = moment(stored[SINCE_AT]) if len(stored) > SINCE_AT else None
        return cls(state=stored[STATE_AT], name=name, since=since, stored=tuple(stored))

    @classmethod
    def new(cls, state: ExitState, name: object, since: datetime) -> ExitNote:
        return cls(state=state.value, name=name, since=since, stored=(state.value, name, seconds_of(since)))

    def since_or(self, otherwise: datetime) -> datetime:
        return otherwise if self.since is None else self.since

    def record(self) -> list[Any]:
        return list(self.stored)


class Event(BaseModel):
    """Something that happened by itself, for the journal."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(description="What happened, in words for the admin")
    bad: bool = Field(default=False, description="Bad news")
    kind: Kind = Field(description="A term that ran out, or an exit")


class Notes(BaseModel):
    """What was seen last."""

    model_config = ConfigDict(frozen=True)

    at: datetime | None = Field(default=None, description="When the look that left these notes was taken")
    clients: dict[str, datetime | None] | None = Field(default=None, description="The users and when each one's term ends; None while nothing was ever seen")
    exits: dict[str, ExitNote] = Field(default_factory=dict, description="The exits by number")
    leaving: dict[str, datetime] = Field(default_factory=dict, description="Users the agent itself is removing, and until when their going is its own doing")

    @classmethod
    def of(cls, record: Mapping[str, Any]) -> Notes:
        """The stored notes, read the forgiving way they always were."""
        clients = record.get("clients")
        exits = record.get("exits")
        leaving = record.get("leaving")
        notes = {str(index): ExitNote.of(stored) for index, stored in exits.items()} if isinstance(exits, dict) else {}
        return cls(
            at=moment(record.get("at")),
            clients={str(name): moment(expires) for name, expires in clients.items()} if isinstance(clients, dict) else None,
            exits={index: note for index, note in notes.items() if note is not None},
            leaving={str(name): until for name, until in ((name, moment(stored)) for name, stored in leaving.items()) if until is not None} if isinstance(leaving, dict) else {},
        )

    def record(self) -> Record:
        """The notes as they are stored: moments as whole Unix seconds."""
        assert self.at is not None
        return {
            "v": NOTES_VERSION,
            "at": seconds_of(self.at),
            "clients": {name: None if expires is None else seconds_of(expires) for name, expires in (self.clients or {}).items()},
            "exits": {index: note.record() for index, note in self.exits.items()},
            "leaving": {name: seconds_of(until) for name, until in self.leaving.items()},
        }


def says_the_same(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Do two stored notes say the same about the users, the exits and who is leaving."""
    return {key: first.get(key) for key in COMPARED} == {key: second.get(key) for key in COMPARED}
