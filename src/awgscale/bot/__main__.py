"""python -m awgscale.bot"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from types import FrameType

from awgscale.service.logs import setup_logging
from awgscale.service.scheduler import get_scheduler

from .commands import MENU
from .factory import get_admin, get_commands, get_listener, get_sampler, get_telegram, get_watch
from .settings import BotSettings, get_settings

log = logging.getLogger("awgscale.bot")


async def serve(settings: BotSettings) -> None:
    """What this process does side by side: listens to the admin, looks at the
    cascade for the alerts and samples the counters (two scheduled jobs)."""
    telegram = get_telegram()
    me = await telegram.get_me()
    log.info("started as @%s", me.username)
    get_commands().named(me.username)
    await telegram.set_my_commands(MENU)
    scheduler = get_scheduler()
    # a look that could not start on time is made late, not skipped
    scheduler.add_job(get_watch().look, "interval", seconds=settings.watch_seconds, max_instances=1, coalesce=True, misfire_grace_time=None)
    scheduler.add_job(get_sampler().sample, "interval", seconds=settings.sample_seconds, max_instances=1, coalesce=True, misfire_grace_time=None)
    scheduler.start()
    await get_admin().notify("Бот VPN-каскада запущен. /help — список команд.")
    try:
        await get_listener().listen()
    finally:
        scheduler.shutdown(wait=False)


def stopped(signal_number: int, frame: FrameType | None) -> None:
    """Asked to stop: a clean end."""
    raise SystemExit(0)


def main() -> int:
    settings = get_settings()
    setup_logging()
    try:
        settings.token()
    except OSError as e:
        print(f"the token cannot be read: {e}", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, stopped)
    asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
