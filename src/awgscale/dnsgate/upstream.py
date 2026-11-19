"""The resolver behind the gate (AdGuard Home): everything the gate does not answer itself goes there."""
from __future__ import annotations

import asyncio
import struct
import time
from dataclasses import dataclass
from ipaddress import IPv4Address

from .settings import DnsgateSettings
from .wire import MAX_MESSAGE_BYTES, address_query, address_records, is_truncated

UDP_TIMEOUT = 4.0
TCP_TIMEOUT = 5.0
LENGTH_PREFIX_BYTES = 2                 # DNS over TCP: every message is preceded by its length
LONGEST_ADDRESS_CACHE = 300             # seconds an address is remembered at most…
SHORTEST_ADDRESS_CACHE = 30             # …and at least


class _Reply(asyncio.DatagramProtocol):
    """Waits for the one datagram that answers a query."""

    def __init__(self) -> None:
        self.__received: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    @property
    def received(self) -> asyncio.Future[bytes]:
        return self.__received

    def datagram_received(self, data: bytes, addr: object) -> None:
        if not self.__received.done():
            self.__received.set_result(data[:MAX_MESSAGE_BYTES])

    def error_received(self, exc: Exception) -> None:
        if not self.__received.done():
            self.__received.set_exception(exc)


@dataclass(frozen=True, slots=True)
class _Remembered:
    until: float                        # on the monotonic clock
    addresses: tuple[IPv4Address, ...]


class Upstream:
    def __init__(self, settings: DnsgateSettings) -> None:
        self.__settings = settings
        self.__addresses: dict[str, _Remembered] = {}

    async def __over_udp(self, message: bytes) -> bytes:
        loop = asyncio.get_running_loop()
        transport, reply = await loop.create_datagram_endpoint(_Reply, remote_addr=(self.__settings.upstream_host, self.__settings.upstream_port))
        try:
            transport.sendto(message)
            return await asyncio.wait_for(reply.received, UDP_TIMEOUT)
        finally:
            transport.close()

    async def __over_tcp(self, message: bytes) -> bytes:
        async with asyncio.timeout(TCP_TIMEOUT):
            reader, writer = await asyncio.open_connection(self.__settings.upstream_host, self.__settings.upstream_port)
            try:
                writer.write(struct.pack("!H", len(message)) + message)
                await writer.drain()
                expected = struct.unpack("!H", await reader.readexactly(LENGTH_PREFIX_BYTES))[0]
                response = b""
                while len(response) < expected:
                    chunk = await reader.read(expected - len(response))
                    if not chunk:
                        break
                    response += chunk
                return response
            except asyncio.IncompleteReadError as e:
                raise ConnectionError("the resolver closed the connection") from e
            finally:
                writer.close()

    async def forward(self, message: bytes) -> bytes:
        """The resolver's answer to `message`, untouched. Raises OSError when it cannot be had."""
        response = await self.__over_udp(message)
        if is_truncated(response):                  # truncated → repeat over TCP
            response = await self.__over_tcp(message)
        return response

    async def real_addresses(self, name: str) -> tuple[IPv4Address, ...]:
        """The A records of `name` as the resolver sees them (not the gate's own rewrite).
        Never a blocked (0.0.0.0), private or the gate's own address."""
        now = time.monotonic()
        remembered = self.__addresses.get(name)
        if remembered and remembered.until > now:
            return remembered.addresses
        response = await self.forward(address_query(name, query_id=int(time.time() * 1000)))
        usable = [record for record in address_records(response) if record.address.is_global and record.address != self.__settings.proxy_ip]
        shortest_ttl = min([LONGEST_ADDRESS_CACHE, *(record.ttl for record in usable)])
        addresses = tuple(record.address for record in usable)
        self.__addresses[name] = _Remembered(now + max(SHORTEST_ADDRESS_CACHE, shortest_ttl), addresses)
        return addresses
