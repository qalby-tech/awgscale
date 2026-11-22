"""The users' groups and the exits chosen by hand, in the database."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from ..store.documents import Kind
from ..store.store import Store
from .models import GroupFilter, StoredGroups

HAND_PINS_KEY = "userpins"
OWN_FILTERS_KEY = "userfilters"


class StoredUserGroups:
    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> StoredGroups:
        async with self.__store.transaction() as tx:
            return StoredGroups.of(await tx.document(Kind.USER_GROUPS))


class StoredHandPins:
    """One value beside the settings (the table `meta`): {name: exit index}."""

    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> dict[str, int]:
        async with self.__store.transaction() as tx:
            held = await tx.meta([HAND_PINS_KEY])
        try:
            pins = json.loads(held[HAND_PINS_KEY]) if HAND_PINS_KEY in held else {}
        except ValueError:
            pins = {}
        if not isinstance(pins, dict):
            return {}
        return {name: index for name, index in pins.items() if isinstance(name, str) and type(index) is int}

    async def save(self, pins: dict[str, int]) -> None:
        async with self.__store.transaction() as tx:
            await tx.set_meta(HAND_PINS_KEY, json.dumps(pins, ensure_ascii=False, sort_keys=True))


class StoredOwnFilters:
    """The filtering a user chose for themselves (in the app), beside the settings: {name: GroupFilter value}."""

    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> dict[str, str]:
        async with self.__store.transaction() as tx:
            held = await tx.meta([OWN_FILTERS_KEY])
        try:
            chosen = json.loads(held[OWN_FILTERS_KEY]) if OWN_FILTERS_KEY in held else {}
        except ValueError:
            chosen = {}
        if not isinstance(chosen, dict):
            return {}
        known = {member.value for member in GroupFilter}
        return {name: level for name, level in chosen.items() if isinstance(name, str) and level in known}

    async def save(self, chosen: dict[str, str]) -> None:
        async with self.__store.transaction() as tx:
            await tx.set_meta(OWN_FILTERS_KEY, json.dumps(chosen, ensure_ascii=False, sort_keys=True))


class StoredPauses:
    """Who is paused and since when; the table holds the moment as Unix seconds."""

    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> dict[str, datetime]:
        async with self.__store.transaction() as tx:
            held = await tx.suspended()
        return {name: datetime.fromtimestamp(since, UTC) for name, since in held.items()}

    async def save(self, pauses: dict[str, datetime]) -> None:
        async with self.__store.transaction() as tx:
            await tx.put(Kind.SUSPENDED, {name: int(since.timestamp()) for name, since in pauses.items()})
