"""python -m awgscale.dnsgate"""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from awgscale.service.logs import setup_logging
from awgscale.service.scheduler import get_scheduler
from awgscale.service.tasks import ended_by_itself, vital

from .app import app
from .factory import get_admission, get_proxy, get_sites
from .settings import DnsgateSettings, get_settings
from .watcher import reload_on_change

log = logging.getLogger("awgscale.dnsgate")

# Traefik keeps its connections to the gate open and reuses them: the gate must not be the one to close an idle one first
KEEP_ALIVE_SECONDS = 120


async def serve(settings: DnsgateSettings) -> int:
    """What this process does side by side: answers DoH, proxies connections to listed sites,
    reads the list and the codes when they change, saves the admissions now and then."""
    sites = get_sites()
    admission = get_admission()
    sites.load()
    admission.load_codes()
    admission.load_state()
    scheduler = get_scheduler()
    scheduler.add_job(admission.save_state, "interval", seconds=settings.save_seconds)
    scheduler.start()
    # no request lines: the address of a request carries the device's personal code
    server = uvicorn.Server(uvicorn.Config(
        app, host=str(settings.bind), port=settings.port,
        access_log=False, log_config=None, server_header=False, timeout_keep_alive=KEEP_ALIVE_SECONDS,
    ))
    proxy = asyncio.create_task(get_proxy().serve())
    files = asyncio.create_task(reload_on_change({settings.list_file: sites.load, settings.tokens_file: admission.load_codes}))
    vital(proxy, server, "the SNI proxy")
    vital(files, server, "the watch of the list and the codes")
    try:
        await server.serve()
        return 1 if ended_by_itself(proxy) or ended_by_itself(files) else 0
    finally:
        proxy.cancel()
        files.cancel()
        scheduler.shutdown(wait=False)


def main() -> int:
    settings = get_settings()
    setup_logging()
    log.info(
        "dnsgate: DoH :%s, SNI proxy :%s; listed names → %s, the rest → %s; static clients %d, proxy admission %d h",
        settings.port, settings.sni_port, settings.proxy_ip, settings.upstream, len(settings.static_allow), settings.allow_for.total_seconds() // 3600,
    )
    return asyncio.run(serve(settings))


if __name__ == "__main__":
    sys.exit(main())
