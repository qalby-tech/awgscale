"""The parts of the bot, each made once, put together from its settings."""
from __future__ import annotations

from functools import lru_cache

import httpx

from .agent import Agent
from .commands import Commands
from .listener import Listener
from .settings import get_settings
from .stats import Sampler, StatsFile
from .telegram import Admin, Telegram
from .watch import Watch


@lru_cache
def get_telegram() -> Telegram:
    return Telegram(get_settings(), httpx.AsyncClient())


@lru_cache
def get_admin() -> Admin:
    return Admin(get_settings(), get_telegram())


@lru_cache
def get_agent() -> Agent:
    return Agent(get_settings())


@lru_cache
def get_sampler() -> Sampler:
    settings = get_settings()
    return Sampler(settings, get_agent(), get_admin(), StatsFile(settings.stats_file))


@lru_cache
def get_watch() -> Watch:
    return Watch(get_settings(), get_agent(), get_admin())


@lru_cache
def get_commands() -> Commands:
    return Commands(get_settings(), get_telegram(), get_admin(), get_agent(), get_sampler())


@lru_cache
def get_listener() -> Listener:
    return Listener(get_settings(), get_telegram(), get_commands())
