"""Changing the cascade's settings: stored, compiled, applied — or put back as they were."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..commands import CountrySet, WeightsSet
from ..files import read_text
from ..errors import Refused, StoreDown
from ..locks import file_lock
from ..payloads import decode
from ..ports import CascadeStore, WatchGate
from ..shares import WHOLE
from ..shell import CascadeGet, CascadeSet
from .conf import compile_to, compiles, text_of
from .models import MAX_WEIGHT, MIN_WEIGHT, CascadeChanged, CascadeFile, CascadeSettings, CascadeShown, checked

HEALTH_TIMEOUT = 10.0
APPLY_TIMEOUT = 120.0
SHOWN_REASON = 200

Change = Callable[[CascadeSettings], dict[str, Any]]      # the stored settings → the document wanted instead
Then = Callable[[CascadeSettings, CascadeSettings], Awaitable[None]]      # (as they were, as they are) → what else belongs to the change


def _with_stored_countries(document: dict[str, Any], old: CascadeSettings) -> dict[str, Any]:
    """The document, its exits without "country" given the country stored for their number (what else it holds is checked later)."""
    records = document.get("exits")
    if not isinstance(records, list):
        return document
    stored = {exit_.index: exit_.country for exit_ in old.exits if exit_.country is not None}
    kept = [
        dict(record, country=stored[record["index"]])
        if isinstance(record, dict) and "country" not in record and type(record.get("index")) is int and record["index"] in stored
        else record
        for record in records
    ]
    return dict(document, exits=kept)


class CascadeService:
    def __init__(self, store: CascadeStore, watch: WatchGate, conf_file: Path, lock_file: Path) -> None:
        self.__store = store
        self.__watch = watch
        self.__conf_file = conf_file
        self.__lock_file = lock_file

    async def __compile(self, settings: CascadeSettings) -> None:
        await asyncio.to_thread(compile_to, self.__conf_file, settings)

    async def __put_back(self, old: CascadeSettings) -> None:
        """The stored settings, the file and the rules as they were, as far as that goes."""
        await self.__store.save(old)
        try:
            await self.__compile(old)
            await self.__watch.apply(APPLY_TIMEOUT)
        except Exception:
            pass

    async def change(self, change: Change, then: Then | None = None) -> CascadeChanged:
        """One change of the settings; the rules are applied at once. What did
        not apply is put back: the stored settings, the file and the rules.
        `then` is what else belongs to the change once it is applied; it runs
        while the settings are still held, so nobody changes them in between."""
        async with file_lock(self.__lock_file):
            try:
                old = await self.__store.load()
            except StoreDown as e:
                raise Refused(f"Хранилище настроек недоступно: {e}") from None
            try:
                new = checked(change(old))
                if not compiles(new):            # before anything is stored: what cannot be compiled is not saved
                    raise ValueError("такие настройки не собираются в cascade.conf")
            except ValueError as e:
                raise Refused(str(e)) from None
            if new == old:
                try:
                    await self.__compile(old)    # the file is made to agree even when nothing changed
                except (ValueError, OSError) as e:
                    raise Refused(str(e)) from None
                return CascadeChanged(already=True, cascade=old)
            if new.routed() == old.routed():
                # only what the pages and the app show (an exit's country): the file and the rules stay as they are
                await self.__store.save(new)
                if then is not None:
                    await then(old, new)
                return CascadeChanged(cascade=new, countries_only=True)
            if (new.client_if, new.client_subnet) != (old.client_if, old.client_subnet):
                raise Refused("туннель пользователей и его сеть задаются при установке сервера и здесь не меняются")
            gone = sorted({exit_.index for exit_ in old.exits} - {exit_.index for exit_ in new.exits})
            if not (await self.__watch.health(HEALTH_TIMEOUT)).ok:
                raise Refused("сторож маршрутов не отвечает — настройки не изменены")
            await self.__store.save(new)
            try:
                await self.__compile(new)
                applied = await self.__watch.apply(APPLY_TIMEOUT)
                if not applied.ok:
                    raise ValueError("правила не применились: " + applied.said.strip()[:SHOWN_REASON])
            except (ValueError, OSError) as e:
                await self.__put_back(old)
                raise Refused(f"{e} — прежние настройки возвращены") from None
            note = "правила убранных выходов (" + ", ".join(map(str, gone)) + ") останутся до перезагрузки сервера" if gone else None
            if then is not None:
                await then(old, new)
            return CascadeChanged(cascade=new, note=note)

    async def set_weights(self, request: WeightsSet) -> CascadeChanged:
        """The shares of the exits alone — what the panel may change. {"weights": {"<index>": percent},
        "base": the shares the page showed}. The admin gives percents: with every exit named they must
        add up to 100. Everything else of the settings stays as stored."""
        data = decode(request.payload)
        weights = data.get("weights")
        base = data.get("base")
        fits = isinstance(weights, dict) and weights and all(
            isinstance(index, str) and index.isdigit() and type(share) is int and MIN_WEIGHT <= share <= MAX_WEIGHT for index, share in weights.items()
        )
        if not fits:
            raise Refused("доля: целое число процентов от 1 до 99")
        assert isinstance(weights, dict)

        def with_these(old: CascadeSettings) -> dict[str, Any]:
            have = old.weights()
            if isinstance(base, dict) and base != have:
                raise Refused("доли выходов уже изменены в другом окне — показаны новые", conflict=True, weights=have)
            if set(weights) - set(have):
                raise ValueError("нет такого выхода")
            if set(weights) == set(have) and sum(weights.values()) != WHOLE:
                raise ValueError(f"доли должны давать в сумме 100%, а получается {sum(weights.values())}%")
            return old.with_weights(weights).wire()

        return await self.change(with_these)

    async def set_country(self, request: CountrySet) -> CascadeChanged:
        """The home country alone: whose networks go direct. The watch service downloads its list
        when it applies the change; a list that cannot be had leaves the settings as they were."""
        return await self.change(lambda old: dict(old.wire(), home_country=request.country))

    # ── from a shell only ────────────────────────────────────────────────────

    async def show(self, request: CascadeGet) -> CascadeShown | CascadeFile:
        """The stored settings and whether the file agrees with them — or, asked for it, the file they compile to."""
        try:
            settings = await self.__store.load()
        except StoreDown as e:
            raise Refused(f"Хранилище настроек недоступно: {e}") from None
        text = text_of(checked(settings.wire()))
        if request.conf:
            return CascadeFile(conf=text)
        return CascadeShown(cascade=settings, compiled=await asyncio.to_thread(read_text, self.__conf_file) == text)

    async def replace(self, request: CascadeSet) -> CascadeChanged:
        """Change the exits, their shares and the rest; the rules are applied at once.
        What did not apply is put back: the stored settings, the file and the rules.
        An exit of the document without "country" keeps the one stored for its number; "country": null clears it."""
        document = decode(request.payload)
        return await self.change(lambda old: _with_stored_countries(document, old))
