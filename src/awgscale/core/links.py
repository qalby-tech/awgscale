"""How a link to an exit is named and numbered. A new exit gets one number N,
and everything of its link follows from it; older exits may differ (an exit
with index 3 on awgk2), so what exists is always asked, never recomputed."""
from __future__ import annotations

from dataclasses import dataclass

LINK_UDP_BASE = 47210
MAX_EXIT_INDEX = 50


@dataclass(frozen=True, slots=True)
class LinkPlan:
    """The link of exit number N, as this stack makes it."""

    index: int

    @property
    def iface(self) -> str:
        return f"awgk{self.index}"

    @property
    def net(self) -> str:
        return f"10.77.{self.index}"

    @property
    def entry_address(self) -> str:
        return f"{self.net}.2"

    @property
    def exit_address(self) -> str:
        return f"{self.net}.1"

    @property
    def udp_port(self) -> int:
        return LINK_UDP_BASE + self.index
