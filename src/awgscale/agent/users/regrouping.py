"""ugroups-set: the users' groups as the page sent them, checked, stored whole and applied."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from awgscale.core.patterns import NAME_RE

from ..commands import UgroupsSet
from ..errors import Refused
from ..journal.texts import FILTER_NAMES
from ..labels import ID_RE, clean_label, new_id
from ..locks import file_lock
from ..payloads import decode
from ..ports import ExitWeights, FilterProfiles, GroupMembers, InviteRecords, SpacesCompiler, SpacesStore, Together, UsersDirectory
from ..store.documents import Record
from .groups import group_filters, group_spaces
from .models import DEFAULT_KIDS_LISTS, MAX_USER_GROUPS, GroupFilter, GroupsShown, KidsList, StoredGroups, UserGroup, kids_lists_of
from .pins import Pinning, group_pins

MAX_NAME_LENGTH = 40
MAX_GROUP_MEMBERS = 253                 # the tunnel's /24 holds as many
NO_GROUP = "без группы"                 # how the page calls the users that are in no group


def invitations_left(stored: object, group_ids: set[Any]) -> list[Record] | None:
    """The stored invitations without those into a group that no longer exists; None when all of them stay."""
    if not isinstance(stored, list):
        return None
    if not any(isinstance(record, dict) and record.get("group") is not None and record.get("group") not in group_ids for record in stored):
        return None
    return [record for record in stored if isinstance(record, dict) and (record.get("group") is None or record.get("group") in group_ids)]


def chosen_kids_lists(group: Record, name: str, sent_id: object, old_filter: dict[Any, str], old_kids_lists: dict[Any, tuple[str, ...] | None]) -> tuple[str, ...] | None:
    """The add-on lists of a group in the children's mode: as the page sent them; as stored when it sent none
    (an older page) — None, none at all, for a record from before them, which stays as it was; the default ones
    for a group that has only now been switched to the children's mode."""
    if "kids_lists" in group:
        sent = group["kids_lists"]
        known = {kids_list.value for kids_list in KidsList}
        if not isinstance(sent, list) or not all(isinstance(item, str) and item in known for item in sent):
            raise Refused(f"{name}: таких списков детского режима нет")
        return kids_lists_of(sent) or ()
    if isinstance(sent_id, str) and old_filter.get(sent_id) == GroupFilter.KIDS.value:
        return old_kids_lists.get(sent_id)
    return tuple(kids_list.value for kids_list in DEFAULT_KIDS_LISTS)


def checked_groups(raw: Sequence[object], known: set[str], old: Sequence[Record], exits: set[int]) -> list[Record]:
    """What the page sent → the records to store. Refuses what is not a list of groups."""
    old_ids = {record.get("id") for record in old}
    old_exit = {record.get("id"): record.get("exit") for record in old}
    old_filter = {record.get("id"): record.get("filter") or GroupFilter.STANDARD.value for record in old}
    old_kids_lists = {record.get("id"): kids_lists_of(record.get("kids_lists")) for record in old}
    records: list[Record] = []
    ids: set[str] = set()
    names: set[str] = set()
    placed: dict[str, str] = {}
    for group in raw:
        if not isinstance(group, dict):
            raise Refused("некорректные данные")
        name = clean_label(group.get("name"), MAX_NAME_LENGTH)
        if not name:
            raise Refused("название группы: от 1 до 40 символов")
        if name.lower() in names or name.lower() == NO_GROUP:
            raise Refused(f"группа «{name}» уже есть")
        names.add(name.lower())
        members = group.get("members", [])
        if not isinstance(members, list) or len(members) > MAX_GROUP_MEMBERS or not all(isinstance(member, str) and NAME_RE.match(member) for member in members):
            raise Refused(f"{name}: некорректный состав")
        mine: list[str] = []
        for member in members:
            if member not in known or member in mine:
                continue
            if member in placed:
                raise Refused(f"пользователь {member} указан в двух группах: «{placed[member]}» и «{name}»")
            placed[member] = name
            mine.append(member)
        sent_id = group.get("id")
        group_id = sent_id
        if not (isinstance(group_id, str) and ID_RE.match(group_id) and group_id in old_ids and group_id not in ids):
            group_id = new_id(ids | {str(held) for held in old_ids})
        ids.add(group_id)
        # a page from before this field sends no "exit": the stored one stays. A stored
        # pin to an exit that is gone becomes "auto" instead of refusing every save.
        was_exit = old_exit.get(sent_id) if isinstance(sent_id, str) else None
        kept_on = group["exit"] if "exit" in group else was_exit
        if kept_on is not None and (isinstance(kept_on, bool) or not isinstance(kept_on, int)):
            raise Refused(f"{name}: такого выхода нет")
        if kept_on is not None and kept_on not in exits:
            if kept_on != was_exit:
                raise Refused(f"{name}: такого выхода нет")
            kept_on = None
        filtered = group["filter"] if "filter" in group else old_filter.get(sent_id, GroupFilter.STANDARD.value)     # an older page sends none: as stored
        if filtered not in FILTER_NAMES:
            raise Refused(f"{name}: такого режима фильтрации нет")
        record: Record = {"id": group_id, "name": name, "space": bool(group.get("space")), "exit": kept_on, "filter": filtered, "members": mine}
        if filtered == GroupFilter.KIDS.value:
            chosen = chosen_kids_lists(group, name, sent_id, old_filter, old_kids_lists)
            if chosen is not None:
                record["kids_lists"] = list(chosen)
        records.append(record)
    return records


class GroupsService:
    def __init__(
        self,
        directory: UsersDirectory,
        groups: GroupMembers,
        invitations: InviteRecords,
        exits: ExitWeights,
        pinning: Pinning,
        spaces: SpacesStore,
        space_rules: SpacesCompiler,
        profiles: FilterProfiles,
        together: Together,
        lock_file: Path,
    ) -> None:
        self.__directory = directory
        self.__groups = groups
        self.__invitations = invitations
        self.__exits = exits
        self.__pinning = pinning
        self.__spaces = spaces
        self.__space_rules = space_rules
        self.__profiles = profiles
        self.__together = together
        self.__lock_file = lock_file

    async def stored_before(self) -> Sequence[Record]:
        """What is stored now: the journal's sentence about a change needs it."""
        return list((await self.__groups.read()).document)

    async def __shown(self, stored: StoredGroups) -> GroupsShown:
        known = {user.name for user in await self.__directory.list()}
        return GroupsShown.of(stored, known)

    async def __store(self, records: list[Record]) -> None:
        """The groups and the invitations into them: stored together."""
        async with self.__together.whole():
            await self.__groups.save(records)
            left = invitations_left(await self.__invitations.read(), {record.get("id") for record in records})
            if left is not None:                       # an invitation into a group that no longer exists is void
                await self.__invitations.save(left)

    async def replace(self, request: UgroupsSet) -> GroupsShown:
        data = decode(request.payload)
        raw = data.get("groups")
        if not isinstance(raw, list) or len(raw) > MAX_USER_GROUPS:
            raise Refused(f"не больше {MAX_USER_GROUPS} групп")
        known = {user.name for user in await self.__directory.list(force=True)}
        async with file_lock(self.__lock_file):
            old = await self.__groups.read()
            shown = await self.__shown(old)
            if data.get("base") != old.version:
                raise Refused("группы изменились в другом окне — показан новый список", conflict=True, limit=shown.limit, v=shown.v, groups=shown.groups)
            records = checked_groups(raw, known, old.document, set(self.__exits.weights()))
            groups = [UserGroup.of(record) for record in records]
            await self.__store(records)
            if group_pins(groups) != group_pins(old.groups):               # who is kept on which exit changed
                await self.__pinning.compile(groups, await self.__directory.list())
            if group_spaces(records) != group_spaces(old.document):        # renaming or regrouping without spaces touches no rule
                failed = await self.__space_rules.compile(list((await self.__spaces.read()).document), records)
                if failed:
                    raise Refused("сохранено, но не удалось применить: " + failed)
            if group_filters(records) != group_filters(old.document):      # who is filtered how changed — last, so nothing else waits for the blocker
                failed = await self.__profiles.sync(groups, await self.__directory.list())
                if failed:
                    raise Refused("сохранено, но фильтрация не применилась: " + failed)
            return await self.__shown(StoredGroups.of(records))
