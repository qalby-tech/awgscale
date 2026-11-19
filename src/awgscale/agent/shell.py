"""The commands only a shell may run: the deploy, a timer, the admin by hand.

    python -m awgscale.agent store-init [--fresh]     move the settings into the database (deploy)
    python -m awgscale.agent store-export             everything the store holds, in the shape of the old files
    python -m awgscale.agent cascade-get [--conf]     the cascade's settings (or the file compiled from them)
    python -m awgscale.agent cascade-set <base64>     change them whole
    python -m awgscale.agent spaces-sync              recompile and reload the spaces
    python -m awgscale.agent suspend-sync             make the kernel match the stored pauses

They are not commands of the socket: the panel's container is the less trusted
side and must not reach them. A command of the socket may be run from a shell
as well. Either way the answer is one JSON line and the exit code says whether
it is a yes.
"""
from __future__ import annotations

import json
from enum import StrEnum
from typing import Literal, Mapping, Sequence

from pydantic import Field, ValidationError

from .commands import CommandRequest, NoArguments, parse
from .dispatcher import Dispatcher
from .protocol import Caller, Refusal, Reply

BY_VARIABLE = "VPN_BY"          # who asks, when a service runs a command through a shell


class ShellCommand(StrEnum):
    STORE_INIT = "store-init"
    STORE_EXPORT = "store-export"
    CASCADE_GET = "cascade-get"
    CASCADE_SET = "cascade-set"
    SPACES_SYNC = "spaces-sync"
    SUSPEND_SYNC = "suspend-sync"


class StoreInit(CommandRequest):
    command = ShellCommand.STORE_INIT

    fresh: Literal["--fresh"] | None = Field(default=None, description="A new server: there are no files to move, the store starts empty")


class StoreExport(NoArguments):
    command = ShellCommand.STORE_EXPORT


class CascadeGet(CommandRequest):
    command = ShellCommand.CASCADE_GET

    conf: Literal["--conf"] | None = Field(default=None, description="Print the file compiled from the settings instead of the settings")


class CascadeSet(CommandRequest):
    command = ShellCommand.CASCADE_SET

    payload: str = Field(description="The cascade's settings, whole; base64 of JSON")


class SpacesSync(NoArguments):
    command = ShellCommand.SPACES_SYNC


class SuspendSync(NoArguments):
    command = ShellCommand.SUSPEND_SYNC


SHELL_REQUESTS: dict[ShellCommand, type[CommandRequest]] = {model.command: model for model in (StoreInit, StoreExport, CascadeGet, CascadeSet, SpacesSync, SuspendSync)}      # type: ignore[misc]


def parse_shell(arguments: Sequence[str]) -> CommandRequest | None:
    """→ the request of a shell-only command; None for anything else."""
    try:
        model = SHELL_REQUESTS[ShellCommand(arguments[0])]
        return model.from_args(arguments[1:])
    except (IndexError, ValueError, ValidationError):
        return None


def caller_of(environment: Mapping[str, str]) -> Caller:
    """Who asks, as the journal will say it: whoever the environment names, the system itself otherwise."""
    named = environment.get(BY_VARIABLE)
    return Caller(named) if named in (Caller.PANEL.value, Caller.BOT.value) else Caller.SYSTEM


class Shell:
    def __init__(self, socket_commands: Dispatcher, shell_commands: Dispatcher) -> None:
        self.__socket_commands = socket_commands
        self.__shell_commands = shell_commands

    async def answer(self, arguments: Sequence[str], caller: Caller) -> tuple[CommandRequest | None, Reply]:
        """→ the request it was taken for (None: for none), and its answer."""
        request = parse_shell(arguments)
        if request is not None:
            return request, await self.__shell_commands.run(request, caller)
        request = parse(list(arguments))
        if request is None:
            return None, Refusal(error="unknown command").reply()
        return request, await self.__socket_commands.run(request, caller)

    async def run(self, arguments: Sequence[str], caller: Caller) -> tuple[str, int]:
        """→ what to print, and the exit code: 0 for a yes."""
        request, reply = await self.answer(arguments, caller)
        code = 0 if reply.get("ok") else 1
        if isinstance(request, CascadeGet) and request.conf and reply.get("ok"):
            return str(reply.get("conf", "")), code              # the file itself, as it would be written
        return json.dumps(reply, ensure_ascii=False) + "\n", code
