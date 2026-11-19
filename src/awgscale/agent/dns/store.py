"""The devices' codes in the database; the table holds the moment as Unix seconds."""
from __future__ import annotations

from datetime import UTC, datetime

from ..store.documents import Kind
from ..store.store import Store
from .models import Device


def unix(moment: datetime) -> int:
    return int(moment.timestamp())


class StoredDevices:
    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> dict[str, Device]:
        held = await self.__store.get(Kind.DNS)
        if not isinstance(held, dict):
            return {}
        return {token: Device(name=record["name"], created=datetime.fromtimestamp(record["created"], UTC)) for token, record in held.items()}

    async def save(self, devices: dict[str, Device]) -> None:
        await self.__store.put(Kind.DNS, {token: {"name": device.name, "created": unix(device.created)} for token, device in devices.items()})
