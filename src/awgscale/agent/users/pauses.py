"""Pause: a user stays (key, groups, term) but nothing passes.

A paused user's address is in the set `cascade_susp`; one rule in the raw
table drops everything that enters the users' tunnel from it — before DNS,
forwarding and the spaces. Kept by name, so the address is looked up again
whenever the users change; `state` re-checks it, so a reboot, a firewall
reload or a user removed by a term cannot leave it wrong for long.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence

from awgscale.core.ashell import AsyncRunner

from ..files import read_text, write_file
from .models import KnownUser

SET_NAME = "cascade_susp"
# A member of the set of paused users:   10.9.9.2
ADDRESS_RE = re.compile(
    r"""
    ^\d{1,3}(?:\.\d{1,3}){3}\Z
    """,
    re.VERBOSE,
)
SAVED_ENTRY_PREFIX = "add "          # a line of `ipset list -o save` that names a member:  add cascade_susp 10.9.9.2


class PauseStore(Protocol):
    """Who is paused, and since when."""

    async def read(self) -> dict[str, datetime]: ...

    async def save(self, pauses: dict[str, datetime]) -> None: ...


class Pauses:
    def __init__(self, store: PauseStore, run: AsyncRunner, client_iface: str, addresses_file: Path) -> None:
        self.__store = store
        self.__run = run
        self.__rule = ["-i", client_iface, "-m", "set", "--match-set", SET_NAME, "src", "-j", "DROP"]
        self.__addresses_file = addresses_file

    async def __rule_present(self) -> bool:
        return (await self.__run(["iptables", "-w", "-t", "raw", "-C", "PREROUTING", *self.__rule])).ok

    async def __keep(self, addresses: set[str]) -> None:
        """The paused addresses, compiled next to the routing's other files: what the
        kernel is given back from when the store cannot be asked (see restore)."""
        text = "".join(address + "\n" for address in sorted(addresses))
        if await asyncio.to_thread(read_text, self.__addresses_file) != text:
            await asyncio.to_thread(write_file, self.__addresses_file, text)

    async def restore(self) -> None:
        """The store is down (a reboot the database did not come back from): the
        kernel has lost its list, the pauses must not lapse. Addresses from the
        compiled file are put back; nothing is ever removed here."""
        text = await asyncio.to_thread(read_text, self.__addresses_file)
        addresses = [line.strip() for line in (text or "").splitlines() if ADDRESS_RE.match(line.strip())]
        if not addresses:
            return
        await self.__run(["ipset", "create", SET_NAME, "hash:ip", "-exist"])
        for address in addresses:
            await self.__run(["ipset", "add", SET_NAME, address, "-exist"])
        if not await self.__rule_present():
            await self.__run(["iptables", "-w", "-t", "raw", "-I", "PREROUTING", "1", *self.__rule])

    async def sync(self, users: Sequence[KnownUser]) -> list[str]:
        """Make the kernel match the stored list. → the paused names that exist."""
        pauses = await self.__store.read()
        address_of = {user.name: user.ip for user in users}
        gone = [name for name in pauses if name not in address_of]
        if gone and address_of:                   # an empty list of users is a failed read, not "everyone left"
            for name in gone:
                pauses.pop(name)
            await self.__store.save(pauses)
        if pauses and not address_of:
            return sorted(pauses)                 # the kernel is left exactly as it is
        wanted = {address_of[name] for name in pauses if address_of.get(name)}
        await self.__keep(wanted)
        existing = [name for name in pauses if name in address_of]
        listed = await self.__run(["ipset", "list", SET_NAME, "-o", "save"])
        if not listed.ok and not wanted:
            return sorted(existing)               # never used: nothing to keep
        if not listed.ok:
            await self.__run(["ipset", "create", SET_NAME, "hash:ip", "-exist"])
        present = {line.split()[2] for line in listed.out.splitlines() if line.startswith(SAVED_ENTRY_PREFIX)}
        for address in wanted - present:
            await self.__run(["ipset", "add", SET_NAME, address, "-exist"])
        for address in present - wanted:
            await self.__run(["ipset", "del", SET_NAME, address, "-exist"])
        rule_present = await self.__rule_present()
        if wanted and not rule_present:
            await self.__run(["iptables", "-w", "-t", "raw", "-I", "PREROUTING", "1", *self.__rule])
        elif not wanted and rule_present:
            await self.__run(["iptables", "-w", "-t", "raw", "-D", "PREROUTING", *self.__rule])
        return sorted(existing)
