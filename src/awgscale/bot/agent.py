"""The agent on the server, asked through its unix socket: one line of JSON there, one back."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from awgscale.agent.commands import Command
from awgscale.agent.state.clients import MAX_PAGE, ClientsQuery, ClientStatus
from awgscale.service.agent_socket import exchange

from .models import ClientsPage, DayTraffic, DeviceKey, Group, InviteRequest, JournalEntry, State
from .settings import BotSettings

CONNECT_TIMEOUT = 5.0
DEFAULT_TIMEOUT = 150.0
STATE_TIMEOUT = 30.0
JOURNAL_TIMEOUT = 15.0
ASKED_BY = "bot"


class AgentError(Exception):
    """The agent refused, or could not be asked. `reason` is what it said, when it said anything."""

    def __init__(self, reason: str | None) -> None:
        super().__init__(reason or "")
        self.__reason = reason

    def reason(self, default: str) -> str:
        return default if self.__reason is None else self.__reason


class Agent:
    def __init__(self, settings: BotSettings) -> None:
        self.__settings = settings

    async def __exchange(self, args: tuple[str, ...], timeout: float) -> dict[str, Any]:
        line = await exchange(self.__settings.agent_socket, args, ASKED_BY, connect_timeout=CONNECT_TIMEOUT, read_timeout=timeout)
        answer = json.loads(line)
        if not isinstance(answer, dict):
            raise ValueError("not an object")
        return answer

    async def ask(self, *args: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        """The agent's answer to a command. Raises AgentError when it says no, or does not answer."""
        try:
            answer = await self.__exchange(args, timeout)
        except (OSError, ValueError, IndexError, TimeoutError) as e:
            raise AgentError(f"агент на сервере недоступен ({type(e).__name__})") from None
        if not answer.get("ok"):
            error = answer.get("error")
            raise AgentError(None if error is None else str(error))
        return answer

    async def state(self) -> State:
        return State.model_validate(await self.ask(Command.STATE, timeout=STATE_TIMEOUT))

    async def clients(
        self, status: ClientStatus = ClientStatus.ALL, within: timedelta | None = None, search: str = "", limit: int = MAX_PAGE, offset: int = 0
    ) -> ClientsPage:
        """One page of users, in the order of their addresses: those whose term ends within `within` (EXPIRING),
        or who have not been heard from for that long (STALE). Never every user at once."""
        query = ClientsQuery(status=status, q=search, limit=limit, offset=offset, **({"within": int(within.total_seconds())} if within is not None else {}))
        packed = base64.b64encode(query.model_dump_json(exclude_defaults=True).encode()).decode()
        return ClientsPage.model_validate(await self.ask(Command.CLIENTS, packed, timeout=STATE_TIMEOUT))

    async def day(self, since: datetime) -> DayTraffic:
        """The users' traffic since the day began, counted by the agent; asked first, it begins counting."""
        return DayTraffic.model_validate(await self.ask(Command.DAY, str(int(since.timestamp())), timeout=STATE_TIMEOUT))

    async def journal(self) -> list[JournalEntry]:
        """The change journal as the agent lists it (the newest first). What is not an entry is left out."""
        answer = await self.ask(Command.JOURNAL, timeout=JOURNAL_TIMEOUT)
        entries: list[JournalEntry] = []
        for item in answer.get("items", []):
            try:
                entries.append(JournalEntry.model_validate(item))
            except ValidationError:
                continue
        return entries

    async def key(self, name: str) -> DeviceKey:
        return DeviceKey.model_validate(await self.ask(Command.QR, name, timeout=STATE_TIMEOUT))

    async def add(self, name: str, term: str | None) -> None:
        await self.ask(Command.ADD, name, *([term] if term else []))

    async def remove(self, name: str) -> None:
        await self.ask(Command.REMOVE, name)

    async def groups(self) -> list[Group]:
        answer = await self.ask(Command.UGROUPS_GET, timeout=STATE_TIMEOUT)
        return [Group.model_validate(group) for group in answer.get("groups") or []]

    async def invite(self, request: InviteRequest) -> str:
        """Makes an invitation. → its secret, the last part of the link."""
        answer = await self.ask(Command.INVITE_ADD, base64.b64encode(request.model_dump_json().encode()).decode(), timeout=STATE_TIMEOUT)
        return str(answer["token"])
