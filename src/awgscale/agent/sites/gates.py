"""The other services, as the sites ask them."""
from __future__ import annotations

from typing import Any

from ..adguard.client import AdGuardClient
from ..clients.gates import SHOWN_REASON, outcome
from ..clients.http import ServiceDown, ServiceRefused
from ..clients.resolver import ResolverClient
from ..clients.watch import WatchClient
from ..ports import NamesReset, Outcome


class WatchForSites:
    """The watch service behind its client."""

    def __init__(self, client: WatchClient) -> None:
        self.__client = client

    async def health(self, timeout: float) -> Outcome:
        return await outcome(self.__client.health(timeout), "сторож не отвечает")

    async def apply(self, timeout: float) -> Outcome:
        return await outcome(self.__client.apply(timeout), "сторож не отвечает")

    async def domains(self, timeout: float) -> NamesReset:
        try:
            answer = await self.__client.domains(timeout)
        except ServiceRefused as e:
            return NamesReset(False, str(e))
        except ServiceDown as e:
            return NamesReset(False, str(e) if e.status else "сторож не отвечает: " + str(e)[:SHOWN_REASON])
        return NamesReset(bool(answer.ok), missing=frozenset(answer.missing))

    async def features(self, timeout: float) -> frozenset[str]:
        """What the running watch service can do; empty when it did not answer, or is of an earlier version."""
        try:
            return frozenset((await self.__client.health(timeout)).features)
        except (ServiceRefused, ServiceDown):
            return frozenset()


class KeeperOfResolver:
    """The resolver's keeper behind its client."""

    def __init__(self, client: ResolverClient) -> None:
        self.__client = client

    async def stop(self, timeout: float) -> Outcome:
        return await outcome(self.__client.stop(timeout), "блокировщик не отвечает")

    async def start(self, timeout: float) -> Outcome:
        return await outcome(self.__client.start(timeout), "блокировщик не отвечает")


class AdGuardBlocker:
    """The resolver's control API (AdGuard Home) behind its client."""

    def __init__(self, client: AdGuardClient) -> None:
        self.__client = client

    async def post(self, path: str, payload: dict[str, Any], timeout: float = 10) -> None:
        await self.__client.post(path, payload, timeout)

    async def get(self, path: str, timeout: float = 5) -> dict[str, Any]:
        answer = await self.__client.get(path, timeout)
        return answer if isinstance(answer, dict) else {}
