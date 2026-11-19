"""What the proxy reads of a connection before passing it on: who it comes from, and where it wants to go."""
from __future__ import annotations

import asyncio
import socket
import struct

HANDSHAKE_RECORD = 0x16
CLIENT_HELLO = 0x01
RECORD_HEAD_BYTES = 5                    # type, version, length
LONGEST_RECORD_BYTES = 16640
SERVER_NAME_EXTENSION = 0
# record header, handshake header, version, random: where the session id of a ClientHello begins
SESSION_ID_OFFSET = 5 + 4 + 2 + 32

PROXY_V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"
PROXY_V1_PREFIX = b"PROXY "
PROXY_COMMAND = 1                        # v2: a proxied connection (0 is the balancer's own check)
PROXY_TCP_OVER_IPV4 = 0x11               # v2: address family and protocol
PROXY_V1_FIELDS = 6                      # PROXY TCP4 <source> <destination> <source port> <destination port>


def record_length(head: bytes) -> int | None:
    """The length of the handshake record that begins with these five bytes; None when it is not one."""
    length = struct.unpack("!H", head[3:5])[0]
    if head[0] != HANDSHAKE_RECORD or not 0 < length <= LONGEST_RECORD_BYTES:
        return None
    return length


def parse_sni(hello: bytes) -> str | None:
    """The server name from a TLS ClientHello record, or None."""
    try:
        if hello[0] != HANDSHAKE_RECORD or hello[5] != CLIENT_HELLO:
            return None
        position = SESSION_ID_OFFSET
        position += 1 + hello[position]                                            # session id
        position += 2 + struct.unpack("!H", hello[position:position + 2])[0]       # cipher suites
        position += 1 + hello[position]                                            # compression methods
        end = position + 2 + struct.unpack("!H", hello[position:position + 2])[0]
        position += 2
        while position + 4 <= end:
            extension, length = struct.unpack("!HH", hello[position:position + 4])
            position += 4
            if extension == SERVER_NAME_EXTENSION:
                name_length = struct.unpack("!H", hello[position + 3:position + 5])[0]
                return hello[position + 5:position + 5 + name_length].decode("ascii").lower().rstrip(".")
            position += length
    except (IndexError, struct.error, UnicodeDecodeError):
        pass
    return None


async def read_proxy_header(reader: asyncio.StreamReader) -> str | None:
    """The client's address from the PROXY protocol header Traefik prepends (v1 or v2); None when there is none to trust."""
    head = await reader.readexactly(len(PROXY_V2_SIGNATURE))
    if head == PROXY_V2_SIGNATURE:
        version_and_command, family, length = struct.unpack("!BBH", await reader.readexactly(4))
        body = await reader.readexactly(length)
        if family == PROXY_TCP_OVER_IPV4 and version_and_command & 0x0F == PROXY_COMMAND:
            return socket.inet_ntoa(body[:4])
        return None
    if head.startswith(PROXY_V1_PREFIX):
        line = head + await reader.readuntil(b"\r\n")
        fields = line.decode("ascii", "replace").split()
        if len(fields) >= PROXY_V1_FIELDS and fields[1] == "TCP4":
            return fields[2]
        return None
    return None
