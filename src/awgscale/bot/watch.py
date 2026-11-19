"""What the bot says by itself: alarms about the cascade, and the changes made in the panel."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from functools import partial

from awgscale.core.clock import utc_now

from awgscale.agent.state.clients import ClientStatus

from .agent import Agent, AgentError
from .format import escaped
from .models import Client, ExitState, State
from .settings import BotSettings
from .telegram import Admin

log = logging.getLogger("awgscale.bot")


LOAD_PER_CORE = 2                  # load above this many per core is high
MEMORY_PERCENT = 90
DISK_PERCENT = 85
KEY_ENDS_WITHIN = timedelta(days=1)
KEYS_ASKED = 2000                  # at most this many keys ending within a day are warned about at one look
ENTRY_TEXT_LENGTH = 300
ENTRIES_SHOWN = 10


class Alarm:
    """Fires after `fire_after` bad checks in a row, clears after `clear_after` good ones.

    The change counts only once the admin was really told: a message that could not
    be delivered (the bot's own way to Telegram goes through an exit, and it breaks
    exactly when an exit goes or comes back) is said again on the next check. An
    alarm that never got through while the trouble lasted is told together with the
    all-clear, with when it began and when it ended."""

    def __init__(
        self,
        admin: Admin,
        fire_text: str,
        clear_text: str,
        fire_after: int = 1,
        clear_after: int = 1,
        zone: tzinfo = UTC,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.__admin = admin
        self.__fire_text = fire_text
        self.__clear_text = clear_text
        self.__fire_after = fire_after
        self.__clear_after = clear_after
        self.__zone = zone
        self.__clock = clock
        self.__bad = 0
        self.__good = 0
        self.__active = False
        self.__bad_since: datetime | None = None
        self.__unsaid: str | None = None

    @property
    def active(self) -> bool:
        return self.__active

    def __moment(self, moment: datetime, other: datetime) -> str:
        local = moment.astimezone(self.__zone)
        if local.date() == other.astimezone(self.__zone).date():
            return f"{local:%H:%M}"
        return f"{local:%d.%m %H:%M}"

    async def check(self, is_bad: bool, detail: str = "") -> None:
        now = self.__clock()
        if is_bad:
            self.__bad += 1
            self.__good = 0
            if self.__bad == 1 and not self.__active and self.__unsaid is None:
                self.__bad_since = now
            if not self.__active and self.__bad >= self.__fire_after:
                text = "🔴 " + self.__fire_text + (f" {detail}" if detail else "")
                self.__active = await self.__admin.notify(text)
                self.__unsaid = None if self.__active else text
            return
        self.__good += 1
        self.__bad = 0
        if self.__good < self.__clear_after:
            return
        if self.__active:
            told = await self.__admin.notify("🟢 " + self.__clear_text)
            self.__active = not told
            return
        if self.__unsaid is not None and self.__bad_since is not None:
            began = self.__moment(self.__bad_since, now)
            ended = self.__moment(now, self.__bad_since)
            text = f"🟢 {self.__clear_text}\nНе дошло вовремя: {self.__unsaid} — с {began} до {ended}."
            if await self.__admin.notify(text):
                self.__unsaid = None


class Watch:
    def __init__(self, settings: BotSettings, agent: Agent, admin: Admin) -> None:
        self.__settings = settings
        self.__agent = agent
        self.__admin = admin
        alarm = partial(Alarm, admin, zone=settings.local_zone)
        self.__unreachable = alarm("Бот не получает данные от входного сервера.", "Данные от входного сервера снова поступают.", 2, 1)
        self.__tunnel = alarm(
            "Туннель для пользователей на входном сервере упал — никто не может подключиться.",
            "Туннель для пользователей снова поднят.",
        )
        self.__dns = alarm(
            "Блокировщик рекламы (DNS) не отвечает — у пользователей могут не открываться сайты.",
            "Блокировщик рекламы снова отвечает.",
            2,
            2,
        )
        self.__all_down = alarm(
            "<b>Все выходы недоступны.</b> Зарубежный трафик заблокирован, российский работает.",
            "Хотя бы один выход снова работает.",
        )
        self.__load = alarm("Высокая нагрузка на входном сервере уже 5 минут:", "Нагрузка на входном сервере пришла в норму.", 10, 6)
        self.__memory = alarm("Память входного сервера почти заполнена:", "Память входного сервера освободилась.", 10, 6)
        self.__disk = alarm("Диск входного сервера почти заполнен:", "На диске входного сервера снова достаточно места.", 2, 2)
        self.__exits: dict[str, Alarm] = {}
        self.__warned_keys: set[str] = set()
        # the journal entries seen on the last successful look (by identity, not by time); None until the first one
        self.__told: set[tuple[int | float | str | None, str | None, str | None, str | None]] | None = None

    # ── the journal, said on ─────────────────────────────────────────────────

    async def echo_journal(self) -> None:
        """Says on to the admin what appeared in the change journal since the last look."""
        try:
            entries = await self.__agent.journal()
        except AgentError:
            return
        seen = {entry.identity for entry in entries}
        if self.__told is None:
            self.__told = seen                         # nothing from before the start
            return
        told = self.__told
        fresh = [entry for entry in reversed(entries) if entry.identity not in told and entry.for_admin]
        lines = [("🔴 " if entry.bad else "") + escaped(str(entry.text or "")[:ENTRY_TEXT_LENGTH]) for entry in fresh]
        more = f"\n… и ещё {len(lines) - ENTRIES_SHOWN}" if len(lines) > ENTRIES_SHOWN else ""
        text = "📝 " + "\n".join(lines[:ENTRIES_SHOWN]) + more
        if not lines or await self.__admin.notify(text):
            # only what was really delivered counts as told — and stays told: the store may answer
            # with fewer entries for a while (a restart), and what comes back then is not news
            self.__told = told | seen

    # ── the alarms ───────────────────────────────────────────────────────────

    def __exit_alarm(self, name: str) -> Alarm:
        if name not in self.__exits:
            self.__exits[name] = Alarm(
                self.__admin,
                f"Выход <b>{escaped(name)}</b> недоступен — его пользователи переведены на другой.",
                f"Выход <b>{escaped(name)}</b> снова работает.",
                zone=self.__settings.local_zone,
            )
        return self.__exits[name]

    async def __check_exits(self, state: State) -> None:
        for exit_ in state.exits:
            alarm = self.__exit_alarm(exit_.name)
            if exit_.state is ExitState.UNKNOWN:       # just booted: no verdict about this exit yet — neither bad nor good news
                continue
            await alarm.check(not exit_.up)
        nothing_works = bool(state.exits) and all(exit_.state not in (ExitState.UP, ExitState.UNKNOWN) for exit_ in state.exits)
        await self.__all_down.check(nothing_works)

    async def __check_entry(self, state: State) -> None:
        entry = state.entry
        await self.__load.check(entry.load1 > entry.cpus * LOAD_PER_CORE, f"{entry.load1} на {entry.cpus} ядра.")
        await self.__memory.check(entry.memory_percent >= MEMORY_PERCENT, f"{entry.memory_percent}%.")
        if entry.disk_pct is not None:
            await self.__disk.check(entry.disk_pct >= DISK_PERCENT, f"{entry.disk_pct}%.")

    async def __warn_about_keys(self, state: State) -> None:
        """The keys that end within a day, asked of the agent (a page of them, not every user)."""
        zone = self.__settings.local_zone
        ending: list[Client] = []
        while True:
            page = await self.__agent.clients(ClientStatus.EXPIRING, KEY_ENDS_WITHIN, offset=len(ending))
            ending += page.clients
            if not page.clients or len(ending) >= page.total or len(ending) >= KEYS_ASKED:
                break
        for client in ending:
            ends = client.expires_at
            if ends is None or client.name in self.__warned_keys:
                continue
            if timedelta(0) < ends - state.time < KEY_ENDS_WITHIN:
                self.__warned_keys.add(client.name)
                await self.__admin.notify(f"⏳ Ключ пользователя <code>{escaped(client.name)}</code> истекает {ends.astimezone(zone):%d.%m в %H:%M}.")
        if len(ending) >= page.total:                      # those warned who are no longer about to lose the key are forgotten
            self.__warned_keys &= {client.name for client in ending}

    async def check_cascade(self) -> None:
        """One look at the state: every alarm is told whether its thing is bad now."""
        try:
            state = await self.__agent.state()
        except AgentError as e:
            await self.__unreachable.check(True, escaped(e.reason("")))
            return
        await self.__unreachable.check(False)
        await self.__tunnel.check(not state.entry.client_if_up)
        await self.__dns.check(not state.dns.ok)
        await self.__check_exits(state)
        await self.__check_entry(state)
        try:
            await self.__warn_about_keys(state)
        except AgentError:
            pass                                        # asked again at the next look

    async def look(self) -> None:
        """What the scheduled job does: the journal, then the alarms; one failing does not stop the other."""
        try:
            await self.echo_journal()
        except Exception:
            log.exception("journal echo")
        try:
            await self.check_cascade()
        except Exception:
            log.exception("watcher")
