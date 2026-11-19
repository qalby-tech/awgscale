"""python -m awgscale.watch"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from types import FrameType

import uvicorn

from awgscale.service.logs import setup_logging
from awgscale.service.scheduler import get_scheduler

from .app import app
from .factory import get_service
from .settings import WatchSettings, get_settings

log = logging.getLogger("awgscale.watch")


async def serve(settings: WatchSettings) -> None:
    """What this process does side by side: answers requests, puts the rules in and
    probes the exits on a schedule, and listens for tunnels coming up."""
    server = uvicorn.Server(uvicorn.Config(app, host=str(settings.bind), port=settings.port, access_log=False, log_config=None))
    service = get_service()
    scheduler = get_scheduler()
    scheduler.start()
    rules_then_jobs = asyncio.create_task(service.start())
    links = asyncio.create_task(service.watch_links())
    try:
        await server.serve()
    finally:
        rules_then_jobs.cancel()
        links.cancel()
        scheduler.shutdown(wait=False)


def stopped(signal_number: int, frame: FrameType | None) -> None:
    """Asked to stop. While it serves, uvicorn takes the signal itself, shuts down and
    hands the signal back — to here: the stop is done, and it is a clean one."""
    raise SystemExit(0)


def main() -> int:
    settings = get_settings()
    setup_logging()
    try:
        settings.token_file.read_text()                    # no token = no start: the API must never be open
    except OSError as e:
        print(f"the token cannot be read: {e}", file=sys.stderr)
        return 1
    log.info("listening on %s:%s", settings.bind, settings.port)
    signal.signal(signal.SIGTERM, stopped)
    asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
