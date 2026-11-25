"""A line of the journal."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_TEXT_LENGTH = 500


class Kind(StrEnum):
    CHANGE = "change"       # somebody changed something through the agent
    REFUSED = "refused"     # a change made on top of an older list was turned down
    TERM = "term"           # a user went without anybody asking: the term ran out
    EXIT = "exit"           # an exit went down or came back
    INVITE = "invite"       # a guest came with an invitation link


class Who(StrEnum):
    PANEL = "panel"
    BOT = "bot"
    SYSTEM = "system"       # timers, the watch service, a term that ran out
    INVITE = "invite"       # a guest with an invitation link
    APP = "app"             # an owner, in the app on their device


class Entry(BaseModel):
    """What is written: when, what, whether it is bad news, of which kind, by whom."""

    at: datetime = Field(description="When it happened")
    text: str = Field(max_length=MAX_TEXT_LENGTH, description="What happened, in words for the admin")
    bad: bool = Field(default=False, description="Bad news: shown in red")
    kind: Kind = Field(default=Kind.CHANGE, description="What sort of event")
    who: Who = Field(default=Who.SYSTEM, description="Who did it")
    device: str | None = Field(default=None, description="With the app: the user whose device it was")

    def line(self) -> str:
        """The entry as the journal holds it: one JSON line, the moment as Unix seconds."""
        wire: dict[str, Any] = {"t": int(self.at.timestamp()), "text": self.text, "bad": self.bad, "kind": self.kind.value, "who": self.who.value}
        if self.device is not None:
            wire["device"] = self.device
        return json.dumps(wire, ensure_ascii=False)


class StoredEntry(BaseModel):
    """A line as it comes back from Loki. Lines are read as they were written,
    by whatever version wrote them: nothing in them is required, nothing is dropped."""

    model_config = ConfigDict(extra="allow")

    t: int | float | None = Field(default=None, description="When it happened, Unix seconds")

    @property
    def at(self) -> datetime | None:
        return None if self.t is None else datetime.fromtimestamp(self.t, UTC)

    def wire(self) -> dict[str, Any]:
        """As the panel and the bot read it: exactly what was written."""
        return self.model_dump(exclude_unset=True)
