"""Removed users leave every space and their group, so that a later user with
the same name does not silently inherit the access — nor a pause, nor an exit
chosen by hand. One reload of the rules for all of them, and only if a space
actually changed."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Iterable

from ..locks import file_lock
from ..ports import FilterProfiles, GroupMembers, HandPins, OwnFilters, SpacesCompiler, SpacesStore, Together, UsersDirectory
from ..store.documents import Record
from .groups import filtered_specially, group_spaces, holds_any, without
from .models import KnownUser, UserGroup
from .pauses import Pauses, PauseStore
from .pins import Pinning, group_pins


def typed(records: Iterable[Record]) -> list[UserGroup]:
    return [UserGroup.of(record) for record in records]


class Forgetting:
    def __init__(
        self,
        directory: UsersDirectory,
        hand_pins: HandPins,
        pause_store: PauseStore,
        pauses: Pauses,
        spaces: SpacesStore,
        groups: GroupMembers,
        pinning: Pinning,
        profiles: FilterProfiles,
        space_rules: SpacesCompiler,
        together: Together,
        lock_file: Path,
        own_filters: OwnFilters | None = None,
    ) -> None:
        self.__directory = directory
        self.__hand_pins = hand_pins
        self.__pause_store = pause_store
        self.__pauses = pauses
        self.__spaces = spaces
        self.__groups = groups
        self.__pinning = pinning
        self.__profiles = profiles
        self.__space_rules = space_rules
        self.__together = together
        self.__lock_file = lock_file
        self.__own_filters = own_filters

    async def __others(self, gone: set[str]) -> list[KnownUser]:
        """The users as they are now, without those who go."""
        return [user for user in await self.__directory.list(force=True) if user.name not in gone]

    async def forget(self, names: Iterable[str]) -> None:
        gone = set(names)
        async with file_lock(self.__lock_file), self.__together.whole():      # pauses, spaces and groups lose the user together or not at all
            await self.stored(gone)

    async def stored(self, gone: set[str]) -> None:
        """What is stored about these users goes; whoever calls holds the users' lock."""
        by_hand = await self.__hand_pins.read()
        if gone & set(by_hand):                        # a removed (or re-created) user does not inherit an exit chosen by hand
            await self.__hand_pins.save({name: index for name, index in by_hand.items() if name not in gone})
            try:
                await self.__pinning.compile((await self.__groups.read()).groups, await self.__others(gone))
            except Exception:
                pass                                   # the next look at the state puts it right
        if self.__own_filters is not None:             # nor the protection someone chose for themselves
            chosen = await self.__own_filters.read()
            if gone & set(chosen):
                await self.__own_filters.save({name: level for name, level in chosen.items() if name not in gone})
        paused = await self.__pause_store.read()
        if gone & set(paused):                         # a removed (or re-created) user does not come back paused
            await self.__pause_store.save({name: since for name, since in paused.items() if name not in gone})
            try:
                await self.__pauses.sync(await self.__others(gone))
            except Exception:
                pass
        spaces: list[Record] = copy.deepcopy(list((await self.__spaces.read()).document))
        groups: list[Record] = copy.deepcopy(list((await self.__groups.read()).document))
        in_spaces = holds_any(spaces, gone)
        in_groups = holds_any(groups, gone)
        were_spaces = group_spaces(groups)
        if in_spaces:
            without(spaces, gone)
            await self.__spaces.save(spaces)
        if in_groups:
            were_pinned = group_pins(typed(groups))
            without(groups, gone)
            await self.__groups.save(groups)
            if group_pins(typed(groups)) != were_pinned:
                try:
                    await self.__pinning.compile(typed(groups), await self.__others(gone))
                except Exception:
                    pass                               # the next look at the state puts it right
            if any(filtered_specially(record) for record in groups):
                try:
                    await self.__profiles.sync(typed(groups), await self.__others(gone))
                except Exception:
                    pass
        if in_spaces or group_spaces(groups) != were_spaces:
            await self.__space_rules.compile(spaces, groups)
