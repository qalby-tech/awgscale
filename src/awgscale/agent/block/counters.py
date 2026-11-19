"""The numbers of the day beside the lists: how much was asked, how much was stopped, and by which list."""
from __future__ import annotations

from ..adguard.api import AdGuard
from ..adguard.models import Counters


class AdGuardCounters:
    """AdGuard's counters of the day. A blocker that does not answer gives zeros: the page still shows the lists."""

    def __init__(self, adguard: AdGuard) -> None:
        self.__adguard = adguard

    async def today(self) -> Counters:
        try:
            return await self.__adguard.counters()
        except Exception:
            return Counters()
