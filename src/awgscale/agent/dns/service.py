"""The commands about the smart-DNS devices: dns-list, dns-add, dns-remove."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from ..commands import DnsAdd, DnsList, DnsRemove
from ..errors import Refused
from ..files import write_file
from ..locks import file_lock
from ..ports import DevicesStore
from .models import Device, DeviceAdded, DeviceRemoved, DeviceShown, DevicesShown
from .store import unix

TOKEN_BYTES = 18
LOCK_SUFFIX = ".lock"


def gate_state(path: Path) -> Mapping[str, Any]:
    """What the DNS gate keeps about the devices it has seen; nothing when its file is not there or cannot be read."""
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def tokens_file_text(devices: Mapping[str, Device]) -> str:
    """The file the DNS gate reads: the codes, and nothing else."""
    tokens = {token: {"name": device.name, "created": unix(device.created)} for token, device in devices.items()}
    return json.dumps({"tokens": tokens}, indent=1)


class DnsDevicesService:
    def __init__(self, devices: DevicesStore, tokens_file: Path, state_file: Path) -> None:
        self.__devices = devices
        self.__tokens_file = tokens_file
        self.__state_file = state_file

    def __compile(self, devices: Mapping[str, Device]) -> None:
        self.__tokens_file.parent.mkdir(parents=True, exist_ok=True)
        write_file(self.__tokens_file, tokens_file_text(devices))

    async def __save(self, devices: dict[str, Device]) -> None:
        """Stored in the database; the file is compiled from it for the DNS gate, which reads nothing else."""
        await self.__devices.save(devices)
        await asyncio.to_thread(self.__compile, devices)

    def __lock(self) -> Path:
        return self.__tokens_file.with_name(self.__tokens_file.name + LOCK_SUFFIX)

    async def show(self, _request: DnsList) -> DevicesShown:
        state = await asyncio.to_thread(gate_state, self.__state_file)
        seen = state.get("seen", {})
        allowed = state.get("allowed", {})
        now = time.time()
        shown: list[DeviceShown] = []
        for token, device in (await self.__devices.read()).items():
            noted = seen.get(device.name, {})
            address = noted.get("ip")
            shown.append(
                DeviceShown(
                    name=device.name,
                    token=token,
                    created=unix(device.created),
                    last=noted.get("last"),
                    ip=address,
                    queries=noted.get("queries", 0),
                    admitted=bool(address) and allowed.get(address, 0) > now,
                )
            )
        return DevicesShown(devices=sorted(shown, key=lambda device: device.name or ""))

    async def add(self, request: DnsAdd) -> DeviceAdded:
        async with file_lock(self.__lock()):      # read, change, save: one at a time, or two devices added at once lose one
            devices = await self.__devices.read()
            if any(device.name == request.name for device in devices.values()):
                raise Refused("устройство с таким именем уже есть")
            token = secrets.token_urlsafe(TOKEN_BYTES)
            devices[token] = Device(name=request.name, created=datetime.fromtimestamp(int(time.time()), UTC))
            await self.__save(devices)
        return DeviceAdded(name=request.name, token=token)

    async def remove(self, request: DnsRemove) -> DeviceRemoved:
        async with file_lock(self.__lock()):
            devices = await self.__devices.read()
            left = {token: device for token, device in devices.items() if device.name != request.name}
            if len(left) == len(devices):
                raise Refused("нет такого устройства")
            await self.__save(left)
        return DeviceRemoved(name=request.name)
