"""The groups of sites and the chosen mode, in the database."""
from __future__ import annotations

from typing import Sequence

from ..commands import RouteMode
from ..rules.store import Part, StoredRules
from ..store.documents import Record
from ..store.store import Store
from .models import SiteGroup

MODE_KEY = "route_mode"


class StoredSites:
    """The groups of sites: the part of the rules that goes abroad or directly (rules/store.py)."""

    def __init__(self, store: Store) -> None:
        self.__rules = StoredRules(store)

    async def read(self) -> list[SiteGroup]:
        return [SiteGroup.of(record) for record in await self.__rules.read_part(Part.SITES)]

    async def save(self, groups: Sequence[SiteGroup]) -> None:
        await self.__rules.save_part(Part.SITES, [group.wire() for group in groups])


class StoredMode:
    """One value beside the settings (the table `meta`)."""

    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> RouteMode:
        async with self.__store.transaction() as tx:
            held = await tx.meta([MODE_KEY])
        try:
            return RouteMode(held.get(MODE_KEY, RouteMode.LIST.value))
        except ValueError:
            return RouteMode.LIST

    async def save(self, mode: RouteMode) -> None:
        async with self.__store.transaction() as tx:
            await tx.set_meta(MODE_KEY, mode.value)


class StoredBlockGroups:
    """The admin's own block groups, read for `check` (their commands live elsewhere)."""

    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> Sequence[Record]:
        return await StoredRules(self.__store).read_part(Part.BLOCK)
