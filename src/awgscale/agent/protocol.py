"""The socket's protocol: one JSON line in, one JSON line out."""
from __future__ import annotations

from contextvars import ContextVar
from enum import StrEnum
from typing import Any

from awgscale.core.patterns import NAME_RE

from pydantic import BaseModel, Field, SecretStr

from .commands import Command, CommandRequest, parse

MAX_LINE_BYTES = 131072
MAX_SECRET_LENGTH = 200

Reply = dict[str, Any]          # a command's answer, as the panel and the bot read it


class Caller(StrEnum):
    """Who asks; it goes into the journal. Anything but the panel, the bot and the app is the system itself."""

    PANEL = "panel"
    BOT = "bot"
    APP = "app"             # the panel, carrying a change an owner made in the app on their device
    SYSTEM = ""


KNOWN_CALLERS = frozenset({Caller.PANEL.value, Caller.BOT.value, Caller.APP.value})

# The user whose device made the change this command carries (the app's owner), for the journal; None for anybody else.
# Set by the daemon for the one request its task answers.
ASKING_DEVICE: ContextVar[str | None] = ContextVar("asking_device", default=None)


class Refusal(BaseModel):
    ok: bool = Field(default=False, description="Always false")
    error: str = Field(description="Why, in words for whoever asked")

    def reply(self) -> Reply:
        return self.model_dump()


class SocketRequest(BaseModel):
    """A line as it came, before anything in it is believed."""

    args: Any = Field(default=None, description="The command and its arguments, a list of strings")
    by: Any = Field(default=None, description='Who asks: "panel", "bot" or "app"')
    device: Any = Field(default=None, description='With "app": the user whose device made the change, as the journal names it')
    secret: Any = Field(default=None, description="The root password of a new exit server: beside the arguments (those are journaled), one command only")

    def command(self) -> CommandRequest | None:
        return parse(self.args)

    def caller(self) -> Caller:
        return Caller(self.by) if self.by in KNOWN_CALLERS else Caller.SYSTEM

    def device_name(self) -> str | None:
        """The user named as the device behind an app's change; anything that is not a user's name is nobody."""
        fits = self.caller() is Caller.APP and isinstance(self.device, str) and NAME_RE.fullmatch(self.device)
        return self.device if fits else None

    def secret_for(self, command: CommandRequest) -> SecretStr | None:
        fits = isinstance(self.secret, str) and 1 <= len(self.secret) <= MAX_SECRET_LENGTH
        return SecretStr(self.secret) if command.command is Command.EXIT_ADD and fits else None
