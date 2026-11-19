"""How sizes, spans and moments are written in the bot's messages."""
from __future__ import annotations

from datetime import datetime

from awgscale.core.clock import DAY, HOUR, MINUTE

BYTE_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ")


def escaped(value: object) -> str:
    """Text made safe for Telegram's HTML."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def size_text(count: float) -> str:
    size = float(count)
    for unit in BYTE_UNITS:
        last = unit == BYTE_UNITS[-1]
        if size < 1024 or last:
            return f"{size:.0f} {unit}" if unit == BYTE_UNITS[0] or size >= 100 else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable: the last unit takes everything")


def duration_text(seconds: float) -> str:
    whole = int(seconds)
    if whole < MINUTE:
        return f"{whole} с"
    if whole < HOUR:
        return f"{whole // MINUTE} мин"
    return f"{whole // HOUR} ч {whole % HOUR // MINUTE} мин"


def ago(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "никогда"
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < MINUTE:
        return f"{seconds} с назад"
    if seconds < HOUR:
        return f"{seconds // MINUTE} мин назад"
    if seconds < DAY:
        return f"{seconds // HOUR} ч назад"
    return f"{seconds // DAY} дн назад"
