"""The resolver's keeper (awgscale.resolver), asked through its own models."""
from __future__ import annotations

from awgscale.resolver.models import Done, Health

from .http import Method, ServiceClient


class ResolverClient:
    def __init__(self, client: ServiceClient) -> None:
        self.__client = client

    async def health(self, timeout: float = 60) -> Health:
        return Health.model_validate(await self.__client.ask(Method.GET, "/v1/health", timeout=timeout))

    async def stop(self, timeout: float = 60) -> Done:
        return Done.model_validate(await self.__client.ask(Method.POST, "/v1/stop", timeout=timeout))

    async def start(self, timeout: float = 60) -> Done:
        return Done.model_validate(await self.__client.ask(Method.POST, "/v1/start", timeout=timeout))
