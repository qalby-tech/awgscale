"""The rules for some groups of people that go abroad or directly, in the kernel. The agent compiles:

  scoped.rules     one line per such rule, in the order of the list:   rs_ab12cd exit rg_g00001 rg_c0ffee
  scoped.members   one line per group, its people's tunnel addresses:  rg_g00001 172.16.17.9 172.16.17.12
  domains.ipset    the resolver's file of sets: a name in "rs_<rule>" is decided by that rule for its groups
  domains-scoped.txt  the names each rule decides, one per line:          rs_ab12cd m.youtube.com
                   (written before domains.ipset names their sets: the agent names them there only once the sets exist)

Every rule has a set of addresses the resolver fills ("rs_"), every group a set of its people ("rg_", only of those
the tunnel has now). One chain, CASCADE-SCOPED, asked for every new connection before the rules for everybody: per
rule and group, in order — the sender in the group's set and the address in the rule's set → abroad (the exit
the sender is pinned to, as for everybody) or directly (a mark of its own, kept with the connection, so it never
falls into the sets for everybody). The chain is loaded by itself, never with the rules for everybody: a broken
file leaves an empty chain and everybody on the rules for everybody. Standard library only."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Sequence

from awgscale.service.errors import Refused

from .conf import CascadeConf
from .places import Places, read_text, write_text
from .rules import CHAIN, DIRECT_CHAIN, SCOPED_CHAIN, ip_key
from .tools import IP_RE, Tools

log = logging.getLogger("awgscale.watch")

# The set of a rule for groups, or of a group's people:   rs_ab12cd   rg_g00001
SCOPED_SET_RE = re.compile(
    r"""
    ^r[sg]_[a-z0-9]{6}$
    """,
    re.VERBOSE,
)
# A line of scoped.rules:   rs_ab12cd exit rg_g00001 rg_c0ffee
RULES_LINE_RE = re.compile(
    r"""
    ^(?P<rule_set>rs_[a-z0-9]{6})                   # rs_ab12cd — the rule's own set
    \s+(?P<way>exit|direct)                         # exit — where it leads
    (?P<groups>(?:\s+rg_[a-z0-9]{6})+)              # rg_g00001 rg_c0ffee — the groups it is for
    \s*$
    """,
    re.VERBOSE,
)
# A line of scoped.members:   rg_g00001 172.16.17.9 172.16.17.12
MEMBERS_LINE_RE = re.compile(
    r"""
    ^(?P<group_set>rg_[a-z0-9]{6})                  # rg_g00001
    (?P<addresses>(?:\s+\d+\.\d+\.\d+\.\d+)*)       # 172.16.17.9 172.16.17.12 — none: nobody in it now
    \s*$
    """,
    re.VERBOSE,
)
# A line of domains.ipset — the sets after its last slash:   youtube.com,m.youtube.com/force_exit,rs_ab12cd
IPSET_LINE_RE = re.compile(
    r"""
    ^(?P<names>[^/\s]+)                             # youtube.com,m.youtube.com
    /(?P<sets>[a-z0-9_]+(?:,[a-z0-9_]+)*)           # force_exit,rs_ab12cd
    \s*$
    """,
    re.VERBOSE,
)
# A line of domains-scoped.txt:   rs_ab12cd m.youtube.com
SCOPED_NAME_LINE_RE = re.compile(
    r"""
    ^(?P<rule_set>rs_[a-z0-9]{6})                   # rs_ab12cd — the set of the rule that decides the name
    \s+\S+\s*$                                      # m.youtube.com
    """,
    re.VERBOSE,
)
# A line of `ipset save`:   add rs_ab12cd 142.250.74.110
SAVED_LINE_RE = re.compile(
    r"""
    ^add\ (?P<set_name>rs_[a-z0-9]{6})\ (?P<address>\d+\.\d+\.\d+\.\d+)\s*$     # add rs_ab12cd 142.250.74.110
    """,
    re.VERBOSE,
)
RULE_PREFIX = "rs_"
GROUP_PREFIX = "rg_"
SET_SEPARATOR = ","
IPTABLES_WAIT = ["iptables", "-w", "5"]


class ScopedWay(StrEnum):
    EXIT = "exit"
    DIRECT = "direct"


@dataclass(frozen=True, slots=True)
class ScopedRule:
    """One line of scoped.rules."""

    rule_set: str
    way: ScopedWay
    groups: tuple[str, ...]


def parse_rules(text: str) -> list[ScopedRule]:
    """The rules for groups, in order; a line that does not fit is left out."""
    rules: list[ScopedRule] = []
    for line in text.splitlines():
        found = RULES_LINE_RE.match(line)
        if found:
            rules.append(ScopedRule(rule_set=found["rule_set"], way=ScopedWay(found["way"]), groups=tuple(dict.fromkeys(found["groups"].split()))))
    return rules


def parse_members(text: str) -> dict[str, list[str]]:
    """{group set: its people's addresses}; a line that does not fit is left out."""
    members: dict[str, list[str]] = {}
    for line in text.splitlines():
        found = MEMBERS_LINE_RE.match(line)
        if found:
            members[found["group_set"]] = [address for address in found["addresses"].split() if IP_RE.match(address)]
    return members


def ipset_sets(text: str) -> set[str]:
    """The sets of the rules for groups that the resolver's file names (it refuses to start when one is not there)."""
    named: set[str] = set()
    for line in text.splitlines():
        found = IPSET_LINE_RE.match(line)
        if found:
            named.update(name for name in found["sets"].split(SET_SEPARATOR) if name.startswith(RULE_PREFIX) and SCOPED_SET_RE.match(name))
    return named


def scoped_name_sets(text: str) -> set[str]:
    """The sets of the rules for groups that domains-scoped.txt names: the resolver's file names them once they exist."""
    return {found["rule_set"] for found in (SCOPED_NAME_LINE_RE.match(line) for line in text.splitlines()) if found}


def scoped_payload(rules: Sequence[ScopedRule]) -> str:
    """The chain as one iptables-restore payload: per rule and group, in order."""
    lines = ["*mangle", f":{SCOPED_CHAIN} - [0:0]", f"-F {SCOPED_CHAIN}"]
    for rule in rules:
        target = f"{CHAIN}-MARK" if rule.way is ScopedWay.EXIT else DIRECT_CHAIN
        for group in rule.groups:
            lines.append(f"-A {SCOPED_CHAIN} -m set --match-set {group} src -m set --match-set {rule.rule_set} dst -g {target}")
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


def named_by(rules: Sequence[ScopedRule]) -> set[str]:
    return {name for rule in rules for name in (rule.rule_set, *rule.groups)}


class Scoped:
    def __init__(self, places: Places, tools: Tools) -> None:
        self.__places = places
        self.__tools = tools

    def __rules(self) -> list[ScopedRule]:
        return parse_rules(read_text(self.__places.scoped_rules))

    def __resolver_sets(self) -> set[str]:
        """The sets of the rules for groups the resolver's file names, or is about to (domains-scoped.txt)."""
        return ipset_sets(read_text(self.__places.domains_ipset)) | scoped_name_sets(read_text(self.__places.domains_scoped))

    def ensure(self, chain: bool = True) -> None:
        """The sets the resolver's file and the rules name, and the chain the rules for everybody jump to. Raises Refused
        when the chain cannot be made (the rules for everybody name it); a set that cannot be made is said and left."""
        tools = self.__tools
        wanted = self.__resolver_sets()
        if chain:
            wanted |= named_by(self.__rules())
        for name in sorted(wanted):
            if not tools.ok(["ipset", "create", name, "hash:ip", "-exist"]):
                log.info("WARN: set %s not made", name)
        listed = [*IPTABLES_WAIT, "-t", "mangle", "-nL", SCOPED_CHAIN]
        if chain and not tools.ok(listed) and not tools.ok([*IPTABLES_WAIT, "-t", "mangle", "-N", SCOPED_CHAIN]) and not tools.ok(listed):
            # asked again: a look that failed while another writer held the lock is not "the chain is not there"
            raise Refused(f"{SCOPED_CHAIN}: цепочка не создана")

    def load(self, conf: CascadeConf, live: Iterable[str]) -> bool:
        """→ True when the chain is loaded as compiled. Never raises."""
        try:
            return self.__load(conf, set(live))
        except Exception as e:
            log.info("WARN: rules for groups: %s", e)
            return False

    def __load(self, conf: CascadeConf, live: set[str]) -> bool:
        tools = self.__tools
        rules = self.__rules()
        members = parse_members(read_text(self.__places.scoped_members))
        self.ensure()
        good = True
        temporary = f"rg_tmp_{os.getpid()}"
        script = ""
        for group in sorted({group for rule in rules for group in rule.groups}):
            script += f"create {group} hash:ip -exist\ncreate {temporary} hash:ip -exist\nflush {temporary}\n"
            script += "".join(f"add {temporary} {address} -exist\n" for address in members.get(group, []) if address in live)
            script += f"swap {temporary} {group}\n"
        if script:
            if not tools.ok(["ipset", "restore"], script):
                good = False
            tools.sh(["ipset", "destroy", temporary])
        # one atomic replace of the chain; if a set is missing it fails as a whole and the previous chain stays
        if not tools.ok(["iptables-restore", "--noflush"], scoped_payload(rules)):
            return False
        kept = named_by(rules) | self.__resolver_sets()
        code, out = tools.sh(["ipset", "list", "-n"])       # sets of rules or groups that are gone — only now, when no rule refers to them
        for name in out.split() if code == 0 else []:
            if SCOPED_SET_RE.match(name) and name not in kept:
                tools.sh(["ipset", "destroy", name])
        log.info("rules for groups loaded: %s rule(s)", len(rules))
        return good

    def follow_members(self, conf: CascadeConf, live: Iterable[str]) -> None:
        """After a pass: who is in each group's set follows scoped.members and the tunnel, one address at a time. Never
        raises; a group that fails is said and the others are followed all the same."""
        try:
            members = parse_members(read_text(self.__places.scoped_members))
            groups = sorted({group for rule in self.__rules() for group in rule.groups})
        except Exception as e:
            log.info("WARN: rules for groups: %s", e)
            return
        alive = set(live)
        for group in groups:
            try:
                self.__follow_group(group, set(members.get(group, [])) & alive)
            except Exception as e:
                log.info("WARN: rules for groups: %s: %s", group, e)

    def __follow_group(self, group: str, wanted: set[str]) -> None:
        tools = self.__tools
        tools.must(["ipset", "create", group, "hash:ip", "-exist"])
        present = set(tools.set_members(group))
        for address in sorted(present - wanted, key=ip_key):
            tools.sh(["ipset", "del", group, address])
            log.info("client %s left %s (new connections only)", address, group)
        for address in sorted(wanted - present, key=ip_key):
            if tools.ok(["ipset", "add", group, address, "-exist"]):
                log.info("client %s joined %s (new connections only)", address, group)
            else:
                log.info("WARN: client %s not added to %s", address, group)

    def reset_names(self) -> None:
        """While the resolver is stopped: the sets its file names, or is about to (domains-scoped.txt), exist and are
        empty, the learned addresses forgotten."""
        tools = self.__tools
        for name in sorted(self.__resolver_sets()):
            tools.must(["ipset", "create", name, "hash:ip", "-exist"])
            tools.must(["ipset", "flush", name])
        self.__places.learned_scoped.unlink(missing_ok=True)

    def save_learned(self) -> None:
        """The addresses learned for the rules' names, kept across a reboot."""
        lines: list[str] = []
        for name in sorted({rule.rule_set for rule in self.__rules()}):
            code, out = self.__tools.sh(["ipset", "save", name])
            lines += [line for line in out.splitlines() if SAVED_LINE_RE.match(line)] if code == 0 else []
        if lines:
            write_text(self.__places.learned_scoped, "\n".join(lines) + "\n")

    def restore_learned(self) -> None:
        """Only into sets that exist: a rule gone since is not made again by its addresses."""
        saved = read_text(self.__places.learned_scoped)
        if not saved:
            return
        code, out = self.__tools.sh(["ipset", "list", "-n"])
        present = set(out.split()) if code == 0 else set()
        lines: list[str] = []
        for line in saved.splitlines():
            found = SAVED_LINE_RE.match(line)
            if found and found["set_name"] in present:
                lines.append(line)
        if lines:
            self.__tools.sh(["ipset", "restore", "-exist"], "\n".join(lines) + "\n")

    def counts(self) -> tuple[int, int]:
        """→ (rules for groups, addresses learned for their names)."""
        rules = self.__rules()
        return len(rules), sum(self.__tools.set_count(name) for name in sorted({rule.rule_set for rule in rules}))
