"""The admin's own block groups, in the database."""
from __future__ import annotations

from ..store.documents import Kind, Record, check
from ..store.store import Store
from .models import is_allow_group


def own_groups(document: object) -> list[Record]:
    """The groups of a stored document, without what is not a group and without a stored «Исключения»."""
    records = [record for record in document if isinstance(record, dict)] if isinstance(document, list) else []
    return [record for record in records if not is_allow_group(record)]


class StoredBlockGroups:
    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> list[Record]:
        return own_groups(await self.__store.get(Kind.BLOCK))

    def check(self, groups: list[Record]) -> None:
        """Raises StoreRefused for what the store would not keep — asked before the blocker is changed for a save."""
        check(Kind.BLOCK, groups)

    async def save(self, groups: list[Record]) -> None:
        await self.__store.put(Kind.BLOCK, groups)
