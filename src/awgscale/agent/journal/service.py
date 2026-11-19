"""The journal as the commands use it."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import SecretStr

from awgscale.agent.commands import CommandRequest
from awgscale.agent.protocol import Caller, Refusal, Reply

from .models import MAX_TEXT_LENGTH, Entry, Kind, Who
from .reader import JournalReader, entries_of
from .writer import JournalWriter

log = logging.getLogger("awgscale.agent")


class Journal:
    def __init__(self, reader: JournalReader, writer: JournalWriter) -> None:
        self.__reader = reader
        self.__writer = writer

    async def write(self, text: str, bad: bool = False, kind: Kind = Kind.CHANGE, who: Who = Who.SYSTEM) -> None:
        await self.__writer.write(Entry(at=datetime.now(UTC), text=text[:MAX_TEXT_LENGTH], bad=bad, kind=kind, who=who))

    async def show(self, _request: CommandRequest, _caller: Caller, _secret: SecretStr | None) -> Reply:
        """The command `journal`: the newest entries, newest first."""
        try:
            lines = await self.__reader.lines()
        except Exception as e:
            log.info("journal not read: %s", repr(e)[:200])
            return Refusal(error="Журнал сейчас недоступен").reply()
        return {"ok": True, "items": entries_of(lines)}
