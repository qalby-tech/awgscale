"""The names of the rules for some groups of people, given to the resolver again after a failed try.

When the watch service's step of a restart of the resolver fails, the resolver is started with the file of the sets
that always exist and the names of those rules are forgotten (sites/routing.py: `relearn`): their people go by the
rules for everybody meanwhile. The next change puts them back; so does this, from the looks at the state, without
waiting for one. Said in the journal once, as soon as it is seen; the first try comes five minutes later (the failure
itself may have restarted the resolver more than once), and the pause doubles while the tries fail, up to an hour —
each one restarts the resolver. A try first asks the watch service whether it makes those sets at all: one of an
earlier version (no "scoped-names"), or one that does not answer, is asked again after the same pause, and the
resolver is not restarted until it does."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable, Protocol

from ..journal.models import Kind, Who
from ..locks import file_lock

log = logging.getLogger("awgscale.agent")

RETRY_EVERY_SECONDS = 300.0         # each try restarts the resolver: about a second without DNS for everybody
RETRY_AT_MOST_SECONDS = 3600.0      # the pause doubles with every failed try, up to this
LOCK_WAIT_SECONDS = 30.0            # a change of the sites or of the rules holds the lock: the next look tries again
SHOWN_REASON = 200
BACK = "Сайты правил для отдельных групп снова переданы блокировщику"
AWAY = "Сайты правил для отдельных групп не переданы блокировщику: их люди пока идут по правилам для всех. Повтор — сам"


class PendingNames(Protocol):
    """sites.routing.Routing: whether the names wait to be given again."""

    def scoped_pending(self) -> bool: ...

    def scoped_settled(self) -> None: ...

    async def names_can_be_made(self) -> bool:
        """The watch service makes the sets of those names (never raises)."""
        ...


class Reapplier(Protocol):
    """sites.service.SitesService: the routing made to follow the stored list. Raises Refused."""

    async def reapply(self) -> bool: ...


class JournalWriter(Protocol):
    async def write(self, text: str, bad: bool = False, kind: Kind = Kind.CHANGE, who: Who = Who.SYSTEM) -> None: ...


class ScopedNamesRetry:
    def __init__(
        self, routing: PendingNames, sites: Reapplier, journal: JournalWriter, lock_file: Path, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.__clock = clock                    # seconds, only ever compared with each other
        self.__routing = routing
        self.__sites = sites
        self.__journal = journal
        self.__lock_file = lock_file            # the lock of the sites and the rules: one change of the routing at a time
        self.__task: asyncio.Task[None] | None = None
        self.__tried_at: float | None = None    # the last try, or when the names were first seen waiting
        self.__failures = 0                     # tries in a row that did not give them back
        self.__said = False                     # the journal was told; it is told again only after they came back

    def check(self) -> None:
        """With every look at the state: a try in the background when one is due. Never raises, never waits."""
        try:
            if self.__task is not None and not self.__task.done():
                return
            if not self.__routing.scoped_pending():
                self.__failures = 0
                self.__tried_at = None
                if self.__said:                 # a change of the sites or of the rules gave them meanwhile
                    self.__said = False
                    self.__task = asyncio.get_running_loop().create_task(self.__say(BACK, False))
                return
            now = self.__clock()
            if self.__tried_at is None:         # just seen: said now, tried after the first pause
                self.__tried_at = now
                if not self.__said:
                    self.__said = True
                    self.__task = asyncio.get_running_loop().create_task(self.__say(AWAY, True))
                return
            if now - self.__tried_at < self.pause():
                return
            self.__tried_at = now
            self.__task = asyncio.get_running_loop().create_task(self.__retry())
        except Exception as e:
            log.info("the names of the rules for groups not tried again: %s", repr(e)[:SHOWN_REASON])

    def pause(self) -> float:
        """How long until the next try: five minutes, doubled with every failed try, an hour at most."""
        return min(RETRY_EVERY_SECONDS * 2**self.__failures, RETRY_AT_MOST_SECONDS)

    async def __say(self, text: str, bad: bool) -> None:
        try:
            await self.__journal.write(text, bad, Kind.CHANGE, Who.SYSTEM)
        except Exception as e:
            log.info("journal not written: %s", repr(e)[:SHOWN_REASON])

    async def __retry(self) -> None:
        waiting = False
        try:
            async with file_lock(self.__lock_file, LOCK_WAIT_SECONDS):
                if not self.__routing.scoped_pending():
                    return                      # a change in between did it
                if not await self.__routing.names_can_be_made():
                    waiting = True              # no restart of the resolver could give them: asked again after the same pause
                    return
                if not await self.__sites.reapply():
                    self.__routing.scoped_settled()     # nothing to give any more: no such rule is on now
        except Exception as e:
            log.info("the names of the rules for groups not given again: %s", repr(e)[:SHOWN_REASON])
            return
        finally:
            if not self.__routing.scoped_pending():
                self.__failures = 0
            elif not waiting:
                self.__failures += 1
            if self.__said and not self.__routing.scoped_pending():
                self.__said = False
                await self.__say(BACK, False)
