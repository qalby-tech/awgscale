"""python -m awgscale.users [import | import-check <installer dir>]"""
from __future__ import annotations

import asyncio
import sys
from enum import StrEnum
from pathlib import Path

import uvicorn

from awgscale.service.logs import setup_logging
from awgscale.service.scheduler import get_scheduler

from .app import app
from .factory import get_service
from .migrate import Importer
from .service import log
from .settings import UsersSettings, get_settings


class Command(StrEnum):
    IMPORT = "import"
    IMPORT_CHECK = "import-check"


async def serve(settings: UsersSettings) -> None:
    """What this process does side by side: answers requests, ends terms when they are over (scheduled jobs), keeps the kernel's peers complete."""
    # no request lines, and never a body: they carry names
    server = uvicorn.Server(uvicorn.Config(app, host=str(settings.bind), port=settings.port, access_log=False, log_config=None))
    service = get_service()
    scheduler = get_scheduler()
    scheduler.start()
    terms = asyncio.create_task(service.plan_terms())
    kernel = asyncio.create_task(service.watch_kernel())
    try:
        await server.serve()
    finally:
        terms.cancel()
        kernel.cancel()
        scheduler.shutdown(wait=False)


def main(argv: list[str]) -> int:
    settings = get_settings()
    setup_logging()
    if len(argv) == 2 and argv[0] in tuple(Command):
        return asyncio.run(Importer(settings).run(Path(argv[1]), dry=Command(argv[0]) is Command.IMPORT_CHECK))
    if not settings.endpoint:
        print("USERS_ENDPOINT is not set", file=sys.stderr)
        return 1
    log.info("users service on %s:%s, interface %s", settings.bind, settings.port, settings.iface)
    asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
