"""The one service of this process, put together from its settings."""
from __future__ import annotations

from functools import lru_cache

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from awgscale.core.ashell import run
from awgscale.service.scheduler import get_scheduler

from .conf import TunnelFile
from .device import DeviceConfigs
from .kernel import Kernel
from .service import UsersService
from .settings import UsersSettings, get_settings
from .store import UserStore


def build_service(settings: UsersSettings, scheduler: AsyncIOScheduler) -> UsersService:
    return UsersService(
        settings=settings,
        store=UserStore(settings),
        kernel=Kernel(settings, run),
        file=TunnelFile(settings.conf_path),
        devices=DeviceConfigs(settings),
        scheduler=scheduler,
    )


@lru_cache
def get_service() -> UsersService:
    return build_service(get_settings(), get_scheduler())
