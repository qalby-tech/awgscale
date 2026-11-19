"""The firewall rules of the users' tunnel: what the file's PostUp stands for."""
from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from awgscale.core.ashell import AsyncRunner

from .conf import InterfaceFile, Refused

# The PostUp of the file, as the installer wrote it: these commands, joined by "; ".
# The outer interface (wan) and the packet size (mss) are read from it.
POSTUP_COMMANDS = (
    r"iptables -I FORWARD -i %i -j ACCEPT",
    r"iptables -t nat -A POSTROUTING -o (?P<wan>[a-z0-9]+) -j MASQUERADE",
    r"iptables -t mangle -A FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss (?P<mss>\d+)",
    r"iptables -t mangle -A FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss (?P=mss)",
    r"while iptables -D FORWARD -i %i -o %i -j DROP 2>/dev/null; do :; done",
    r"iptables -I FORWARD -i %i -o %i -j DROP",
)
POSTUP_RE = re.compile("; ".join(POSTUP_COMMANDS))
# A rule of FORWARD that is one of the jumps opening it — the cascade's own (the spaces) or Docker's:
#     -A FORWARD -i awg0 -o awg0 -j CASCADE-PEERS
#     -A FORWARD -j DOCKER-USER
TOP_JUMP_RE = re.compile(
    r"""
    -j\ (?P<chain>CASCADE-PEERS|DOCKER[A-Z-]*)$      # -j DOCKER-USER
    """,
    re.VERBOSE,
)
IPTABLES = ("iptables", "-w", "5")
ERROR_LENGTH = 160


class Placement(StrEnum):
    AFTER_JUMPS = "after-jumps"      # right below the jumps at the top of FORWARD — where the rules stand on the server
    APPEND = "append"


class Rule(BaseModel):
    """One iptables rule this service keeps."""

    model_config = ConfigDict(frozen=True)

    table: str = Field(description="The table: filter, nat or mangle")
    chain: str = Field(description="The chain the rule stands in")
    spec: tuple[str, ...] = Field(description="The rule itself, as iptables arguments")
    placement: Placement = Field(description="Where in the chain a missing rule is put")


class RulesPut(BaseModel):
    """What putting the missing rules in came to."""

    model_config = ConfigDict(frozen=True)

    added: int = Field(description="How many rules were put in")
    errors: tuple[str, ...] = Field(description="What went wrong, one line per rule that was not put in")


def rules_for(iface: str, file: InterfaceFile) -> tuple[Rule, ...]:
    """The rules the file's PostUp stands for. Raises Refused for a PostUp that is not the known line."""
    if not file.postup:
        return ()
    match = POSTUP_RE.fullmatch(file.postup[0]) if len(file.postup) == 1 else None
    if match is None:
        raise Refused("PostUp в файле не тот, что ставил установщик — правила из него этим сервисом не ставятся")
    clamp = ("-p", "tcp", "--tcp-flags", "SYN,RST", "SYN", "-j", "TCPMSS", "--set-mss", match["mss"])
    return (
        Rule(table="filter", chain="FORWARD", spec=("-i", iface, "-j", "ACCEPT"), placement=Placement.AFTER_JUMPS),
        # put in second: it ends up above the ACCEPT
        Rule(table="filter", chain="FORWARD", spec=("-i", iface, "-o", iface, "-j", "DROP"), placement=Placement.AFTER_JUMPS),
        Rule(table="nat", chain="POSTROUTING", spec=("-o", match["wan"], "-j", "MASQUERADE"), placement=Placement.APPEND),
        Rule(table="mangle", chain="FORWARD", spec=("-o", iface, *clamp), placement=Placement.APPEND),
        Rule(table="mangle", chain="FORWARD", spec=("-i", iface, *clamp), placement=Placement.APPEND),
    )


class Firewall:
    def __init__(self, run: AsyncRunner) -> None:
        self.__run = run

    async def gate_open(self, iface: str, chain: str) -> bool:
        """The cascade marks this interface's traffic: its rule is in the kernel."""
        result = await self.__run([*IPTABLES, "-t", "mangle", "-S", "PREROUTING"])
        if not result.ok:
            return False
        return any(f"-i {iface} " in line + " " and line.rstrip().endswith(f"-j {chain}") for line in result.out.splitlines())

    async def has(self, rule: Rule) -> bool:
        result = await self.__run([*IPTABLES, "-t", rule.table, "-C", rule.chain, *rule.spec])
        return result.ok

    async def missing(self, rules: tuple[Rule, ...]) -> int:
        count = 0
        for rule in rules:
            if not await self.has(rule):
                count += 1
        return count

    async def __after_jumps(self) -> int:
        """The position (1-based) right below the jumps that open FORWARD. The isolation rule must
        not land above the spaces' jump, and the accept must stay below Docker's own rules."""
        result = await self.__run([*IPTABLES, "-S", "FORWARD"])
        position = 0
        rules = [line for line in result.out.splitlines() if line.startswith("-A FORWARD ")]
        for number, line in enumerate(rules, 1):
            if TOP_JUMP_RE.search(line.strip()):
                position = number
        return position + 1

    async def ensure(self, rules: tuple[Rule, ...]) -> RulesPut:
        """Each rule only if it is not there."""
        added = 0
        errors: list[str] = []
        for rule in rules:
            if await self.has(rule):
                continue
            if rule.placement is Placement.AFTER_JUMPS:
                where = ["-I", rule.chain, str(await self.__after_jumps())]
            else:
                where = ["-A", rule.chain]
            result = await self.__run([*IPTABLES, "-t", rule.table, *where, *rule.spec])
            if result.ok:
                added += 1
            else:
                errors.append(f"rule not installed ({rule.table} {rule.chain}): {result.err[:ERROR_LENGTH]}")
        return RulesPut(added=added, errors=tuple(errors))
