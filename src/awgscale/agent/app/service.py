"""The commands of the app. The panel tells whose device asks by the tunnel
address the request came from; here that address becomes a user, and the
answers say nothing about anybody else."""
from __future__ import annotations

import logging

from ..adguard.profiles import effective
from ..commands import AUTO_FILTER, AppExit, AppExits as AppExitsRequest, AppFilter, AppMe as AppMeRequest, AppRoute, RouteChoice, UserExit
from ..errors import Refused, StoreDown
from pathlib import Path

from ..locks import file_lock
from ..ports import CascadeStore, ExitsSeen, FilterProfiles, GroupsStore, HandPins, OwnFilters, OwnersStore, TunnelFacts, UsersDirectory, WorkingExits
from ..state.models import ExitFigures
from ..tunnel import endpoint_host
from ..users.models import ExitChosen, KnownUser, UserGroup
from ..users.service import UsersService
from ..users.models import GroupFilter
from ..users.pins import Pinning
from .models import PERCENT, AppExits, AppMe, ExitChoice, ExitShown, FilterChoice, FilterChosen, Member, MyGroup, RouteChosen, RouteShown
from .owners import is_owner

log = logging.getLogger("awgscale.agent")

ERROR_LENGTH = 200


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
        own_filters: OwnFilters,
        profiles: FilterProfiles,
        users_lock: Path,
        pinning: Pinning,
        seen: ExitsSeen,
        tunnel: TunnelFacts,
    ) -> None:
        self.__directory = directory
        self.__groups = groups
        self.__hand_pins = hand_pins
        self.__owners = owners
        self.__cascade = cascade
        self.__working = working
        self.__users = users
        self.__own_filters = own_filters
        self.__profiles = profiles
        self.__users_lock = users_lock
        self.__pinning = pinning
        self.__seen = seen
        self.__tunnel = tunnel

    async def __user_at(self, address: str, force: bool) -> tuple[KnownUser, list[KnownUser]]:
        """→ the user with this tunnel address, and everybody. Refused when nobody has it."""
        users = await self.__directory.list(force=force)
        user = next((user for user in users if user.ip == address), None)
        if user is None:
            raise Refused("это устройство не найдено в сети")
        return user, users

    async def __has_exits(self) -> bool:
        try:
            return bool((await self.__cascade.load()).exits)
        except StoreDown as e:
            raise Refused(f"Хранилище настроек недоступно: {e}") from None

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
            filter=self.__filter_of(group, (await self.__own_filters.read()).get(user.name)),
            route=RouteShown(all=user.name in await self.__hand_pins.whole(), available=await self.__has_exits()),
        )

    @staticmethod
    def __filter_of(group: UserGroup | None, own: str | None) -> FilterChoice:
        locked = group is not None and group.filter == GroupFilter.KIDS.value
        return FilterChoice(applies=effective(group, own), own=None if locked else own, locked=locked)

    async def choose_filter(self, request: AppFilter) -> FilterChosen:
        """The user chooses how their own requests are filtered, or goes back to their group's;
        not in a children's group (an adult chose that)."""
        async with file_lock(self.__users_lock):
            user, users = await self.__user_at(request.address, force=True)
            groups = (await self.__groups.read()).groups
            group = next((group for group in groups if user.name in group.members), None)
            if group is not None and group.filter == GroupFilter.KIDS.value:
                raise Refused("защиту этого устройства задаёт взрослый: в детской группе её не поменять")
            chosen = await self.__own_filters.read()
            if request.filter == AUTO_FILTER:
                chosen.pop(user.name, None)
            else:
                chosen[user.name] = request.filter
            await self.__own_filters.save(chosen)
            failed = await self.__profiles.sync(groups, users)
            if failed:
                raise Refused(f"Защита сохранена, но не применилась: {failed}")
        return FilterChosen(name=user.name, filter=self.__filter_of(group, chosen.get(user.name)))

    async def choose_route(self, request: AppRoute) -> RouteChosen:
        """The user sends everything through their exit (Russian sites too), or lets the network's mode decide.
        Applied at once to new connections; open ones keep their way. Their names are then looked up through the exit too."""
        if not await self.__has_exits():
            raise Refused("в этой сети нет зарубежного выхода: весь трафик и так идёт с сервера")
        async with file_lock(self.__users_lock):
            user, users = await self.__user_at(request.address, force=True)
            whole = await self.__hand_pins.whole()
            if request.route is RouteChoice.ALL:
                whole.add(user.name)
            else:
                whole.discard(user.name)
            await self.__hand_pins.save_whole(whole)
            groups = (await self.__groups.read()).groups
            if not await self.__pinning.compile(groups, users):
                raise Refused("сохранено, но список пользователей не прочитан — применится при следующем опросе")
            failed = await self.__profiles.sync(groups, users)
            if failed:
                raise Refused(f"Маршрут сохранён, но DNS не перенастроен: {failed}")
        return RouteChosen(name=user.name, route=RouteShown(all=user.name in whole, available=True))

    async def exits(self, _request: AppExitsRequest) -> AppExits:
        try:
            settings = await self.__cascade.load()
        except StoreDown as e:
            raise Refused(f"Хранилище настроек недоступно: {e}") from None
        whole = sum(exit_.weight for exit_ in settings.exits) or 1
        up = self.__working.up()
        figures = await self.__figures()
        shown: list[ExitShown] = []
        for exit_ in settings.exits:
            seen = figures.get(exit_.index, ExitFigures())
            shown.append(ExitShown(
                index=exit_.index,
                name=exit_.name,
                up=exit_.index in up,
                share=round(exit_.weight * PERCENT / whole),
                address=seen.address or await self.__address_of(exit_.iface),
                country=exit_.country,
                users=seen.users,
                online=seen.online,
            ))
        return AppExits(exits=shown)

    async def __figures(self) -> dict[int, ExitFigures]:
        """The last look at the state; without one the exits are still answered, with nothing counted."""
        try:
            return await self.__seen.exit_figures()
        except Exception as e:
            log.warning("app-exits without the look at the state: %s", repr(e)[:ERROR_LENGTH])
            return {}

    async def __address_of(self, iface: str) -> str:
        """The host the link points to, asked of the tunnel itself when the look has none."""
        try:
            return endpoint_host(await self.__tunnel.endpoint_of(iface)) or ""
        except Exception:
            return ""

    async def choose(self, request: AppExit) -> ExitChosen:
        """The user keeps their own device on an exit, or lets the shares decide again."""
        user, _users = await self.__user_at(request.address, force=True)
        return await self.__users.choose_exit(UserExit(exit=request.exit, names=[user.name]))
