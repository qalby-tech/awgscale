"""python -m awgscale.resolver"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from types import FrameType

import uvicorn

from awgscale.service.logs import setup_logging
from awgscale.service.scheduler import get_scheduler
from awgscale.service.tasks import ended_by_itself, vital

from .app import app
from .factory import get_keeper
from .settings import ResolverSettings, get_settings

log = logging.getLogger("awgscale.resolver")


def stay_alive(signal_number: int, frame: FrameType | None) -> None:
    """The server hands a stop signal back once it has closed, for whoever was
    listening before it. That is this function: the process goes on, to let
    the resolver exit cleanly."""


async def serve(settings: ResolverSettings) -> int:
    """What this process does side by side: answers the agent, and keeps the resolver running."""
    signal.signal(signal.SIGTERM, stay_alive)
    signal.signal(signal.SIGINT, stay_alive)
    keeper = get_keeper()
    scheduler = get_scheduler()
    scheduler.start()
    server = uvicorn.Server(uvicorn.Config(app, host=str(settings.bind), port=settings.port, access_log=False, log_config=None))
    keeping = asyncio.create_task(keeper.keep())
    vital(keeping, server, "the keeping of the resolver")
    try:
        await server.serve()
        return 1 if ended_by_itself(keeping) else 0
    finally:
        await keeper.shut_down()
        keeping.cancel()
        scheduler.shutdown(wait=False)


def main() -> int:
    settings = get_settings()
    setup_logging()
    settings.token_file.read_text()             # no token = no start: the API must never be open
    log.info("keeper: listening on %s:%s; resolver: %s", settings.bind, settings.port, " ".join(settings.child_command))
    return asyncio.run(serve(settings))


if __name__ == "__main__":
    sys.exit(main())
