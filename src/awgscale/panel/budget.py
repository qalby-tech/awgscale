"""Invitation links are the only part of this server strangers reach: every address gets a small budget of requests."""
from __future__ import annotations

import collections
import ipaddress
from datetime import datetime, timedelta
from enum import IntEnum

from .sessions import Clock, utc_now

BUDGET = 30
WINDOW = timedelta(minutes=10)
HITS_REMEMBERED = 60
ADDRESSES_BEFORE_SWEEP = 2000
SWEEP_NOT_SOONER = timedelta(seconds=30)
ADDRESSES_AT_MOST = 20000
UNKNOWN_ADDRESS = "?"
IPV6_CUSTOMER_PREFIX = 64


class Cost(IntEnum):
    """What a request takes out of an address's budget."""

    PAGE = 1                   # the page, or a good answer
    REDEEM_FAILED = 2          # on top of the attempt, when no user came of it
    REDEEM = 3                 # an attempt to create a user
    WRONG_SECRET = 4           # on top of the look, when the secret was wrong: five in all


def _owner(address: str) -> str:
    """Whom the budget belongs to: one IPv6 customer is a whole /64, not one address."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return UNKNOWN_ADDRESS
    if parsed.version == 6:
        return str(ipaddress.ip_network(f"{address}/{IPV6_CUSTOMER_PREFIX}", strict=False).network_address)
    return str(parsed)


class InviteBudget:
    """30 points per 10 minutes for an address."""

    def __init__(self, clock: Clock = utc_now) -> None:
        self.__clock = clock
        self.__hits: dict[str, collections.deque[tuple[datetime, int]]] = {}
        self.__swept: datetime | None = None

    def __forget_the_quiet(self, now: datetime) -> None:
        """At most twice a minute, once the table has grown."""
        if len(self.__hits) <= ADDRESSES_BEFORE_SWEEP or (self.__swept is not None and now - self.__swept <= SWEEP_NOT_SOONER):
            return
        self.__swept = now
        for owner in [owner for owner, hits in self.__hits.items() if not hits or now - hits[-1][0] > WINDOW]:
            del self.__hits[owner]

    def allows(self, address: str, cost: Cost) -> bool:
        """Takes `cost` out of the address's budget; False when it does not fit."""
        now = self.__clock()
        owner = _owner(address)
        self.__forget_the_quiet(now)
        if owner not in self.__hits and len(self.__hits) >= ADDRESSES_AT_MOST:
            return False                                 # the table is full of strangers: no new ones until it drains
        hits = self.__hits.setdefault(owner, collections.deque(maxlen=HITS_REMEMBERED))
        spent = sum(points for moment, points in hits if now - moment < WINDOW)
        if spent + cost > BUDGET:
            return False
        hits.append((now, int(cost)))
        return True
