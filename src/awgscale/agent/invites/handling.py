"""An invitation command that writes its own line into the journal needs to know who asked."""
from __future__ import annotations

from typing import Awaitable, Callable, Generic, TypeVar

from pydantic import SecretStr

from ..commands import CommandRequest
from ..handling import Answer, answered
from ..protocol import Caller, Reply

Request = TypeVar("Request", bound=CommandRequest)


class HandledFor(Generic[Request]):
    """A handler that is told the caller, as the dispatcher runs it."""

    def __init__(self, work: Callable[[Request, Caller], Awaitable[Answer]]) -> None:
        self.__work = work

    async def __call__(self, request: CommandRequest, caller: Caller, _secret: SecretStr | None) -> Reply:
        return await answered(self.__work(request, caller))      # type: ignore[arg-type]
