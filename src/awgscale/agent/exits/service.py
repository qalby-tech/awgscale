"""The commands about the exits as servers."""
from __future__ import annotations

import logging
from typing import Any

from pydantic import SecretStr

from ..cascade.models import MAX_EXITS, MAX_INDEX, MIN_INDEX, CascadeChanged, CascadeSettings
from ..cascade.service import CascadeService
from ..clients.exits import Registry
from ..clients.http import ERROR_LENGTH
from ..commands import AUTO_COUNTRY, ExitAdd, ExitAttach, ExitCountry, ExitRemove, ExitRename, ExitsGet, ExitUpdate
from ..errors import Refused, StoreDown
from ..payloads import decode
from ..ports import CascadeStore, CountryFinder, ExitsGate, KeptPins, WorkingExits
from ..shares import shares_with
from ..iso_countries import is_country
from .models import AddStarted, CountryChanged, ExitChanged, NewServer, ProvenExit, ServersShown, UpdateStarted, link_address, link_iface

log = logging.getLogger("awgscale.agent")


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
    def __init__(self, cascade: CascadeService, settings: CascadeStore, exits: ExitsGate, working: WorkingExits, kept: KeptPins, countries: CountryFinder) -> None:
        self.__cascade = cascade
        self.__settings = settings
        self.__exits = exits
        self.__working = working
        self.__kept = kept
        self.__countries = countries

    async def __stored(self) -> CascadeSettings:
        try:
            return await self.__settings.load()
        except StoreDown as e:
            raise Refused(f"Хранилище настроек недоступно: {e}") from None

    def __others_working(self, index: int) -> bool:
        """Does any exit but this one work right now."""
        return bool(self.__working.up() - {index})

    async def show(self, _request: ExitsGet) -> ServersShown:
        """The servers as the exits service has them, and where each exit of the settings is: the stored
        country, else the guess its name gives. The settings never make it fail: unread, there are no countries."""
        registry = await self.__exits.registry()
        try:
            settings = await self.__settings.load()
        except Exception as e:
            log.warning("exits-get without the countries: %s", repr(e)[:ERROR_LENGTH])
            return ServersShown(registry=registry)
        return ServersShown(registry=registry, countries={exit_.index: exit_.shown_country for exit_ in settings.exits}, stored={exit_.index: exit_.country for exit_ in settings.exits})

    async def __is_new(self, index: int) -> bool:
        """Is this exit not in the settings yet; with the store down the change itself says so."""
        try:
            return index not in {exit_.index for exit_ in (await self.__settings.load()).exits}
        except StoreDown:
            return False

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
        # where it is, found by its address before the settings are held: no answer in time is no country, never a refusal
        country = await self.__countries.find(proven.name, link_iface(proven.index)) if await self.__is_new(proven.index) else None

        def with_it(old: CascadeSettings) -> dict[str, Any]:
            if any(exit_.index == proven.index for exit_ in old.exits):
                return _renamed(old, proven.index, proven.name)
            # `weight` is the percent the new exit takes; the others keep their proportion in the rest
            others, mine = shares_with([exit_.weight for exit_ in old.exits], proven.weight)
            kept = tuple(exit_.model_copy(update={"weight": weight}) for exit_, weight in zip(old.exits, others))
            # the first exit of a single server makes it a cascade
            return old.model_copy(update={"exits": (*kept, proven.setting(mine, country)), "single_server": False}).wire()

        return ExitChanged(changed=await self.__cascade.change(with_it), name=proven.name)

    async def rename(self, request: ExitRename) -> ExitChanged:
        names: dict[str, str] = {}

        def with_name(old: CascadeSettings) -> dict[str, Any]:
            index = _index_in(request.index, old)
            names["was"] = _name_of(index, old)
            return _renamed(old, index, request.name)

        changed = await self.__cascade.change(with_name)
        return ExitChanged(changed=changed, name=request.name, was=None if changed.already else names.get("was"))

    async def set_country(self, request: ExitCountry) -> CountryChanged:
        """Where an exit is, as the admin says; "auto" finds it again by its address. Found nothing,
        the country stored stays (or there is still none): no lookup ever takes a known country away."""
        if request.country == AUTO_COUNTRY:
            settings = await self.__stored()
            index = _index_in(request.index, settings)
            exit_ = next(exit_ for exit_ in settings.exits if exit_.index == index)
            found = await self.__countries.find(exit_.name, exit_.iface)
            if found is None:
                kept = exit_.country
                said = f"оставлена прежняя ({kept.upper()})" if kept else "страны по-прежнему нет"
                note = f"страна по адресу сервера не найдена — {said}"
                log.info("exit-country %s auto: nothing found, %s", index, kept or "still none")
                return CountryChanged(changed=CascadeChanged(already=True, cascade=settings, note=note), index=index, name=exit_.name, country=kept, kept=True)
            country = found
        elif is_country(request.country):
            country = request.country
        else:
            raise Refused("нет такой страны: нужен код ISO 3166 из двух латинских букв, например nl или de")
        names: dict[str, str] = {}

        def with_country(old: CascadeSettings) -> dict[str, Any]:
            index = _index_in(request.index, old)
            names["name"] = _name_of(index, old)
            return old.with_country(index, country).wire()

        changed = await self.__cascade.change(with_country)
        return CountryChanged(changed=changed, index=int(request.index), name=names["name"], country=country)

    async def backfill(self) -> None:
        """The exits without a stored country are looked up by their public address (a timed job, never a request).
        What is found is stored; what is not is asked again next time."""
        settings = await self.__stored()
        found: dict[int, str] = {}
        for exit_ in settings.exits:
            if exit_.country is None:
                country = await self.__countries.find(exit_.name, exit_.iface)
                if country is not None:
                    found[exit_.index] = country
        if not found:
            return

        def with_found(old: CascadeSettings) -> dict[str, Any]:
            exits = tuple(exit_.model_copy(update={"country": found[exit_.index]}) if exit_.country is None and exit_.index in found else exit_ for exit_ in old.exits)
            return old.model_copy(update={"exits": exits}).wire()

        await self.__cascade.change(with_found)
        log.info("the countries of the exits found: %s", ", ".join(f"{index} {country}" for index, country in sorted(found.items())))

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
