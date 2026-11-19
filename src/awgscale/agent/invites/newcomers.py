"""What making a user for a guest takes, beside the invitation itself."""
from __future__ import annotations

from typing import Sequence

from ..ports import AccountsGate, FilterProfiles, LeavingNotes, Readiness
from ..store.documents import Record
from ..users.forgetting import Forgetting
from ..users.models import KnownUser, UserGroup
from ..users.pins import Pinning


class GuestUsers:
    def __init__(self, store: Readiness, forgetting: Forgetting, seen: LeavingNotes, users: AccountsGate, pinning: Pinning, profiles: FilterProfiles) -> None:
        self.__store = store
        self.__forgetting = forgetting
        self.__seen = seen
        self.__users = users
        self.__pinning = pinning
        self.__profiles = profiles

    async def ready(self) -> None:
        await self.__store.ping()

    async def forget(self, name: str) -> None:
        await self.__forgetting.forget([name])
        await self.__seen.back(name)

    async def add(self, name: str) -> bool:
        made = await self.__users.add(name, None)
        if made is None:
            raise ConnectionError("сервис пользователей не отвечает")      # not a "no": whoever asked looks at the list of users before giving the use back
        return made.ok

    async def pin(self, groups: Sequence[Record], users: Sequence[KnownUser]) -> None:
        await self.__pinning.compile([UserGroup.of(record) for record in groups], users)

    async def filter(self, groups: Sequence[Record], users: Sequence[KnownUser]) -> None:
        await self.__profiles.sync([UserGroup.of(record) for record in groups], users)
