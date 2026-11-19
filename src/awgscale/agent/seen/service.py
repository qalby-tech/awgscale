"""The notes kept between looks at the state, and what is done with them."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Sequence

from ..errors import StoreDown
from ..journal.models import Who
from ..locks import file_lock
from ..ports import JournalWriter, SeenStore, Together
from ..users.models import KnownUser
from .models import ExitSeen, Notes, says_the_same, seconds_of
from .notes import next_notes

LEAVING_FOR = timedelta(seconds=600)      # for this long a removed user's going is the agent's own doing
FIRST_SIGHT_MARK = "clients"              # notes without it were never written by a look


class SeenNotes:
    def __init__(self, store: SeenStore, together: Together, journal: JournalWriter, lock_file: Path) -> None:
        self.__store = store
        self.__together = together
        self.__journal = journal
        self.__lock_file = lock_file

    async def check(self, users: Sequence[KnownUser], exits: Sequence[ExitSeen], now: datetime) -> None:
        """Compare a look with what was seen last, and write what happened by itself
        into the journal. Raises StoreDown: then there is no comparison this time."""
        stored = await self.__store.load()
        if FIRST_SIGHT_MARK in stored:
            ahead, events = next_notes(Notes.of(stored), users, exits, now)
            if not events and says_the_same(ahead.record(), stored):
                return                                       # the usual case: nothing to write, no lock taken
        async with file_lock(self.__lock_file), self.__together.whole():
            stored = await self.__store.load()
            if type(stored.get("at")) is int and stored["at"] > seconds_of(now):
                return                                       # a look that started later has already been here
            ahead, events = next_notes(Notes.of(stored), users, exits, now)
            if FIRST_SIGHT_MARK not in stored:
                events = []                                  # the first sight of anything is not news
            if events or not says_the_same(ahead.record(), stored):
                await self.__store.save(ahead.record())
        for event in events:
            await self.__journal.write(event.text, event.bad, event.kind, Who.SYSTEM)

    async def leaving(self, names: Sequence[str]) -> None:
        """Called before the agent itself removes users: for a while their going is
        the agent's doing, whatever a look that runs meanwhile still sees."""
        try:
            async with file_lock(self.__lock_file), self.__together.whole():
                stored = await self.__store.load()
                held = stored.get("leaving")
                leaving = dict(held) if isinstance(held, dict) else {}
                until = seconds_of(datetime.now(UTC) + LEAVING_FOR)
                for name in names:
                    leaving[name] = until
                await self.__store.save({**stored, "leaving": leaving})
        except StoreDown:
            pass                                             # the removal itself is not stopped by a missing note

    async def back(self, name: str) -> None:
        """A user created (again) by the agent is nobody's leftover."""
        try:
            async with file_lock(self.__lock_file), self.__together.whole():
                stored = await self.__store.load()
                held = stored.get("leaving")
                if isinstance(held, dict) and name in held:
                    await self.__store.save({**stored, "leaving": {other: until for other, until in held.items() if other != name}})
        except StoreDown:
            pass
