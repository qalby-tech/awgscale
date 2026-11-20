"""The owners of the network: the users whose devices may run it from the app.

An owner is remembered by name together with the moment the user was made, so
that a later user who gets the same name (after the first one was removed) is
not an owner by accident.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..store.store import Store
from ..users.models import KnownUser

OWNERS_KEY = "owners"


class Owner(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Name of the user")
    made: datetime | None = Field(default=None, description="When that user was made; None when the users service did not say")

    def is_(self, user: KnownUser) -> bool:
        """This user, and not another one made later under the same name. To the whole
        second: the list of users is cached with its moments in seconds."""
        if user.name != self.name:
            return False
        return self.made is None or (user.created_at is not None and whole_seconds(user.created_at) == whole_seconds(self.made))


def whole_seconds(moment: datetime) -> int:
    return int(moment.timestamp())


def owners_of(text: str | None) -> list[Owner]:
    """The stored value → the owners; anything unreadable is nobody."""
    try:
        held = json.loads(text) if text else []
    except ValueError:
        return []
    if not isinstance(held, list):
        return []
    owners: list[Owner] = []
    for record in held:
        try:
            owners.append(Owner.model_validate(record))
        except ValueError:
            continue
    return owners


def is_owner(owners: Sequence[Owner], user: KnownUser) -> bool:
    return any(owner.is_(user) for owner in owners)


class StoredOwners:
    """One value beside the settings (the table `meta`): a list of {name, made}."""

    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> list[Owner]:
        async with self.__store.transaction() as tx:
            held = await tx.meta([OWNERS_KEY])
        return owners_of(held.get(OWNERS_KEY))

    async def add(self, user: KnownUser) -> None:
        async with self.__store.transaction() as tx:
            held = await tx.meta([OWNERS_KEY])
            owners = [owner for owner in owners_of(held.get(OWNERS_KEY)) if owner.name != user.name]
            made = None if user.created_at is None else user.created_at.replace(microsecond=0)
            owners.append(Owner(name=user.name, made=made))
            await tx.set_meta(OWNERS_KEY, json.dumps([owner.model_dump(mode="json") for owner in owners], ensure_ascii=False))
