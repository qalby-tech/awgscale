"""The names of the rules for some groups of people, given to the resolver again after a failed try.

When the watch service's step of a restart of the resolver fails, the resolver is started with the file of the sets
that always exist and the names of those rules are forgotten (sites/routing.py: `relearn`): their people go by the
rules for everybody meanwhile. The next change puts them back; so does this, from the looks at the state, without
waiting for one — said in the journal once, with a pause between tries that doubles while they fail (each one
restarts the resolver)."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Protocol

from ..journal.models import Kind, Who
from ..locks import file_lock

log = logging.getLogger("awgscale.agent")

RETRY_EVERY_SECONDS = 300.0         # each try restarts the resolver: about a second without DNS for everybody
RETRY_AT_MOST_SECONDS = 3600.0      # the pause doubles with every failed try, up to this
LOCK_WAIT_SECONDS = 30.0            # a change of the sites or of the rules holds the lock: the next look tries again
SHOWN_REASON = 200
BACK = "Сайты правил для отдельных групп снова переданы блокировщику"


class PendingNames(Protocol):
    """sites.routing.Routing: whether the names wait to be given again."""

    def scoped_pending(self) -> bool: ...

    def scoped_settled(self) -> None: ...


class Reapplier(Protocol):
    """sites.service.SitesService: the routing made to follow the stored list. Raises Refused."""

    async def reapply(self) -> bool: ...


class JournalWriter(Protocol):
    async def write(self, text: str, bad: bool = False, kind: Kind = Kind.CHANGE, who: Who = Who.SYSTEM) -> None: ...


class ScopedNamesRetry:
    def __init__(self, routing: PendingNames, sites: Reapplier, journal: JournalWriter, lock_file: Path) -> None:
        self.__routing = routing
        self.__sites = sites
        self.__journal = journal
        self.__lock_file = lock_file            # the lock of the sites and the rules: one change of the routing at a time
        self.__task: asyncio.Task[None] | None = None
        self.__tried_at: float | None = None
        self.__failures = 0                     # tries in a row that did not give them back
        self.__said = False                     # the journal was told; it is told again only after they came back

    def check(self) -> None:
        """With every look at the state: a try in the background when one is due. Never raises, never waits."""
        try:
            if self.__task is not None and not self.__task.done():
                return
            if not self.__routing.scoped_pending():
                self.__failures = 0
                if self.__said:                 # a change of the sites or of the rules gave them meanwhile
                    self.__said = False
                    self.__task = asyncio.get_running_loop().create_task(self.__say(BACK, False))
                return
            pause = min(RETRY_EVERY_SECONDS * 2 ** self.__failures, RETRY_AT_MOST_SECONDS)
            if self.__tried_at is not None and time.monotonic() - self.__tried_at < pause:
                return
            self.__tried_at = time.monotonic()
            self.__task = asyncio.get_running_loop().create_task(self.__retry())
        except Exception as e:
            log.info("the names of the rules for groups not tried again: %s", repr(e)[:SHOWN_REASON])

    async def __say(self, text: str, bad: bool) -> None:
        try:
            await self.__journal.write(text, bad, Kind.CHANGE, Who.SYSTEM)
        except Exception as e:
            log.info("journal not written: %s", repr(e)[:SHOWN_REASON])

    async def __retry(self) -> None:
        if not self.__said:
            self.__said = True
            await self.__say("Сайты правил для отдельных групп не переданы блокировщику: их люди пока идут по правилам для всех. Повтор — сам", True)
        try:
            async with file_lock(self.__lock_file, LOCK_WAIT_SECONDS):
                if not self.__routing.scoped_pending():
                    return                      # a change in between did it
                if not await self.__sites.reapply():
                    self.__routing.scoped_settled()     # nothing to give any more: no such rule is on now
        except Exception as e:
            log.info("the names of the rules for groups not given again: %s", repr(e)[:SHOWN_REASON])
            return
        finally:
            if self.__routing.scoped_pending():
                self.__failures += 1
            else:
                self.__failures = 0
            if self.__said and not self.__routing.scoped_pending():
                self.__said = False
                await self.__say(BACK, False)
