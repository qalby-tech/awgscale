"""The order of the rules: the first rule that names a site decides where it goes.

A site may be in several rules. An entry of a rule is «covered» when a rule above it, switched on, already names
it — the same name, or a domain it is under (`youtube.com` above covers `m.youtube.com` below). A covered entry is
left out where it would act:

  * the routing (abroad, directly) looks only at the rules that route: a blocked rule above does not take a name
    out of the routing — a blocked name never resolves, so it makes no difference, and «Блокируется» saved never
    restarts the resolver;
  * the blocking looks at every rule above: a site sent abroad above is not blocked below.

A name under a domain that is routed otherwise works only when its rule stands above the domain's: the resolver and
the sets of addresses always take the most specific name, and the order shown is the order that acts. Networks
(Telegram's, a list's) take no part: no blocked or direct rule holds one. Nor do the lists by link: thousands of
names each, kept up to date by themselves, they neither cover nor are covered. A rule for some groups of people
covers nobody: everybody else goes by the rules below it; a rule for everybody above it covers it as any other."""
from __future__ import annotations

from typing import Sequence

from ..sites.entries import covers, is_network
from ..store.documents import Record
from .store import Way, scope_of, way_of

ROUTING: frozenset[Way] = frozenset({Way.EXIT, Way.DIRECT})
EVERY: frozenset[Way] = frozenset(Way)

Covered = dict[str, dict[str, str]]         # rule id → {entry: the name of the rule above that covers it}


def _covered(rules: Sequence[Record], counted: frozenset[Way], only: frozenset[Way]) -> Covered:
    """The entries of the rules leading to `only` that a rule above, leading to `counted`, covers."""
    above: list[tuple[str, str]] = []           # (entry, its rule's name), of the rules on, in order
    covered: Covered = {}
    for rule in rules:
        if not rule.get("on") or rule.get("url"):
            continue
        way = way_of(rule)
        entries = [entry for entry in rule.get("items") or [] if isinstance(entry, str) and not is_network(entry)]
        if way in only:
            for entry in entries:
                hit = next((name for earlier, name in above if covers(earlier, entry)), None)
                if hit is not None:
                    covered.setdefault(str(rule.get("id")), {})[entry] = hit
        if way in counted and not scope_of(rule):     # a rule for some groups decides only for them: it covers nobody else
            name = str(rule.get("name"))
            above.extend((entry, name) for entry in entries)
    return covered


def routing_covered(rules: Sequence[Record]) -> Covered:
    """What the routing leaves out: entries of the rules abroad or direct under such a rule above."""
    return _covered(rules, ROUTING, ROUTING)


def block_covered(rules: Sequence[Record]) -> Covered:
    """What the blocker leaves out: entries of the blocked rules under any rule above."""
    return _covered(rules, EVERY, frozenset({Way.BLOCK}))


def shown_covered(rules: Sequence[Record]) -> Covered:
    """What the pages mark: the routing's for the rules that route, the blocker's for the blocked ones."""
    return {**routing_covered(rules), **block_covered(rules)}


def without(records: Sequence[Record], covered: Covered) -> list[Record]:
    """The records with their covered entries taken out: what acts."""
    result: list[Record] = []
    for record in records:
        gone = covered.get(str(record.get("id")), {})
        result.append({**record, "items": [entry for entry in record.get("items") or [] if entry not in gone]} if gone else record)
    return result
