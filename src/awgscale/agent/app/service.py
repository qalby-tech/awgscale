"""The commands of the app. The panel tells whose device asks by the tunnel
address the request came from; here that address becomes a user, and the
answers say nothing about anybody else."""
from __future__ import annotations

from ..commands import AppExit, AppExits as AppExitsRequest, AppMe as AppMeRequest, UserExit
from ..errors import Refused, StoreDown
from ..ports import CascadeStore, GroupsStore, HandPins, OwnersStore, UsersDirectory, WorkingExits
from ..users.models import ExitChosen, KnownUser, UserGroup
from ..users.service import UsersService
from .models import PERCENT, AppExits, AppMe, ExitChoice, ExitShown, Member, MyGroup
from .owners import is_owner


class AppService:
    def __init__(
        self,
        directory: UsersDirectory,
        groups: GroupsStore,
        hand_pins: HandPins,
        owners: OwnersStore,
        cascade: CascadeStore,
        working: WorkingExits,
        users: UsersService,
    ) -> None:
        self.__directory = directory
        self.__groups = groups
        self.__hand_pins = hand_pins
        self.__owners = owners
        self.__cascade = cascade
        self.__working = working
        self.__users = users

    async def __user_at(self, address: str, force: bool) -> tuple[KnownUser, list[KnownUser]]:
        """→ the user with this tunnel address, and everybody. Refused when nobody has it."""
        users = await self.__directory.list(force=force)
        user = next((user for user in users if user.ip == address), None)
        if user is None:
            raise Refused("это устройство не найдено в сети")
        return user, users

    async def __exit_names(self) -> dict[int, str]:
        try:
            settings = await self.__cascade.load()
        except StoreDown as e:
            raise Refused(f"Хранилище настроек недоступно: {e}") from None
        return {exit_.index: exit_.name for exit_ in settings.exits}

    @staticmethod
    def __group_shown(group: UserGroup, users: list[KnownUser]) -> MyGroup:
        """A space shows its members (they reach one another anyway); any other group only itself."""
        address_of = {user.name: user.ip for user in users}
        members = [Member(name=name, address=address_of[name]) for name in group.members if address_of.get(name)] if group.space else []
        return MyGroup(name=group.name, filter=group.filter, space=group.space, members=members)

    async def me(self, request: AppMeRequest) -> AppMe:
        user, users = await self.__user_at(request.address, force=False)
        group = next((group for group in (await self.__groups.read()).groups if user.name in group.members), None)
        pinned = (await self.__hand_pins.read()).get(user.name)
        group_exit = group.exit if group is not None and type(group.exit) is int else None
        applies = pinned if pinned is not None else group_exit
        exit_name = None if applies is None else (await self.__exit_names()).get(applies)
        return AppMe(
            name=user.name,
            address=user.ip,
            owner=is_owner(await self.__owners.read(), user),
            expires_at=user.expires_at,
            exit=ExitChoice(pinned=pinned, group=group_exit, name=exit_name),
            group=None if group is None else self.__group_shown(group, users),
        )

    async def exits(self, _request: AppExitsRequest) -> AppExits:
        try:
            settings = await self.__cascade.load()
        except StoreDown as e:
            raise Refused(f"Хранилище настроек недоступно: {e}") from None
        whole = sum(exit_.weight for exit_ in settings.exits) or 1
        up = self.__working.up()
        return AppExits(exits=[
            ExitShown(index=exit_.index, name=exit_.name, up=exit_.index in up, share=round(exit_.weight * PERCENT / whole))
            for exit_ in settings.exits
        ])

    async def choose(self, request: AppExit) -> ExitChosen:
        """The user keeps their own device on an exit, or lets the shares decide again."""
        user, _users = await self.__user_at(request.address, force=True)
        return await self.__users.choose_exit(UserExit(exit=request.exit, names=[user.name]))
