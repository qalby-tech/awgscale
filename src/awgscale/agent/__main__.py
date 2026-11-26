"""python -m awgscale.agent [<command> …]"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import sys
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from awgscale.service.logs import setup_logging

from .commands import Command
from .factory import get_daemon, get_dispatcher, get_exit_servers, get_group_lists, get_shell, get_state, get_store, get_threats_list
from .settings import AgentSettings, get_settings
from .shell import caller_of

log = logging.getLogger("awgscale.agent")

REPLY_LOG_LENGTH = 300
COUNTRY_BACKFILL_DELAY_SECONDS = 30      # the first look for the exits' countries waits this long after the start: the network is up by then


def nightly_trigger(settings: AgentSettings) -> CronTrigger:
    """When the lists by link are refreshed: once a night, at the same UTC time."""
    return CronTrigger(hour=settings.nightly_hour, minute=settings.nightly_minute, timezone=UTC)


async def refresh_lists() -> None:
    """The lists by link: every one that is switched on is downloaded and applied
    only if its content changed (that restarts the resolver for a second, hence at night)."""
    await asyncio.sleep(random.uniform(0, get_settings().nightly_jitter_seconds))
    try:
        reply = await get_dispatcher().dispatch([Command.SITES_REFRESH.value])
        log.info("nightly lists refresh: %s", str(reply)[:REPLY_LOG_LENGTH])
    except Exception as e:
        log.error("nightly lists refresh failed: %s", repr(e)[:REPLY_LOG_LENGTH])


async def keep_lists() -> None:
    """«Угрозы» is put into AdGuard when it is missing; the lists the groups need are downloaded
    again (and the file of them taken away when none is needed). Never raises."""
    try:
        if await get_threats_list().ensure():
            log.info("the threats list was missing: put in")
    except Exception as e:
        log.warning("the threats list not checked: %s", repr(e)[:REPLY_LOG_LENGTH])
    try:
        await get_group_lists().keep(refresh=True)
    except Exception as e:
        log.error("group lists keeping failed: %s", repr(e)[:REPLY_LOG_LENGTH])


async def keep_countries() -> None:
    """The exits that do not know where they are yet are looked up by their public address. Never raises."""
    try:
        await get_exit_servers().backfill()
    except Exception as e:
        log.warning("the countries of the exits not looked up: %s", repr(e)[:REPLY_LOG_LENGTH])


async def serve(settings: AgentSettings) -> None:
    """Answers on the socket until asked to stop; looks at the state every few seconds;
    refreshes the lists once a night, and the groups' own lists (and «Угрозы») at the start and every few hours;
    looks up where the exits without a stored country are shortly after the start, then every hour or so while any has none."""
    daemon = get_daemon()
    asked_to_stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop_signal, asked_to_stop.set)
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(refresh_lists, nightly_trigger(settings))
    scheduler.add_job(keep_lists, IntervalTrigger(hours=settings.group_lists_refresh_hours, timezone=UTC), next_run_time=datetime.now(UTC))
    # where the exits are: once the network is up after the start, then again while one has no stored country — never while a request waits
    if settings.country_lookup_url is not None:
        first_look = datetime.now(UTC) + timedelta(seconds=COUNTRY_BACKFILL_DELAY_SECONDS)
        scheduler.add_job(keep_countries, IntervalTrigger(hours=settings.country_backfill_hours, timezone=UTC), next_run_time=first_look, max_instances=1, coalesce=True)
    # the look at the state: the tunnel's peers, the users and the counters, once per interval — never per question
    scheduler.add_job(
        get_state().sample,
        IntervalTrigger(seconds=settings.sample_seconds, timezone=UTC),
        next_run_time=datetime.now(UTC),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    await daemon.start()
    await asked_to_stop.wait()
    scheduler.shutdown(wait=False)
    await daemon.stop(settings.stop_wait_seconds)
    get_state().keep_day()
    await get_store().close()


async def one_command(arguments: list[str]) -> int:
    """A command from a shell (the deploy's store-init, a timer, by hand): its answer is printed, the exit code says whether it is a yes."""
    try:
        printed, code = await get_shell().run(arguments, caller_of(os.environ))
    finally:
        await get_store().close()
    sys.stdout.write(printed)
    return code


def main(arguments: list[str]) -> int:
    setup_logging()
    if arguments:
        return asyncio.run(one_command(arguments))
    asyncio.run(serve(get_settings()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
