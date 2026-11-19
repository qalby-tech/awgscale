"""The cascade's settings: cascade.conf (bash syntax, read as text — nothing in it is executed)
and the admin's choice of what goes abroad. Standard library only."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from awgscale.service.errors import Refused

from .places import Places

# NAME=( "one" two   # a comment
#        three )
ARRAY_RE = re.compile(
    r"""
    ^(?P<name>[A-Z_]+)=\(      # NAME=(
    \s*(?P<body>.*?)\s*        # everything up to the closing bracket, over several lines
    \)\s*$                     # )
    """,
    re.MULTILINE | re.DOTALL | re.VERBOSE,
)
# one thing inside the brackets: "quoted", a comment to the end of the line, or a bare word
ARRAY_ITEM_RE = re.compile(
    r"""
    "(?P<quoted>[^"]*)"
    | (?P<comment>\#.*)
    | (?P<bare>[^\s"#]+)
    """,
    re.VERBOSE,
)
# NAME="value"   NAME='value'   NAME=value
SCALAR_RE = re.compile(
    r"""
    ^(?P<name>[A-Z_]+)=(?!\()          # NAME=  (not an array)
    (?:
        "(?P<double>[^"]*)"
      | '(?P<single>[^']*)'
      | (?P<bare>[^\s\#]*)
    )
    """,
    re.MULTILINE | re.VERBOSE,
)
MODE_FILE_BYTES = 16
DEFAULT_WEIGHT = 1

ConfValues = dict[str, str | list[str]]


class RouteMode(StrEnum):
    """What goes abroad: only what is listed, or everything that is not Russian."""

    LIST = "list"
    FOREIGN = "geo"


def conf_read(path: Path) -> ConfValues:
    """→ {"CLIENT_IF": str, ..., "EXITS": [str], ...}. Scalars and (…) arrays,
    with or without quotes; comments and anything else are skipped."""
    text = path.read_text()
    values: ConfValues = {}
    for array in ARRAY_RE.finditer(text):
        items: list[str] = []
        for line in array["body"].splitlines():
            for item in ARRAY_ITEM_RE.finditer(line):
                if item["comment"]:
                    break
                items.append(item["quoted"] if item["quoted"] is not None else item["bare"])
        values[array["name"]] = items
    for scalar in SCALAR_RE.finditer(text):
        if scalar["double"] is not None:
            values[scalar["name"]] = scalar["double"]
        elif scalar["single"] is not None:
            values[scalar["name"]] = scalar["single"]
        else:
            values[scalar["name"]] = scalar["bare"]
    return values


def route_mode(places: Places) -> RouteMode:
    """The admin's choice, compiled by the agent. Anything but a plain "geo" is the list."""
    try:
        with open(places.route_mode) as f:
            chosen = f.read(MODE_FILE_BYTES).strip()
    except OSError:
        return RouteMode.LIST
    return RouteMode.FOREIGN if chosen == RouteMode.FOREIGN else RouteMode.LIST


@dataclass(frozen=True, slots=True)
class ExitSpec:
    """One exit as cascade.conf names it: "index:interface:tunnel address:name:weight"."""

    index: int
    iface: str
    tip: str            # this server's address inside the tunnel to that exit
    name: str
    weight: int

    @classmethod
    def parse(cls, text: str) -> ExitSpec:
        parts = text.split(":")
        weight = int(parts[4]) if len(parts) > 4 and parts[4] else DEFAULT_WEIGHT
        return cls(index=int(parts[0]), iface=parts[1], tip=parts[2], name=parts[3] if len(parts) > 3 else "", weight=weight)


def _list(values: ConfValues, key: str) -> list[str]:
    value = values.get(key) or []
    return list(value) if isinstance(value, list) else []


def _text(values: ConfValues, key: str, default: str = "") -> str:
    value = values.get(key, default)
    return value if isinstance(value, str) else default


@dataclass(frozen=True, slots=True)
class CascadeConf:
    client_if: str
    client_subnet: str
    exits: tuple[ExitSpec, ...]
    udp_via_exit: bool
    dns_via_exit: tuple[str, ...]
    via_exit_subnets: tuple[str, ...]
    probe_targets: tuple[str, ...]
    extra_ru_nets: tuple[str, ...]
    mode: RouteMode
    single_server: bool = False

    @classmethod
    def load(cls, places: Places) -> CascadeConf:
        """The settings as they are on disk right now. Refused when there is not one exit in them,
        unless the server is a single server (SINGLE_SERVER=1): then there must be none."""
        values = conf_read(places.conf)
        exits = tuple(ExitSpec.parse(text) for text in _list(values, "EXITS"))
        single_server = _text(values, "SINGLE_SERVER", "0") == "1"
        if single_server and exits:
            raise Refused("в cascade.conf SINGLE_SERVER=1, но выходы названы")
        if not single_server and not exits:
            raise Refused("в cascade.conf нет ни одного выхода")
        return cls(
            client_if=str(values["CLIENT_IF"]),
            client_subnet=str(values["CLIENT_SUBNET"]),
            exits=exits,
            udp_via_exit=_text(values, "UDP_VIA_EXIT", "0") == "1",
            dns_via_exit=tuple(_list(values, "DNS_VIA_EXIT")),
            via_exit_subnets=tuple(subnet for subnet in _list(values, "VIA_EXIT_SUBNETS") if subnet),
            probe_targets=tuple(_list(values, "PROBE_TARGETS")),
            extra_ru_nets=tuple(_text(values, "EXTRA_RU_NETS").split()),
            mode=route_mode(places),
            single_server=single_server,
        )
