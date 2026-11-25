"""The agent: the one thing the panel asks. A command and its arguments go in as a line of JSON, its answer comes back as one."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from awgscale.service.agent_socket import exchange

# What the agent answers is the agent's own: the server reads "ok" (and a few flags) and passes the rest to the page as it is.
AgentAnswer = dict[str, Any]

DEFAULT_TIMEOUT = 150.0
CALLER = "panel"
APP_CALLER = "app"      # a change an owner made in the app: the journal names their device


class AgentClient:
    def __init__(self, socket_path: Path) -> None:
        self.__socket_path = socket_path

    async def call(self, *args: str, timeout: float = DEFAULT_TIMEOUT, secret: str | None = None, device: str | None = None) -> AgentAnswer:
        """Runs one command of the agent. Never raises: an agent that cannot be
        asked is an answer too. `secret` travels beside the arguments and is never logged.
        `device`: the user whose device in the app made this change; the agent's journal names them."""
        try:
            async with asyncio.timeout(timeout):
                answer = await exchange(self.__socket_path, args, APP_CALLER if device else CALLER, secret, device=device)
            return json.loads(answer)
        except TimeoutError:
            return {"ok": False, "error": "агент на сервере недоступен: timed out"}
        except (OSError, ValueError, IndexError) as e:
            return {"ok": False, "error": f"агент на сервере недоступен: {e}"}
