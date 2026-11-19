"""The commands about the invitations."""
from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from ..commands import InviteAdd, InviteInfo, InviteList, InviteRedeem, InviteRemove
from ..errors import Refused
from ..locks import file_lock
from ..payloads import decode
from ..ports import GroupMembers, InviteNotes, InvitesStore, Newcomers, SpacesCompiler, SpacesStore, UsersDirectory, UsersGate, WatchGate
from ..protocol import Caller
from ..labels import new_id
from ..store.documents import Record
from ..users.models import GroupFilter, KnownUser, UserGroup
from .models import MAX_CLIENTS, MAX_DAYS, MAX_INVITES, MAX_USES, MIN_DAYS, MIN_USES, Invitation, InviteInfoAnswer, InviteMade, InviteRevoked, InvitesShown, Redeemed, new_token

REDEEM_WAIT_SECONDS = 300.0        # using an invitation waits its turn; its page waits 400 s
PIN_TIMEOUT = 30.0                 # the watch service does the same within 15 s anyway


def shown(text: str) -> Refused:
    """A refusal meant for the person holding the link: the public side passes
    on only such texts, never an internal error."""
    return Refused(text, shown=True)


def bad_link() -> Refused:
    return shown("Ссылка недействительна или уже использована")


def whole_number(value: object, least: int, most: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and least <= value <= most


class InvitesService:
    def __init__(
        self,
        invites: InvitesStore,
        groups: GroupMembers,
        directory: UsersDirectory,
        users: UsersGate,
        newcomers: Newcomers,
        watch: WatchGate,
        spaces: SpacesStore,
        space_rules: SpacesCompiler,
        notes: InviteNotes,
        users_lock: Path,
        invite_lock: Path,
    ) -> None:
        self.__invites = invites
        self.__groups = groups
        self.__directory = directory
        self.__users = users
        self.__newcomers = newcomers
        self.__watch = watch
        self.__spaces = spaces
        self.__space_rules = space_rules
        self.__notes = notes
        self.__users_lock = users_lock
        self.__invite_lock = invite_lock

    # ── what the admin sees ──────────────────────────────────────────────────

    async def __usable(self, invitations: Sequence[Invitation]) -> list[Invitation]:
        group_ids = {group.id for group in (await self.__groups.read()).groups}
        return [invitation for invitation in invitations if invitation.usable_in(group_ids)]

    async def __public(self, invitations: Sequence[Invitation]) -> list[dict[str, object]]:
        groups = (await self.__groups.read()).groups
        names = {group.id: group.name for group in groups}
        return [invitation.shown(names.get(invitation.group)) for invitation in invitations if invitation.usable_in(set(names))]

    async def __found(self, token: str) -> Invitation | None:
        usable = await self.__usable(await self.__invites.read())
        return next((invitation for invitation in usable if invitation.opens_with(token)), None)

    async def list(self, _request: InviteList) -> InvitesShown:
        return InvitesShown(invites=await self.__public(await self.__invites.read()))

    async def add(self, request: InviteAdd, caller: Caller) -> InviteMade:
        data = decode(request.payload)
        group = data.get("group")
        uses = data.get("uses")
        days = data.get("days")
        if not (whole_number(uses, MIN_USES, MAX_USES) and whole_number(days, MIN_DAYS, MAX_DAYS)):
            raise Refused("некорректные данные")
        group_name = None
        if isinstance(group, str):
            groups = (await self.__groups.read()).groups
            group_name = next((each.name for each in groups if each.id == group), None)
        if group is not None and group_name is None:
            raise Refused("такой группы нет")
        async with file_lock(self.__users_lock):
            invitations = await self.__invites.read()
            if len(await self.__usable(invitations)) >= MAX_INVITES:
                raise Refused(f"не больше {MAX_INVITES} приглашений одновременно")
            token = new_token()
            made = Invitation.new(new_id({invitation.id for invitation in invitations}), token, group, uses, datetime.now(UTC).replace(microsecond=0), days)
            await self.__invites.save([*invitations, made])
        try:
            where = f" в группу «{group_name}»" if group_name else ""
            await self.__notes.made(f"Создано приглашение{where}: устройств {uses}, дней {days}", caller)
        except Exception:
            pass
        return InviteMade(id=made.id, token=token, invites=await self.__public([*invitations, made]))

    async def remove(self, request: InviteRemove, caller: Caller) -> InviteRevoked:
        async with file_lock(self.__users_lock):
            invitations = await self.__invites.read()
            left = [invitation for invitation in invitations if invitation.id != request.invite_id]
            if len(left) == len(invitations):
                raise Refused("такого приглашения нет")
            await self.__invites.save(left)
        try:
            await self.__notes.made("Приглашение отозвано", caller)
        except Exception:
            pass
        return InviteRevoked(invites=await self.__public(left))

    # ── what a guest with a link gets ────────────────────────────────────────

    async def info(self, request: InviteInfo) -> InviteInfoAnswer:
        """For the public page: is the link good, and for what. Says nothing else."""
        found = await self.__found(request.token)
        if found is None:
            raise bad_link()
        groups = (await self.__groups.read()).groups
        group_name = next((group.name for group in groups if group.id == found.group), None)
        return InviteInfoAnswer(group=group_name, left=found.left, total=found.total, expires_at=found.record()["expires_at"])

    async def __users_now(self) -> list[KnownUser]:
        try:
            return await self.__directory.list(force=True)
        except Exception:
            return []

    async def __take(self, token: str, name: str) -> Invitation:
        """The use is taken before the user is made."""
        async with file_lock(self.__users_lock):
            invitations = await self.__invites.read()
            usable = await self.__usable(invitations)
            found = next((invitation for invitation in usable if invitation.opens_with(token)), None)
            if found is None:
                raise bad_link()
            taken = found.taken_by(name)
            await self.__invites.save([taken if invitation.id == found.id else invitation for invitation in invitations])
        return taken

    async def __give_back(self, invite_id: str, name: str) -> None:
        async with file_lock(self.__users_lock):
            invitations = await self.__invites.read()
            await self.__invites.save([invitation.given_back_by(name) if invitation.id == invite_id else invitation for invitation in invitations])

    async def __make(self, name: str) -> bool:
        """→ did the users service say the user was made. Never raises."""
        try:
            await self.__newcomers.forget(name)
            return await self.__newcomers.add(name)
        except Exception:
            return False

    async def __join(self, group_id: object, name: str, users: Sequence[KnownUser]) -> object:
        """The newcomer joins the invitation's group. → the group's name when they did."""
        async with file_lock(self.__users_lock):
            records: list[Record] = copy.deepcopy(list((await self.__groups.read()).document))
            record = next((each for each in records if each.get("id") == group_id), None)
            if record is None or any(name in each.get("members", []) for each in records):
                return None
            record.setdefault("members", []).append(name)
            await self.__groups.save(records)
            group = UserGroup.of(record)
            if group.exit is not None:
                await self.__newcomers.pin(records, users)
            if group.filter != GroupFilter.STANDARD.value:
                await self.__newcomers.filter(records, users)
            if group.space:
                await self.__space_rules.compile(list((await self.__spaces.read()).document), records)
            return group.name

    async def redeem(self, request: InviteRedeem) -> Redeemed:
        name = request.name
        await self.__newcomers.ready()
        # The secret first: without a good link nobody learns anything (not even
        # whether a name is taken) and nothing is run.
        if await self.__found(request.token) is None:
            raise bad_link()
        # One invitation is used at a time: creating a user takes seconds, and two
        # people (or one impatient double tap) must not interleave in it.
        async with file_lock(self.__invite_lock, REDEEM_WAIT_SECONDS):
            users = await self.__users_now()
            if not users:
                raise shown("Сейчас подключить нельзя — попробуйте ещё раз чуть позже")
            if any(user.name == name for user in users):
                raise shown("Такое имя уже занято — выберите другое")
            if len(users) >= MAX_CLIENTS:
                raise shown("Сейчас подключить нельзя — напишите тому, кто дал ссылку")
            taken = await self.__take(request.token, name)
            said_yes = await self.__make(name)
            users = await self.__users_now()
            try:
                await self.__watch.pin(PIN_TIMEOUT)
            except Exception:
                pass
            if not any(user.name == name for user in users):
                # Given back only when it is certain that nobody was made: the users service
                # did not say yes AND a list that could be read does not have the name.
                if users and not said_yes:
                    await self.__give_back(taken.id, name)
                raise shown("Не получилось создать подключение — попробуйте ещё раз чуть позже")
            group_name: object = None
            try:
                if taken.group:
                    group_name = await self.__join(taken.group, name, users)
            except Exception:
                pass                    # the state poll puts pins and filtering right; the user exists
            try:
                where = f" в группе «{group_name}»" if group_name else ""
                await self.__notes.used(f"По приглашению создан пользователь {name}{where}")
            except Exception:
                pass
        payload = await self.__users.config(name)
        if not payload:
            raise shown("Подключение создано, но код не найден — напишите тому, кто дал ссылку")
        return Redeemed(payload=payload, left=taken.left, group=group_name)
