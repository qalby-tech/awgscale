"""cascade.conf: the settings as the file the server boots from (bash syntax; the watch service reads it too)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from ..files import read_text, write_file
from awgscale.watch.conf import DEFAULT_HOME_COUNTRY

from .models import DEFAULT_WEIGHT, CascadeSettings, ExitSetting, checked

# NAME=( "a" b   # comment
#        "c" )
ARRAY_RE = re.compile(
    r"""
    ^(?P<name>[A-Z_]+)=\(      # EXITS=(
    \s*(?P<body>.*?)\s*        # the items, over as many lines as it takes
    \)\s*$                     # )
    """,
    re.VERBOSE | re.MULTILINE | re.DOTALL,
)
# one line inside an array: a quoted item, a comment to the end of the line, or a bare item
ARRAY_ITEM_RE = re.compile(
    r"""
    "(?P<quoted>[^"]*)"        # "1:awgk1:10.77.1.2:exit1-nl:2"
    |(?P<comment>\#.*)         # # the rest is a comment
    |(?P<bare>[^\s"#]+)        # 1.1.1.1
    """,
    re.VERBOSE,
)
# NAME="value", NAME='value' or NAME=value — but not NAME=( …
SCALAR_RE = re.compile(
    r"""
    ^(?P<name>[A-Z_]+)=(?!\()                                          # UDP_VIA_EXIT=
    (?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s\#]*))     # "1", '1' or 1
    """,
    re.VERBOSE | re.MULTILINE,
)
EXIT_FIELDS_WITH_NAME = 4
EXIT_FIELDS_WITH_WEIGHT = 5
PREVIOUS_SUFFIX = ".prev"


def _array_items(body: str) -> list[str]:
    items: list[str] = []
    for line in body.splitlines():
        for match in ARRAY_ITEM_RE.finditer(line):
            if match["comment"]:
                break
            items.append(match["quoted"] or match["bare"])
    return items


def _exit(text: str) -> ExitSetting:
    """ "index:interface:tunnel address:display name:weight" """
    parts = text.split(":")
    name = parts[3] if len(parts) >= EXIT_FIELDS_WITH_NAME else ""
    weight = int(parts[4]) if len(parts) >= EXIT_FIELDS_WITH_WEIGHT and parts[4] else DEFAULT_WEIGHT
    return ExitSetting(index=int(parts[0]), iface=parts[1], tip=parts[2], name=name, weight=weight)


def parse(text: str) -> CascadeSettings:
    """cascade.conf → the settings. Reads it the way the watch service does."""
    arrays = {match["name"]: _array_items(match["body"]) for match in ARRAY_RE.finditer(text)}
    scalars: dict[str, str] = {}
    for match in SCALAR_RE.finditer(text):
        held = match["double"] if match["double"] is not None else match["single"] if match["single"] is not None else match["bare"]
        scalars[match["name"]] = held
    return CascadeSettings(
        client_if=scalars["CLIENT_IF"],
        client_subnet=scalars["CLIENT_SUBNET"],
        udp_via_exit=str(scalars.get("UDP_VIA_EXIT", "0")) == "1",
        exits=tuple(_exit(item) for item in arrays.get("EXITS", [])),
        dns_via_exit=tuple(arrays.get("DNS_VIA_EXIT", [])),
        via_exit_subnets=tuple(item for item in arrays.get("VIA_EXIT_SUBNETS", []) if item),
        probe_targets=tuple(arrays.get("PROBE_TARGETS", [])),
        extra_ru_nets=tuple(scalars.get("EXTRA_RU_NETS", "").split()),
        single_server=str(scalars.get("SINGLE_SERVER", "0")) == "1",
        home_country=scalars.get("HOME_COUNTRY", DEFAULT_HOME_COUNTRY),
    )


def _array(items: Sequence[str]) -> str:
    return "(" + " ".join(items) + ")"


def text_of(settings: CascadeSettings) -> str:
    """The settings → cascade.conf."""
    lines = [
        "# Cascade settings for the entry server.",
        "# COMPILED from the database by the agent. Do not edit: an edit made here is",
        "# lost with the next change. To change:  python -m awgscale.agent cascade-get / cascade-set",
        f'CLIENT_IF="{settings.client_if}"',
        f'CLIENT_SUBNET="{settings.client_subnet}"',
        "# In list mode: also send UDP, except port 443, to non-Russian addresses abroad - calls",
        f"UDP_VIA_EXIT={1 if settings.udp_via_exit else 0}",
        '# "index:interface:tunnel address of the entry on that exit:display name:weight"',
        "EXITS=(",
        *[f'    "{exit_.index}:{exit_.iface}:{exit_.tip}:{exit_.name}:{exit_.weight}"' for exit_ in settings.exits],
        ")",
        "# This server's own DNS upstreams - resolved through an exit",
        "DNS_VIA_EXIT=" + _array(settings.dns_via_exit),
        "# Local Docker subnets whose outgoing traffic must leave through an exit",
        "VIA_EXIT_SUBNETS=" + _array(settings.via_exit_subnets),
        "# Health-probe targets, pinged through each exit",
        "PROBE_TARGETS=" + _array(settings.probe_targets),
        "# Addresses to force through the Russian side despite the list",
        f'EXTRA_RU_NETS="{" ".join(settings.extra_ru_nets)}"',
    ]
    if settings.single_server:
        lines += ["# A single server: no exits, the users' internet leaves from this server", "SINGLE_SERVER=1"]
    if settings.home_country != DEFAULT_HOME_COUNTRY:
        lines += ["# The country whose networks go direct (ISO 3166 code)", f'HOME_COUNTRY="{settings.home_country}"']
    return "".join(line + "\n" for line in lines)


def compiles(settings: CascadeSettings) -> bool:
    """Does the file made from these settings read back as these very settings (all the file carries of them)."""
    return parse(text_of(settings)) == settings.routed()


def compile_to(path: Path, settings: CascadeSettings) -> bool:
    """Make the file say what the settings say. → True when the file was replaced.
    Raises ValueError for settings that must not reach the file; OSError when it cannot be written."""
    settings = checked(settings.wire())
    text = text_of(settings)
    if parse(text) != settings.routed():
        raise ValueError("собранный cascade.conf читается не так, как настройки — файл не тронут")
    current = read_text(path)
    if current == text:
        return False
    if current is not None:
        write_file(path.with_name(path.name + PREVIOUS_SUFFIX), current)
    write_file(path, text)
    return True
