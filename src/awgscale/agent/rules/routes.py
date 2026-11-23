"""The rules for some groups of people that go abroad or directly, handed to the watch service: the chain of those
rules (scoped.rules, after a change of the list) and who is in each group (scoped.members, with every look at the
state). The names each rule decides go with the routing (sites/routing.py: domains-scoped.txt)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Sequence

from ..errors import Refused
from ..files import read_text, write_file
from ..ports import PIN_TIMEOUT, GroupsStore, PinApplier, RulesView, ScopedWatch
from ..users.models import KnownUser
from .scoped import members_text, scoped_rules_text

log = logging.getLogger("awgscale.agent")

SCOPED_TIMEOUT = 60.0
PUBLIC_MODE = 0o644
SHOWN_REASON = 200


class ScopedRoutes:
    """The chain of the rules for some groups. Called only on the way of a change of the list, under its lock."""

    def __init__(self, rules: RulesView, watch: ScopedWatch, rules_file: Path) -> None:
        self.__rules = rules
        self.__watch = watch
        self.__rules_file = rules_file
        self.__failed = False               # the last load was refused: the next change loads it again even when the file is the same

    async def follow(self) -> None:
        """The chain made to follow the stored list: written and loaded when it came out otherwise than the file says
        (with no such rule, never — a watch service of an earlier version is never asked). Raises Refused."""
        text = scoped_rules_text(await self.__rules.read())
        on_disk = await asyncio.to_thread(read_text, self.__rules_file) or ""
        if text == on_disk and not self.__failed:
            return
        if text != on_disk:
            await asyncio.to_thread(write_file, self.__rules_file, text, PUBLIC_MODE)
        loaded = await self.__watch.scoped(SCOPED_TIMEOUT)
        self.__failed = not loaded.ok
        if not loaded.ok:
            raise Refused("правила для групп не загрузились: " + (loaded.said or "сторож отказал")[:SHOWN_REASON])


class ScopedMembers:
    """Who is in each group, for the watch service's sets of the groups' people. With every look at the state, without
    a lock (it writes only its own file and asks only a pass of the watch service)."""

    def __init__(self, groups: GroupsStore, watch: PinApplier, members_file: Path) -> None:
        self.__groups = groups
        self.__watch = watch
        self.__members_file = members_file
        self.__applied: str | None = None       # what the watch service last took

    async def check(self, users: Sequence[KnownUser]) -> None:
        """A person moved, came or went: the file written and a pass asked (the sets follow it, new connections only).
        Never raises."""
        try:
            text = members_text((await self.__groups.read()).groups, users)
            if text is None:
                return
            on_disk = await asyncio.to_thread(read_text, self.__members_file)
            if text == self.__applied and text == on_disk:
                return
            if text != on_disk:
                await asyncio.to_thread(write_file, self.__members_file, text, PUBLIC_MODE)
            pinned = await self.__watch.pin(PIN_TIMEOUT)
            if pinned.ok:
                self.__applied = text
        except Exception as e:
            log.info("the groups' people not handed to the watch service: %s", repr(e)[:SHOWN_REASON])
