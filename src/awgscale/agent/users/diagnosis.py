"""«У него не работает»: what the entry server knows about one user, as a short
list of findings. Read-only. Counts only — never which sites the user asks for."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable

from awgscale.watch.health import ExitState

from ..commands import Diag
from ..errors import Refused
from ..ports import ClientCounts, TunnelPeers, UsersDirectory, WatchStatus
from ..spans import ago
from ..tunnel import Peer
from .models import Diagnosis, Finding, FindingState
from .pauses import PauseStore

MEASURE_SECONDS = 2.0                          # the counters are read twice, this far apart
BITS_PER_SECOND = 4                            # bytes in two seconds → bits per second
ONLINE_WITHIN = timedelta(seconds=180)
SOME_TRAFFIC = 20000                           # bits per second, both ways
MEGABIT = 1e6
STATUS_TIMEOUT = 20.0
USED_ENOUGH = 5_000_000                        # bytes: a device that moved this much and never asked our DNS asks somebody else's
TERM_SOON = timedelta(days=3)

Sleep = Callable[[float], Awaitable[None]]


class DiagnosisService:
    def __init__(self, directory: UsersDirectory, tunnel: TunnelPeers, pauses: PauseStore, watch: WatchStatus, counts: ClientCounts, sleep: Sleep = asyncio.sleep) -> None:
        self.__directory = directory
        self.__tunnel = tunnel
        self.__pauses = pauses
        self.__watch = watch
        self.__counts = counts
        self.__sleep = sleep

    async def __peer(self, address: str) -> Peer | None:
        return next((peer for peer in await self.__tunnel.peers() if peer.address == address), None)

    async def check(self, request: Diag) -> Diagnosis:
        name = request.name
        user = next((each for each in await self.__directory.list(force=True) if each.name == name), None)
        if user is None:
            raise Refused("такого пользователя нет")
        now = datetime.now(UTC).replace(microsecond=0)
        before = await self.__peer(user.ip)
        await self.__sleep(MEASURE_SECONDS)
        after = await self.__peer(user.ip)
        findings: list[Finding] = []

        def say(state: FindingState, text: str) -> None:
            findings.append(Finding(state=state, text=text))

        if before is None or after is None:
            say(FindingState.BAD, "Ключа нет в туннеле сервера — пользователя нужно создать заново")
            return Diagnosis(name=name, items=findings)
        online = after.handshake is not None and now - after.handshake < ONLINE_WITHIN
        if name in await self.__pauses.read():
            say(FindingState.BAD, "Приостановлен: ключ на месте, но трафик не пропускается")
        if after.handshake is None:
            say(FindingState.BAD, "Ни разу не подключался: код не добавлен в приложение, VPN не включали, или приложение старое (нужна свежая AmneziaVPN)")
        elif online:
            say(FindingState.OK, f"На связи, последняя связь {ago(now - after.handshake)} назад")
        else:
            say(FindingState.WARN, f"Не на связи {ago(now - after.handshake)}: VPN на устройстве выключен или у него нет интернета")
        if online:
            down = (after.sent - before.sent) * BITS_PER_SECOND
            up = (after.received - before.received) * BITS_PER_SECOND
            if down + up > SOME_TRAFFIC:
                say(FindingState.OK, f"Сейчас идёт трафик: ↓ {down / MEGABIT:.1f} Мбит/с, ↑ {up / MEGABIT:.1f} Мбит/с")
            else:
                say(FindingState.OK, "Сейчас трафика почти нет")
        # the exit this user is on
        status = await self.__watch.status(STATUS_TIMEOUT)
        exits = status.exits if status is not None else []
        mine = next((exit_ for exit_ in exits if user.ip in [str(address) for address in exit_.clients]), None)
        if mine is not None and mine.state is ExitState.UP:
            say(FindingState.OK, f"Выход {mine.name} работает")
        elif mine is not None:
            say(FindingState.BAD, f"Его выход {mine.name} недоступен")
        elif exits and not any(exit_.state is ExitState.UP for exit_ in exits):
            say(FindingState.BAD, "Все выходы недоступны: зарубежные сайты не открываются ни у кого")
        elif exits:
            say(FindingState.WARN, "Не закреплён ни за одним выходом — закрепится при подключении")
        # does the device ask our DNS at all? (a count for the day, nothing more)
        if after.handshake is not None:
            try:
                asked = await self.__counts.asked_by(user.ip)
                if asked:
                    say(FindingState.OK, f"Пользуется нашим DNS: {asked} запросов за сутки")
                elif online and after.received + after.sent > USED_ENOUGH:
                    say(FindingState.BAD, "Не спрашивает наш DNS: на устройстве включён «частный DNS» или защищённый DNS в браузере. Сайты из списка могут идти мимо заграницы, реклама не режется")
            except Exception:
                say(FindingState.WARN, "Блокировщик рекламы (DNS) не отвечает")
        if user.expires_at is not None:
            left = user.expires_at.replace(microsecond=0) - now
            say(FindingState.WARN if left < TERM_SOON else FindingState.OK, f"Срок истекает через {ago(max(timedelta(0), left))}")
        return Diagnosis(name=name, endpoint=(after.endpoint or "").rsplit(":", 1)[0] or None, with_endpoint=True, items=findings)
