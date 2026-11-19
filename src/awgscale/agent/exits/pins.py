"""What was kept on an exit by hand goes back to "auto" when that exit is removed."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..files import write_file
from ..locks import file_lock
from ..ports import PIN_TIMEOUT, GroupExits, HandPins, UsersDirectory, WatchGate
from ..store.documents import Kind
from ..store.store import Store
from ..users.models import UserGroup
from ..users.pins import pins_text

Record = dict[str, Any]


class StoredGroupExits:
    """The users' groups in the database, as far as an exit's removal changes them."""

    def __init__(self, store: Store) -> None:
        self.__store = store

    async def records(self) -> list[Record]:
        async with self.__store.transaction() as tx:
            document = await tx.document(Kind.USER_GROUPS)
        return [record for record in document if isinstance(record, dict)] if isinstance(document, list) else []

    async def save(self, records: list[Record]) -> None:
        async with self.__store.transaction() as tx:
            await tx.put(Kind.USER_GROUPS, records)


class KeptOnExit:
    """The groups and the single users kept on one exit."""

    def __init__(self, groups: GroupExits, hand_pins: HandPins, directory: UsersDirectory, watch: WatchGate, pins_file: Path, lock_file: Path) -> None:
        self.__groups = groups
        self.__hand_pins = hand_pins
        self.__directory = directory
        self.__watch = watch
        self.__pins_file = pins_file
        self.__lock_file = lock_file

    async def __compile(self, records: list[Record]) -> None:
        """The pins file for the watch service, which applies it now. Left alone when the users could not be read."""
        users = await self.__directory.list()
        text = pins_text([UserGroup.of(record) for record in records], users, await self.__hand_pins.read())
        if text is None:
            return
        await asyncio.to_thread(write_file, self.__pins_file, text)
        await self.__watch.pin(PIN_TIMEOUT)

    async def release(self, index: int) -> None:
        """Everybody kept on this exit — a whole group, or one user by hand — goes back to "auto"."""
        async with file_lock(self.__lock_file):
            records = await self.__groups.records()
            if any(record.get("exit") == index for record in records):
                for record in records:
                    if record.get("exit") == index:
                        record["exit"] = None
                await self.__groups.save(records)
            by_hand = await self.__hand_pins.read()
            if index in by_hand.values():
                await self.__hand_pins.save({name: kept for name, kept in by_hand.items() if kept != index})
            try:
                await self.__compile(records)
            except Exception:
                pass                                   # the next state poll puts it right
