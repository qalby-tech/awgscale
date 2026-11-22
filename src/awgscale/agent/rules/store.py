"""The rules, in the database: one ordered list of every group of sites, whether it goes abroad, directly or is
blocked. «Сайты» and «Блокируется» are two views of it: each reads its part and saves its part back into the same
places, leaving the other part where it stands.

The list joined the store after the groups of sites and the block groups had their own documents. It is made of
them once — by `store-init`, or by the first save that finds it missing — and they are written on beside it for
now: an agent of an earlier version, put back, reads them and finds everything as it was left."""
from __future__ import annotations

from enum import StrEnum
from typing import Sequence

from ..block.models import is_allow_group
from ..errors import StoreRefused
from ..labels import new_id
from ..store.documents import Kind, Record
from ..store.store import Store, StoreTransaction, same

RULES_MOVED_KEY = "moved:rules"
ROUTE = "route"
DIRECT = "direct"
BLOCK_KIND = "block"


class Way(StrEnum):
    """Where a rule leads when it is on (stored with it)."""

    EXIT = "exit"
    DIRECT = "direct"
    BLOCK = "block"


class Part(StrEnum):
    """Which view of the list: the groups of sites (abroad or direct), or the blocked ones."""

    SITES = "sites"
    BLOCK = "block"


WAYS_OF: dict[Part, frozenset[Way]] = {Part.SITES: frozenset({Way.EXIT, Way.DIRECT}), Part.BLOCK: frozenset({Way.BLOCK})}


def way_of(rule: Record) -> Way:
    try:
        return Way(rule.get(ROUTE))
    except ValueError:
        return Way.EXIT


def rule_of_site(group: Record) -> Record:
    """A group of sites as a rule: its "direct" mark becomes where it leads."""
    rule = {key: held for key, held in group.items() if key != DIRECT}
    rule[ROUTE] = Way.DIRECT.value if group.get(DIRECT) else Way.EXIT.value
    return rule


def rule_of_block(group: Record) -> Record:
    rule = {key: held for key, held in group.items() if key != "kind"}
    rule[ROUTE] = Way.BLOCK.value
    return rule


def rules_of(sites: Sequence[Record], blocks: Sequence[Record]) -> list[Record]:
    """The list made of the two documents it replaces: the groups of sites in their order, then the block groups.
    A block group whose id a group of sites has already is given a new one (they were made apart)."""
    rules = [rule_of_site(group) for group in sites]
    taken = {str(rule.get("id")) for rule in rules}
    for group in blocks:
        if not isinstance(group, dict) or is_allow_group(group):
            continue
        rule = rule_of_block(group)
        if rule.get("id") in taken:
            rule["id"] = new_id(taken)
        taken.add(str(rule["id"]))
        rules.append(rule)
    return rules


def sites_of(rules: Sequence[Record]) -> list[Record]:
    """The groups of sites as «Сайты» and the routing know them."""
    groups: list[Record] = []
    for rule in rules:
        way = way_of(rule)
        if way is Way.BLOCK:
            continue
        group = {key: held for key, held in rule.items() if key != ROUTE}
        if way is Way.DIRECT:
            group[DIRECT] = True
        groups.append(group)
    return groups


def blocks_of(rules: Sequence[Record]) -> list[Record]:
    """The block groups as «Блокируется» and the blocker know them."""
    return [{**{key: held for key, held in rule.items() if key != ROUTE}, "kind": BLOCK_KIND} for rule in rules if way_of(rule) is Way.BLOCK]


def merged(rules: Sequence[Record], part: Part, records: Sequence[Record]) -> list[Record]:
    """The list with one part replaced: the part's records take the places its rules had, in their own order (one
    more than before goes after the last of them; with none before, at the end). The other part stays where it is.
    Raises StoreRefused when a new record's id is a rule's of the other part (the two are made apart)."""
    convert = rule_of_site if part is Part.SITES else rule_of_block
    incoming = [convert(record) for record in records]
    others = {str(rule.get("id")) for rule in rules if way_of(rule) not in WAYS_OF[part]}
    clash = next((rule["id"] for rule in incoming if rule.get("id") in others), None)
    if clash is not None:
        raise StoreRefused(f"rules: id {clash} уже у правила другого вида — сохраните ещё раз")
    result: list[Record] = []
    queue = iter(incoming)
    slots = [index for index, rule in enumerate(rules) if way_of(rule) in WAYS_OF[part]]
    for index, rule in enumerate(rules):
        if index not in slots:
            result.append(rule)
            continue
        following = next(queue, None)
        if following is not None:
            result.append(following)
        if index == slots[-1]:
            result.extend(queue)
    if not slots:
        result.extend(queue)
    return result


async def ensure_moved(tx: StoreTransaction) -> list[Record]:
    """The list, made of the two documents first if it is not there yet (inside the caller's transaction). → the list."""
    if await tx.marked(RULES_MOVED_KEY):
        rules: list[Record] = await tx.get(Kind.RULES)
        return rules
    rules = rules_of(await tx.get(Kind.SITES), await tx.get(Kind.BLOCK))
    await tx.put(Kind.RULES, rules)
    await tx.mark(RULES_MOVED_KEY, "1")
    return rules


async def reconcile(tx: StoreTransaction) -> bool:
    """`store-init`, on every deploy: an agent of an earlier version, put back for a while, saved into the two old
    documents only. When they no longer say what the list says, the list is made of them again (C1 has no order of
    its own beyond theirs). → whether it was."""
    if not await tx.marked(RULES_MOVED_KEY):
        return False
    await tx.hold(Kind.RULES)
    rules = list(await tx.get(Kind.RULES))
    sites = await tx.get(Kind.SITES)
    blocks = [group for group in await tx.get(Kind.BLOCK) if isinstance(group, dict) and not is_allow_group(group)]
    if same(sites_of(rules), sites) and same(blocks_of(rules), blocks):
        return False
    await tx.put(Kind.RULES, rules_of(sites, blocks))
    return True


class StoredRules:
    def __init__(self, store: Store) -> None:
        self.__store = store

    async def read(self) -> list[Record]:
        """The list. Before it was made (`store-init` not yet run), it is what the two documents make, not stored."""
        async with self.__store.transaction() as tx:
            await tx.ready()
            if await tx.marked(RULES_MOVED_KEY):
                return list(await tx.get(Kind.RULES))
            return rules_of(await tx.get(Kind.SITES), await tx.get(Kind.BLOCK))

    async def read_part(self, part: Part) -> list[Record]:
        rules = await self.read()
        return sites_of(rules) if part is Part.SITES else blocks_of(rules)

    async def save_part(self, part: Part, records: Sequence[Record]) -> None:
        """One part saved into its places; the two documents of the earlier version are written beside it."""
        async with self.__store.transaction() as tx:
            await tx.hold(Kind.RULES)           # «Сайты», «Блокируется» and the nightly refresh each rewrite the whole list
            rules = merged(await ensure_moved(tx), part, records)
            await tx.put(Kind.RULES, rules)
            await tx.put(Kind.SITES, sites_of(rules))
            await tx.put(Kind.BLOCK, blocks_of(rules))
