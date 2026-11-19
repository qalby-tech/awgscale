"""The parts of this process, each made once from the settings."""
from __future__ import annotations

from functools import lru_cache

from .agent import AgentClient
from .budget import InviteBudget
from .password import read_password
from .prometheus import Prometheus
from .sessions import LoginAttempts, Sessions
from .settings import get_settings
from .state import StateBoard
from .web import WebFiles


@lru_cache
def get_agent() -> AgentClient:
    return AgentClient(get_settings().agent_socket)


@lru_cache
def get_sessions() -> Sessions:
    return Sessions(get_settings().session_ttl)


@lru_cache
def get_login_attempts() -> LoginAttempts:
    return LoginAttempts()


@lru_cache
def get_invite_budget() -> InviteBudget:
    return InviteBudget()


@lru_cache
def get_prometheus() -> Prometheus:
    return Prometheus(get_settings())


@lru_cache
def get_state() -> StateBoard:
    return StateBoard(get_settings(), get_agent(), get_prometheus())


@lru_cache
def get_web_files() -> WebFiles:
    settings = get_settings()
    return WebFiles(settings.web_dir, settings.fonts_dir)


@lru_cache
def get_password() -> str:
    """The admin's password; the file is made on the first start."""
    return read_password(get_settings().password_file)
