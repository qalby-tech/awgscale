"""The shared site list: which names go abroad."""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("awgscale.dnsgate")

COMMENT = "#"
WILDCARD = "*"
SUBDOMAINS_PREFIX = "*."


def _is_address_line(entry: str) -> bool:
    """A network or an address: such lines are for the VPN side."""
    return entry[0].isdigit() and "/" in entry or entry.replace(".", "").isdigit()


@dataclass(frozen=True, slots=True)
class SiteRules:
    """The list, ready to be asked."""

    domains: frozenset[str] = frozenset()        # each with all its subdomains
    patterns: tuple[str, ...] = ()               # globs over the whole name

    @classmethod
    def parse(cls, text: str) -> SiteRules:
        domains: set[str] = set()
        patterns: list[str] = []
        for line in text.splitlines():
            entry = line.strip().lower().rstrip(".")
            if not entry or entry.startswith(COMMENT) or _is_address_line(entry):
                continue
            if entry.startswith(SUBDOMAINS_PREFIX):
                entry = entry[len(SUBDOMAINS_PREFIX):]
            if WILDCARD in entry:
                patterns.append(entry)
            else:
                domains.add(entry)
        return cls(frozenset(domains), tuple(patterns))

    def lists(self, name: str) -> bool:
        labels = name.split(".")
        for start in range(len(labels)):          # the name itself and every parent domain
            if ".".join(labels[start:]) in self.domains:
                return True
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.patterns)


class SiteList:
    """The rules as the file has them now. A file that cannot be read leaves the rules as they were."""

    def __init__(self, path: Path) -> None:
        self.__path = path
        self.__rules = SiteRules()

    @property
    def path(self) -> Path:
        return self.__path

    def lists(self, name: str) -> bool:
        return self.__rules.lists(name)

    def load(self) -> None:
        try:
            self.__rules = SiteRules.parse(self.__path.read_text())
        except OSError:
            return
        log.info("list loaded: %d domains, %d patterns", len(self.__rules.domains), len(self.__rules.patterns))
