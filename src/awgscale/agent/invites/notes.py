"""What the invitations write into the journal."""
from __future__ import annotations

from ..journal.models import Kind, Who
from ..journal.service import Journal
from ..protocol import Caller


class JournalNotes:
    def __init__(self, journal: Journal) -> None:
        self.__journal = journal

    async def made(self, text: str, caller: Caller) -> None:
        """A line about what the admin did with an invitation."""
        who = Who(caller.value) if caller.value else Who.SYSTEM
        await self.__journal.write(text, False, Kind.CHANGE, who)

    async def used(self, text: str) -> None:
        """A line about a guest who came with a link."""
        await self.__journal.write(text, False, Kind.INVITE, Who.INVITE)
