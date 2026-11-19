"""The commands about the exits as servers."""
from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from ..cascade.models import MAX_EXITS, MAX_INDEX, MIN_INDEX, CascadeSettings
from ..cascade.service import CascadeService
from ..clients.exits import Registry
from ..commands import ExitAdd, ExitAttach, ExitRemove, ExitRename, ExitsGet, ExitUpdate
from ..errors import Refused, StoreDown
from ..payloads import decode
from ..ports import CascadeStore, ExitsGate, KeptPins, WorkingExits
from ..shares import shares_with
from .models import AddStarted, ExitChanged, NewServer, ProvenExit, ServersShown, UpdateStarted, link_address, link_iface


def _index_in(which: str, settings: CascadeSettings) -> int:
    """The number of an exit the settings know."""
    index = int(which)
    if index not in {exit_.index for exit_ in settings.exits}:
        raise Refused("нет такого выхода")
    return index


def _name_of(index: int, settings: CascadeSettings) -> str:
    return next(exit_.name for exit_ in settings.exits if exit_.index == index)


def _renamed(settings: CascadeSettings, index: int, name: str) -> dict[str, Any]:
    exits = tuple(exit_.model_copy(update={"name": name}) if exit_.index == index else exit_ for exit_ in settings.exits)
    return settings.model_copy(update={"exits": exits}).wire()


def _free_number(settings: CascadeSettings, registry: Registry) -> int:
    """A number free in every sense: the interface awgk<N> and the network 10.77.<N> follow from it,
    and an older exit may hold another number's interface (exit 3 on awgk2)."""
    used = {exit_.index for exit_ in settings.exits}
    taken = {exit_.iface for exit_ in settings.exits} | {exit_.tip for exit_ in settings.exits}
    for index in range(MIN_INDEX, MAX_INDEX + 1):
        if index not in used and str(index) not in registry.nodes and link_iface(index) not in taken and link_address(index) not in taken:
            return index
    raise Refused("свободных номеров для выхода не осталось")


class ExitServers:
    def __init__(self, cascade: CascadeService, settings: CascadeStore, exits: ExitsGate, working: WorkingExits, kept: KeptPins) -> None:
        self.__cascade = cascade
        self.__settings = settings
        self.__exits = exits
        self.__working = working
        self.__kept = kept

    async def __stored(self) -> CascadeSettings:
        try:
            return await self.__settings.load()
        except StoreDown as e:
            raise Refused(f"Хранилище настроек недоступно: {e}") from None

    def __others_working(self, index: int) -> bool:
        """Does any exit but this one work right now."""
        return bool(self.__working.up() - {index})

    async def show(self, _request: ExitsGet) -> ServersShown:
        return ServersShown(registry=await self.__exits.registry())

    async def add(self, request: ExitAdd, password: SecretStr | None) -> AddStarted:
        """A rented server → an exit: {"address", "name", "weight"?, "ssh_port"?}; the root password
        comes beside the arguments (or none, when the service's key was put on the server by hand).
        Only starts the job: the exit joins the cascade when the job has proven it works."""
        server = NewServer.of(decode(request.payload))
        settings = await self.__stored()
        share = server.share_among(len(settings.exits))
        registry = await self.__exits.registry()
        # started before: the same number, the job goes on
        index = next((int(number) for number, node in registry.nodes.items() if str(node.address) == server.address), None)
        if index is None:
            if len(settings.exits) >= MAX_EXITS:
                raise Refused("выходов уже восемь — больше нельзя")
            index = _free_number(settings, registry)
        if any(exit_.name == server.name and exit_.index != index for exit_ in settings.exits):
            raise Refused(f"выход с именем {server.name} уже есть")
        begun = await self.__exits.add(index, server.name, share, server.address, server.ssh_port, password)
        if not begun.ok:
            raise Refused(begun.said or "сервис выходов не отвечает")
        return AddStarted(index=index, name=server.name, address=server.address)

    async def attach(self, request: ExitAttach) -> ExitChanged:
        """Called by the exits service when a new exit answers through its tunnel: it joins the
        cascade (or, already there, only takes the name). Interface and addresses follow from the number."""
        proven = ProvenExit.of(decode(request.payload))

        def with_it(old: CascadeSettings) -> dict[str, Any]:
            if any(exit_.index == proven.index for exit_ in old.exits):
                return _renamed(old, proven.index, proven.name)
            # `weight` is the percent the new exit takes; the others keep their proportion in the rest
            others, mine = shares_with([exit_.weight for exit_ in old.exits], proven.weight)
            kept = tuple(exit_.model_copy(update={"weight": weight}) for exit_, weight in zip(old.exits, others))
            # the first exit of a single server makes it a cascade
            return old.model_copy(update={"exits": (*kept, proven.setting(mine)), "single_server": False}).wire()

        return ExitChanged(changed=await self.__cascade.change(with_it), name=proven.name)

    async def rename(self, request: ExitRename) -> ExitChanged:
        names: dict[str, str] = {}

        def with_name(old: CascadeSettings) -> dict[str, Any]:
            index = _index_in(request.index, old)
            names["was"] = _name_of(index, old)
            return _renamed(old, index, request.name)

        changed = await self.__cascade.change(with_name)
        return ExitChanged(changed=changed, name=request.name, was=None if changed.already else names.get("was"))

    async def remove(self, request: ExitRemove) -> ExitChanged:
        """Take an exit out of the cascade and away from the entry. Its users go to the others at
        once; groups and users kept on it by hand go back to "auto". The last exit is never removed,
        and the only WORKING one only when asked twice (force)."""
        force = request.force is not None
        gone: dict[str, Any] = {}
        said: dict[str, str] = {}

        def without_it(old: CascadeSettings) -> dict[str, Any]:
            index = _index_in(request.index, old)
            if len(old.exits) == 1:
                raise Refused("это единственный выход — без выхода туннель не работает. Сначала добавьте другой")
            if not force and not self.__others_working(index):
                raise Refused("остальные выходы сейчас не работают: без этого у пользователей не будет зарубежного трафика", confirm=True)
            removed = next(exit_ for exit_ in old.exits if exit_.index == index)
            gone.update(index=index, name=removed.name, iface=removed.iface)
            return old.model_copy(update={"exits": tuple(exit_ for exit_ in old.exits if exit_.index != index)}).wire()

        async def then(_old: CascadeSettings, _new: CascadeSettings) -> None:
            await self.__kept.release(gone["index"])
            cleared = await self.__exits.remove(gone["index"], gone["iface"], gone["name"])
            if not cleared.ok:
                said["note"] = "из каскада выход убран, но его связь на входе осталась: " + cleared.said

        changed = await self.__cascade.change(without_it, then)
        return ExitChanged(changed=changed, name=gone["name"], index=gone["index"], note=said.get("note"))

    async def update(self, request: ExitUpdate) -> UpdateStarted:
        reboot = request.reboot is not None
        settings = await self.__stored()
        index = _index_in(request.index, settings)
        name = _name_of(index, settings)
        if reboot and not self.__others_working(index):
            raise Refused("перезагрузка отложена: остальные выходы сейчас не работают, пользователи остались бы без зарубежного трафика")
        begun = await self.__exits.update(index, reboot, name)
        if not begun.ok:
            raise Refused(begun.said or "сервис выходов не отвечает")
        return UpdateStarted(index=index, name=name, reboot=reboot)
