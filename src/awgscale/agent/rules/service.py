"""The rules as one list: where each leads, and in which order.

A change of the canvas — rules sent elsewhere, the order changed — is one change of the list: checked against the
version the page showed, stored whole, and then followed by the world: the routing (when what goes abroad or
directly changed: the resolver is restarted, about a second without DNS) and the blocker's own rules. If the
routing does not come up, the list as it was is stored again and followed again: nothing is left half done."""
from __future__ import annotations

import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Collection, Protocol, Sequence

from pydantic import BaseModel, Field

from ..commands import RuleRoute, RuleRoutesSet, RulesGet
from ..errors import Refused
from ..locks import file_lock
from ..payloads import decode
from ..ports import GroupsStore
from ..sites.entries import is_network
from ..store.documents import Record
from ..users.models import version_of
from .compile import Covered, shown_covered
from .store import GROUPS, Way, scope_of, way_of

log = logging.getLogger("awgscale.agent")

SHOWN_NETWORK = 40
SHOWN_REASON = 200
MAX_CHANGES = 120
MAX_GROUPS = 60


class RuleRouted(BaseModel):
    """One rule, after the change."""

    rule: str = Field(description="Its id (a rule keeps it whatever it leads to)")
    name: str = Field(description="What the admin called it")
    route: RuleRoute = Field(description="Where it leads now")
    groups: list[str] = Field(default_factory=list, description="The names of the groups of people it is for; empty: everybody")


class RulesRouted(BaseModel):
    """The answer of rule-route."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    rules: list[RuleRouted] = Field(description="The rules asked about, in the order asked")
    ordered: bool = Field(default=False, description="The order of the rules was changed")
    v: str = Field(description="The version of the list now")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class RulesShown(BaseModel):
    """The answer of rules-get: the whole list, in its order."""

    ok: bool = Field(default=True, description="Always true")
    rules: list[Record] = Field(description="Every rule, as stored, in the order that decides")
    covered: Covered = Field(description="Entries a rule above already names: {rule id: {entry: that rule's name}}")
    v: str = Field(description="The version of the list: a change based on another one is refused")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class RulesStore(Protocol):
    async def read(self) -> list[Record]: ...

    async def save_all(self, rules: Sequence[Record]) -> None: ...


class Routing(Protocol):
    """sites.service.SitesService: the routing made again from the stored list. Raises Refused when it did not come up."""

    async def reapply(self) -> bool: ...


class Blocking(Protocol):
    """block.service.BlockService: the blocker's rules made again from the stored list."""

    async def follow(self) -> None: ...


def route_of(rule: Record) -> RuleRoute:
    if not rule.get("on"):
        return RuleRoute.OFF
    return RuleRoute(way_of(rule).value)


def _names_only(rule: Record, where: str) -> None:
    """Blocking and going directly work by names: a list by link, the group whose networks update themselves and
    a group with networks cannot be sent there."""
    name = str(rule.get("name"))
    if rule.get("url") or rule.get("auto"):
        raise Refused(f"«{name}»: список по ссылке и группу, чьи сети обновляются сами, {where} не направить")
    networks = [entry for entry in rule.get("items") or [] if isinstance(entry, str) and is_network(entry)]
    if networks:
        raise Refused(f"«{name}»: {where} направляются только сайты, а здесь есть сеть {networks[0][:SHOWN_NETWORK]}")


@dataclass(frozen=True, slots=True)
class Change:
    """One rule as the panel asks it changed: where it leads, for whom (None: as it is)."""

    rule: str
    route: RuleRoute | None
    groups: list[str] | None


def _changes(raw: object) -> list[Change]:
    """The changes as the panel sends them: [{"rule": id, "route": … (optional), "groups": [ids] (optional)}, …], each rule once."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_CHANGES:
        raise Refused("некорректные данные")
    changes: list[Change] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("rule"), str) or item["rule"] in seen:
            raise Refused("некорректные данные")
        route: RuleRoute | None = None
        if item.get("route") is not None:
            try:
                route = RuleRoute(item["route"])
            except ValueError:
                raise Refused("некорректные данные") from None
        groups = item.get("groups")
        if groups is not None and not (isinstance(groups, list) and len(groups) <= MAX_GROUPS and all(isinstance(group, str) for group in groups)):
            raise Refused("некорректные данные")
        if route is None and groups is None:
            raise Refused("некорректные данные")
        seen.add(item["rule"])
        changes.append(Change(rule=item["rule"], route=route, groups=sorted(set(groups)) if groups is not None else None))
    return changes


def changed(rules: Sequence[Record], changes: Sequence[Change], order: Sequence[str] | None, groups_known: Collection[str]) -> list[Record]:
    """The list with the routes, the groups and the order asked for. Raises Refused for a rule that is not there or
    cannot go where asked, and for a group of people that is not there."""
    by_id = {str(rule.get("id")): dict(rule) for rule in rules}
    for change in changes:
        rule = by_id.get(change.rule)
        if rule is None:
            raise Refused("такого правила больше нет — обновите страницу")
        if change.groups is not None:
            unknown = [group for group in change.groups if group not in groups_known]
            if unknown:
                raise Refused("такой группы людей больше нет — обновите страницу")
            if change.groups:
                rule[GROUPS] = change.groups
            else:
                rule.pop(GROUPS, None)
        route = change.route
        if route is RuleRoute.BLOCK:
            _names_only(rule, "в блокировку")
        elif route is RuleRoute.DIRECT:
            _names_only(rule, "напрямую")
        if route is RuleRoute.OFF:
            rule["on"] = False
        elif route is not None:
            rule["on"] = True
            rule["route"] = Way(route.value).value
        if scope_of(rule):
            _names_only(rule, "для отдельных групп")
            if way_of(rule) is not Way.BLOCK:
                raise Refused(f"«{rule.get('name')}»: для отдельных групп людей пока можно только блокировать — «за границу» и «напрямую» для групп появятся следующим шагом")
    ids = [str(rule.get("id")) for rule in rules]
    if order is None:
        return [by_id[rule_id] for rule_id in ids]
    if sorted(order) != sorted(ids):
        raise Refused("порядок правил устарел — обновите страницу")
    return [by_id[rule_id] for rule_id in order]


class RulesService:
    def __init__(self, store: RulesStore, routing: Routing, blocking: Blocking, lock_file: Path, groups: GroupsStore | None = None) -> None:
        self.__groups = groups                  # the groups of people: a rule may be for some of them
        self.__store = store
        self.__routing = routing
        self.__blocking = blocking
        self.__lock_file = lock_file            # one change of the list at a time

    async def show(self, _request: RulesGet | None = None) -> RulesShown:
        rules = await self.__store.read()
        return RulesShown(rules=rules, covered=shown_covered(rules), v=version_of(rules))

    async def __follow(self) -> None:
        """The world made to follow the stored list. Raises Refused when the routing did not come up."""
        await self.__routing.reapply()
        await self.__blocking.follow()

    async def route(self, request: RuleRoutesSet) -> RulesRouted:
        data = decode(request.payload)
        changes = _changes(data.get("changes"))
        order = data.get("order")
        if order is not None and not (isinstance(order, list) and all(isinstance(rule_id, str) for rule_id in order)):
            raise Refused("некорректные данные")
        if not changes and order is None:
            raise Refused("некорректные данные")
        async with file_lock(self.__lock_file):
            old = await self.__store.read()
            base = data.get("base")
            if isinstance(base, str) and base and base != version_of(old):
                shown = {key: held for key, held in (await self.show()).wire().items() if key != "ok"}
                raise Refused("правила изменились в другом окне — показан новый список", conflict=True, **shown)
            people = (await self.__groups.read()).groups if self.__groups is not None else []
            names = {str(group.id): group.name for group in people if group.id is not None}
            new = changed(old, changes, order, names)
            await self.__store.save_all(new)
            try:
                await self.__follow()
            except Exception as e:
                reason = e.error if isinstance(e, Refused) else str(e)[:SHOWN_REASON] or "сбой"
                await self.__store.save_all(old)
                try:
                    await self.__follow()
                except Exception as back:
                    said = back.error if isinstance(back, Refused) else repr(back)
                    raise Refused(f"не применено: {reason} — прежние правила сохранены, но не применились: {said[:SHOWN_REASON]}") from None
                raise Refused(f"не применено: {reason} — возвращено как было") from None
        by_id = {str(rule.get("id")): rule for rule in new}
        routed = [
            RuleRouted(
                rule=change.rule,
                name=str(by_id[change.rule].get("name")),
                route=route_of(by_id[change.rule]),
                groups=[names.get(group, group) for group in scope_of(by_id[change.rule])],
            )
            for change in changes
        ]
        ordered = order is not None and [str(rule.get("id")) for rule in old] != list(order)
        return RulesRouted(rules=routed, ordered=ordered, v=version_of(new))
