#!/usr/bin/env python3
"""Rules for some groups of people that go abroad or directly (agent/rules/scoped.py), checked against their meaning.

The meaning: a person in group G goes by the list as G sees it — the rules for everybody and the rules for G, in
their order, as if for everybody — exactly as the routing for everybody would take that list (the resolver's most
specific name, then the marks: sent directly before sent abroad). What is made instead is one file of sets for the
resolver and the chain of the watch service. Random lists of rules are made, and for every person and every name
the way the resolver's sets and the chain give is compared with the way the meaning gives.

    uv run python tests/test_scoped.py
"""
from __future__ import annotations

import random
import sys

from awgscale.agent.commands import RouteMode
from awgscale.agent.rules.scoped import group_set, scoped_names, scoped_rules_text, view_groups
from awgscale.agent.sites.entries import covers, widened
from awgscale.agent.sites.groups import direct_entries, flat_entries
from awgscale.agent.sites.routing import ipset_text

PASS = 0
FAIL = 0
EXIT = "exit"
DIRECT = "direct"
GROUPS = ("g00001", "g00002", "g00003")
POOL = (
    "x.com", "m.x.com", "a.m.x.com", "b.x.com", "y.com", "n.y.com", "z.org", "k.z.org", "q.k.z.org", "w.net",
)
PATTERNS = ("api*.x.com", "s*.m.x.com", "cdn*.y.com")
ROUNDS = 4000


def ok(condition: object, what: str, detail: object = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print("ok   " + what)
    else:
        FAIL += 1
        print("FAIL " + what + (f" → {detail}" if detail != "" else ""))


def suffixes(name: str) -> list[str]:
    labels = name.split(".")
    return [".".join(labels[start:]) for start in range(len(labels))]


def default_of(mode: RouteMode) -> str:
    return DIRECT if mode is RouteMode.LIST else EXIT


def way_of_sets(sets: set[str], mode: RouteMode) -> str:
    """The marks for everybody: sent directly first, then abroad, then the mode."""
    if "force_direct" in sets:
        return DIRECT
    if "force_exit" in sets:
        return EXIT
    return default_of(mode)


def meant(rules: list[dict], group: str | None, host: str, hand: list[str], mode: RouteMode) -> str:
    """The way of a host for a person of `group`, by the list as that group sees it, as the routing for everybody takes it."""
    if group is not None and any(covers(widened(name), host) for name in hand):
        return meant(rules, None, host, hand, mode)          # under a name sent directly by hand: as for everybody
    groups = view_groups(rules if group is None else without_hand(rules, hand), group)
    names_exit = {widened(entry) for entry in flat_entries(groups) if "/" not in entry}
    names_direct = {widened(entry) for entry in [*direct_entries(groups), *hand]}
    for name in suffixes(host):
        if name in names_exit or name in names_direct:
            return way_of_sets(({"force_exit"} if name in names_exit else set()) | ({"force_direct"} if name in names_direct else set()), mode)
    return default_of(mode)


def without_hand(rules: list[dict], hand: list[str]) -> list[dict]:
    """The approximations kept on purpose, for the people of a group: a pattern acts as the domain it is widened to (as
    the resolver takes it), and an entry of a rule for groups under a name sent directly by hand is left to that file."""
    result = []
    for rule in rules:
        if rule.get("groups"):
            entries = list(dict.fromkeys(widened(entry) for entry in rule["items"]))
            kept = [entry for entry in entries if not any(covers(name, entry) for name in hand)]
            rule = {**rule, "items": kept}
        result.append(rule)
    return result


def made(rules: list[dict], hand: list[str]) -> tuple[dict[str, set[str]], list[tuple[str, str, list[str]]]]:
    """What is made: the resolver's file (a name → its sets; a name twice is in both lines' sets) and the chain."""
    everybody = view_groups(rules, None)
    text = ipset_text("".join(e + "\n" for e in flat_entries(everybody)), "".join(e + "\n" for e in [*direct_entries(everybody), *hand]), scoped_names(rules), hand)
    sets: dict[str, set[str]] = {}
    for line in text.splitlines():
        names, _, named = line.rpartition("/")
        for name in names.split(","):
            sets.setdefault(name, set()).update(named.split(","))
    chain = [(parts[0], parts[1], parts[2:]) for parts in (line.split() for line in scoped_rules_text(rules).splitlines())]
    return sets, chain


def before_d2(rules: list[dict], hand: list[str], host: str, mode: RouteMode) -> str:
    """The way of a host for everybody as the resolver's file made without any rule for groups gives it (before them)."""
    everybody = view_groups(rules, None)
    text = ipset_text("".join(e + "\n" for e in flat_entries(everybody)), "".join(e + "\n" for e in [*direct_entries(everybody), *hand]))
    sets: dict[str, set[str]] = {}
    for line in text.splitlines():
        names, _, named = line.rpartition("/")
        for name in names.split(","):
            sets.setdefault(name, set()).update(named.split(","))
    return way_of_sets(next((sets[name] for name in suffixes(host) if name in sets), set()), mode)


def given(sets: dict[str, set[str]], chain: list[tuple[str, str, list[str]]], group: str | None, host: str, mode: RouteMode) -> str:
    """The way the resolver's most specific name and the chain give (a new connection)."""
    found = next((sets[name] for name in suffixes(host) if name in sets), set())
    if group is not None:
        for rule_set, way, groups in chain:
            if group_set(group) in groups and rule_set in found:
                return way
    return way_of_sets(found, mode)


def random_rules(chance: random.Random, global_patterns: bool = False) -> tuple[list[dict], list[str]]:
    rules = []
    for index in range(chance.randint(1, 8)):
        items = chance.sample(POOL, chance.randint(1, 3))
        rule = {"id": f"r{index:05d}", "name": f"правило {index}", "on": chance.random() < 0.85, "route": chance.choice([EXIT, EXIT, DIRECT, "block"]), "items": items}
        if chance.random() < 0.5:
            rule["groups"] = sorted(chance.sample(GROUPS, chance.randint(1, 2)))
            if chance.random() < 0.15:          # a pattern in a rule for groups acts as its widened domain
                items.append(chance.choice(PATTERNS))
        elif global_patterns and chance.random() < 0.15:
            items.append(chance.choice(PATTERNS))
        rules.append(rule)
    hand = chance.sample(POOL, 1) if chance.random() < 0.2 else []
    return rules, hand


def t_examples() -> None:
    print("\n— примеры: порядок решает, для групп — только для их людей")
    rules = [
        {"id": "s00001", "name": "Детям YouTube", "on": True, "route": DIRECT, "groups": ["g00001"], "items": ["youtube.com"]},
        {"id": "a00001", "name": "YouTube", "on": True, "route": EXIT, "items": ["youtube.com", "m.youtube.com"]},
        {"id": "s00002", "name": "Взрослым x", "on": True, "route": EXIT, "groups": ["g00002"], "items": ["x.com"]},
        {"id": "s00003", "name": "Гостям m.x", "on": True, "route": DIRECT, "groups": ["g00003"], "items": ["m.x.com"]},
    ]
    sets, chain = made(rules, [])
    ok(given(sets, chain, "g00001", "m.youtube.com", RouteMode.LIST) == DIRECT and given(sets, chain, None, "m.youtube.com", RouteMode.LIST) == EXIT,
       "правило для детей выше общего: m.youtube.com детям напрямую, остальным — за границу", sets)
    ok(given(sets, chain, "g00002", "m.x.com", RouteMode.LIST) == EXIT and given(sets, chain, "g00003", "m.x.com", RouteMode.LIST) == DIRECT
       and given(sets, chain, None, "m.x.com", RouteMode.LIST) == DIRECT, "x.com для одной группы, m.x.com для другой: каждая получает своё", sets)
    ok(given(sets, chain, "g00002", "m.x.com", RouteMode.LIST) == meant(rules, "g00002", "m.x.com", [], RouteMode.LIST), "и это — то, что значит список группы")


def t_random() -> None:
    print(f"\n— {ROUNDS} случайных списков: каждый человек, каждое имя — как в списке его группы")
    chance = random.Random(20261123)
    wrong: list[str] = []
    checked = 0
    for _ in range(ROUNDS):
        rules, hand = random_rules(chance)
        mode = chance.choice([RouteMode.LIST, RouteMode.FOREIGN])
        sets, chain = made(rules, hand)
        for group in (None, *GROUPS):
            for host in (*POOL, *("q." + name for name in POOL), "api1.x.com", "s1.m.x.com"):
                checked += 1
                expected = meant(rules, group, host, hand, mode)
                got = given(sets, chain, group, host, mode)
                if expected != got and len(wrong) < 5:
                    wrong.append(f"{group} {host}: должно {expected}, вышло {got}; hand={hand} mode={mode.value} rules={rules}")
    ok(not wrong, f"{checked} проверок (человек × имя) совпали со смыслом", "\n".join(wrong))


def t_approximations() -> None:
    print("\n— то, что остаётся приближением (как и сегодня у резолвера): списки по ссылке и шаблоны правил для всех")
    listed = {"id": "l00001", "name": "Список", "on": True, "route": EXIT, "url": "https://lists.example/list.txt", "items": ["x.com", "k.org"]}
    rules = [
        {"id": "s00001", "name": "Детям m.x", "on": True, "route": DIRECT, "groups": ["g00001"], "items": ["m.x.com", "k.org"]},
        listed,
    ]
    sets, chain = made(rules, [])
    ok(given(sets, chain, "g00001", "m.x.com", RouteMode.LIST) == DIRECT and given(sets, chain, None, "m.x.com", RouteMode.LIST) == EXIT,
       "имя правила для группы под именем списка по ссылке — решает правило (список никого не накрывает)", sets)
    ok(given(sets, chain, "g00001", "k.org", RouteMode.LIST) == DIRECT and given(sets, chain, None, "k.org", RouteMode.LIST) == EXIT,
       "то же имя, что в списке по ссылке: «напрямую» группы побеждает (как «напрямую» и «за границу» одного имени для всех)", sets)
    chance = random.Random(5)
    differ = 0
    checked = 0
    outside = 0
    moved: list[str] = []
    for _ in range(ROUNDS // 4):
        rules, hand = random_rules(chance)
        for rule in rules:
            if not rule.get("groups") and rule["route"] == EXIT and chance.random() < 0.3:
                rule["url"] = "https://lists.example/list.txt"
        sets, chain = made(rules, hand)
        mode = chance.choice([RouteMode.LIST, RouteMode.FOREIGN])
        for group in GROUPS:
            for host in (*POOL, *("q." + name for name in POOL)):
                checked += 1
                differ += meant(rules, group, host, hand, RouteMode.LIST) != given(sets, chain, group, host, RouteMode.LIST)
        for host in (*POOL, *("q." + name for name in POOL), "api1.x.com", "s1.m.x.com"):
            outside += 1
            if given(sets, chain, None, host, mode) != before_d2(rules, hand, host, mode) and len(moved) < 5:
                moved.append(f"{host} mode={mode.value} hand={hand} rules={rules}")
    print(f"     для сведения: списки по ссылке рядом с правилами для групп — {differ} из {checked} ответов не как в списке группы")
    ok(differ * 100 < checked, "списки по ссылке расходятся со списком группы реже чем в 1% случаев", differ)
    ok(not moved, f"люди вне групп (списки по ссылке): {outside} ответов — ровно как до правил для групп", "\n".join(moved))
    chance = random.Random(7)
    differ = 0
    checked = 0
    outside = 0
    moved = []
    for _ in range(ROUNDS // 4):
        rules, hand = random_rules(chance, global_patterns=True)
        sets, chain = made(rules, hand)
        mode = chance.choice([RouteMode.LIST, RouteMode.FOREIGN])
        for group in GROUPS:
            for host in (*POOL, *("q." + name for name in POOL), "api1.x.com", "s1.m.x.com"):
                checked += 1
                differ += meant(rules, group, host, hand, RouteMode.LIST) != given(sets, chain, group, host, RouteMode.LIST)
        for host in (*POOL, *("q." + name for name in POOL), "api1.x.com", "s1.m.x.com"):
            outside += 1
            if given(sets, chain, None, host, mode) != before_d2(rules, hand, host, mode) and len(moved) < 5:
                moved.append(f"{host} mode={mode.value} hand={hand} rules={rules}")
    print(f"     для сведения: шаблоны в правилах для всех рядом с правилами для групп — {differ} из {checked} ответов не как в списке группы")
    ok(differ * 100 < checked, "шаблоны правил для всех расходятся со списком группы реже чем в 1% случаев", differ)
    ok(not moved, f"люди вне групп (шаблоны правил для всех): {outside} ответов — ровно как до правил для групп", "\n".join(moved))


def asked_via(servers: list[str], host: str) -> str:
    """Where the resolver asks a name with these upstreams: the most specific [/domain/] line, else the plain ones."""
    by_domain: dict[str, str] = {}
    plain: list[str] = []
    for line in servers:
        if line.startswith("[/"):
            domains, _, asked = line[2:].partition("/]")
            for domain in domains.split("/"):
                by_domain[domain] = asked
        else:
            plain.append(line)
    asked = next((by_domain[name] for name in suffixes(host) if name in by_domain), " ".join(plain))
    return EXIT if "1.1.1.1" in asked else DIRECT


def t_dns() -> None:
    print("\n— DNS групп: имя спрашивается там же, куда идёт трафик (режим «по списку»)")
    import asyncio

    from awgscale.agent.sites.scoped import GroupDns

    class Listed:
        def __init__(self, rules: list[dict]) -> None:
            self.rules = rules

        async def read(self) -> list[dict]:
            return self.rules

    class Facts:
        def __init__(self, hand: list[str]) -> None:
            self.hand = hand

        def mode(self) -> RouteMode:
            return RouteMode.LIST

        def hand_names(self) -> list[str]:
            return self.hand

    chance = random.Random(99)
    wrong: list[str] = []
    checked = 0
    for _ in range(ROUNDS // 4):
        rules, hand = random_rules(chance)
        routes = asyncio.run(GroupDns(Listed(rules), Facts(hand)).of())
        for group, dns in routes.items():
            for host in (*POOL, *("q." + name for name in POOL)):
                checked += 1
                expected = meant(rules, group, host, hand, RouteMode.LIST)
                got = asked_via(list(dns.upstreams), host)
                if expected != got and len(wrong) < 5:
                    wrong.append(f"{group} {host}: трафик {expected}, DNS {got}; hand={hand} rules={rules}")
    ok(not wrong, f"{checked} проверок (группа × имя): DNS группы — по её списку", "\n".join(wrong))
    rules = [{"id": "s00001", "name": "Детям x", "on": True, "route": EXIT, "groups": ["g00001"], "items": ["x.com"]},
             {"id": "a00001", "name": "m.x напрямую", "on": True, "route": DIRECT, "items": ["m.x.com"]}]
    dns = asyncio.run(GroupDns(Listed(rules), Facts([])).of())["g00001"]
    ok(asked_via(list(dns.upstreams), "m.x.com") == EXIT, "правило для всех «напрямую» ниже правила группы «за границу»: у группы имя спрашивается через выход", dns.upstreams)


ADDRESS_OF = {None: "10.0.0.9", "g00001": "10.0.0.1", "g00002": "10.0.0.2", "g00003": "10.0.0.3"}


def blocked_by(lines: list[str], address: str, host: str) -> bool:
    """What AdGuard does with the blocker's lines for one person: ||name^ for everybody, $client='a'|'b' for those,
    $client=~'a'|~'b' for everybody but those (checked on the pinned version in tests/stack.sh)."""
    for line in lines:
        rule, _, clients = line.partition("$client=")
        entry = rule.removeprefix("||").removesuffix("^")
        if not covers(entry, host):
            continue
        if not clients:
            return True
        named = [one.strip("~'") for one in clients.split("|")]
        if clients.startswith("~") and address not in named or not clients.startswith("~") and address in named:
            return True
    return False


def t_block() -> None:
    print(f"\n— блокировка: {ROUNDS // 4} случайных списков, каждый человек и каждое имя — как в списке его группы")
    import asyncio
    from pathlib import Path

    from awgscale.agent.block.service import BlockService
    from awgscale.agent.rules.compile import block_covered, without
    from awgscale.agent.rules.scoped import view
    from awgscale.agent.rules.store import blocks_of

    class Members:
        async def addresses(self, users=None):
            return {group: [address] for group, address in ADDRESS_OF.items() if group is not None}

    class Blocker:
        lines: list[str] = []

        async def set_rules(self, lines):
            Blocker.lines = list(lines)

    class Rules:
        def __init__(self, rules):
            self.rules = rules

        async def read(self):
            return self.rules

        async def preview(self, part, records):
            return self.rules

    chance = random.Random(11)
    wrong: list[str] = []
    checked = 0
    for _ in range(ROUNDS // 4):
        rules, _hand = random_rules(chance)
        asyncio.run(BlockService(Blocker(), None, None, None, Path("/nonexistent"), rules=Rules(rules), members=Members()).follow())
        for group in (None, *GROUPS):
            seen = view(rules, group)
            acting = [rule for rule in without(blocks_of(seen), block_covered(seen)) if rule.get("on")]
            for host in (*POOL, *("q." + name for name in POOL), "api1.x.com", "s1.m.x.com"):
                checked += 1
                expected = any(covers(entry, host) for rule in acting for entry in rule.get("items") or [])
                got = blocked_by(Blocker.lines, ADDRESS_OF[group], host)
                if expected != got and len(wrong) < 5:
                    wrong.append(f"{group} {host}: должно {expected}, вышло {got}; lines={Blocker.lines} rules={rules}")
    ok(not wrong, f"{checked} проверок (человек × имя): блокирует то же, что список его группы", "\n".join(wrong))


def main() -> int:
    t_examples()
    t_random()
    t_approximations()
    t_block()
    t_dns()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
