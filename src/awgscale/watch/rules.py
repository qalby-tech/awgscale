"""The rules themselves, as text and as numbers: what is marked, which table it leaves through,
who is pinned where. Pure — nothing here touches the kernel. Standard library only."""
from __future__ import annotations

from enum import IntEnum
from typing import Collection, Iterable

from .conf import CascadeConf, RouteMode
from .tools import IP_RE

CHAIN = "CASCADE"
NAT_CHAIN = "CASCADE-NAT"
PEER_CHAIN = "CASCADE-PEERS"
TUNNEL_MSS = 1240            # exit tunnels have MTU 1280
BLACKHOLE_METRIC = "1000"
EXIT_METRIC = "10"
PRIVATE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16")


class Mark(IntEnum):
    EXIT_BASE = 0x100        # exit N → fwmark 0x100+N
    FALLBACK = 0x1FF         # clients not pinned yet + local DNS


class Table(IntEnum):
    EXIT_BASE = 200          # exit N → table 200+N
    SERVICE = 298            # local service subnets (VIA_EXIT_SUBNETS) → a healthy exit
    FALLBACK = 299


class Priority(IntEnum):
    FROM_EXIT = 9000         # from <exit tunnel ip>  (health probes)
    DNS = 9500               # local DNS upstreams
    SERVICE_INSIDE = 9590    # traffic inside a service subnet stays
    SERVICE = 9600
    MARK = 10000
    FALLBACK = 10099


def mark_of(index: int) -> str:
    return "0x%x" % (Mark.EXIT_BASE + index)


def table_of(index: int) -> str:
    return str(Table.EXIT_BASE + index)


FALLBACK_MARK = "0x%x" % Mark.FALLBACK
FALLBACK_TABLE = str(int(Table.FALLBACK))
SERVICE_TABLE = str(int(Table.SERVICE))


def exit_set(index: int) -> str:
    """The ipset of the clients pinned to that exit."""
    return f"cx_{index}"


def mangle_rules(conf: CascadeConf) -> str:
    """The marking, as one iptables-restore payload (clients are live: a
    flush-then-append would let packets through unmarked for a moment).
      tunnel subnet            → untouched
      private / CGNAT dst      → dropped (never forwarded into an exit)
      domain forced direct     → unmarked (leaves from this server)
      listed domain / network  → marked (sent through an exit)
      list mode                → nothing else is marked (but UDP for calls, if switched on)
      everything foreign       → RU list unmarked, everything else marked
    "Marked" = fallback mark, then the mark of the exit the client is pinned to.
    A single server (no exits): nothing is marked, everything leaves from this server."""
    lines = ["*mangle", f":{CHAIN} - [0:0]", f":{CHAIN}-MARK - [0:0]", f":{CHAIN}-MSS - [0:0]", f"-F {CHAIN}", f"-F {CHAIN}-MARK", f"-F {CHAIN}-MSS"]
    if conf.single_server:
        lines.append(f"-A {CHAIN} -d {conf.client_subnet} -j RETURN")
        lines += [f"-A {CHAIN} -d {net} -j DROP" for net in PRIVATE_NETS]
        lines.append("COMMIT")
        return "\n".join(lines) + "\n"
    for e in conf.exits:
        lines.append(f"-A {CHAIN}-MSS -o {e.iface} -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss {TUNNEL_MSS}")
        lines.append(f"-A {CHAIN}-MSS -i {e.iface} -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss {TUNNEL_MSS}")
    lines.append(f"-A {CHAIN} -d {conf.client_subnet} -j RETURN")
    lines += [f"-A {CHAIN} -d {net} -j DROP" for net in PRIVATE_NETS]
    # a connection keeps the exit it started on: the mark is stored with it and restored here
    lines.append(f"-A {CHAIN} -m connmark ! --mark 0 -j CONNMARK --restore-mark")
    lines.append(f"-A {CHAIN} -m mark ! --mark 0 -j RETURN")
    lines.append(f"-A {CHAIN} -m set --match-set force_direct dst -j RETURN")
    lines.append(f"-A {CHAIN} -m set --match-set force_exit dst -g {CHAIN}-MARK")
    lines.append(f"-A {CHAIN} -m set --match-set force_exit_net dst -g {CHAIN}-MARK")
    if conf.mode is RouteMode.LIST:
        if conf.udp_via_exit:     # calls: UDP to non-Russian addresses, except QUIC
            lines.append(f"-A {CHAIN} -p udp -m udp ! --dport 443 -m set ! --match-set ru dst -g {CHAIN}-MARK")
    else:
        lines.append(f"-A {CHAIN} -m set --match-set ru dst -j RETURN")
        lines.append(f"-A {CHAIN} -g {CHAIN}-MARK")
    lines.append(f"-A {CHAIN}-MARK -j MARK --set-mark {FALLBACK_MARK}")
    for e in conf.exits:
        lines.append(f"-A {CHAIN}-MARK -m set --match-set {exit_set(e.index)} src -j MARK --set-mark {mark_of(e.index)}")
    lines.append(f"-A {CHAIN}-MARK -j CONNMARK --save-mark")
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


def nat_rules(conf: CascadeConf) -> str:
    """NAT towards each exit (the exit only accepts our single tunnel address);
    one restore, so there is no moment without it."""
    lines = ["*nat", f":{NAT_CHAIN} - [0:0]", f"-F {NAT_CHAIN}"]
    lines += [f"-A {NAT_CHAIN} -o {e.iface} -j MASQUERADE" for e in conf.exits]
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


def ip_key(ip: str) -> tuple[int, ...]:
    return tuple(int(part) for part in ip.split("."))


def pins_wanted(conf: CascadeConf, ips: Iterable[str], up: Collection[int], pins_text: str) -> dict[str, int]:
    """→ {client address: exit index}. Pure: the whole pinning rule is here.
    Every client has a preferred exit (stable: its address position over the
    weighted slots, or the admin's pin). If that exit is down, a healthy one;
    nothing healthy → it stays where it is pinned (blackhole, fail closed)."""
    slots: list[int] = []
    healthy_slots: list[int] = []
    for e in conf.exits:      # an exit with weight W appears W times, in config order
        for _ in range(e.weight):
            slots.append(e.index)
            if e.index in up:
                healthy_slots.append(e.index)
    known = {e.index for e in conf.exits}
    pinned: dict[str, int] = {}
    for line in pins_text.splitlines():      # "address index"; only a configured exit counts
        parts = line.split()
        if len(parts) >= 2 and IP_RE.match(parts[0]) and parts[1].isdigit() and int(parts[1]) in known:
            pinned[parts[0]] = int(parts[1])
    wanted: dict[str, int] = {}
    for ip in ips:
        position = (int(ip.rsplit(".", 1)[1]) + 254) % 256      # .2 → 0, .3 → 1, ...
        preferred = pinned.get(ip, slots[position % len(slots)])
        if preferred in up:
            wanted[ip] = preferred
        elif healthy_slots:
            wanted[ip] = healthy_slots[position % len(healthy_slots)]
        else:
            wanted[ip] = preferred
    return wanted
