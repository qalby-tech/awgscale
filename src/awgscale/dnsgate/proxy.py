"""The SNI proxy: a TLS connection to a listed name, from an admitted address, is passed abroad as it is."""
from __future__ import annotations

import asyncio
import struct
from typing import Awaitable, Callable

from awgscale.core.net import HTTPS_PORT

from .admission import Admission
from .settings import DnsgateSettings
from .sites import SiteList
from .tls import RECORD_HEAD_BYTES, parse_sni, read_proxy_header, record_length
from .upstream import Upstream

Connect = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]

PROXY_HEADER_TIMEOUT = 5.0
HELLO_TIMEOUT = 10.0
CONNECT_TIMEOUT = 10.0
PIPE_CHUNK_BYTES = 65536
READ_LIMIT_BYTES = 1 << 17               # a ClientHello with every extension fits


def _close(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    try:
        writer.close()
    except OSError:
        pass


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(PIPE_CHUNK_BYTES):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        _close(writer)


class SniProxy:
    def __init__(self, settings: DnsgateSettings, admission: Admission, sites: SiteList, upstream: Upstream, connect: Connect = asyncio.open_connection) -> None:
        self.__settings = settings
        self.__admission = admission
        self.__sites = sites
        self.__upstream = upstream
        self.__connect = connect

    async def __read_hello(self, reader: asyncio.StreamReader) -> bytes | None:
        head = await asyncio.wait_for(reader.readexactly(RECORD_HEAD_BYTES), HELLO_TIMEOUT)
        length = record_length(head)
        if length is None:
            return None
        return head + await asyncio.wait_for(reader.readexactly(length), HELLO_TIMEOUT)

    def __may_go(self, name: str | None) -> bool:
        """Only names on the site list, and never the server's own: this is not a general-purpose proxy."""
        return bool(name) and name != self.__settings.own_name and self.__sites.lists(name or "")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One connection. Anything that is not right ends it without a word."""
        site_writer: asyncio.StreamWriter | None = None
        try:
            ip = await asyncio.wait_for(read_proxy_header(reader), PROXY_HEADER_TIMEOUT)
            if not ip or not self.__admission.proxy_admits(ip):
                return
            hello = await self.__read_hello(reader)
            if hello is None:
                return
            name = parse_sni(hello)
            if name is None or not self.__may_go(name):
                return
            addresses = await self.__upstream.real_addresses(name)
            if not addresses:
                return
            site_reader, site_writer = await asyncio.wait_for(self.__connect(str(addresses[0]), HTTPS_PORT), CONNECT_TIMEOUT)
            site_writer.write(hello)
            await site_writer.drain()
            await asyncio.gather(_pipe(reader, site_writer), _pipe(site_reader, writer))
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, struct.error, IndexError):
            pass
        finally:
            _close(writer)
            _close(site_writer)

    async def serve(self) -> None:
        server = await asyncio.start_server(self.handle, str(self.__settings.bind), self.__settings.sni_port, limit=READ_LIMIT_BYTES)
        async with server:
            await server.serve_forever()
