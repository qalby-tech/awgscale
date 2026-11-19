"""The spaces in the database."""
from __future__ import annotations

from typing import Sequence

from ..store.documents import Kind, Record
from ..store.store import Store
from .models import StoredSpaces


class SpacesInStore:
    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> StoredSpaces:
        async with self.__store.transaction() as tx:
            return StoredSpaces.of(await tx.document(Kind.SPACES))

    async def save(self, records: Sequence[Record]) -> None:
        async with self.__store.transaction() as tx:
            await tx.put(Kind.SPACES, list(records))
