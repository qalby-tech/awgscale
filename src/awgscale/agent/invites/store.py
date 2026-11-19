"""The invitations in the database, and the groups' members for a guest who joins one."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence

from ..store.documents import Kind, Record
from ..store.store import Store
from ..users.models import StoredGroups
from .models import Invitation


def unexpired(document: object, now: datetime) -> list[Invitation]:
    """The invitations of a stored document that have not expired. One with no
    uses left stays until it expires (a use may be coming back, and the admin can still revoke it)."""
    if not isinstance(document, list):
        return []
    read = [Invitation.of(record) for record in document if isinstance(record, dict)]
    return [invitation for invitation in read if invitation is not None and invitation.expires_at > now]


class InvitesInStore:
    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> list[Invitation]:
        async with self.__store.transaction() as tx:
            return unexpired(await tx.document(Kind.INVITES), datetime.now(UTC).replace(microsecond=0))

    async def save(self, invitations: Sequence[Invitation]) -> None:
        async with self.__store.transaction() as tx:
            await tx.put(Kind.INVITES, [invitation.record() for invitation in invitations])


class InviteRecordsInStore:
    """The invitations as records, expired ones included."""

    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> object:
        async with self.__store.transaction() as tx:
            return await tx.document(Kind.INVITES)

    async def save(self, records: Sequence[Record]) -> None:
        async with self.__store.transaction() as tx:
            await tx.put(Kind.INVITES, list(records))


class GroupMembersInStore:
    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> StoredGroups:
        async with self.__store.transaction() as tx:
            return StoredGroups.of(await tx.document(Kind.USER_GROUPS))

    async def save(self, records: Sequence[Record]) -> None:
        async with self.__store.transaction() as tx:
            await tx.put(Kind.USER_GROUPS, list(records))
