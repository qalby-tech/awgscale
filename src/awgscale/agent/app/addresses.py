"""An exit's public address, as the app is told it: what a person on that exit is seen as abroad."""
from __future__ import annotations

import asyncio
import ipaddress
import logging

from ..clients.http import ERROR_LENGTH
from ..ports import ExitsGate, TunnelFacts
from ..tunnel import endpoint_host

log = logging.getLogger("awgscale.agent")

REGISTRY_SECONDS = 3.0          # the app's question waits no longer for the exits service's record


def is_ipv4(address: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(address), ipaddress.IPv4Address)
    except ValueError:
        return False


class PublicAddresses:
    """For one answer: the host of the exit's link in the last look, else as the tunnel says it, else the
    server's address in the exits service's record (asked at most once, and only when needed). An IPv4
    address is preferred: a link that points to an IPv6 one still asks the record for the server's IPv4."""

    def __init__(self, tunnel: TunnelFacts, exits: ExitsGate) -> None:
        self.__tunnel = tunnel
        self.__exits = exits
        self.__registered: dict[int, str] | None = None

    async def __asked_of_tunnel(self, iface: str) -> str | None:
        try:
            return endpoint_host(await self.__tunnel.endpoint_of(iface))
        except Exception as e:
            log.warning("app-exits: the link %s not asked of the tunnel: %s", iface, repr(e)[:ERROR_LENGTH])
            return None

    async def __record(self) -> dict[int, str]:
        if self.__registered is None:
            try:
                registry = await asyncio.wait_for(self.__exits.registry(), REGISTRY_SECONDS)
                self.__registered = {int(index): str(node.address) for index, node in registry.nodes.items() if index.isdigit()}
            except Exception as e:
                log.warning("app-exits without the exits service's record: %s", repr(e)[:ERROR_LENGTH])
                self.__registered = {}
        return self.__registered

    async def of(self, index: int, iface: str, seen: str | None) -> str:
        """Never raises; empty only when nothing at all knows the address."""
        if seen is not None and is_ipv4(seen):
            return seen
        asked = await self.__asked_of_tunnel(iface)
        if asked is not None and is_ipv4(asked):
            return asked
        registered = (await self.__record()).get(index)
        return registered or seen or asked or ""
