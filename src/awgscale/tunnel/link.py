"""The interface in the kernel: what is there, making it, and where it differs from its file."""
from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from awgscale.core.ashell import AsyncRunner

from .conf import InterfaceFile

ERROR_LENGTH = 160
LINK_ERROR_LENGTH = 120
IPV4_FAMILY = "inet"                # an address of `ip -j addr` that is IPv4


class Difference(StrEnum):
    """Where an interface that is there is not what its file says. Words, never values."""

    ADDRESS = "адрес"
    MTU = "MTU"
    DOWN = "интерфейс выключен"
    PORT = "порт"
    KEY = "ключ"


class LinkState(BaseModel):
    """An interface as the kernel has it."""

    model_config = ConfigDict(frozen=True)

    up: bool = Field(description="The interface is switched on")
    mtu: int | None = Field(description="Its MTU")
    addresses: tuple[str, ...] = Field(description="Its IPv4 addresses with their masks, sorted")


class MakeFailed(Exception):
    """The interface could not be made. `module` — the link itself was refused (no kernel module?)."""

    def __init__(self, text: str, module: bool = False) -> None:
        super().__init__(text)
        self.module = module


class Links:
    def __init__(self, run: AsyncRunner, awg: str, link_type: str) -> None:
        self.__run = run
        self.__awg = awg
        self.__link_type = link_type

    async def state(self, iface: str) -> LinkState | None:
        """The interface as it is now; None when it is not there."""
        result = await self.__run(["ip", "-j", "-4", "addr", "show", "dev", iface])
        if not result.ok:
            return None
        try:
            link = json.loads(result.out)[0]
        except (ValueError, IndexError):
            return None
        addresses = sorted(f'{a["local"]}/{a["prefixlen"]}' for a in link.get("addr_info", []) if a.get("family") == IPV4_FAMILY)
        return LinkState(up="UP" in link.get("flags", []), mtu=link.get("mtu"), addresses=tuple(addresses))

    async def make(self, iface: str, file: InterfaceFile) -> None:
        """The interface from nothing, the way awg-quick does it. What was half made is taken
        away again: a later look starts clean. Raises MakeFailed."""
        added = await self.__run(["ip", "link", "add", iface, "type", self.__link_type])
        if not added.ok:
            raise MakeFailed(added.err[:LINK_ERROR_LENGTH], module=True)
        switch_on = ["ip", "link", "set", *(["mtu", str(file.mtu)] if file.mtu else []), "up", "dev", iface]
        steps: tuple[tuple[list[str], str | None], ...] = (
            ([self.__awg, "setconf", iface, "/dev/stdin"], file.setconf.get_secret_value()),      # through stdin, never a file
            (["ip", "-4", "address", "add", file.address, "dev", iface], None),
            (switch_on, None),
        )
        for command, stdin in steps:
            done = await self.__run(command, stdin)
            if not done.ok:
                await self.__run(["ip", "link", "del", iface])
                raise MakeFailed(f"{' '.join(command[:3])} failed: {done.err[:ERROR_LENGTH]}")

    async def differences(self, iface: str, file: InterfaceFile, have: LinkState) -> list[Difference]:
        differences: list[Difference] = []
        if file.address not in have.addresses:
            differences.append(Difference.ADDRESS)
        if file.mtu and have.mtu != file.mtu:
            differences.append(Difference.MTU)
        if not have.up:
            differences.append(Difference.DOWN)
        if file.port is not None:
            port = await self.__run([self.__awg, "show", iface, "listen-port"])
            if not port.ok or port.out.strip() != str(file.port):
                differences.append(Difference.PORT)
        expected = await self.__run([self.__awg, "pubkey"], file.private_key.get_secret_value() + "\n")
        present = await self.__run([self.__awg, "show", iface, "public-key"])
        if not expected.ok or not present.ok or not expected.out.strip() or expected.out.strip() != present.out.strip():
            differences.append(Difference.KEY)
        return differences
