"""Device spaces. The installer isolates VPN users from each other (awg0 → awg0 is
dropped). A space is a set of users who may reach each other; spaces.sets is compiled by
the agent: "sp_<id> <tunnel ip>,<public key> ...". One ipset per space and one rule:
source AND destination in the set → accept. An address is loaded only while the tunnel
still has that very key on it. Standard library only."""
from __future__ import annotations

import hashlib
import logging
import os
import re

from awgscale.core.net import HOST_SUFFIX

from .conf import CascadeConf
from .places import Places, flock, read_text, write_text
from .rules import PEER_CHAIN
from .tools import IP_RE, Tools

log = logging.getLogger("awgscale.watch")

# The set of a space:   sp_ab12
SPACE_NAME_RE = re.compile(
    r"""
    ^sp_[a-z0-9]{1,16}$
    """,
    re.VERBOSE,
)
SPACE_PREFIX = "sp_"
IPTABLES_WAIT = ["iptables", "-w", "5"]
MAX_JUMP_COPIES = 50
SIGNATURE_LENGTH = 16


def peers_signature(raw: str) -> str:
    return hashlib.sha1(raw.encode()).hexdigest()[:SIGNATURE_LENGTH]


class Spaces:
    def __init__(self, places: Places, tools: Tools) -> None:
        self.__places = places
        self.__tools = tools

    def ensure_jump(self, conf: CascadeConf) -> bool:
        """Our jump must stay above the installer's isolation rule."""
        tools = self.__tools
        if not tools.ok([*IPTABLES_WAIT, "-nL", PEER_CHAIN]):
            return True
        code, out = tools.sh([*IPTABLES_WAIT, "-S", "FORWARD"])
        if code != 0:
            return True
        rules = out.splitlines()
        between_clients = ["-i", conf.client_if, "-o", conf.client_if]
        jump_rule = f"-A FORWARD -i {conf.client_if} -o {conf.client_if} -j {PEER_CHAIN}"
        drop_rule = f"-A FORWARD -i {conf.client_if} -o {conf.client_if} -j DROP"
        jump = rules.index(jump_rule) if jump_rule in rules else None
        drop = rules.index(drop_rule) if drop_rule in rules else None
        if jump is not None and (drop is None or jump < drop):
            return True
        for _ in range(MAX_JUMP_COPIES):
            if not tools.ok([*IPTABLES_WAIT, "-D", "FORWARD", *between_clients, "-j", PEER_CHAIN]):
                break
        if not tools.ok([*IPTABLES_WAIT, "-I", "FORWARD", "1", *between_clients, "-j", PEER_CHAIN]):
            return False
        log.info("device spaces: rule placed above the client isolation rule")
        return True

    def count(self) -> int:
        return len([line for line in read_text(self.__places.spaces).splitlines() if line.startswith(SPACE_PREFIX)])

    def load(self, conf: CascadeConf) -> bool:
        """→ True when the spaces are loaded as compiled. Never raises."""
        try:
            return self.__load(conf)
        except Exception as e:
            log.info("WARN: device spaces: %s", e)
            return False

    def __load(self, conf: CascadeConf) -> bool:
        tools = self.__tools
        good = True
        temporary = f"sp_tmp_{os.getpid()}"
        peers, raw = tools.tunnel_peers(conf.client_if)
        live = {address[:-len(HOST_SUFFIX)]: key for key, allowed in peers for address in allowed if address.endswith(HOST_SUFFIX)}
        script = ""
        rules = ""
        kept: set[str] = set()
        for line in read_text(self.__places.spaces).splitlines():
            parts = line.split()
            if not parts or not SPACE_NAME_RE.match(parts[0]):
                continue
            name = parts[0]
            script += f"create {name} hash:ip -exist\ncreate {temporary} hash:ip -exist\nflush {temporary}\n"
            for pair in parts[1:]:
                ip, _, key = pair.partition(",")
                if not IP_RE.match(ip) or not key or live.get(ip) != key:     # that address now belongs to someone else, or to nobody
                    continue
                script += f"add {temporary} {ip} -exist\n"
            script += f"swap {temporary} {name}\n"
            rules += f"-A {PEER_CHAIN} -m set --match-set {name} src -m set --match-set {name} dst -j ACCEPT\n"
            kept.add(name)
        if script:
            if not tools.ok(["ipset", "restore"], script):
                good = False
            tools.sh(["ipset", "destroy", temporary])
        # one atomic replace of the chain; if a set is missing it fails as a whole and the previous rules stay
        if not tools.ok(["iptables-restore", "--noflush"], f"*filter\n:{PEER_CHAIN} - [0:0]\n-F {PEER_CHAIN}\n{rules}COMMIT\n"):
            good = False
        code, out = tools.sh(["ipset", "list", "-n"])       # sets of spaces that are gone — only now, when no rule refers to them
        for name in out.split() if code == 0 else []:
            if name.startswith(SPACE_PREFIX) and name not in kept:
                tools.sh(["ipset", "destroy", name])
        if not self.ensure_jump(conf):
            good = False
        if good:
            self.__places.run.mkdir(parents=True, exist_ok=True)
            write_text(self.__places.peers_signature, peers_signature(raw) + "\n")
        return good

    def follow_peers(self, conf: CascadeConf) -> None:
        """After a pass: users were added, removed or expired → check every member against the tunnel again. Never raises."""
        try:
            self.ensure_jump(conf)
            if not read_text(self.__places.spaces).strip():
                return
            _, raw = self.__tools.tunnel_peers(conf.client_if)
            if peers_signature(raw) == read_text(self.__places.peers_signature).strip():
                return
            with flock(self.__places.lock, 0) as got:
                if got:
                    self.load(conf)
        except Exception as e:
            log.info("WARN: device spaces: %s", e)
