"""Writing: one entry to journald over its native socket."""
from __future__ import annotations

import asyncio
import socket
import struct
from pathlib import Path

from .models import Entry

PRIORITY_INFO = "6"


def _field(key: str, value: str) -> bytes:
    """One field of a journald datagram. A value with a line break in it takes the length-prefixed form."""
    data = value.encode()
    if b"\n" in data:
        return key.encode() + b"\n" + struct.pack("<Q", len(data)) + data + b"\n"
    return key.encode() + b"=" + data + b"\n"


def datagram(line: str, identifier: str) -> bytes:
    """A line as journald takes it: MESSAGE, PRIORITY, SYSLOG_IDENTIFIER."""
    return _field("MESSAGE", line) + _field("PRIORITY", PRIORITY_INFO) + _field("SYSLOG_IDENTIFIER", identifier)


class JournalWriter:
    def __init__(self, socket_path: Path, identifier: str) -> None:
        self.__socket_path = socket_path
        self.__identifier = identifier

    def send(self, line: str) -> None:
        """One line, as one datagram. Raises OSError when journald cannot be reached."""
        sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sender.sendto(datagram(line, self.__identifier), str(self.__socket_path))
        finally:
            sender.close()

    async def write(self, entry: Entry) -> None:
        await asyncio.to_thread(self.send, entry.line())
