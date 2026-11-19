"""The one keeper of this process, put together from its settings."""
from __future__ import annotations

from functools import lru_cache


from awgscale.service.scheduler import get_scheduler

from .service import Keeper
from .settings import get_settings


@lru_cache
def get_keeper() -> Keeper:
    return Keeper(get_settings(), get_scheduler())
