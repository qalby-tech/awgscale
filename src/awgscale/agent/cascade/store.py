"""The cascade's settings in the database: one JSON document under one key."""
from __future__ import annotations

import json
import time

from ..errors import StoreDown
from ..store.store import Store
from .models import CascadeSettings, checked

SETTINGS_KEY = "cascade"
MOVED_KEY = "moved:cascade"


class StoredCascade:
    def __init__(self, store: Store) -> None:
        self.__store = store

    async def load(self) -> CascadeSettings:
        async with self.__store.transaction() as tx:
            held = await tx.meta([MOVED_KEY, SETTINGS_KEY])
        if MOVED_KEY not in held or SETTINGS_KEY not in held:
            raise StoreDown("настройки каскада ещё не перенесены в базу (store-init)")
        return checked(json.loads(held[SETTINGS_KEY]))

    async def save(self, settings: CascadeSettings) -> None:
        async with self.__store.transaction() as tx:
            await tx.set_meta(SETTINGS_KEY, json.dumps(settings.wire(), ensure_ascii=False, sort_keys=True))
            await tx.mark(MOVED_KEY, str(int(time.time())))
