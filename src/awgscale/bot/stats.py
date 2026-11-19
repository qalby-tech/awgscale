"""The day's counters: exit downtime, peak online — and the daily summary made from them. The users' traffic of
the day is counted by the agent (`day`, from the moment the bucket began): the bot never holds every user."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import ValidationError

from awgscale.agent.state.clients import ClientStatus

from .agent import Agent, AgentError
from .models import Bucket, DayTraffic, ExitState, HistoryDay, State, Stats
from .settings import BotSettings
from .telegram import Admin
from .texts import EXPIRING_WITHIN, STALE_AFTER, STALE_SHOWN, Attention, report_text

log = logging.getLogger("awgscale.bot")

HISTORY_DAYS = 30
MIN_SAMPLES_FOR_FIRST_REPORT = 5       # a bot started a moment ago does not send an empty summary: it just begins the day
DAY_FORMAT = "%Y-%m-%d"


class StatsFile:
    """stats.json: read at the start, written beside and moved into place after every sample."""

    def __init__(self, path: Path) -> None:
        self.__path = path

    def load(self) -> Stats:
        try:
            return Stats.model_validate_json(self.__path.read_text())
        except (OSError, ValueError, ValidationError):
            return Stats.fresh()

    def save(self, stats: Stats) -> None:
        temporary = self.__path.with_name(self.__path.name + ".tmp")
        temporary.write_text(stats.model_dump_json())
        os.replace(temporary, self.__path)


class Sampler:
    def __init__(self, settings: BotSettings, agent: Agent, admin: Admin, file: StatsFile) -> None:
        self.__settings = settings
        self.__agent = agent
        self.__admin = admin
        self.__file = file
        self.__stats = file.load()

    @property
    def stats(self) -> Stats:
        return self.__stats

    def fold(self, state: State) -> None:
        """Folds one snapshot of the state into the current day (the users' traffic is the agent's to count)."""
        stats = self.__stats
        bucket = stats.bucket
        for exit_ in state.exits:
            if exit_.up:
                bucket.exit_down.setdefault(exit_.name, 0)
            else:
                bucket.exit_down[exit_.name] = bucket.exit_down.get(exit_.name, 0) + self.__settings.sample_seconds
                if stats.prev_exit_state.get(exit_.name, ExitState.UP) == ExitState.UP:
                    bucket.incidents += 1
            stats.prev_exit_state[exit_.name] = exit_.state.value
        bucket.peak_online = max(bucket.peak_online, state.users.online)
        bucket.samples += 1

    async def __attention(self) -> Attention:
        """Who the summary names, asked of the agent a page each."""
        stale = await self.__agent.clients(ClientStatus.STALE, STALE_AFTER, limit=STALE_SHOWN)
        soon = await self.__agent.clients(ClientStatus.EXPIRING, EXPIRING_WITHIN)
        return Attention(stale=stale, soon=soon)

    async def __day(self) -> DayTraffic:
        """The users' traffic since the bucket began; asking also keeps the agent counting it."""
        return await self.__agent.day(self.__stats.bucket.since)

    async def report(self, state: State, final: bool) -> str:
        """The summary of the current day, as text. Raises AgentError."""
        last_day = self.__stats.history[-1] if self.__stats.history else None
        text, _ = report_text(state, self.__stats.bucket, last_day, self.__settings.local_zone, final, await self.__day(), await self.__attention())
        return text

    async def __close_day(self, state: State, today: str) -> None:
        """The daily summary goes out, and the counters start again."""
        stats = self.__stats
        if stats.last_report_day is None and stats.bucket.samples < MIN_SAMPLES_FOR_FIRST_REPORT:
            stats.last_report_day = today
            return
        last_day = stats.history[-1] if stats.history else None
        text, totals = report_text(state, stats.bucket, last_day, self.__settings.local_zone, True, await self.__day(), await self.__attention())
        await self.__admin.notify(text)
        stats.history = [*stats.history, HistoryDay(day=today, **totals.model_dump())][-HISTORY_DAYS:]
        stats.bucket = Bucket(since=state.time)
        stats.last_report_day = today
        await self.__day()                  # the agent starts counting the new day now

    async def sample(self) -> None:
        """One sample: the state is folded in; the first one after the report hour closes the day."""
        try:
            state = await self.__agent.state()
            await self.__day()              # the agent counts the users' traffic of the day from the first time it is asked
        except AgentError:
            return
        except ValidationError as e:
            log.error("sampler: the state is not one the bot can read: %s", e)
            return
        self.fold(state)
        local_now = state.time.astimezone(self.__settings.local_zone)
        today = local_now.strftime(DAY_FORMAT)
        if local_now.hour >= self.__settings.report_hour and self.__stats.last_report_day != today:
            try:
                await self.__close_day(state, today)
            except (AgentError, ValidationError) as e:
                log.error("sampler: the summary was not made: %s", e)
                return
        self.__file.save(self.__stats)
