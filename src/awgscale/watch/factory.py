"""The one service of this process, put together from its settings."""
from __future__ import annotations

from functools import lru_cache


from awgscale.service.scheduler import get_scheduler

from .cascade import Cascade
from .service import WatchService
from .settings import get_settings


@lru_cache
def get_cascade() -> Cascade:
    return Cascade(get_settings().places)


@lru_cache
def get_service() -> WatchService:
    return WatchService(get_settings(), get_cascade(), get_scheduler())
