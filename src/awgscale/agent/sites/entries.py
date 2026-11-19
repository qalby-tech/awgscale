"""An entry of a list of sites: a domain (with its subdomains), a pattern with
stars, or an address / a network. Nothing here touches the world."""
from __future__ import annotations

import fnmatch
import re

# example.com, sub.example.co.uk — labels of letters, digits and inner hyphens, at least two of them
DOMAIN_RE = re.compile(
    r"""
    ^(?=.{1,253}$)                                          # the whole name is 1…253 characters
    (?P<first>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)         # example
    (?P<rest>(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)   # .com, .co.uk
    \Z
    """,
    re.VERBOSE,
)
# api.*.cdn.example.net, *cdn.example.org — stars anywhere but in the last two labels
PATTERN_RE = re.compile(
    r"""
    ^(?=.{1,253}$)                                          # the whole pattern is 1…253 characters
    (?P<starred>[a-z0-9*-]+(?:\.[a-z0-9*-]+)*)              # api.*.cdn — labels that may hold stars
    \.(?P<domain>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)      # .example — a plain label
    \.(?P<zone>[a-z]{2,24})                                 # .net — a plain zone
    \Z
    """,
    re.VERBOSE,
)
# 149.154.160.0/20, 91.108.4.1
NETWORK_RE = re.compile(
    r"""
    ^(?P<a>\d{1,3})\.(?P<b>\d{1,3})\.(?P<c>\d{1,3})\.(?P<d>\d{1,3})     # 149.154.160.0
    (?:/(?P<prefix>\d{1,2}))?                                           # /20
    \Z
    """,
    re.VERBOSE,
)
# The scheme an entry may be typed with, taken off:   https://example.com → example.com
SCHEME_RE = re.compile(
    r"""
    ^(?P<scheme>https?://)?
    """,
    re.VERBOSE,
)
# The star in front of a name, taken off:   *.example.com → example.com
LEADING_STAR_RE = re.compile(
    r"""
    ^\*\.
    """,
    re.VERBOSE,
)
# The name ends in a zone that is a word, not in a number:   example.com   пример.xn--p1ai
WORD_ZONE_RE = re.compile(
    r"""
    \.(?P<zone>[a-z][a-z0-9-]*)$       # .com
    """,
    re.VERBOSE,
)

WILDCARD = "*"
OCTETS = ("a", "b", "c", "d")
MAX_OCTET = 255
MIN_PREFIX = 8
MAX_PREFIX = 32
SHOWN_ENTRY = 60


def is_network(text: str) -> bool:
    """Is this an address or a network, as far as its shape goes."""
    return NETWORK_RE.match(text) is not None


def network_fits(match: re.Match[str], narrowest_prefix: int) -> bool:
    """Every octet is one, and the prefix (when given) is no wider than allowed."""
    if any(int(match[octet]) > MAX_OCTET for octet in OCTETS):
        return False
    prefix = match["prefix"]
    return not prefix or narrowest_prefix <= int(prefix) <= MAX_PREFIX


def checked_entries(items: object, limit: int) -> list[str]:
    """What the admin typed into one group, put in order: lower case, no scheme,
    no path, no repeats. Raises ValueError with the reason for the admin."""
    if not isinstance(items, list) or len(items) > limit:
        raise ValueError(f"не больше {limit} доменов в списке")
    entries: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("некорректный домен")
        entry = SCHEME_RE.sub("", item.strip().lower().rstrip("."))
        network = NETWORK_RE.match(entry)
        if not network:
            entry = LEADING_STAR_RE.sub("", entry.split("/")[0])     # "*.example.com" is the same as "example.com"
        if not entry:
            continue
        if WILDCARD in entry:
            if not PATTERN_RE.match(entry):
                raise ValueError(f"некорректный шаблон: {entry[:SHOWN_ENTRY]} (звёздочка не может стоять в двух последних частях имени)")
        elif network:      # an address or network, for apps that connect by IP (e.g. Telegram)
            if not network_fits(network, MIN_PREFIX):
                raise ValueError(f"некорректная сеть: {entry}")
        elif not DOMAIN_RE.match(entry):
            raise ValueError(f"некорректный домен: {entry[:SHOWN_ENTRY]}")
        if entry not in entries:
            entries.append(entry)
    return entries


def widened(entry: str) -> str:
    """A pattern as the resolver understands it: the literal part after its last
    label with a star — the resolver only knows "a domain and all its subdomains"."""
    if WILDCARD not in entry:
        return entry
    labels = entry.split(".")
    last_starred = max(index for index, label in enumerate(labels) if WILDCARD in label)
    return ".".join(labels[last_starred + 1:])


def resolver_names(entries: list[str]) -> list[str]:
    """What the resolver (the VPN path) is given: plain domains as they are, each
    pattern widened; no networks, no repeats."""
    names: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if is_network(entry):
            continue
        name = widened(entry)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def covers(entry: str, host: str) -> bool:
    """The exact meaning of an entry: a plain domain covers itself and its
    subdomains, a pattern is matched as written."""
    if WILDCARD in entry:
        return fnmatch.fnmatchcase(host, entry)
    return host == entry or host.endswith("." + entry)
