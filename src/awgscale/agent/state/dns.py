"""The blocker's day for the state: its counters, and the tops counted from its log."""
from __future__ import annotations

from ..ports import DayCounters, DayTops
from .models import DnsShown

MILLISECONDS = 1000


class DnsDay:
    def __init__(self, tops: DayTops, counters: DayCounters) -> None:
        self.__tops = tops
        self.__counters = counters

    async def stats(self) -> DnsShown:
        """Never raises: the first page must still render without the blocker."""
        tops = await self.__tops.today()
        try:
            counters = await self.__counters.counters()
            return DnsShown(counters=counters, average_ms=round(counters.average_seconds * MILLISECONDS, 1), tops=tops)
        except Exception as e:
            return DnsShown(counters=None, error=str(e), tops=tops)
