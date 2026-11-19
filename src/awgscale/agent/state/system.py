"""How the servers are doing: load, memory, disk, traffic — of the entry itself and of each exit."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from awgscale.core.ashell import AsyncRunner

from ..clients.tls import tls_context

PROC_DIR = Path("/proc")
NET_DIR = Path("/sys/class/net")
ROOT_DIR = Path("/")
KIBIBYTE = 1024
DEFAULT_WAN = "eth0"
WHOLE_PERCENT = 100
# The default route:   default via 203.0.113.1 dev ens3 proto static
ROUTE_DEVICE_RE = re.compile(
    r"""
    \bdev\ (?P<device>\S+)     # dev ens3
    """,
    re.VERBOSE,
)
# The tunnel's own address:   7: awgk2    inet 10.77.2.2/30 scope global awgk2
LINK_ADDRESS_RE = re.compile(
    r"""
    inet\ (?P<network>\d+\.\d+\.\d+)     # 10.77.2
    \.(?P<host>\d+)/                     # .2/
    """,
    re.VERBOSE,
)
EXIT_ASK_TIMEOUT = 2.0
EXIT_ANSWER_BYTES = 65536
ASK_AGAIN_SECONDS = 20          # an exit that answered is asked again this often
AFTER_SILENCE_SECONDS = 60      # one that did not: the state must not wait for it
FIRST_PEER_HOST = 2             # the link is a /30: the exit is the address before ours


def read_counter(path: Path) -> int:
    """A counter the kernel keeps in a file; 0 when it cannot be read."""
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


class EntryFacts(BaseModel):
    """The entry server, as its own kernel reports it."""

    model_config = ConfigDict(frozen=True)

    hostname: str = Field(description="The server's name")
    load1: float = Field(description="Load average of the last minute")
    cpus: int = Field(description="Processors")
    mem_total: int = Field(description="Memory, bytes")
    mem_used: int = Field(description="Memory not available to new programs, bytes")
    uptime: int = Field(description="Seconds since boot")
    disk_pct: int | None = Field(description="How full the root disk is, percent; None when it cannot be asked")
    wan_if: str = Field(description="The interface of the default route")
    wan_rx: int = Field(description="Bytes received on it since boot")
    wan_tx: int = Field(description="Bytes sent on it since boot")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class Entry:
    def __init__(self, run: AsyncRunner, proc_dir: Path = PROC_DIR, net_dir: Path = NET_DIR, root_dir: Path = ROOT_DIR) -> None:
        self.__run = run
        self.__proc_dir = proc_dir
        self.__net_dir = net_dir
        self.__root_dir = root_dir

    def __memory(self) -> dict[str, int]:
        """/proc/meminfo: {"MemTotal": bytes, …}"""
        memory: dict[str, int] = {}
        for line in (self.__proc_dir / "meminfo").read_text().splitlines():
            name, held = line.split(":", 1)
            memory[name] = int(held.split()[0]) * KIBIBYTE
        return memory

    def __disk_percent(self) -> int | None:
        try:
            disk = os.statvfs(self.__root_dir)
            return round(WHOLE_PERCENT * (1 - disk.f_bavail / disk.f_blocks))
        except OSError:
            return None

    def __read(self, wan: str) -> EntryFacts:
        memory = self.__memory()
        total = memory.get("MemTotal", 0)
        return EntryFacts(
            hostname=os.uname().nodename,
            load1=float((self.__proc_dir / "loadavg").read_text().split()[0]),
            cpus=os.cpu_count() or 1,
            mem_total=total,
            mem_used=total - memory.get("MemAvailable", 0),
            uptime=int(float((self.__proc_dir / "uptime").read_text().split()[0])),
            disk_pct=self.__disk_percent(),
            wan_if=wan,
            wan_rx=read_counter(self.__net_dir / wan / "statistics" / "rx_bytes"),
            wan_tx=read_counter(self.__net_dir / wan / "statistics" / "tx_bytes"),
        )

    async def facts(self) -> EntryFacts:
        route = await self.__run(["ip", "-4", "route", "show", "default"])
        found = ROUTE_DEVICE_RE.search(route.out)
        return await asyncio.to_thread(self.__read, found["device"] if found else DEFAULT_WAN)

    async def link_counters(self, iface: str) -> tuple[int, int]:
        """→ (bytes received, bytes sent) on an interface since boot."""
        received = await asyncio.to_thread(read_counter, self.__net_dir / iface / "statistics" / "rx_bytes")
        sent = await asyncio.to_thread(read_counter, self.__net_dir / iface / "statistics" / "tx_bytes")
        return received, sent

    async def link_there(self, iface: str) -> bool:
        return await asyncio.to_thread((self.__net_dir / iface).exists)


class ExitSystem(BaseModel):
    """How an exit server is doing, as the small service on it says. Only what it gave as a number is kept."""

    model_config = ConfigDict(frozen=True)

    load1: int | float | None = Field(default=None, description="Load average of the last minute")
    cpus: int | float | None = Field(default=None, description="Processors")
    mem_total: int | float | None = Field(default=None, description="Memory, bytes")
    mem_used: int | float | None = Field(default=None, description="Memory in use, bytes")
    uptime: int | float | None = Field(default=None, description="Seconds since boot")
    disk_pct: int | float | None = Field(default=None, description="How full the root disk is, percent")

    @classmethod
    def of(cls, answer: object) -> ExitSystem | None:
        """The service's answer; None for what is not one or says nothing."""
        if not isinstance(answer, dict):
            return None
        figures = {name: answer[name] for name in cls.model_fields if type(answer.get(name)) in (int, float)}
        return cls(**figures) if figures else None

    def wire(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


Asked = dict[str, tuple[float, ExitSystem | None]]      # iface → (asked at, by this process's clock; the answer or None)


class ExitSystems:
    """Asked from the small service on the exit, through the tunnel and from the
    tunnel's own address — the only one the exit lets in. An exit that does not
    answer is not asked again for a minute: the state must not wait for it."""

    def __init__(self, run: AsyncRunner, port: int, asked: Asked) -> None:
        self.__run = run
        self.__port = port
        self.__asked = asked

    async def __ask(self, iface: str) -> ExitSystem | None:
        shown = await self.__run(["ip", "-4", "-o", "addr", "show", iface])
        found = LINK_ADDRESS_RE.search(shown.out)
        if not found or int(found["host"]) < FIRST_PEER_HOST:
            return None
        local = f"{found['network']}.{found['host']}"
        peer = f"{found['network']}.{int(found['host']) - 1}"
        transport = httpx.AsyncHTTPTransport(local_address=local, verify=tls_context())
        async with httpx.AsyncClient(transport=transport, timeout=EXIT_ASK_TIMEOUT) as client:
            response = await client.get(f"http://{peer}:{self.__port}/v1/system")
        if response.status_code != HTTPStatus.OK:
            return None
        return ExitSystem.of(json.loads(response.content[:EXIT_ANSWER_BYTES].decode()))

    async def of(self, iface: str, now: float | None = None) -> ExitSystem | None:
        """→ the figures of the exit behind this tunnel, or None."""
        moment = time.time() if now is None else now
        asked_at, had = self.__asked.get(iface, (0.0, None))
        if abs(moment - asked_at) < (ASK_AGAIN_SECONDS if had else AFTER_SILENCE_SECONDS):
            return had
        try:
            got = await self.__ask(iface)
        except Exception:
            got = None
        self.__asked[iface] = (moment, got)
        return got
