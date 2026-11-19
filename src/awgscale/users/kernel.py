"""The tunnel as the kernel has it: peers are added and removed one at a time, the interface is never restarted."""
from __future__ import annotations

from typing import AsyncIterator

from awgscale.core.ashell import AsyncRunner
from awgscale.core.linkevents import MonitorEnded, link_events
from awgscale.service.errors import Down

from .models import Peer
from .settings import UsersSettings

AWG_TIMEOUT = 15.0
ERROR_LENGTH = 160


class Kernel:
    def __init__(self, settings: UsersSettings, run: AsyncRunner) -> None:
        self.__settings = settings
        self.__run = run

    async def __awg(self, *args: str, stdin: str | None = None) -> str:
        result = await self.__run([str(self.__settings.awg), *args], stdin, AWG_TIMEOUT)
        if not result.ok:
            raise Down(f"awg {args[0]}: {result.err[:ERROR_LENGTH]}")
        return result.out

    async def peers(self) -> set[str]:
        """The public keys the tunnel knows now."""
        return set((await self.__awg("show", self.__settings.iface, "peers")).split())

    async def add_peer(self, peer: Peer) -> None:
        # the preshared key goes in on stdin, never on a command line
        await self.__awg(
            "set", self.__settings.iface, "peer", peer.public_key,
            "preshared-key", "/dev/stdin", "allowed-ips", f"{peer.ip}/32",
            stdin=peer.preshared_key.get_secret_value() + "\n",
        )

    async def remove_peer(self, public_key: str) -> None:
        await self.__awg("set", self.__settings.iface, "peer", public_key, "remove")

    async def came_up(self) -> AsyncIterator[None]:
        """Yields every time the kernel reports the tunnel's interface up (its
        own notifications, nothing is polled). Down when they cannot be listened to, or stop."""
        try:
            async for event in link_events(self.__settings.ip):
                if event.up and event.name == self.__settings.iface:
                    yield
        except MonitorEnded as e:
            raise Down(f"ip monitor: {e}") from None
