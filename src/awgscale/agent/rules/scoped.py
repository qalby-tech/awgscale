"""A rule for some groups of people that goes abroad or directly: what the resolver and the watch service are given
for it. Nothing here touches the world.

The meaning (the spec): a person in group G goes by the list as G sees it — every rule for everybody and every rule
for G, in their order, as if they were for everybody (`view`); a person in no such group goes by the rules for
everybody. It is made to act without anything per person in the resolver's sets:

  * every rule for groups that routes has a set of addresses of its own, "rs_<rule>", and every group a set of its
    people's tunnel addresses, "rg_<group>";
  * a name joins the set of rule R when a name R routes covers it and no rule for everybody above R does
    (`scoped_names`): that rule decides it, as it would in G's own list;
  * the watch service asks, for a new connection, the rules for groups in their order — the first whose people's set
    holds the sender and whose own set holds the address decides (`scoped_rules_text`); nobody else's way changes.

Approximations, all of them the resolver's own (it knows only "a domain and its subdomains"): a pattern in a rule for
groups acts as the domain it is widened to; a pattern of a rule for everybody next to a rule for groups may decide a
name otherwise than in the group's own list (as two patterns may today); a name of a list by link (thousands of
names, neither covering nor covered) stops a rule for groups whose name is above it, as the most specific name; and a name under one of the
names sent directly by hand (domains-direct.txt) is left to that file, as for everybody."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

from ..labels import ID_RE
from ..sites.entries import WILDCARD, covers, is_network, widened
from ..sites.models import SiteGroup
from ..store.documents import Record
from ..users.models import KnownUser, UserGroup
from .compile import ROUTING, routing_covered, without
from .store import GROUPS, scope_of, sites_of, way_of

RULE_SET = "rs_"            # the addresses of the names a rule for groups decides (never "sp_": those are the spaces')
GROUP_SET = "rg_"           # the tunnel addresses of a group's people
SET_ID_LENGTH = 6
LABEL_DOT = "."


def set_id(raw: str) -> str:
    """An id as a set's name takes it: six lower-case letters or digits (a stored id is), anything else its digest."""
    return raw if ID_RE.match(raw) else hashlib.sha1(raw.encode()).hexdigest()[:SET_ID_LENGTH]


def rule_set(rule: Record) -> str:
    return RULE_SET + set_id(str(rule.get("id")))


def group_set(group_id: str) -> str:
    return GROUP_SET + set_id(group_id)


def is_scoped_route(rule: Record) -> bool:
    """A rule for some groups of people, switched on, that goes abroad or directly."""
    return bool(rule.get("on")) and way_of(rule) in ROUTING and bool(scope_of(rule)) and not rule.get("url")


def view(rules: Sequence[Record], group: str | None) -> list[Record]:
    """The list as the people of `group` go by it: every rule for everybody and every rule for that group, in their
    order, as if for everybody. None: the list of the people in no group a rule is for."""
    seen: list[Record] = []
    for rule in rules:
        scope = scope_of(rule)
        if not scope:
            seen.append(rule)
        elif group is not None and group in scope:
            seen.append({key: held for key, held in rule.items() if key != GROUPS})
    return seen


def view_groups(rules: Sequence[Record], group: str | None) -> list[SiteGroup]:
    """The groups of sites as the routing would take them for the people of `group` (what a rule above covers left out)."""
    seen = view(rules, group)
    return [SiteGroup.of(record) for record in without(sites_of(seen), routing_covered(seen))]


def under_hand(rules: Sequence[Record], hand: Sequence[str]) -> list[Record]:
    """The list with the approximations of the module made: in a rule for groups that routes, a pattern is the domain it
    is widened to, and a name under one sent directly by hand is left to that file."""
    by_hand = [widened(name) for name in hand if not is_network(name)]
    result: list[Record] = []
    for rule in rules:
        if is_scoped_route(rule):
            entries = [widened(entry) if isinstance(entry, str) and not is_network(entry) else entry for entry in rule.get("items") or []]
            kept = [entry for entry in dict.fromkeys(entries) if not (isinstance(entry, str) and any(covers(name, entry) for name in by_hand))]
            rule = {**rule, "items": kept}
        result.append(rule)
    return result


def routed_groups(rules: Sequence[Record]) -> list[str]:
    """The groups of people some rule that routes is for, switched on."""
    return sorted({group for rule in rules if is_scoped_route(rule) for group in scope_of(rule)})


@dataclass(frozen=True, slots=True)
class ScopedName:
    """A name and the set of the rule for groups that decides it: one line of domains-scoped.txt."""

    set_name: str           # rs_ab12cd
    name: str               # m.youtube.com — as the resolver takes it (a pattern widened)

    def line(self) -> str:
        return f"{self.set_name} {self.name}"


def _suffixes(name: str) -> list[str]:
    """m.youtube.com → m.youtube.com, youtube.com, com: every domain the name is under, itself first."""
    labels = name.split(LABEL_DOT)
    return [LABEL_DOT.join(labels[start:]) for start in range(len(labels))]


def _names(rule: Record, covered: dict[str, str]) -> list[str]:
    """What a rule routes, as the resolver takes it: its names (not networks) a rule above does not cover, widened."""
    entries = [entry for entry in rule.get("items") or [] if isinstance(entry, str) and not is_network(entry) and entry not in covered]
    return [name for name in dict.fromkeys(widened(entry) for entry in entries) if name]


def scoped_names(rules: Sequence[Record]) -> list[ScopedName]:
    """Which names each rule for groups decides (see the module). Every name of the resolver's file is looked at — the
    names of the rules for everybody and those of the rules for groups — by walking up its labels, never pair by pair."""
    covered = routing_covered(rules)
    deciders: dict[str, list[int]] = {}         # a name a rule for groups routes → the places of those rules in the list
    plain_first: dict[str, int] = {}             # an entry of a rule for everybody that routes → the first place it is at
    patterns: list[tuple[str, int]] = []
    names: dict[str, None] = {}
    listed: set[str] = set()
    for index, rule in enumerate(rules):
        if not rule.get("on") or way_of(rule) not in ROUTING:
            continue
        rule_covered = covered.get(str(rule.get("id")), {})
        if is_scoped_route(rule):
            for name in _names(rule, rule_covered):
                deciders.setdefault(name, []).append(index)
                names[name] = None
            continue
        if scope_of(rule):
            continue
        names.update(dict.fromkeys(_names(rule, rule_covered)))
        if rule.get("url"):                     # a list by link neither covers nor is covered (rules/compile.py): its names decide in every list
            listed.update(_names(rule, {}))
            continue
        for entry in rule.get("items") or []:
            if not isinstance(entry, str) or is_network(entry):
                continue
            if WILDCARD in entry:
                patterns.append((entry, index))
            else:
                plain_first.setdefault(entry, index)
    pairs: set[ScopedName] = set()
    for name in names:
        above = [plain_first[suffix] for suffix in _suffixes(name) if suffix in plain_first]
        above += [index for pattern, index in patterns if covers(pattern, name)]      # as a rule above covers: exactly (rules/compile.py)
        first_global = min(above, default=len(rules))
        for suffix in _suffixes(name):
            for index in deciders.get(suffix, ()):
                if index < first_global:
                    pairs.add(ScopedName(set_name=rule_set(rules[index]), name=name))
            if suffix in listed:                # a name of a list by link decides what is under it
                break
    return sorted(pairs, key=lambda pair: (pair.set_name, pair.name))


def scoped_rules_text(rules: Sequence[Record]) -> str:
    """The watch service's scoped.rules: one line per rule for groups that routes, in the order of the list —
    "rs_<rule> exit|direct rg_<group> …"."""
    lines = [
        " ".join([rule_set(rule), way_of(rule).value, *(group_set(group) for group in scope_of(rule))])
        for rule in rules
        if is_scoped_route(rule)
    ]
    return "".join(line + "\n" for line in lines)


def members_text(groups: Sequence[UserGroup], users: Sequence[KnownUser]) -> str | None:
    """The watch service's scoped.members: one line per group, "rg_<group> address …". None when the users could not
    be read (then nothing is changed: an empty list is not "everybody left")."""
    if not users and any(group.members for group in groups):
        return None
    address_of = {user.name: user.ip for user in users if user.ip}
    lines = []
    for group in groups:
        if group.id is None:
            continue
        addresses = sorted({address_of[member] for member in group.members if member in address_of}, key=lambda ip: tuple(int(part) for part in ip.split(".")))
        lines.append(" ".join([group_set(str(group.id)), *addresses]))
    return "".join(line + "\n" for line in sorted(lines))
