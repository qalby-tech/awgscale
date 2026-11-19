"""The address lists that come from outside: the home country's networks and the networks of
services whose apps connect by address. Standard library only."""
from __future__ import annotations

import http.client
import re
import ssl
import time
import urllib.parse
import urllib.request
from typing import Sequence

from awgscale.core.net import HTTPS_PORT

from .places import Places, write_text

# The networks of a country, by its two-letter code:   ru   kz
ZONE_URL = "https://www.ipdeny.com/ipblocks/data/aggregated/{country}-aggregated.zone"
# A second source, for Russia only
RU_ZONE_MIRROR = "https://raw.githubusercontent.com/bivlked/amneziawg-installer/v6.3.0/cascade/ru.zone"
RUSSIA = "ru"
NETS_URL = "https://core.telegram.org/resources/cidr.txt"
# A network of a list:   149.154.160.0/20
NET_RE = re.compile(
    r"""
    ^[0-9]{1,3}(?:\.[0-9]{1,3}){3}     # 149.154.160.0
    /[0-9]{1,2}$                       # /20
    """,
    re.VERBOSE,
)
# A network or a single address:   149.154.160.0/20   91.108.4.1
NET_OR_IP_RE = re.compile(
    r"""
    ^[0-9]{1,3}(?:\.[0-9]{1,3}){3}     # 91.108.4.1
    (?:/[0-9]{1,2})?$                  # /20, may be left out
    """,
    re.VERBOSE,
)
USER_AGENT = "curl/8"
MIN_RU_NETWORKS = 1000
MIN_HOME_NETWORKS = 1                  # a small country has a few dozen networks: only Russia's size is known
MIN_SERVICE_NETWORKS = 5
MAX_BODY_BYTES = 8 << 20
MAX_BODY_BYTES_VIA_EXIT = 4 << 20


def fetch(url: str, source: str | None = None, timeout: float = 30) -> str | None:
    """→ the body as text, or None. `source` = leave from that address (through that exit)."""
    try:
        if source:
            parts = urllib.parse.urlparse(url)
            connection = http.client.HTTPSConnection(parts.hostname or "", HTTPS_PORT, timeout=timeout, source_address=(source, 0), context=ssl.create_default_context())
            try:
                connection.request("GET", parts.path or "/", headers={"User-Agent": USER_AGENT})
                response = connection.getresponse()
                return response.read(MAX_BODY_BYTES_VIA_EXIT).decode("utf-8", "replace") if response.status == 200 else None
            finally:
                connection.close()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(MAX_BODY_BYTES).decode("utf-8", "replace")
    except Exception:
        return None


def zone_urls(country: str) -> tuple[str, ...]:
    """Where a country's networks are downloaded from, in order."""
    first = ZONE_URL.format(country=country)
    return (first, RU_ZONE_MIRROR) if country == RUSSIA else (first,)


def zone_valid(text: str | None, country: str) -> bool:
    """A DPI stub page with HTTP 200 must not pass: every line a network, and for Russia a thousand at least."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    least = MIN_RU_NETWORKS if country == RUSSIA else MIN_HOME_NETWORKS
    return len(lines) >= least and all(NET_RE.match(line) for line in lines)


def fetch_home_zone(places: Places, country: str) -> bool:
    """Downloads the home country's address list into its file. → False when no source gave a good one."""
    for url in zone_urls(country):
        text = fetch(url)
        if text is not None and zone_valid(text, country):
            write_text(places.home_zone(country), text if text.endswith("\n") else text + "\n")
            return True
    return False


def fetch_service_nets(places: Places, exit_addresses: Sequence[str]) -> list[str]:
    """Networks of services whose apps connect by address, from the service's own
    published list (Telegram), fetched through an exit — the site is not
    reachable from a Russian address. Written to their file when there are any. → the networks"""
    nets: list[str] = []
    for address in exit_addresses:
        text = fetch(NETS_URL, source=address, timeout=20)
        nets = [line for line in (text or "").splitlines() if NET_RE.match(line)]
        if len(nets) >= MIN_SERVICE_NETWORKS:
            break
        nets = []
    if nets:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        write_text(places.nets_auto, f"# Telegram networks, from core.telegram.org/resources/cidr.txt — {today}\n" + "\n".join(nets) + "\n")
    return nets
