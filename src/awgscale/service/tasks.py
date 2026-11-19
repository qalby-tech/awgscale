"""Tasks a service runs beside its HTTP server."""
from __future__ import annotations

import asyncio
import logging
from functools import partial

import uvicorn

log = logging.getLogger("awgscale.service")


def _stop_server(server: uvicorn.Server, what: str, task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    log.error("ERROR %s ended: %r — the service stops", what, task.exception())
    server.should_exit = True


def vital(task: asyncio.Task[None], server: uvicorn.Server, what: str) -> None:
    """Marks a task the service cannot do without: if it ever ends by itself,
    the server stops too, and whoever runs the process starts it afresh —
    instead of a service that answers and quietly does half its work."""
    task.add_done_callback(partial(_stop_server, server, what))


def ended_by_itself(task: asyncio.Task[None]) -> bool:
    return task.done() and not task.cancelled()
