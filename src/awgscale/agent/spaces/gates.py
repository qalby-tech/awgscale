"""The watch service, for what the spaces ask of it."""
from __future__ import annotations

from ..clients.gates import outcome
from ..clients.watch import WatchClient
from ..ports import Outcome


class WatchSpaces:
    def __init__(self, client: WatchClient) -> None:
        self.__client = client

    async def load(self, timeout: float) -> Outcome:
        return await outcome(self.__client.spaces(timeout), "сторож не отвечает")
