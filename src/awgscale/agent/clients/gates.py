"""The other services as the commands of this package ask them: done or not, and what was said when not."""
from __future__ import annotations

from typing import Awaitable, Protocol

from ..ports import Outcome
from .http import ServiceDown, ServiceRefused
from awgscale.watch.models import Status

from .watch import WatchClient

SHOWN_REASON = 120


class Told(Protocol):
    ok: bool


async def outcome(call: Awaitable[Told], silent: str) -> Outcome:
    """A call to a service → how it ended. `silent` is what to say of a service that did not answer at all."""
    try:
        answer = await call
    except ServiceRefused as e:
        return Outcome(False, str(e))
    except ServiceDown as e:
        return Outcome(False, str(e) if e.status else silent + ": " + str(e)[:SHOWN_REASON])
    return Outcome(bool(answer.ok))


class WatchService:
    """The watch service behind its client."""

    def __init__(self, client: WatchClient) -> None:
        self.__client = client

    async def health(self, timeout: float) -> Outcome:
        return await outcome(self.__client.health(timeout), "сторож не отвечает")

    async def apply(self, timeout: float) -> Outcome:
        return await outcome(self.__client.apply(timeout), "сторож не отвечает")

    async def pin(self, timeout: float) -> Outcome:
        return await outcome(self.__client.pin(timeout), "сторож не отвечает")

    async def status(self, timeout: float) -> Status | None:
        """How the exits are; None when the service did not answer."""
        try:
            return await self.__client.status(timeout)
        except (ServiceRefused, ServiceDown):
            return None
