"""How long ago, in words for the admin."""
from __future__ import annotations

from datetime import timedelta

from awgscale.core.clock import DAY, HOUR, MINUTE

SECONDS_UNTIL = 90              # below a minute and a half: seconds
MINUTES_UNTIL = 5400            # below an hour and a half: minutes
HOURS_UNTIL = 172800            # below two days: hours


def ago(span: timedelta) -> str:
    """A span as it is said in the journal and in a user's check: «89 с», «5 мин», «3 ч», «2 дн»."""
    seconds = int(span.total_seconds())
    if seconds < SECONDS_UNTIL:
        return f"{seconds} с"
    if seconds < MINUTES_UNTIL:
        return f"{seconds // MINUTE} мин"
    if seconds < HOURS_UNTIL:
        return f"{seconds // HOUR} ч"
    return f"{seconds // DAY} дн"
