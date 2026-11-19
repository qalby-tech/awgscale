"""python -m awgscale.panel"""
from __future__ import annotations

import asyncio
import logging
import signal
import ssl
import sys
from datetime import datetime
from types import FrameType

import uvicorn

from awgscale.service.logs import setup_logging
from awgscale.service.scheduler import get_scheduler
from awgscale.service.tasks import ended_by_itself, vital

from .app import app
from .factory import get_password, get_state
from .metrics import metrics_app
from .settings import PanelSettings, get_settings

log = logging.getLogger("awgscale.panel")

SERVER_NAME = ("Server", "panel")


def page_server(settings: PanelSettings) -> uvicorn.Server:
    """The page and its API. No request lines are logged: addresses carry names and invitation secrets."""
    config = uvicorn.Config(
        app, host=str(settings.bind), port=settings.port,
        access_log=False, log_config=None, server_header=False, headers=[SERVER_NAME],
    )
    if settings.tls_cert and settings.tls_key:
        config.ssl_certfile = str(settings.tls_cert)
        config.ssl_keyfile = str(settings.tls_key)
        config.ssl_version = ssl.PROTOCOL_TLS_SERVER
    return uvicorn.Server(config)


def metrics_server(settings: PanelSettings) -> uvicorn.Server:
    return uvicorn.Server(uvicorn.Config(metrics_app, host=str(settings.bind), port=settings.metrics_port, access_log=False, log_config=None, server_header=False))


async def serve(settings: PanelSettings) -> int:
    """What this process does side by side: serves the page and its API, looks at
    the cascade every few seconds, and (on a port of its own) answers Prometheus."""
    scheduler = get_scheduler()
    scheduler.add_job(get_state().look, "interval", seconds=settings.poll_seconds, next_run_time=datetime.now().astimezone(), max_instances=1, coalesce=True)
    scheduler.start()
    server = page_server(settings)
    metrics = None
    if settings.metrics_port:
        metrics = asyncio.create_task(metrics_server(settings).serve())
        vital(metrics, server, "the metrics port")
    try:
        await server.serve()
        return 1 if metrics is not None and ended_by_itself(metrics) else 0
    finally:
        if metrics is not None:
            metrics.cancel()
        scheduler.shutdown(wait=False)


def stopped(signal_number: int, frame: FrameType | None) -> None:
    """Asked to stop. While it serves, uvicorn takes the signal itself, shuts down and
    hands the signal back — to here: the stop is done, and it is a clean one."""
    raise SystemExit(0)


def main() -> int:
    settings = get_settings()
    setup_logging()
    logging.getLogger("apscheduler").setLevel(logging.ERROR)      # not a line per look, and not one for a look skipped while a slow one still runs
    get_password()                                         # made on the first start; never printed
    log.info("panel: %s://%s:%s  (password in %s)", "https" if settings.tls_cert else "http", settings.bind, settings.port, settings.password_file)
    signal.signal(signal.SIGTERM, stopped)
    return asyncio.run(serve(settings))


if __name__ == "__main__":
    sys.exit(main())
