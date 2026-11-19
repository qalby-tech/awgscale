"""Who is logged in, and who has guessed the password too many times."""
from __future__ import annotations

import collections
import secrets
from datetime import datetime, timedelta
from typing import Callable

from awgscale.core.clock import utc_now

Clock = Callable[[], datetime]

SESSION_ID_BYTES = 32
FAILURES_REMEMBERED = 20
FAILURES_ALLOWED = 8
FAILURE_WINDOW = timedelta(minutes=5)


class Sessions:
    """The logins that are open now: an id in the admin's cookie, and when it ends."""

    def __init__(self, ttl: timedelta, clock: Clock = utc_now) -> None:
        self.__ttl = ttl
        self.__clock = clock
        self.__ends: dict[str, datetime] = {}

    def open(self) -> str:
        session_id = secrets.token_urlsafe(SESSION_ID_BYTES)
        self.__ends[session_id] = self.__clock() + self.__ttl
        return session_id

    def is_open(self, session_id: str) -> bool:
        ends = self.__ends.get(session_id)
        if ends is not None and ends > self.__clock():
            return True
        self.__ends.pop(session_id, None)
        return False

    def close(self, session_id: str) -> None:
        self.__ends.pop(session_id, None)


class LoginAttempts:
    """Wrong passwords per address: after eight in five minutes the address waits."""

    def __init__(self, clock: Clock = utc_now) -> None:
        self.__clock = clock
        self.__failures: dict[str, collections.deque[datetime]] = collections.defaultdict(lambda: collections.deque(maxlen=FAILURES_REMEMBERED))

    def must_wait(self, address: str) -> bool:
        now = self.__clock()
        recent = [moment for moment in self.__failures[address] if now - moment < FAILURE_WINDOW]
        return len(recent) >= FAILURES_ALLOWED

    def failed(self, address: str) -> None:
        self.__failures[address].append(self.__clock())
