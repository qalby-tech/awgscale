"""The addresses of the people of each group: what a rule for some groups is made of."""
from __future__ import annotations

from typing import Protocol, Sequence

from ..ports import GroupsStore
from ..users.models import KnownUser


class Users(Protocol):
    async def list(self, force: bool = False) -> list[KnownUser]: ...


class StoredGroupMembers:
    def __init__(self, groups: GroupsStore, users: Users) -> None:
        self.__groups = groups
        self.__users = users

    async def addresses(self, users: Sequence[KnownUser] | None = None) -> dict[str, list[str]]:
        """{group id: the tunnel addresses of its members}: a member the users service does not list has none."""
        known = list(users) if users is not None else await self.__users.list()
        address_of = {user.name: user.ip for user in known if user.ip}
        return {
            str(group.id): sorted({address_of[name] for name in group.members if name in address_of})
            for group in (await self.__groups.read()).groups
            if group.id is not None
        }
