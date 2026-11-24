"""The watch service: the engine's operations for callers, and its passes as scheduled jobs."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from awgscale.core.linkevents import MonitorEnded, link_events

from .cascade import Cascade, utc_now
from .models import FEATURES, Applied, DomainsReset, Health, NetsRefreshed, Pinned, RuRefreshed, ScopedLoaded, SpacesLoaded, Status, Work
from .settings import WatchSettings

log = logging.getLogger("awgscale.watch")

Clock = Callable[[], datetime]


class Job(StrEnum):
    PROBE = "probe"
    SAVE_LEARNED = "save-learned"
    REFRESH_LISTS = "refresh-lists"


class RefreshedList(StrEnum):
    """The lists that are downloaded again when they grow old."""

    RU = "ru"
    NETS = "nets"


class WatchService:
    def __init__(self, settings: WatchSettings, cascade: Cascade, scheduler: AsyncIOScheduler, clock: Clock = utc_now) -> None:
        self.__settings = settings
        self.__cascade = cascade
        self.__scheduler = scheduler
        self.__clock = clock
        self.__ticks = 0
        self.__ticks_failed = 0
        self.__last_tick: datetime | None = None
        self.__tick_seconds = 0.0
        self.__ask_again_after: dict[RefreshedList, datetime] = {}      # a source that did not answer is left alone until then

    # ── what callers ask for ─────────────────────────────────────────────────

    def health(self) -> Health:
        stats = self.__cascade.stats
        return Health(started=stats.started, applied=stats.applied, last_tick=self.__last_tick, features=list(FEATURES))

    def __work(self) -> Work:
        stats = self.__cascade.stats
        return Work(
            started=stats.started,
            applied=stats.applied,
            last_tick=self.__last_tick,
            tick_seconds=self.__tick_seconds,
            ticks=self.__ticks,
            tick_failed=self.__ticks_failed,
        )

    async def status(self) -> Status:
        return Status.of(await asyncio.to_thread(self.__cascade.status), self.__work())

    async def apply(self) -> Applied:
        return Applied(mode=await asyncio.to_thread(self.__cascade.apply))

    async def pin(self) -> Pinned:
        """Who goes where, from the health already known: no exit is probed."""
        return Pinned(ok=await asyncio.to_thread(self.__cascade.tick, False))

    async def spaces(self) -> SpacesLoaded:
        return SpacesLoaded(spaces=await asyncio.to_thread(self.__cascade.spaces_apply))

    async def scoped(self) -> ScopedLoaded:
        return ScopedLoaded(rules=await asyncio.to_thread(self.__cascade.scoped_apply))

    async def domains(self) -> DomainsReset:
        done = await asyncio.to_thread(self.__cascade.domains_reset)
        return DomainsReset(nets=done.nets, missing=done.missing)

    async def update_ru(self) -> RuRefreshed:
        refresh = await asyncio.to_thread(self.__cascade.update_ru)
        return RuRefreshed(ru_networks=refresh.ru_networks, refreshed=refresh.refreshed)

    async def update_nets(self) -> NetsRefreshed:
        refresh = await asyncio.to_thread(self.__cascade.update_nets)
        return NetsRefreshed(nets=refresh.nets, refreshed=refresh.refreshed)

    # ── what the service does by itself: scheduled jobs ──────────────────────

    async def probe_pass(self) -> None:
        """Probes every exit, moves clients off a dead one and back. Never raises."""
        started = time.monotonic()
        try:
            if not await asyncio.to_thread(self.__cascade.tick):
                log.info("WARN: tick skipped (another pass is running)")
            self.__ticks += 1
        except Exception as e:
            self.__ticks_failed += 1
            log.info("WARN: tick failed: %s", e)
        self.__last_tick = self.__clock()
        self.__tick_seconds = round(time.monotonic() - started, 2)

    async def save_learned(self) -> None:
        try:
            await asyncio.to_thread(self.__cascade.save_learned)
        except Exception as e:
            log.info("WARN: %s", e)

    def __is_due(self, which: RefreshedList, path: Path) -> bool:
        now = self.__clock()
        if now < self.__ask_again_after.get(which, now):
            return False
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return True
        return age > timedelta(days=self.__settings.refresh_days).total_seconds()

    async def refresh_lists(self) -> None:
        """The weekly refreshes: an address list that grew old is downloaded again — one list per look, never both."""
        places = self.__cascade.places
        try:
            if self.__is_due(RefreshedList.RU, places.home_zone(self.__cascade.conf().home_country)):
                which = RefreshedList.RU
                refreshed = (await self.update_ru()).refreshed
            elif self.__is_due(RefreshedList.NETS, places.nets_auto):
                which = RefreshedList.NETS
                refreshed = (await self.update_nets()).refreshed
            else:
                return
            if not refreshed:      # the source did not answer: not asked again at every look
                self.__ask_again_after[which] = self.__clock() + timedelta(hours=self.__settings.refresh_retry_hours)
        except Exception as e:
            log.info("WARN: %s", e)

    def schedule(self) -> None:
        """The jobs: the probe at once and then on its interval, the save and the refresh look on theirs."""
        settings = self.__settings
        now = self.__clock()
        # a pass that is still running when the next one is due is not doubled; passes missed meanwhile are one pass
        common = {"coalesce": True, "max_instances": 1, "misfire_grace_time": None, "replace_existing": True}
        self.__scheduler.add_job(self.probe_pass, "interval", seconds=settings.probe_seconds, id=Job.PROBE, next_run_time=now, **common)
        self.__scheduler.add_job(self.save_learned, "interval", seconds=settings.save_seconds, id=Job.SAVE_LEARNED, **common)
        first_look = now + timedelta(seconds=settings.refresh_check_seconds / 2)      # between two saves, as it always was
        self.__scheduler.add_job(self.refresh_lists, "interval", seconds=settings.refresh_check_seconds, id=Job.REFRESH_LISTS, next_run_time=first_look, **common)
        log.info("watchdog started (every %ss)", settings.probe_seconds)

    async def start(self) -> None:
        """The rules first — a service that cannot apply them says so and keeps trying — then the jobs."""
        for _ in range(self.__settings.apply_attempts):
            try:
                await self.apply()
                break
            except Exception as e:
                log.error("ERROR: rules not applied: %s", e)
                await asyncio.sleep(self.__settings.apply_retry_seconds)
        self.schedule()

    async def watch_links(self) -> None:
        """A tunnel that has just come up is probed at once, not at the next interval
        (the kernel says when; nothing is polled). It never dies."""
        while True:
            try:
                async for event in link_events(self.__settings.ip):
                    if not event.up or event.name not in await asyncio.to_thread(self.__watched_links):
                        continue
                    await asyncio.sleep(self.__settings.link_settle_seconds)
                    self.__probe_now()
            except MonitorEnded as e:
                log.info("links not listened to: %s", e)
            except Exception as e:
                log.error("listening to the links failed: %s", repr(e)[:200])
            await asyncio.sleep(self.__settings.apply_retry_seconds)

    def __watched_links(self) -> set[str]:
        conf = self.__cascade.conf()
        return {e.iface for e in conf.exits} | {conf.client_if}

    def __probe_now(self) -> None:
        job = self.__scheduler.get_job(Job.PROBE)
        if job is not None:
            job.modify(next_run_time=self.__clock())
