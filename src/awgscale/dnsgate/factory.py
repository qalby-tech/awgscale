"""The parts of this process, each made once from the settings."""
from __future__ import annotations

from functools import lru_cache

from .admission import Admission
from .proxy import SniProxy
from .service import Gate
from .settings import get_settings
from .sites import SiteList
from .upstream import Upstream


@lru_cache
def get_sites() -> SiteList:
    return SiteList(get_settings().list_file)


@lru_cache
def get_admission() -> Admission:
    return Admission(get_settings())


@lru_cache
def get_upstream() -> Upstream:
    return Upstream(get_settings())


@lru_cache
def get_gate() -> Gate:
    return Gate(get_settings(), get_sites(), get_upstream())


@lru_cache
def get_proxy() -> SniProxy:
    return SniProxy(get_settings(), get_admission(), get_sites(), get_upstream())
