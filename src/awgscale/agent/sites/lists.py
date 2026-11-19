"""Lists by link: a group whose entries are the file behind its link, downloaded
through an exit and refreshed nightly."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from awgscale.core.ashell import AsyncRunner
from awgscale.core.net import HTTPS_PORT

from .entries import DOMAIN_RE, LEADING_STAR_RE, NETWORK_RE, WORD_ZONE_RE, network_fits
from .models import MAX_LIST_ENTRIES

MAX_LINK_LENGTH = 500
HTTPS = "https"
# what may follow the host in a link: a path and a query, no spaces, quotes or brackets:   /lists/friend.txt?v=2
LINK_TAIL_RE = re.compile(
    r"""
    ^(?P<tail>[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,400})\Z
    """,
    re.VERBOSE,
)
# an AdGuard-style rule:  ||example.com^
BLOCK_RULE_RE = re.compile(
    r"""
    ^\|\|               # ||
    (?P<name>[^\^/$]+)   # example.com
    \^\Z                # ^
    """,
    re.VERBOSE,
)
# a line of cascade.conf's EXITS that names an exit:   "1:203.0.113.10:10.77.1.2:nl:2"
EXIT_LINE_RE = re.compile(
    r"""
    ^\s*"(?P<index>\d+):        # "1:
    [^:"]+:                     # 203.0.113.10:   the exit's public address
    (?P<tunnel_ip>[0-9.]+):     # 10.77.1.2:      the entry's address on the tunnel to it
    """,
    re.VERBOSE | re.MULTILINE,
)
HOSTS_FILE_ADDRESSES = ("0.0.0.0", "127.0.0.1")
NARROWEST_LISTED_PREFIX = 16        # a network of a list: /16 and narrower
LOCAL_NAMES = "localhost."
LIST_BYTES = 2 << 20
CURL_TIMEOUT = 45.0
IPV4 = 4
SHOWN_CURL_ERROR = 100
EXIT_UP = "up"
MIN_SKIPPED_TOLERATED = 20


@dataclass(frozen=True, slots=True)
class ListLink:
    """A link to a list file, put in order."""

    url: str
    host: str


def link_of(url: object) -> ListLink | None:
    """A link to a list file as typed → the link in order; None for what is not
    one. https only and a real host name only: the blocker's "add list" call also
    accepts a path on this server, and nothing typed in the panel may reach that,
    an address literal or a non-standard port."""
    if not isinstance(url, str) or len(url) > MAX_LINK_LENGTH:
        return None
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    tail = parts.path + ("?" + parts.query if parts.query else "")
    if parts.scheme.lower() != HTTPS or parts.username or parts.password or parts.fragment or port not in (None, HTTPS_PORT):
        return None
    if not DOMAIN_RE.match(host) or not WORD_ZONE_RE.search(host):      # a name, not an address
        return None
    if not parts.path.strip("/") or not LINK_TAIL_RE.match(tail):
        return None
    return ListLink(url=f"{HTTPS}://{host}{tail}", host=host)


@dataclass(frozen=True, slots=True)
class ParsedList:
    entries: list[str]
    skipped: int                    # lines that were not understood


def _listed(name: str) -> bool:
    """May this name of a list's file be an entry: an address or a network no
    wider than allowed, or a real domain — nothing as wide as a whole zone."""
    network = NETWORK_RE.match(name)
    if network:
        return network_fits(network, NARROWEST_LISTED_PREFIX)
    return bool(DOMAIN_RE.match(name)) and bool(WORD_ZONE_RE.search(name)) and not name.startswith(LOCAL_NAMES)


def parse_list(text: str) -> ParsedList:
    """Entries of a list file. Understood: a name per line, hosts-file lines,
    ||name^ rules, addresses and networks (/16 and narrower). Anything else is
    skipped and counted — one odd line must not cost the whole list — and so is
    anything as wide as a whole zone."""
    entries: list[str] = []
    seen: set[str] = set()
    skipped = 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if not line or line.startswith("!"):
            continue
        rule = BLOCK_RULE_RE.match(line)
        parts = [rule["name"]] if rule else line.split()
        if len(parts) >= 2 and parts[0] in HOSTS_FILE_ADDRESSES:
            parts = parts[1:]
        elif len(parts) != 1:
            skipped += 1
            continue
        for part in parts:
            name = LEADING_STAR_RE.sub("", part.rstrip("."))
            if not _listed(name):
                skipped += 1
            elif name not in seen:
                seen.add(name)
                entries.append(name)
    return ParsedList(entries=entries, skipped=skipped)


class ListFailed(Exception):
    """A list could not be taken; the text is for the admin. `skipped` is how many of its lines were not understood, when it got that far."""

    def __init__(self, reason: str, skipped: int = 0) -> None:
        super().__init__(reason)
        self.__skipped = skipped

    @property
    def skipped(self) -> int:
        return self.__skipped


def judged(parsed: ParsedList) -> ParsedList:
    """A downloaded file that is a list of addresses, or ListFailed."""
    if not parsed.entries or parsed.skipped > max(MIN_SKIPPED_TOLERATED, len(parsed.entries)):
        raise ListFailed("файл не похож на список адресов", parsed.skipped)
    if len(parsed.entries) > MAX_LIST_ENTRIES:
        raise ListFailed(f"в списке {len(parsed.entries)} адресов — больше {MAX_LIST_ENTRIES} нельзя", parsed.skipped)
    return parsed


def exits_up(conf_text: str, run_dir: Path) -> list[str]:
    """Tunnel addresses of the exits that are up: a request bound to one of them leaves through that exit."""
    sources: list[str] = []
    for exit_line in EXIT_LINE_RE.finditer(conf_text):
        try:
            state = (run_dir / f"exit{exit_line['index']}.state").read_text().strip()
        except OSError:
            continue
        if state == EXIT_UP:
            sources.append(exit_line["tunnel_ip"])
    return sources


def global_addresses(host: str) -> list[str]:
    """Every address of a name, IPv4 and IPv6, sorted, when each of them is an
    ordinary internet address; otherwise none. The one judgement of "is the
    link's host public" for the lists of sites and the block lists alike."""
    try:
        found = socket.getaddrinfo(host, HTTPS_PORT, proto=socket.IPPROTO_TCP)
        addresses = sorted({str(info[4][0]) for info in found})
        if addresses and all(ipaddress.ip_address(address).is_global for address in addresses):
            return addresses
    except (OSError, ValueError):
        pass
    return []


def is_public(host: str) -> bool:
    """Every address of the name, of either family, is an ordinary internet address."""
    return bool(global_addresses(host))


def public_addresses(host: str) -> list[str]:
    """The IPv4 addresses of a public name (see global_addresses): a list is
    downloaded through an exit, and the tunnels to the exits carry IPv4."""
    return [address for address in global_addresses(host) if ipaddress.ip_address(address).version == IPV4]


class ListReader:
    """Downloads the file behind a list link through an exit. The name is
    resolved once, checked and pinned for the download, so the address that was
    checked is the address that is fetched; redirects are not followed, for the
    same reason."""

    def __init__(self, run: AsyncRunner, cascade_conf: Path, run_dir: Path) -> None:
        self.__run = run
        self.__cascade_conf = cascade_conf
        self.__run_dir = run_dir

    def __sources(self) -> list[str]:
        try:
            conf_text = self.__cascade_conf.read_text()
        except OSError:
            return []
        return exits_up(conf_text, self.__run_dir)

    async def __download(self, link: ListLink, address: str, sources: list[str]) -> str:
        last = "ошибка"
        self.__run_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="list-", dir=self.__run_dir)
        os.close(descriptor)
        try:
            for tunnel_ip in sources:
                done = await self.__run(
                    [
                        "curl", "-sS", "--proto", "=https", "--max-redirs", "0", "--connect-timeout", "10", "--max-time", "30",
                        "--max-filesize", str(LIST_BYTES), "--interface", tunnel_ip, "--resolve", f"{link.host}:{HTTPS_PORT}:{address}",
                        "-o", temporary, "-w", "%{http_code}", link.url,
                    ],
                    None,
                    CURL_TIMEOUT,
                )
                status = done.out.strip()[-3:]
                if done.ok and status == "200":
                    with open(temporary, "rb") as downloaded:
                        return downloaded.read(LIST_BYTES + 1).decode("utf-8", "replace")
                last = f"ответ {status}" if done.ok else (done.err.strip().splitlines() or ["ошибка"])[-1][-SHOWN_CURL_ERROR:]
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise ListFailed("не скачался: " + last)

    async def text_of(self, link: ListLink) -> str:
        addresses = await asyncio.to_thread(public_addresses, link.host)
        if not addresses:
            raise ListFailed("адрес из ссылки не найден")
        sources = await asyncio.to_thread(self.__sources)
        if not sources:
            raise ListFailed("нет работающего выхода за границу")
        return await self.__download(link, addresses[0], sources)

    async def read(self, url: str) -> ParsedList:
        """The entries of the list behind a link. Raises ListFailed."""
        link = link_of(url)
        if link is None:
            raise ListFailed("некорректная ссылка")
        return judged(parse_list(await self.text_of(link)))
