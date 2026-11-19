"""The watch service (awgscale.watch), asked through its own models."""
from __future__ import annotations

from awgscale.watch.models import Applied, DomainsReset, Health, Pinned, SpacesLoaded, Status

from .http import Method, ServiceClient


class WatchClient:
    def __init__(self, client: ServiceClient) -> None:
        self.__client = client

    async def health(self, timeout: float = 60) -> Health:
        return Health.model_validate(await self.__client.ask(Method.GET, "/v1/health", timeout=timeout))

    async def status(self, timeout: float = 60) -> Status:
        return Status.model_validate(await self.__client.ask(Method.GET, "/v1/status", timeout=timeout))

    async def apply(self, timeout: float = 60) -> Applied:
        return Applied.model_validate(await self.__client.ask(Method.POST, "/v1/apply", timeout=timeout))

    async def pin(self, timeout: float = 60) -> Pinned:
        return Pinned.model_validate(await self.__client.ask(Method.POST, "/v1/pin", timeout=timeout))

    async def spaces(self, timeout: float = 60) -> SpacesLoaded:
        return SpacesLoaded.model_validate(await self.__client.ask(Method.POST, "/v1/spaces", timeout=timeout))

    async def domains(self, timeout: float = 60) -> DomainsReset:
        return DomainsReset.model_validate(await self.__client.ask(Method.POST, "/v1/domains", timeout=timeout))
