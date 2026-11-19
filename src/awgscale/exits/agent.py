"""The agent on the entry: the only writer of the cascade's settings. This service asks it, never writes them itself."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

from awgscale.service.agent_socket import exchange

AGENT_TIMEOUT = 200.0
ERROR_LENGTH = 120
ASKED_BY = "panel"          # who the journal says asked; kept as it was

Answer = dict[str, Any]


class Agent(Protocol):
    async def __call__(self, args: list[str], timeout: float = AGENT_TIMEOUT) -> Answer: ...


class SocketAgent:
    """The agent behind its unix socket: one request, one line of JSON back."""

    def __init__(self, path: Path) -> None:
        self.__path = path

    async def __ask(self, args: list[str]) -> Answer:
        found: Answer = json.loads(await exchange(self.__path, args, ASKED_BY))
        return found

    async def __call__(self, args: list[str], timeout: float = AGENT_TIMEOUT) -> Answer:
        try:
            return await asyncio.wait_for(self.__ask(args), timeout)
        except (OSError, ValueError, IndexError, TimeoutError) as e:
            return {"ok": False, "error": f"агент не отвечает: {str(e)[:ERROR_LENGTH]}"}
