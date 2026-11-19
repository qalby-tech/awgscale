"""The keeper: one child process, wanted or not, started again whenever it should be there and is not."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .models import Health, NotDone, Quitting
from .settings import ResolverSettings

log = logging.getLogger("awgscale.resolver")

HOLD_JOB = "hold"
KILL_WAIT_SECONDS = 10.0
LISTEN_PROBE_SECONDS = 1.0
LISTEN_STEP_SECONDS = 0.1


class Keeper:
    def __init__(self, settings: ResolverSettings, scheduler: AsyncIOScheduler) -> None:
        self.__settings = settings
        self.__scheduler = scheduler
        self.__change = asyncio.Lock()            # one stop or start at a time
        self.__wanted_again = asyncio.Event()     # a stopped resolver is wanted again
        self.__child: asyncio.subprocess.Process | None = None
        self.__wanted = True
        self.__quitting = False
        self.__started: datetime | None = None
        self.__starts = 0
        self.__exits = 0
        self.__last_exit: int | None = None

    # ── the child ────────────────────────────────────────────────────────────

    def __alive(self) -> bool:
        return self.__child is not None and self.__child.returncode is None

    async def __listening(self) -> bool:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(self.__settings.dns_host, self.__settings.dns_port), LISTEN_PROBE_SECONDS)
        except OSError:
            return False
        writer.close()
        return True

    async def __spawn(self) -> bool:
        """Starts the child if it is not running. → True when it runs. Called with the lock held."""
        if self.__alive():
            return True
        try:
            self.__child = await asyncio.create_subprocess_exec(*self.__settings.child_command, stdin=asyncio.subprocess.DEVNULL)
        except OSError as e:
            log.error("keeper: ERROR: cannot start: %s", e)
            return False
        self.__started = datetime.now(UTC)
        self.__starts += 1
        return True

    async def __halt(self) -> bool:
        """Stops the child and waits until it is gone. → True when it is. Called with the lock held."""
        child = self.__child
        if child is None or child.returncode is not None:
            return True
        child.terminate()
        try:
            await asyncio.wait_for(child.wait(), self.__settings.stop_seconds)
        except TimeoutError:
            log.warning("keeper: WARN: did not exit in time — killed")
            child.kill()
            try:
                await asyncio.wait_for(child.wait(), KILL_WAIT_SECONDS)
            except TimeoutError:
                return False
        self.__last_exit = child.returncode
        self.__child = None
        return True

    # ── what the agent asks ──────────────────────────────────────────────────

    def __refuse_when_quitting(self) -> None:
        if self.__quitting:
            raise Quitting("блокировщик останавливается")

    async def stop(self) -> None:
        """Stops the resolver; returns once the process is gone. From now it is held:
        not started again until asked — or until the hold is over."""
        self.__refuse_when_quitting()
        async with self.__change:
            self.__wanted = False
            self.__wanted_again.clear()
            release_at = datetime.now(UTC) + timedelta(seconds=self.__settings.hold_seconds)
            self.__scheduler.add_job(self.release_hold, "date", run_date=release_at, id=HOLD_JOB, replace_existing=True, misfire_grace_time=None)
            gone = await self.__halt()
        if not gone:
            log.error("keeper: ERROR: could not be stopped")
            raise NotDone("блокировщик не остановился")
        log.info("keeper: stopped on request")

    async def start(self) -> None:
        """Starts the resolver; returns once it listens for DNS."""
        self.__refuse_when_quitting()
        async with self.__change:
            self.__want_again()
            running = await self.__spawn()
        if not running:
            raise NotDone("блокировщик не запустился")
        clock = asyncio.get_running_loop()
        deadline = clock.time() + self.__settings.listen_seconds
        while clock.time() < deadline:
            if await self.__listening():
                log.info("keeper: started on request")
                return
            if not self.__alive():
                break
            await asyncio.sleep(LISTEN_STEP_SECONDS)
        raise NotDone("блокировщик запущен, но не отвечает" if self.__alive() else "блокировщик не запустился")

    async def health(self) -> Health:
        running = self.__alive()
        return Health(
            running=running,
            listening=running and await self.__listening(),
            wanted=self.__wanted,
            started=self.__started,
            starts=self.__starts,
            exits=self.__exits,
            last_exit=self.__last_exit,
        )

    # ── what it does by itself ───────────────────────────────────────────────

    def __want_again(self) -> None:
        self.__wanted = True
        self.__wanted_again.set()
        if self.__scheduler.get_job(HOLD_JOB):
            self.__scheduler.remove_job(HOLD_JOB)

    async def release_hold(self) -> None:
        """The hold is over and nobody asked to start the resolver: nobody is left without DNS by a caller that died."""
        if self.__wanted or self.__quitting:
            return
        log.warning("keeper: WARN: stopped for %d s and nobody asked to start it — starting", int(self.__settings.hold_seconds))
        self.__want_again()

    async def keep(self) -> None:
        """A child that is wanted and not there is started again. Nothing is polled:
        the keeper waits for the child to exit, or for a stopped one to be wanted again."""
        while not self.__quitting:
            if not self.__wanted:
                await self.__wanted_again.wait()
                continue
            async with self.__change:
                running = self.__wanted and await self.__spawn()
                child = self.__child
            if not running or child is None:
                await asyncio.sleep(self.__settings.retry_seconds)
                continue
            await child.wait()
            async with self.__change:
                died_by_itself = self.__child is child and self.__wanted and not self.__quitting
                if died_by_itself:
                    self.__exits += 1
                    self.__last_exit = child.returncode
                    self.__child = None
                    log.warning("keeper: WARN: exited with code %s — will be started again", child.returncode)
            if died_by_itself:
                await asyncio.sleep(self.__settings.retry_seconds)

    async def shut_down(self) -> None:
        """On the keeper's own way out: no more requests, and the resolver exits cleanly first."""
        self.__quitting = True
        self.__wanted_again.set()
        async with self.__change:
            self.__wanted = False
            await self.__halt()
