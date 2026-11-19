"""The kernel's tools as the engine asks them: ip, ipset, iptables, awg. Standard library only."""
from __future__ import annotations

import re
from typing import Sequence

from awgscale.core.shell import Runner, run
from awgscale.service.errors import Refused

# An address, the first word of a line of `ipset list`:   172.16.17.23
IP_RE = re.compile(
    r"""
    ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$
    """,
    re.VERBOSE,
)
# The size of a set, in the header of `ipset list <name> -t`:   Number of entries: 8664
ENTRIES_RE = re.compile(
    r"""
    ^Number\ of\ entries:\ (?P<count>\d+)     # Number of entries: 8664
    """,
    re.VERBOSE | re.MULTILINE,
)
DEFAULT_TIMEOUT = 60.0
SHOWN_ARGS = 6

Peers = list[tuple[str, list[str]]]


class Tools:
    def __init__(self, runner: Runner = run) -> None:
        self.__run = runner

    def sh(self, args: Sequence[str], stdin: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str]:
        """Run a system tool → (exit code, stdout). Never raises for a tool that failed."""
        result = self.__run(args, stdin, timeout)
        return result.code, result.out

    def ok(self, args: Sequence[str], stdin: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> bool:
        return self.sh(args, stdin, timeout)[0] == 0

    def must(self, args: Sequence[str], stdin: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
        code, out = self.sh(args, stdin, timeout)
        if code != 0:
            raise Refused(" ".join(args[:SHOWN_ARGS]) + f": код {code}")
        return out

    def link_exists(self, iface: str) -> bool:
        return self.ok(["ip", "link", "show", iface])

    def set_count(self, name: str) -> int:
        code, out = self.sh(["ipset", "list", name, "-t"])
        found = ENTRIES_RE.search(out if code == 0 else "")
        return int(found["count"]) if found else 0

    def set_members(self, name: str) -> list[str]:
        code, out = self.sh(["ipset", "list", name])
        return [line for line in out.splitlines() if IP_RE.match(line)] if code == 0 else []

    def tunnel_peers(self, client_if: str) -> tuple[Peers, str]:
        """→ [(public key, [allowed ips])] of the client tunnel, and the tool's answer as it came."""
        code, out = self.sh(["awg", "show", client_if, "allowed-ips"])
        raw = out if code == 0 else ""
        peers: Peers = []
        for line in raw.splitlines():
            key, _, rest = line.partition("\t")
            peers.append((key, rest.split()))
        return peers, raw
