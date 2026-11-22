"""Where rules lead: a group of sites goes abroad, is blocked, or is switched off.

Today a rule lives in one of two lists — the groups of sites that go abroad
(sites) and the groups of blocked names (block) — and each list has its own way
to the world: the routing and the resolver for the first (a save that changes
what goes abroad restarts the resolver: about a second without DNS), the
blocker's own rules for the second. Every change asked at once is made with the
fewest saves, through the same saves the pages use:

    1. the blocking: switches, and the rules that come in from the sites
    2. the sites — once, however many rules changed
    3. the blocking again: the rules that went to the sites taken out

If a step fails, the steps made before it are undone, so a rule is never lost in
between; while both lists hold it for a moment, blocking wins (the name does
not resolve), which is the safe side. What a step did is read back from the
store, not taken from its answer: a save may store and still fail to answer."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, Field

from ..commands import BlockSet, RuleRoute, RuleRoutesSet, SitesSet
from ..errors import Refused
from ..locks import file_lock
from ..payloads import decode, encode
from ..sites.entries import is_network
from ..sites.groups import version_of_sites
from ..sites.models import SiteGroup
from ..store.documents import Record

log = logging.getLogger("awgscale.agent")

SHOWN_NETWORK = 40
SHOWN_REASON = 200
MAX_CHANGES = 120


class RuleRouted(BaseModel):
    """One rule, after the change."""

    rule: str = Field(description="The id it had when asked")
    now: str = Field(description="The id it has now: a rule that moved to the other list gets a new one there")
    name: str = Field(description="What the admin called it")
    route: RuleRoute = Field(description="Where it leads now")


class RulesRouted(BaseModel):
    """The answer of rule-route."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    rules: list[RuleRouted] = Field(description="The rules asked about, in the order asked")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SitesStore(Protocol):
    async def read(self) -> list[SiteGroup]: ...


class BlockGroupsStore(Protocol):
    async def read(self) -> list[Record]: ...


class SitesSaver(Protocol):
    """sites.service.SitesService: its save, as the page calls it."""

    async def save(self, request: SitesSet) -> Any: ...


class BlockSaver(Protocol):
    """block.service.BlockService: its save, as the page calls it."""

    async def save(self, request: BlockSet) -> Any: ...


@dataclass(slots=True)
class Plan:
    """What the changes come to in each list."""

    sites: list[Record]
    blocks: list[Record]
    to_block: list[SiteGroup] = field(default_factory=list)
    to_sites: list[Record] = field(default_factory=list)
    sites_change: bool = False
    blocks_change: bool = False
    leaving_blocks: set[str] = field(default_factory=set)                       # ids of the block groups that go to the sites
    routed: list[tuple[str, str, RuleRoute]] = field(default_factory=list)     # (id asked, name, route)


def _changes(raw: object) -> list[tuple[str, RuleRoute]]:
    """The changes as the panel sends them: [{"rule": id, "route": …}, …], each rule once."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_CHANGES:
        raise Refused("некорректные данные")
    changes: list[tuple[str, RuleRoute]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("rule"), str) or item["rule"] in seen:
            raise Refused("некорректные данные")
        try:
            route = RuleRoute(item.get("route"))
        except ValueError:
            raise Refused("некорректные данные") from None
        seen.add(item["rule"])
        changes.append((item["rule"], route))
    return changes


def _same_items(record: Record, items: Sequence[str]) -> bool:
    return list(record.get("items") or []) == list(items)


class RulesService:
    def __init__(
        self,
        sites_store: SitesStore,
        block_store: BlockGroupsStore,
        sites: SitesSaver,
        block: BlockSaver,
        lock_file: Path,
    ) -> None:
        self.__sites_store = sites_store
        self.__block_store = block_store
        self.__sites = sites
        self.__block = block
        self.__lock_file = lock_file            # one change of the rules at a time: it is up to three saves

    # ── the plan ─────────────────────────────────────────────────────────────

    @staticmethod
    def __plan(changes: list[tuple[str, RuleRoute]], sites: list[SiteGroup], blocks: list[Record]) -> Plan:
        plan = Plan(sites=[group.wire() for group in sites], blocks=[dict(group) for group in blocks])
        by_site = {group.id: group for group in sites}
        by_block = {str(group.get("id")): group for group in blocks}
        leaving_sites: set[str] = set()
        leaving_blocks: set[str] = set()
        for rule, route in changes:
            site = by_site.get(rule)
            block = by_block.get(rule)
            if site is not None:
                plan.routed.append((rule, site.name, route))
                if route is RuleRoute.BLOCK:
                    if site.by_link or site.auto:
                        raise Refused(f"«{site.name}»: список по ссылке и группу, чьи сети обновляются сами, в блокировку не перенести")
                    networks = [entry for entry in site.items if is_network(entry)]
                    if networks:
                        raise Refused(f"«{site.name}»: в блокировку переносятся только сайты, а здесь есть сеть {networks[0][:SHOWN_NETWORK]}")
                    plan.to_block.append(site)
                    leaving_sites.add(site.id)
                elif site.on != (route is RuleRoute.EXIT):
                    plan.sites = [{**record, **({"on": route is RuleRoute.EXIT} if record.get("id") == site.id else {})} for record in plan.sites]
                    plan.sites_change = True
            elif block is not None:
                name = str(block.get("name"))
                plan.routed.append((rule, name, route))
                if route is RuleRoute.EXIT:
                    plan.to_sites.append(block)
                    leaving_blocks.add(rule)
                elif bool(block.get("on")) != (route is RuleRoute.BLOCK):
                    plan.blocks = [{**record, **({"on": route is RuleRoute.BLOCK} if str(record.get("id")) == rule else {})} for record in plan.blocks]
                    plan.blocks_change = True
            else:
                raise Refused("такого правила больше нет — обновите страницу")
        if plan.to_block:
            plan.blocks = plan.blocks + [{"name": group.name, "on": True, "items": list(group.items)} for group in plan.to_block]
            plan.sites = [record for record in plan.sites if record.get("id") not in leaving_sites]
            plan.blocks_change = True
            plan.sites_change = True
        if plan.to_sites:
            plan.sites = plan.sites + [{"name": str(group.get("name")), "on": True, "items": list(group.get("items") or [])} for group in plan.to_sites]
            plan.sites_change = True
        plan.leaving_blocks = leaving_blocks
        return plan

    # ── the saves, judged by what the store holds afterwards ─────────────────

    async def __save_blocks(self, groups: list[Record]) -> None:
        """The block groups saved as the page saves them (the lists and the dangerous-sites check are left as they are)."""
        try:
            await self.__block.save(BlockSet(payload=encode({"groups": groups})))
        except Exception as e:
            stored = await self.__block_store.read()
            if [(record.get("name"), bool(record.get("on")), list(record.get("items") or [])) for record in stored] == [
                (record.get("name"), bool(record.get("on")), list(record.get("items") or [])) for record in groups
            ]:
                log.info("rules: the blocking was stored, its answer failed: %s", repr(e)[:SHOWN_REASON])
                return
            raise

    async def __save_sites(self, groups: list[Record]) -> None:
        """The groups of sites saved as the page saves them, based on what is stored now."""
        now = await self.__sites_store.read()
        try:
            await self.__sites.save(SitesSet(payload=encode({"base": version_of_sites(now), "groups": groups})))
        except Exception as e:
            stored = await self.__sites_store.read()
            if [(group.name, group.on, list(group.items)) for group in stored if not group.by_link] == [
                (str(record.get("name")), bool(record.get("on")), list(record.get("items") or [])) for record in groups if not record.get("url")
            ] and len(stored) == len(groups):
                log.info("rules: the sites were stored, their answer failed: %s", repr(e)[:SHOWN_REASON])
                return
            raise

    async def __undo(self, what: str, sites: list[Record] | None, blocks: list[Record] | None) -> str:
        """Puts back the lists as they were. → the words to add to the refusal."""
        try:
            if sites is not None:
                await self.__save_sites(sites)
            if blocks is not None:
                await self.__save_blocks(blocks)
        except Exception as e:
            reason = e.error if isinstance(e, Refused) else repr(e)
            return f" — и вернуть как было не удалось ({reason[:SHOWN_REASON]}): {what} сейчас и в «Сайтах», и в «Блокируется», проверьте оба"
        return " — возвращено как было"

    # ── the command ──────────────────────────────────────────────────────────

    async def route(self, request: RuleRoutesSet) -> RulesRouted:
        changes = _changes(decode(request.payload).get("changes"))
        async with file_lock(self.__lock_file):
            sites = await self.__sites_store.read()
            blocks = list(await self.__block_store.read())
            plan = self.__plan(changes, sites, blocks)
            old_sites = [group.wire() for group in sites]
            old_blocks = [dict(group) for group in blocks]
            names = ", ".join(f"«{group.name}»" for group in plan.to_block) or "правило"
            if plan.blocks_change:
                await self.__save_blocks(plan.blocks)
            if plan.sites_change:
                try:
                    await self.__save_sites(plan.sites)
                except Exception as e:
                    reason = e.error if isinstance(e, Refused) else "сбой"
                    undone = await self.__undo(names, None, old_blocks if plan.blocks_change else None)
                    raise Refused(f"не применено: {reason}{undone}") from None
            leaving = plan.leaving_blocks
            if leaving:
                kept = await self.__block_store.read()
                try:
                    await self.__save_blocks([record for record in kept if str(record.get("id")) not in leaving])
                except Exception as e:
                    reason = e.error if isinstance(e, Refused) else "сбой"
                    moved = ", ".join(f"«{group.get('name')}»" for group in plan.to_sites)
                    undone = await self.__undo(moved, old_sites, old_blocks)
                    raise Refused(f"не применено: {reason}{undone}") from None
            return await self.__answer(plan)

    async def __answer(self, plan: Plan) -> RulesRouted:
        """Each rule asked about, with the id it has now: a moved one is found by its name and entries in its new list."""
        sites = await self.__sites_store.read()
        blocks = await self.__block_store.read()
        moved_to_block = {group.id: group for group in plan.to_block}
        moved_to_sites = {str(group.get("id")): group for group in plan.to_sites}
        routed: list[RuleRouted] = []
        for rule, name, route in plan.routed:
            now = rule
            if rule in moved_to_block:
                group = moved_to_block[rule]
                now = next((str(record.get("id")) for record in blocks if record.get("name") == group.name and _same_items(record, group.items)), rule)
            elif rule in moved_to_sites:
                record = moved_to_sites[rule]
                now = next((site.id for site in sites if site.name == record.get("name") and list(site.items) == list(record.get("items") or [])), rule)
            routed.append(RuleRouted(rule=rule, now=now, name=name, route=route))
        return RulesRouted(rules=routed)
