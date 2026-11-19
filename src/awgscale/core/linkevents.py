"""The kernel's own word about its network links: `ip monitor link`, as events.

Nothing is polled: the kernel sends a notification when a link appears, changes
or goes, and `ip` prints one line for each.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import AsyncIterator

# A line of `ip -o monitor link`. A link that is there:
#     7: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280 qdisc noqueue state UNKNOWN …
# and one that has just been removed:
#     Deleted 7: awg0: <POINTOPOINT,NOARP> mtu 1280 qdisc noop state DOWN …
LINK_EVENT_RE = re.compile(
    r"""
    ^(?P<deleted>Deleted\s)?    # Deleted — only when the link is gone
    (?P<index>\d+):\s           # 7:
    (?P<name>[\w.-]+)           # awg0
    (?:@\S+)?:\s                # @eth0 after the name of a link stacked on another, then the colon
    <(?P<flags>[A-Z_,]*)>       # <POINTOPOINT,NOARP,UP,LOWER_UP>
    """,
    re.VERBOSE,
)
UP_FLAG = "UP"


class MonitorEnded(Exception):
    """The notifications cannot be listened to, or stopped coming."""


@dataclass(frozen=True, slots=True)
class LinkEvent:
    """What the kernel said about one link."""

    name: str
    flags: frozenset[str]
    deleted: bool

    @property
    def up(self) -> bool:
        """The link is there and switched on."""
        return not self.deleted and UP_FLAG in self.flags


def parse(line: str) -> LinkEvent | None:
    """A line of `ip -o monitor link` → the event; None for a line that is not about a link."""
    match = LINK_EVENT_RE.match(line)
    if match is None:
        return None
    flags = frozenset(flag for flag in match["flags"].split(",") if flag)
    return LinkEvent(name=match["name"], flags=flags, deleted=match["deleted"] is not None)


async def link_events(ip: str = "ip") -> AsyncIterator[LinkEvent]:
    """Every link event of the kernel, as it happens, for as long as the caller
    listens. Raises MonitorEnded when `ip monitor` cannot be started or ends."""
    try:
        monitor = await asyncio.create_subprocess_exec(
            ip, "-o", "monitor", "link",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as e:
        raise MonitorEnded(str(e)) from None
    assert monitor.stdout is not None
    try:
        async for line in monitor.stdout:
            event = parse(line.decode(errors="replace"))
            if event is not None:
                yield event
    finally:
        if monitor.returncode is None:
            monitor.kill()
        await monitor.wait()
    raise MonitorEnded("ip monitor ended")
