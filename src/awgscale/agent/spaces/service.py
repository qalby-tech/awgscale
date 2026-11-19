"""The commands about the device spaces, and the file compiled from them."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Sequence

from awgscale.core.ashell import AsyncRunner
from awgscale.core.patterns import NAME_RE

from ..commands import SpacesGet, SpacesSet
from ..errors import Refused
from ..files import write_file
from ..locks import file_lock
from ..payloads import decode
from ..ports import GroupsStore, SpaceRules, SpacesStore, UsersDirectory
from ..store.documents import Record
from ..shell import SpacesSync
from .models import MAX_MEMBERS, MAX_NAME_LENGTH, MAX_SPACES, SpacesApplied, SpacesShown, StoredSpaces
from ..labels import ID_RE, clean_label, new_id
from .sets import keys_by_address, sets_text

LOAD_TIMEOUT = 90.0
SHOWN_REASON = 200


class SpacesService:
    def __init__(
        self,
        directory: UsersDirectory,
        spaces: SpacesStore,
        groups: GroupsStore,
        rules: SpaceRules,
        run: AsyncRunner,
        client_iface: str,
        sets_file: Path,
        lock_file: Path,
    ) -> None:
        self.__directory = directory
        self.__spaces = spaces
        self.__groups = groups
        self.__rules = rules
        self.__run = run
        self.__client_iface = client_iface
        self.__sets_file = sets_file
        self.__lock_file = lock_file

    async def __shown(self, stored: StoredSpaces) -> SpacesShown:
        known = {user.name for user in await self.__directory.list()}
        return SpacesShown.of(stored, known)

    async def show(self, _request: SpacesGet) -> SpacesShown:
        return await self.__shown(await self.__spaces.read())

    async def stored_before(self) -> Sequence[Record]:
        """What is stored now: the journal's sentence about a change needs it."""
        return list((await self.__spaces.read()).document)

    async def compile(self, spaces: Sequence[Record], groups: Sequence[Record] | None = None) -> str | None:
        """Write the file the watch service loads and let it load it now.
        `groups`: the users' groups as they are being stored; read from the store when not given.
        → why it could not be applied, or None."""
        addresses = {user.name: user.ip for user in await self.__directory.list(force=True)}
        shown = await self.__run(["awg", "show", self.__client_iface, "allowed-ips"])
        keys = keys_by_address(shown.out)
        if groups is None:
            groups = list((await self.__groups.read()).document)
        await asyncio.to_thread(write_file, self.__sets_file, sets_text(spaces, groups, addresses, keys))
        loaded = await self.__rules.load(LOAD_TIMEOUT)
        if loaded.ok:
            return None
        return loaded.said.strip()[-SHOWN_REASON:] or "ошибка"

    def __checked(self, raw: Sequence[object], known: set[str], old_ids: set[object]) -> list[Record]:
        """What the page sent → the records to store. Refuses what is not a list of spaces."""
        records: list[Record] = []
        ids: set[str] = set()
        for space in raw:
            if not isinstance(space, dict):
                raise Refused("некорректные данные")
            name = clean_label(space.get("name"), MAX_NAME_LENGTH)
            if not name:
                raise Refused("название: от 1 до 40 символов")
            members = space.get("members", [])
            if not isinstance(members, list) or len(members) > MAX_MEMBERS or not all(isinstance(member, str) and NAME_RE.match(member) for member in members):
                raise Refused(f"{name}: некорректный состав")
            kept = [member for position, member in enumerate(members) if member in known and member not in members[:position]]
            space_id = space.get("id")
            if not (isinstance(space_id, str) and ID_RE.match(space_id) and space_id in old_ids and space_id not in ids):
                space_id = new_id(ids | {str(old) for old in old_ids})
            ids.add(space_id)
            records.append({"id": space_id, "name": name, "on": bool(space.get("on")), "members": kept})
        return records

    async def replace(self, request: SpacesSet) -> SpacesShown:
        """Store the spaces the page sent, whole, and apply them."""
        data = decode(request.payload)
        raw = data.get("spaces")
        if not isinstance(raw, list) or len(raw) > MAX_SPACES:
            raise Refused(f"не больше {MAX_SPACES} пространств")
        known = {user.name for user in await self.__directory.list(force=True)}
        async with file_lock(self.__lock_file):
            old = await self.__spaces.read()
            shown = await self.__shown(old)
            if data.get("base") != old.version:
                raise Refused("пространства изменились в другом окне — показан новый список", conflict=True, limit=shown.limit, v=shown.v, spaces=shown.spaces)
            records = self.__checked(raw, known, {record.get("id") for record in old.document})
            await self.__spaces.save(records)
            failed = await self.compile(records)
            if failed:
                raise Refused("сохранено, но не удалось применить: " + failed)
            return await self.__shown(StoredSpaces.of(records))

    async def resync(self, _request: SpacesSync) -> SpacesApplied:
        """Recompile and reload from the stored spaces (after a deploy or by hand)."""
        failed = await self.compile(list((await self.__spaces.read()).document))
        if failed:
            raise Refused(failed)
        return SpacesApplied()
