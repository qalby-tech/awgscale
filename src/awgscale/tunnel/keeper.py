"""The service itself: one look at an interface, making or mending it, and what sets a look off."""
from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, ConfigDict, Field

from awgscale.core.ashell import AsyncRunner, run as system_run
from awgscale.core.linkevents import MonitorEnded, link_events
from awgscale.service.logs import SaidOnce

from .conf import InterfaceFile, Refused, read_interface_file
from .firewall import Firewall, Rule, rules_for
from .link import Links, MakeFailed
from .settings import Role, TunnelSettings

# The name of an interface this service keeps: a letter, then letters and digits, 15 at most (awg0, awgk12)
IFACE_RE = re.compile(
    r"""
    [a-z][a-z0-9]{0,14}       # awg0
    """,
    re.VERBOSE,
)
CONF_SUFFIX = ".conf"
WHO = "tunnel"

tunnel_log = logging.getLogger("awgscale.tunnel")


class Status(BaseModel):
    """What one look found. Also what `check` prints."""

    iface: str = Field(description="The interface looked at")
    exists: bool = Field(default=False, description="It is in the kernel")
    matches: bool | None = Field(default=None, description="It is what its file says; None when that could not be compared")
    differs: list[str] = Field(default_factory=list, description="Where it differs from its file, in words")
    gate: bool | None = Field(default=None, description="The cascade's rules are in the kernel; asked only when the interface is not there")
    rules_missing: int = Field(default=0, description="How many of its firewall rules are not in the kernel")
    error: str | None = Field(default=None, description="Why the file cannot be brought up")

    @property
    def fine(self) -> bool:
        return bool(self.exists and self.matches and not self.rules_missing and not self.error)


class Look(BaseModel):
    """One look at an interface: what was found, and what the file asks for."""

    model_config = ConfigDict(frozen=True)

    status: Status = Field(description="What the look found")
    file: InterfaceFile | None = Field(default=None, description="The interface's file, when it could be read")
    rules: tuple[Rule, ...] = Field(default=(), description="The firewall rules the file stands for")


class Keeper:
    def __init__(self, settings: TunnelSettings, run: AsyncRunner = system_run, log: SaidOnce | None = None) -> None:
        self.__settings = settings
        self.__log = log or SaidOnce(tunnel_log, WHO)
        self.__links = Links(run, settings.awg, settings.link_type)
        self.__firewall = Firewall(run)
        self.__one_at_a_time = asyncio.Lock()      # an interface is never looked at while it is being made
        self.__waiting: set[str] = set()           # interfaces a look is already on its way to

    @property
    def settings(self) -> TunnelSettings:
        return self.__settings

    # ── which interfaces ─────────────────────────────────────────────────────

    def interfaces(self) -> list[str]:
        """The one, or every file matching the pattern."""
        settings = self.__settings
        if settings.role is Role.CLIENTS:
            return [settings.iface]
        names = sorted(path.name[:-len(CONF_SUFFIX)] for path in settings.dir.glob(f"{settings.glob}{CONF_SUFFIX}"))
        return [name for name in names if IFACE_RE.fullmatch(name)]

    async def __gate_open(self, iface: str) -> bool:
        """A link to an exit needs no gate: nothing goes into it until it is routed into."""
        if self.__settings.role is Role.EXIT_LINKS:
            return True
        return await self.__firewall.gate_open(iface, self.__settings.gate_chain)

    def read(self, iface: str) -> InterfaceFile:
        return read_interface_file(self.__settings.conf_of(iface), self.__settings.role)

    # ── one look ─────────────────────────────────────────────────────────────

    async def look(self, iface: str) -> Look:
        status = Status(iface=iface)
        try:
            file = self.read(iface)
            rules = rules_for(iface, file)
        except (OSError, Refused) as e:
            status.error = str(e)
            status.exists = await self.__links.state(iface) is not None
            return Look(status=status)
        have = await self.__links.state(iface)
        status.exists = have is not None
        if have is not None:
            status.differs = [difference.value for difference in await self.__links.differences(iface, file, have)]
            status.matches = not status.differs
            status.rules_missing = await self.__firewall.missing(rules)
        else:
            status.gate = await self.__gate_open(iface)
        return Look(status=status, file=file, rules=rules)

    # ── one interface: look, then make or mend ───────────────────────────────

    async def keep(self, iface: str) -> None:
        """Looks at the interface and makes or mends it. Looks that pile up for the
        same interface (a burst of the kernel's events) become one."""
        if iface in self.__waiting:
            return
        self.__waiting.add(iface)
        async with self.__one_at_a_time:
            self.__waiting.discard(iface)
            await self.__keep(iface)

    async def __keep(self, iface: str) -> None:
        look = await self.look(iface)
        status = look.status
        state_key = (iface, "state")
        say = self.__log.say
        if status.error:
            say(f"ERROR: {status.error} — {iface} is {'left as it is' if status.exists else 'not made'}", state_key)
        elif not status.exists:
            if not status.gate:
                say(f"{iface} is not there and the cascade's rules are not in the kernel — waiting for them (no rules, no tunnel)", state_key)
            else:
                await self.__make(iface, look)
        elif status.differs:
            say(f"WARN: {iface} is there but is NOT what the file says ({', '.join(status.differs)}) — nothing is changed", state_key)
        else:
            added = await self.__ensure_rules(look.rules) if status.rules_missing else 0
            say(f"{iface} is there and is what the file says — taken over as it is" + (f"; firewall rules put back: {added}" if added else ""), state_key)

    async def __make(self, iface: str, look: Look) -> None:
        assert look.file is not None
        try:
            await self.__links.make(iface, look.file)
        except MakeFailed as e:
            if e.module:
                self.__log.say(f"cannot make {iface} ({e}) — is the kernel module loaded? waiting", (iface, "create"))
            else:
                self.__log.say(f"ERROR: {e} — {iface} is removed again")
            return
        self.__log.forget((iface, "create"))
        added = await self.__ensure_rules(look.rules)
        if self.__settings.role is Role.EXIT_LINKS:
            tail = ", link to an exit"
        else:
            tail = f", port {look.file.port}, firewall rules put in: {added}"
        self.__log.say(f"{iface} made: {look.file.address}{tail}")
        self.__log.forget((iface, "state"))

    async def __ensure_rules(self, rules: tuple[Rule, ...]) -> int:
        put = await self.__firewall.ensure(rules)
        for error in put.errors:
            self.__log.say(f"ERROR: {error}")
        return put.added

    # ── what sets a look off ─────────────────────────────────────────────────

    async def keep_all(self) -> None:
        """A look at every interface there is a file for. Run at the start, and
        now and then for what the kernel does not announce: a firewall rule
        gone, the cascade's rules arriving, a new file."""
        settings = self.__settings
        todo = self.interfaces()
        if not todo:
            self.__log.say(f"no file {settings.dir}/{settings.glob}.conf — nothing to keep", "none")
        for iface in todo:
            await self.keep(iface)

    async def watch_links(self) -> None:
        """Looks at an interface the moment the kernel says something about it:
        it is gone, it was switched off, it has appeared. Nothing is polled
        here. When the kernel's events cannot be listened to this is said once,
        and listening is tried again; the looks on a clock go on meanwhile. It never dies."""
        while True:
            try:
                async for event in link_events(self.__settings.ip):
                    if event.name in self.interfaces():
                        await self.keep(event.name)
            except MonitorEnded as e:
                self.__log.say(f"WARN: the kernel's link events cannot be listened to ({e}) — the interfaces are looked at every {self.__settings.pass_seconds:g} s only", "events")
            except Exception as e:
                self.__log.say(f"ERROR: watching the kernel's link events failed: {repr(e)[:200]}", "events")
            await asyncio.sleep(self.__settings.retry_seconds)

    def say_start(self) -> None:
        settings = self.__settings
        if settings.role is Role.CLIENTS:
            what = f"{settings.iface} from {settings.conf_of(settings.iface)}"
        else:
            what = f"the exit links {settings.dir}/{settings.glob}.conf"
        self.__log.say(f"keeping {what}")

    def say_stop(self) -> None:
        settings = self.__settings
        left = f"{settings.iface} and its rules are" if settings.role is Role.CLIENTS else "the links are"
        self.__log.say(f"stopping; {left} left as they are")
