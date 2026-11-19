"""What Prometheus is asked: the traffic of the last days, and when a user was last in touch."""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel, Field

from awgscale.core.clock import DAY, HOUR

from .settings import PanelSettings

# the admin's day is Moscow's; UTC+3 all year round since 2014
MSK_OFFSET = 3 * 3600
HOURS_SHOWN = 24
DAYS_SHOWN = 30
RANGE_TIMEOUT = 20.0
INSTANT_TIMEOUT = 10.0
REPORT_FRESH = timedelta(seconds=30)       # Prometheus is asked at most twice a minute, whatever the number of open pages
SEEN_FRESH = timedelta(seconds=120)
LEAST_BYTES = 0.5                  # less than this over a bucket is nothing

GROWTH_QUERY = "sum by (user, dir) (increase(vpn_user_bytes_total[{window}]))"
# The tunnel forgets every handshake when it is restarted (a reboot), and a
# user who has not reconnected yet would read "never connected" with months of
# traffic behind them. The traffic counters kept in Prometheus know better: the
# last moment a user's counter grew.
SEEN_QUERY = 'max by (user) (max_over_time((timestamp(vpn_user_bytes_total{dir="down"}) and on(user) (sum by (user) (rate(vpn_user_bytes_total[5m])) > 0))[30d:5m]))'

PROMETHEUS_SUCCESS = "success"     # the "status" of an answer that holds a result

Series = list[dict[str, Any]]
AskRange = Callable[[str, int, int, int], Awaitable[Series]]
AskInstant = Callable[[str, int], Awaitable[Series]]


class Direction(StrEnum):
    """The `dir` label of vpn_user_bytes_total: the user's download or upload."""

    DOWN = "down"
    UP = "up"


class UserTraffic(BaseModel):
    """One user's bytes, [download, upload] per bucket, oldest first."""

    hours: list[list[int]] = Field(description="The last 24 hours by hour")
    days: list[list[int]] = Field(description="The last 30 days by day")
    totals: dict[str, list[int]] = Field(default_factory=dict, description='The sums: "day" over the hours, "month" over the days')


class TrafficReport(BaseModel):
    days: list[str] = Field(description="The days of the report, Moscow time, oldest first")
    hours: list[str] = Field(description="The hours of the report, Moscow time, oldest first")
    users: dict[str, UserTraffic] = Field(description="Per user; a user without a byte in the report is left out")


class Bucketing(BaseModel):
    """One way the report cuts time: by hour or by day."""

    field: str = Field(description="The field of UserTraffic it fills")
    end: int = Field(description="Unix time the running bucket ends at")
    step: int = Field(description="Seconds in a bucket")
    count: int = Field(description="How many buckets")
    window: str = Field(description="The same step as Prometheus writes it")
    key_format: str = Field(description="How a bucket is named, strftime")

    @property
    def first(self) -> int:
        return self.end - self.step * (self.count - 1)

    def keys(self) -> list[str]:
        starts = [self.end - self.step * (index + 1) for index in range(self.count - 1, -1, -1)]
        return [time.strftime(self.key_format, time.gmtime(start + MSK_OFFSET)) for start in starts]


def bucketings(now: int) -> list[Bucketing]:
    hour_end = (now // HOUR + 1) * HOUR
    day_end = ((now + MSK_OFFSET) // DAY + 1) * DAY - MSK_OFFSET
    return [
        Bucketing(field="hours", end=hour_end, step=HOUR, count=HOURS_SHOWN, window="1h", key_format="%Y-%m-%dT%H"),
        Bucketing(field="days", end=day_end, step=DAY, count=DAYS_SHOWN, window="1d", key_format="%Y-%m-%d"),
    ]


def _empty_usage() -> UserTraffic:
    return UserTraffic(hours=[[0, 0] for _ in range(HOURS_SHOWN)], days=[[0, 0] for _ in range(DAYS_SHOWN)])


async def traffic_report(now: int, ask: AskRange) -> TrafficReport:
    """The last 30 days by day and the last 24 hours by hour (Moscow time),
    oldest first; per user [down, up] for each, zeros filled in; a user without
    a byte in them is left out. Each bucket is the counters' growth over it,
    asked at the bucket's end (the running one is asked at its future end and
    holds what has come so far)."""
    plan = bucketings(now)
    users: dict[str, UserTraffic] = {}
    for bucketing in plan:
        for series in await ask(GROWTH_QUERY.format(window=bucketing.window), bucketing.first, bucketing.end, bucketing.step):
            labels = series.get("metric", {})
            name = labels.get("user")
            side = labels.get("dir")
            if not name or side not in (Direction.DOWN, Direction.UP):
                continue
            for moment, value in series.get("values") or []:
                index = round((float(moment) - bucketing.first) / bucketing.step)
                amount = float(value)
                if 0 <= index < bucketing.count and amount == amount and amount >= LEAST_BYTES:      # amount == amount: not NaN
                    usage = users.setdefault(name, _empty_usage())
                    getattr(usage, bucketing.field)[index][0 if side == Direction.DOWN else 1] = int(round(amount))
    for usage in users.values():
        usage.totals = {
            "day": [sum(pair[0] for pair in usage.hours), sum(pair[1] for pair in usage.hours)],
            "month": [sum(pair[0] for pair in usage.days), sum(pair[1] for pair in usage.days)],
        }
    by_field = {bucketing.field: bucketing.keys() for bucketing in plan}
    return TrafficReport(days=by_field["days"], hours=by_field["hours"], users=users)


class Prometheus:
    """The history, asked sparingly: answers are kept for a while and shared by every open page."""

    def __init__(self, settings: PanelSettings) -> None:
        self.__settings = settings
        self.__report: dict[str, Any] | None = None
        self.__report_at: datetime | None = None
        self.__report_lock = asyncio.Lock()        # pages that ask at the same moment share one question to Prometheus
        self.__seen: dict[str, datetime] = {}
        self.__seen_at: datetime | None = None

    async def __get(self, path: str, params: dict[str, str | int], timeout: float) -> Any:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(self.__settings.prometheus + path, params=params)
        response.raise_for_status()
        return response.json()

    async def ask_range(self, query: str, start: int, end: int, step: int) -> Series:
        """Raises when Prometheus cannot be asked or does not answer with a result."""
        data = await self.__get("/api/v1/query_range", {"query": query, "start": start, "end": end, "step": step}, RANGE_TIMEOUT)
        if data.get("status") != PROMETHEUS_SUCCESS:
            raise ValueError(data.get("error") or "prometheus")
        return data["data"]["result"]

    async def ask_instant(self, query: str, moment: int) -> Series:
        data = await self.__get("/api/v1/query", {"query": query, "time": moment}, INSTANT_TIMEOUT)
        return data["data"]["result"]

    async def traffic(self, ask: AskRange | None = None) -> dict[str, Any]:
        """What the page is answered about the traffic history."""
        if not self.__settings.prometheus:
            return {"ok": False, "error": "История не подключена"}
        async with self.__report_lock:
            now = int(time.time())
            moment = datetime.fromtimestamp(now, UTC)
            if self.__report is None or self.__report_at is None or abs(moment - self.__report_at) >= REPORT_FRESH:
                try:
                    report = await traffic_report(now, ask or self.ask_range)
                except Exception:
                    return {"ok": False, "error": "История сейчас недоступна"}
                self.__report = {"ok": True, "time": now, **report.model_dump()}
                self.__report_at = moment
            return self.__report

    def forget_traffic(self) -> None:
        self.__report = None

    async def last_seen(self, now: datetime, ask: AskInstant | None = None) -> dict[str, datetime]:
        """{user: the moment of their last traffic, to the second}; asked at most
        every two minutes, and the previous answer is kept when Prometheus does not give one."""
        if self.__seen_at is not None and abs(now - self.__seen_at) < SEEN_FRESH:
            return self.__seen
        self.__seen_at = now
        if ask is None and not self.__settings.prometheus:
            return self.__seen
        try:
            rows = await (ask or self.ask_instant)(SEEN_QUERY, int(now.timestamp()))
            self.__seen = {
                row["metric"]["user"]: datetime.fromtimestamp(int(float(row["value"][1])), UTC) for row in rows if row.get("metric", {}).get("user")
            }
        except Exception:
            pass
        return self.__seen
