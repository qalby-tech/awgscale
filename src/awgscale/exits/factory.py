"""The one service of this process, put together from its settings."""
from __future__ import annotations

from functools import lru_cache

from awgscale.core.ashell import run

from .agent import Agent, SocketAgent
from .entry import EntryLinks
from .registry import DatabaseKeyValue, FileKeyValue, KeyValue, Registry
from .service import ExitsService
from .settings import ExitsSettings, get_settings
from .ssh import Ssh
from .steps import JobSteps


def build_store(settings: ExitsSettings) -> KeyValue:
    if settings.state_file:
        return FileKeyValue(settings.state_file)
    return DatabaseKeyValue(settings.dsn, settings.db_password_file)


def build_service(settings: ExitsSettings, agent: Agent) -> ExitsService:
    registry = Registry(build_store(settings))
    ssh = Ssh(settings, run)
    entry = EntryLinks(settings, run)
    return ExitsService(
        settings=settings,
        registry=registry,
        ssh=ssh,
        entry=entry,
        steps=JobSteps(settings, ssh, entry, registry, agent),
    )


@lru_cache
def get_service() -> ExitsService:
    settings = get_settings()
    return build_service(settings, SocketAgent(settings.agent_socket))
