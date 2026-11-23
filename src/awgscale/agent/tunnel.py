"""The users' tunnel as the kernel reports it: who is connected from where, when they last spoke, how much went through."""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from awgscale.core.ashell import AsyncRunner

# A peer's line of `awg show <iface> dump`, tab-separated:
#     <public key>  <preshared key>  203.0.113.7:51820  10.9.9.2/32  1763300000  1024  2048  off
# The preshared key is never taken out of the line.
ENDPOINT_AT = 2
ALLOWED_AT = 3
HANDSHAKE_AT = 4
RECEIVED_AT = 5
SENT_AT = 6
PEER_FIELDS = 7
NO_ENDPOINT = "(none)"
# Where a link points, as `awg` writes it:   198.51.100.38:51820   [2001:db8::1]:51820
ENDPOINT_RE = re.compile(
    r"""
    ^\[?(?P<host>[^\[\]]+?)\]?   # 198.51.100.38
    :(?P<port>\d+)\Z           # :51820
    """,
    re.VERBOSE,
)


def endpoint_host(endpoint: str | None) -> str | None:
    """198.51.100.38:51820 → 198.51.100.38; None for no endpoint or one that is not address:port."""
    found = ENDPOINT_RE.match(endpoint.strip()) if endpoint else None
    return found["host"] if found else None


class Peer(BaseModel):
    """A user's device as the tunnel sees it."""

    model_config = ConfigDict(frozen=True)

    address: str = Field(description="The user's address inside the tunnel")
    endpoint: str | None = Field(description="Where the device connects from, address:port; None while it never did")
    handshake: datetime | None = Field(description="When it last spoke; None when never")
    received: int = Field(description="Bytes received FROM the device (its upload)")
    sent: int = Field(description="Bytes sent TO the device (its download)")


def peers_of(dump: str) -> list[Peer]:
    """The peers of a dump, in its order; the first line is the interface's own."""
    peers: list[Peer] = []
    for line in dump.splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) < PEER_FIELDS:
            continue
        spoke = int(fields[HANDSHAKE_AT])
        peers.append(
            Peer(
                address=fields[ALLOWED_AT].split(",")[0].split("/")[0],
                endpoint=None if fields[ENDPOINT_AT] == NO_ENDPOINT else fields[ENDPOINT_AT],
                handshake=datetime.fromtimestamp(spoke, UTC) if spoke else None,
                received=int(fields[RECEIVED_AT]),
                sent=int(fields[SENT_AT]),
            )
        )
    return peers


class Tunnel:
    """The users' tunnel, asked through the `awg` tool."""

    def __init__(self, run: AsyncRunner, client_iface: str) -> None:
        self.__run = run
        self.__client_iface = client_iface

    async def peers(self) -> list[Peer]:
        shown = await self.__run(["awg", "show", self.__client_iface, "dump"])
        return await asyncio.to_thread(peers_of, shown.out)      # 100 000 lines are parsed beside the loop

    async def listen_port(self) -> int | None:
        """The port the tunnel listens on; None when the tool does not say."""
        port = (await self.__run(["awg", "show", self.__client_iface, "listen-port"])).out.strip()
        return int(port) if port.isdigit() else None

    async def endpoint_of(self, iface: str) -> str | None:
        """Where the link to an exit points: the last field of `awg show <iface> endpoints`."""
        shown = await self.__run(["awg", "show", iface, "endpoints"])
        return shown.out.split("\t")[-1].strip() or None
