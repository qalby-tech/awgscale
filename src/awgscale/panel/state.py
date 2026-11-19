"""The state of the cascade as the page sees it: asked from the agent every few seconds, with the exits' speeds worked out
between two looks. The users are counted by the agent; a page of them is asked for when the page wants one."""
from __future__ import annotations

import collections
from datetime import datetime
from typing import Callable

from awgscale.agent.commands import Command
from awgscale.core.clock import utc_now

from .agent import AgentClient
from .looks import ClientsLook, TrafficLook, ExitLook, HistoryPoint, PanelState, Totals, UsersLook
from .prometheus import Prometheus
from .settings import PanelSettings

STATE_TIMEOUT = 30.0
BITS = 8
# what a good look cannot do without; one without them fails the way it always has, the poll error naming the part
GOOD_LOOK_PARTS = ("time", "users", "exits")

Clock = Callable[[], datetime]


def _rate(now: int | float | None, before: int | float | None, seconds: float) -> float:
    """Bits per second a counter of bytes grew by between two looks."""
    grown = (now or 0) - (before or 0)
    return max(grown, 0) / seconds * BITS if seconds > 0 else 0.0


class StateBoard:
    def __init__(self, settings: PanelSettings, agent: AgentClient, prometheus: Prometheus, clock: Clock = utc_now) -> None:
        self.__settings = settings
        self.__agent = agent
        self.__prometheus = prometheus
        self.__clock = clock
        self.__latest = PanelState(ok=False, error="ещё нет данных")
        self.__history: collections.deque[HistoryPoint] = collections.deque(maxlen=settings.history_points)
        self.__previous: PanelState | None = None
        self.__traffic: TrafficLook | None = None

    @property
    def latest(self) -> PanelState:
        """The last good look (marked stale when a later one failed)."""
        return self.__latest.model_copy()

    def snapshot(self) -> PanelState:
        """What the page is given: the last look and the speed chart."""
        return self.__latest.model_copy(update={"history": list(self.__history)})

    def __failed(self, error: str | None, look: PanelState | None = None) -> None:
        self.__latest.stale = True
        self.__latest.poll_error = error
        if look is not None and self.__latest.time is None:
            # no good look yet: the failed one is shown, over what was shown before
            merged = {**self.__latest.model_dump(exclude_unset=True), **look.model_dump(exclude_unset=True)}
            self.__latest = PanelState.model_validate(merged)

    def __fill_in(self, look: PanelState, before: PanelState | None) -> None:
        """Adds what the agent does not know: how fast the exits go, and the totals of the speed chart."""
        for part in GOOD_LOOK_PARTS:
            if part not in look.model_fields_set:
                raise KeyError(part)
        now = look.time
        users = look.users or UsersLook()
        exits = look.exits or []
        seconds = (now - before.time).total_seconds() if before is not None and now is not None and before.time is not None else 0.0
        exits_before: dict[int | None, ExitLook] = {exit_.index: exit_ for exit_ in before.exits or []} if before is not None else {}
        for exit_ in exits:
            was_exit = exits_before.get(exit_.index)
            if was_exit is not None and seconds <= 0:          # the agent's look has not changed since: its speeds stay
                exit_.down_bps = was_exit.down_bps
                exit_.up_bps = was_exit.up_bps
                continue
            exit_.down_bps = _rate(exit_.iface_rx, was_exit.iface_rx, seconds) if was_exit is not None else 0.0
            exit_.up_bps = _rate(exit_.iface_tx, was_exit.iface_tx, seconds) if was_exit is not None else 0.0
        look.totals = Totals(
            down_bps=float(users.down_bps),
            up_bps=float(users.up_bps),
            via_exits_down_bps=sum(exit_.down_bps for exit_ in exits),
            online=users.online,
            clients=users.users,
        )

    async def look(self) -> None:
        """One look at the cascade. It never raises: a look that failed leaves the last good one, marked stale."""
        try:
            look = PanelState.model_validate(await self.__agent.call(Command.STATE, timeout=STATE_TIMEOUT))
            look.fetched_at = self.__clock()
            if not look.ok:
                self.__failed(look.error, look)
                return
            same = self.__previous is not None and look.time == self.__previous.time      # the agent looks as often as it is polled
            self.__fill_in(look, self.__previous)
            totals = look.totals or Totals()
            if not same:
                self.__history.append(HistoryPoint(t=look.time, down=totals.down_bps, up=totals.up_bps))
                self.__previous = look
            self.__latest = look
        except Exception as e:
            self.__failed(str(e))

    async def clients(self, query: str) -> ClientsLook:
        """One page of users, as the agent answers `clients` (`query`: base64 of its JSON). A user the tunnel
        does not know since a restart is given the moment Prometheus last saw their traffic."""
        page = ClientsLook.model_validate(await self.__agent.call(Command.CLIENTS, query, timeout=STATE_TIMEOUT))
        if not page.ok:
            return page
        seen = await self.__prometheus.last_seen(self.__clock())
        now = page.time or self.__clock()
        for client in page.clients or []:
            if client.handshake is None:                    # the tunnel was restarted and they are not back yet
                moment = seen.get(client.name or "")
                client.handshake = None if moment is None else min(moment, now)
        return page

    async def traffic(self) -> TrafficLook | None:
        """Every user's byte counters (for the metrics), asked once per look; None when the agent did not give them."""
        latest = self.__latest
        if latest.time is None:
            return None
        if self.__traffic is not None and self.__traffic.time == latest.time:
            return self.__traffic
        try:
            answer = TrafficLook.model_validate(await self.__agent.call(Command.TRAFFIC, timeout=STATE_TIMEOUT))
        except ValueError:
            return None
        if not answer.ok:
            return None
        self.__traffic = answer
        return answer
