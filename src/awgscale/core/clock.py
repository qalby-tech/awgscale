"""Moments and spans, the same for every service. Standard library only: the boot gate imports it."""
from __future__ import annotations

from datetime import UTC, datetime

MINUTE = 60                     # seconds
HOUR = 3600
DAY = 86400


def utc_now() -> datetime:
    return datetime.now(UTC)


def moment_of(value: object) -> object:
    """A moment as it is kept: Unix seconds (how the store and earlier versions
    kept one) are read as the moment they were; anything else is left to the model."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC)
    return value
