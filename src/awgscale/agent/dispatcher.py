"""One place every command goes through: found by its name, run by its handler, counted while it runs."""
from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import Awaitable, Callable, Collection, Sequence

from pydantic import SecretStr

from .commands import CommandRequest, parse
from .protocol import Caller, Refusal, Reply

log = logging.getLogger("awgscale.agent")

Handler = Callable[[CommandRequest, Caller, SecretStr | None], Awaitable[Reply]]


class Dispatcher:
    def __init__(self) -> None:
        self.__handlers: dict[StrEnum, Handler] = {}
        self.__running = 0
        self.__stopping = False
        self.__idle = asyncio.Event()
        self.__idle.set()
        self.__after_change: Callable[[], None] | None = None
        self.__reading: frozenset[StrEnum] = frozenset()

    def register(self, command: StrEnum, handler: Handler) -> None:
        self.__handlers[command] = handler

    def after_change(self, hook: Callable[[], None], reading: Collection[StrEnum]) -> None:
        """`hook` is called after every command but those that only read: whatever was answered from an earlier look is looked at again."""
        self.__after_change = hook
        self.__reading = frozenset(reading)

    @property
    def commands(self) -> frozenset[StrEnum]:
        return frozenset(self.__handlers)

    @property
    def running(self) -> int:
        """How many commands are being carried out now."""
        return self.__running

    @property
    def stopping(self) -> bool:
        return self.__stopping

    async def run(self, request: CommandRequest, caller: Caller = Caller.SYSTEM, secret: SecretStr | None = None) -> Reply:
        """One command, counted while it runs. Once stopping, nothing new is begun."""
        if self.__stopping:
            return Refusal(error="агент перезапускается, повторите через несколько секунд").reply()
        handler = self.__handlers.get(request.command)
        if handler is None:
            return Refusal(error="bad request").reply()
        self.__running += 1
        self.__idle.clear()
        try:
            return await handler(request, caller, secret)
        finally:
            if self.__after_change is not None and request.command not in self.__reading:
                self.__after_change()
            self.__running -= 1
            if not self.__running:
                self.__idle.set()

    async def dispatch(self, arguments: Sequence[str], caller: Caller = Caller.SYSTEM, secret: SecretStr | None = None) -> Reply:
        """A request as a list of strings → its answer; what is not a command of this agent is refused."""
        request = parse(list(arguments))
        if request is None:
            return Refusal(error="bad request").reply()
        return await self.run(request, caller, secret)

    async def stop(self, wait_seconds: float) -> int:
        """Takes no new commands and lets the running ones finish: a command cut in
        the middle could leave the resolver stopped. → how many were still running when the wait ended."""
        self.__stopping = True
        try:
            await asyncio.wait_for(self.__idle.wait(), wait_seconds)
        except TimeoutError:
            pass
        return self.__running
