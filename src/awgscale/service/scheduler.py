"""The timed work of a service: one APScheduler per process, on UTC, started in `__main__`."""
from __future__ import annotations

from datetime import UTC
from functools import lru_cache

from apscheduler.schedulers.asyncio import AsyncIOScheduler


@lru_cache
def get_scheduler() -> AsyncIOScheduler:
    """Runs the service's jobs (each service adds its own: the look at the
    cascade, the end of a term, the save of the admissions, …)."""
    return AsyncIOScheduler(timezone=UTC)
