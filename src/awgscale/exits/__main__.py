"""python -m awgscale.exits"""
from __future__ import annotations

import asyncio
import sys

import uvicorn

from awgscale.service.logs import setup_logging

from .app import app
from .factory import get_service
from .service import log
from .settings import ExitsSettings, get_settings


async def serve(settings: ExitsSettings) -> None:
    service = get_service()
    try:
        await service.prepare()
    except Exception as e:
        log.info("WARN at start: %s %s", type(e).__name__, str(e)[:200])
    # never a request line or a body: one of them carries a password
    server = uvicorn.Server(uvicorn.Config(app, host=str(settings.bind), port=settings.port, access_log=False, log_config=None))
    await server.serve()


def main() -> int:
    settings = get_settings()
    setup_logging()
    log.info("exits service on %s:%s", settings.bind, settings.port)
    asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
