"""The commands about the users and their groups."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from ..commands import AUTO_EXIT, NO_TERM, Expire, Qr, Suspend, Switch, UgroupsGet, UserExit
from ..errors import Refused, StoreDown
from ..files import read_text
from ..locks import file_lock
from ..ports import CascadeStore, GroupsStore, HandPins, UsersDirectory, UsersGate, WatchGate
from ..shell import SuspendSync
from .models import DeviceCode, ExitChosen, GroupsShown, KnownUser, Paused, PausedNow, TermSet
from .pauses import Pauses, PauseStore
from .pins import Pinning, pins_text

SHOWN_MISSING = 5
SHOWN_REASON = 160


class UsersService:
    def __init__(
        self,
        directory: UsersDirectory,
        users: UsersGate,
        groups: GroupsStore,
        hand_pins: HandPins,
        cascade: CascadeStore,
        watch: WatchGate,
        pause_store: PauseStore,
        pauses: Pauses,
        pins_file: Path,
        lock_file: Path,
    ) -> None:
        self.__directory = directory
        self.__users = users
        self.__pause_store = pause_store
        self.__pauses = pauses
        self.__groups = groups
        self.__hand_pins = hand_pins
        self.__cascade = cascade
        self.__pins_file = pins_file
        self.__pinning = Pinning(hand_pins, watch, pins_file)
        self.__lock_file = lock_file

    # ── one user ─────────────────────────────────────────────────────────────

    async def __all_known(self, names: Sequence[str]) -> None:
        """Refuses when one of these is not a user. The list is asked afresh."""
        known = {user.name for user in await self.__directory.list(force=True)}
        missing = [name for name in names if name not in known]
        if missing:
            raise Refused("нет такого пользователя: " + ", ".join(missing[:SHOWN_MISSING]))

    async def set_term(self, request: Expire) -> TermSet:
        """Give existing users a new term, counted from now — or none."""
        names = list(dict.fromkeys(request.names))
        term = None if request.expires == NO_TERM else request.expires
        await self.__all_known(names)
        done = await self.__users.set_expiry(names, term)
        await self.__directory.list(force=True)
        if not done.ok:
            raise Refused("не удалось изменить срок: " + done.said[-SHOWN_REASON:])
        return TermSet(names=names, expires=term)

    async def device_code(self, request: Qr) -> DeviceCode:
        payload = await self.__users.config(request.name)
        if not payload:
            raise Refused("конфиг клиента не найден")
        return DeviceCode(payload=payload)

    async def pause(self, request: Suspend) -> Paused:
        """Pause users, or let them through again. The key, the groups and the term stay."""
        names = list(dict.fromkeys(request.names))
        on = request.switch is Switch.ON
        users = await self.__directory.list(force=True)
        known = {user.name for user in users}
        missing = [name for name in names if name not in known]
        if missing:
            raise Refused("нет такого пользователя: " + ", ".join(missing[:SHOWN_MISSING]))
        async with file_lock(self.__lock_file):
            stored = await self.__pause_store.read()
            for name in names:
                if on:
                    stored.setdefault(name, datetime.now(UTC))
                else:
                    stored.pop(name, None)
            await self.__pause_store.save(stored)
            paused = await self.__pauses.sync(users)
        wrong = [name for name in names if (name in paused) != on]
        if wrong:
            raise Refused("не удалось применить: " + ", ".join(wrong))
        return Paused(mode=request.switch.value, names=names, paused=paused)

    async def resync_pauses(self, _request: SuspendSync) -> PausedNow:
        """Make the kernel match the stored pauses (after a boot, from a timer or by hand)."""
        async with file_lock(self.__lock_file):
            return PausedNow(paused=await self.__pauses.sync(await self.__directory.list(force=True)))

    # ── groups ───────────────────────────────────────────────────────────────

    async def show_groups(self, _request: UgroupsGet) -> GroupsShown:
        stored = await self.__groups.read()
        known = {user.name for user in await self.__directory.list()}
        return GroupsShown.of(stored, known)

    # ── who is kept on which exit ────────────────────────────────────────────

    async def choose_exit(self, request: UserExit) -> ExitChosen:
        """Keep users on one exit, by hand ("auto": as the group or the shares decide).
        A preference, not a lock: while that exit is down the watch service carries
        them through another one. New connections move; open ones stay."""
        names = list(dict.fromkeys(request.names))
        index = None if request.exit == AUTO_EXIT else int(request.exit)
        users = await self.__directory.list(force=True)
        known = {user.name for user in users}
        missing = [name for name in names if name not in known]
        if missing:
            raise Refused("нет такого пользователя: " + ", ".join(missing[:SHOWN_MISSING]))
        exit_name: str | None = None
        if index is not None:
            try:
                settings = await self.__cascade.load()
            except StoreDown as e:
                raise Refused(f"Хранилище настроек недоступно: {e}") from None
            exit_name = {exit_.index: exit_.name for exit_ in settings.exits}.get(index)
            if exit_name is None:
                raise Refused("нет такого выхода")
        async with file_lock(self.__lock_file):
            pins = await self.__hand_pins.read()
            for name in names:
                if index is None:
                    pins.pop(name, None)
                else:
                    pins[name] = index
            await self.__hand_pins.save(pins)
            stored = await self.__groups.read()
            if not await self.__pinning.compile(stored.groups, users):
                raise Refused("сохранено, но список пользователей не прочитан — применится при следующем опросе")
        return ExitChosen(names=names, exit=index, exit_name=exit_name)

    async def check_pins(self, users: Sequence[KnownUser]) -> None:
        """Called with every state poll: a user removed by a term must not leave a
        line that would pin whoever gets that address next."""
        stored = await self.__groups.read()
        have = await asyncio.to_thread(read_text, self.__pins_file) or ""
        want = pins_text(stored.groups, users, await self.__hand_pins.read(), await self.__hand_pins.whole())
        if want is not None and want != have:
            async with file_lock(self.__lock_file):
                again = await self.__groups.read()
                await self.__pinning.compile(again.groups, users)
