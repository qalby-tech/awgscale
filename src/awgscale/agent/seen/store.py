"""The notes in the database: one JSON document under one key."""
from __future__ import annotations

import json

from ..errors import StoreDown
from ..store.documents import Record
from ..store.store import Store

NOTES_KEY = "seen"
MOVED_KEY = "moved:seen"


def notes_of(text: str | None) -> Record:
    """The stored text → the notes; {} for what is not notes."""
    try:
        notes = json.loads(text or "{}")
    except ValueError:
        return {}
    return notes if isinstance(notes, dict) else {}


def text_of(notes: Record) -> str:
    return json.dumps(notes, ensure_ascii=False, sort_keys=True)


class StoredNotes:
    def __init__(self, store: Store) -> None:
        self.__store = store

    async def load(self) -> Record:
        async with self.__store.transaction() as tx:
            held = await tx.meta([MOVED_KEY, NOTES_KEY])
        if MOVED_KEY not in held:
            raise StoreDown("наблюдения журнала ещё не перенесены в базу (store-init)")
        return notes_of(held.get(NOTES_KEY))

    async def save(self, notes: Record) -> None:
        async with self.__store.transaction() as tx:
            await tx.set_meta(NOTES_KEY, text_of(notes))
