"""Who is kept on which exit: by the group, or by hand for one user.

Compiled into a file for the watch service ("address index" per line), which
applies it at once. New connections move; open ones stay where they are.
Who sends all their traffic through their exit has a line "address all" there
too (a watch that does not know it skips it).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Collection, Mapping, Protocol, Sequence

from ..files import write_file
from ..ports import PIN_TIMEOUT
from .models import KnownUser, UserGroup

GroupPins = list[tuple[str, int]]
WHOLE_ROUTE = "all"


def group_pins(groups: Sequence[UserGroup]) -> GroupPins:
    """What of the groups reaches the exit choice: who is kept on which exit."""
    return sorted((member, group.exit) for group in groups if group.exit is not None for member in group.members)


def pins_text(groups: Sequence[UserGroup], users: Sequence[KnownUser], by_hand: Mapping[str, int], whole: Collection[str] = ()) -> str | None:
    """The pins file as it should be now, or None when the list of users could
    not be read (then the file is left alone rather than emptied). One line per
    address: the exit chosen for the user by hand wins over the group's. Then a
    line per address whose user sends everything through their exit."""
    pins = dict(group_pins(groups))
    pins.update(by_hand)
    if (pins or whole) and not users:
        return None
    address_of = {user.name: user.ip for user in users}
    kept = "".join(f"{address_of[name]} {index}\n" for name, index in sorted(pins.items()) if address_of.get(name))
    return kept + "".join(f"{address_of[name]} {WHOLE_ROUTE}\n" for name in sorted(whole) if address_of.get(name))


class StoredHandPins(Protocol):
    async def read(self) -> dict[str, int]: ...

    async def whole(self) -> set[str]: ...


class PinApplier(Protocol):
    """The watch service, told to apply the pins now."""

    async def pin(self, timeout: float) -> object: ...


class Pinning:
    """The pins file, written for the watch service and applied by it at once. Takes no lock: whoever calls holds it."""

    def __init__(self, hand_pins: StoredHandPins, watch: PinApplier, pins_file: Path) -> None:
        self.__hand_pins = hand_pins
        self.__watch = watch
        self.__pins_file = pins_file

    async def compile(self, groups: Sequence[UserGroup], users: Sequence[KnownUser]) -> bool:
        """→ False when the list of users could not be read: the file is left alone rather than emptied."""
        text = pins_text(groups, users, await self.__hand_pins.read(), await self.__hand_pins.whole())
        if text is None:
            return False
        await asyncio.to_thread(write_file, self.__pins_file, text)
        await self.__watch.pin(PIN_TIMEOUT)
        return True
