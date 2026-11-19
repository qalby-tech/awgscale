"""The socket's protocol: one JSON line in, one JSON line out."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, SecretStr

from .commands import Command, CommandRequest, parse

MAX_LINE_BYTES = 131072
MAX_SECRET_LENGTH = 200

Reply = dict[str, Any]          # a command's answer, as the panel and the bot read it


class Caller(StrEnum):
    """Who asks; it goes into the journal. Anything but the panel and the bot is the system itself."""

    PANEL = "panel"
    BOT = "bot"
    SYSTEM = ""


class Refusal(BaseModel):
    ok: bool = Field(default=False, description="Always false")
    error: str = Field(description="Why, in words for whoever asked")

    def reply(self) -> Reply:
        return self.model_dump()


class SocketRequest(BaseModel):
    """A line as it came, before anything in it is believed."""

    args: Any = Field(default=None, description="The command and its arguments, a list of strings")
    by: Any = Field(default=None, description='Who asks: "panel" or "bot"')
    secret: Any = Field(default=None, description="The root password of a new exit server: beside the arguments (those are journaled), one command only")

    def command(self) -> CommandRequest | None:
        return parse(self.args)

    def caller(self) -> Caller:
        return Caller(self.by) if self.by in (Caller.PANEL.value, Caller.BOT.value) else Caller.SYSTEM

    def secret_for(self, command: CommandRequest) -> SecretStr | None:
        fits = isinstance(self.secret, str) and 1 <= len(self.secret) <= MAX_SECRET_LENGTH
        return SecretStr(self.secret) if command.command is Command.EXIT_ADD and fits else None
