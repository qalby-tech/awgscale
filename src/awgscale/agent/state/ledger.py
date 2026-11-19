"""The traffic of a day, per user, counted by the sampling job: whoever makes a
daily summary (the bot) names the moment its day began and is answered with
the totals and the users who took the most since then — never with every user.

A day is counted from the first time it is asked about; the counters are folded
in at every look (a counter that went back means the tunnel restarted: what it
shows now is the growth). Kept in a file now and then, so a restart of the
agent does not lose the day."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field, ValidationError

from ..files import write_file
from ..wire import unix_or_zero
from .clients import ClientRow

log = logging.getLogger("awgscale.agent")

DAYS_KEPT = 2                   # the day being counted and the one before it (asked once more for its summary)
TOP_USERS = 5


def grown(now: int, before: int | None) -> int:
    """How much a counter grew since the last look: a counter that went back started again from zero; a user seen for the first time adds nothing."""
    if before is None:
        return 0
    return now - before if now >= before else now


@dataclass(slots=True)
class UserDay:
    rx: int = 0
    tx: int = 0
    seen: bool = False          # online at least once


@dataclass(slots=True)
class Day:
    since: datetime             # as the asker named it
    counted_from: datetime      # when the counting really began
    users: dict[str, UserDay] = field(default_factory=dict)


class DayAnswer(BaseModel):
    """The answer of `day`."""

    since: datetime = Field(description="The moment the asker's day began")
    counted_from: datetime = Field(description="When the counting began: later than `since` for a day first asked about late")
    down: int = Field(description="Bytes the users downloaded")
    up: int = Field(description="Bytes the users uploaded")
    active: int = Field(description="Users seen online")
    top: list[tuple[str, int, int]] = Field(description="The users who took the most: name, bytes received from them, bytes sent to them")

    def wire(self) -> dict[str, Any]:
        return {
            "ok": True,
            "since": unix_or_zero(self.since),
            "counted_from": unix_or_zero(self.counted_from),
            "down": self.down,
            "up": self.up,
            "active": self.active,
            "top": [{"name": name, "rx": rx, "tx": tx} for name, rx, tx in self.top],
        }


class StoredDay(BaseModel):
    since: datetime = Field(description="The moment the asker's day began")
    counted_from: datetime = Field(description="When the counting began")
    users: dict[str, tuple[int, int, bool]] = Field(default_factory=dict, description="Per user: received, sent, seen online")


class StoredDays(BaseModel):
    days: list[StoredDay] = Field(default_factory=list, description="The days counted")


class Ledger:
    def __init__(self, file: Path) -> None:
        self.__file = file
        self.__days: list[Day] = []
        self.__lock = threading.Lock()          # folded beside the loop, answered and kept on it

    def fold(self, rows: Sequence[ClientRow], before: Mapping[str, ClientRow]) -> None:
        """One look into every day being counted."""
        with self.__lock:
            self.__fold(rows, before)

    def __fold(self, rows: Sequence[ClientRow], before: Mapping[str, ClientRow]) -> None:
        for day in self.__days:
            users = day.users
            for row in rows:
                was = before.get(row.name)
                usage = users.get(row.name)
                if usage is None:
                    usage = UserDay()
                    users[row.name] = usage
                usage.rx += grown(row.rx, was.rx if was is not None else None)
                usage.tx += grown(row.tx, was.tx if was is not None else None)
                usage.seen = usage.seen or row.online

    def answer(self, since: datetime, now: datetime) -> DayAnswer:
        """The day that began at `since`; one not counted yet is counted from now on."""
        with self.__lock:
            return self.__answer(since, now)

    def __answer(self, since: datetime, now: datetime) -> DayAnswer:
        day = next((day for day in self.__days if day.since == since), None)
        if day is None:
            day = Day(since=since, counted_from=now)
            self.__days = [*self.__days, day][-DAYS_KEPT:]
        usage = day.users
        top = sorted(usage.items(), key=lambda item: item[1].rx + item[1].tx, reverse=True)[:TOP_USERS]
        return DayAnswer(
            since=since,
            counted_from=day.counted_from,
            down=sum(each.tx for each in usage.values()),          # tx on the server = what users downloaded
            up=sum(each.rx for each in usage.values()),
            active=sum(1 for each in usage.values() if each.seen),
            top=[(name, each.rx, each.tx) for name, each in top],
        )

    # ── kept in a file ───────────────────────────────────────────────────────

    def save(self) -> None:
        with self.__lock:
            stored = self.__stored()
        try:
            self.__file.parent.mkdir(parents=True, exist_ok=True)
            write_file(self.__file, stored.model_dump_json())
        except OSError as e:
            log.info("the day's traffic not kept: %s", e)

    def __stored(self) -> StoredDays:
        return StoredDays(
            days=[
                StoredDay(since=day.since, counted_from=day.counted_from, users={name: (each.rx, each.tx, each.seen) for name, each in day.users.items()})
                for day in self.__days
            ]
        )

    def load(self) -> None:
        try:
            stored = StoredDays.model_validate(json.loads(self.__file.read_text()))
        except (OSError, ValueError, ValidationError):
            return
        self.__days = [
            Day(since=day.since, counted_from=day.counted_from, users={name: UserDay(rx=rx, tx=tx, seen=seen) for name, (rx, tx, seen) in day.users.items()})
            for day in stored.days[-DAYS_KEPT:]
        ]
