"""The resolver as the people of a group some rule sends abroad or directly ask it: the upstreams of their own list
(rules/scoped.py: `view`), so that a name sent directly for them is asked directly, and one sent abroad is asked
abroad. AdGuard takes them per client (adguard.profiles: the variants of the clients for such a group)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..commands import RouteMode
from ..ports import RulesView
from ..rules.scoped import is_scoped_route, routed_groups, view_groups
from ..rules.store import Way, scope_of, way_of
from ..users.models import version_of
from .groups import direct_entries, flat_entries
from .routing import upstreams

DOMAIN_LINE_START = "[/"


@dataclass(frozen=True, slots=True)
class RouteDns:
    """How the people of one such group ask."""

    upstreams: tuple[str, ...]          # the whole spec of their list, as the routing makes it for everybody
    direct_lines: tuple[str, ...]       # "[/name/]direct resolvers" of the names their rules send directly: for those of them who send everything abroad


class RoutingFacts(Protocol):
    """sites.routing.Routing: the mode, and the names sent directly by hand."""

    def mode(self) -> RouteMode: ...

    def direct_names(self) -> list[str]: ...


class GroupDns:
    def __init__(self, rules: RulesView, routing: RoutingFacts) -> None:
        self.__rules = rules
        self.__routing = routing
        self.__memo_key: tuple[str, RouteMode, tuple[str, ...]] | None = None
        self.__memo: dict[str, RouteDns] = {}

    async def of(self) -> dict[str, RouteDns]:
        """{group id: how its people ask}, for every group a rule sends abroad or directly. Made again only when the
        list, the mode or the names sent by hand changed (it is asked with every look at the state)."""
        rules = await self.__rules.read()
        mode = self.__routing.mode()
        hand = self.__routing.direct_names()
        key = (version_of(rules), mode, tuple(hand))
        if key == self.__memo_key:
            return self.__memo
        made: dict[str, RouteDns] = {}
        for group in routed_groups(rules):
            seen = view_groups(rules, group)
            own_direct = {str(rule.get("id")) for rule in rules if is_scoped_route(rule) and group in scope_of(rule) and way_of(rule) is Way.DIRECT}
            direct = direct_entries([site for site in seen if site.id in own_direct])
            lines = [line for line in upstreams([], RouteMode.FOREIGN, direct)["upstream_dns"] if line.startswith(DOMAIN_LINE_START)]
            made[group] = RouteDns(upstreams=tuple(upstreams(flat_entries(seen), mode, [*direct_entries(seen), *hand])["upstream_dns"]), direct_lines=tuple(lines))
        self.__memo_key = key
        self.__memo = made
        return made
