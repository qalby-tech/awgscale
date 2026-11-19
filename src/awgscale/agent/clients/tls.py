"""The certificates the agent's HTTP clients trust, loaded once for the process."""
from __future__ import annotations

import ssl
from functools import lru_cache

import httpx


@lru_cache
def tls_context() -> ssl.SSLContext:
    """One context for every client. A client made without it loads the whole
    list of authorities anew — most of a megabyte with every request, which the
    process never gives back: asked for the state every few seconds, the agent
    would grow by the hour."""
    return httpx.create_ssl_context()
