#!/usr/bin/env python3
"""The rules as one list (agent/rules): where each leads, the order that decides, what a rule above covers, and the
change of the canvas — stored whole, followed by the routing and the blocker, put back whole when they refuse.

    uv run python tests/agent_rules.py
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from awgscale.agent.commands import RuleRoute, RuleRoutesSet
from awgscale.agent.errors import Refused
from awgscale.agent.journal.texts import describe
from awgscale.agent.payloads import encode
from awgscale.agent.rules.compile import block_covered, routing_covered, shown_covered, without
from awgscale.agent.rules.service import RulesService
from awgscale.agent.rules.store import Part, blocks_of, merged, rules_of, sites_of
from awgscale.agent.users.models import StoredGroups, version_of

PASS = 0
FAIL = 0


def ok(condition: object, what: str, detail: object = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print("ok   " + what)
    else:
        FAIL += 1
        print("FAIL " + what + (f" → {detail}" if detail != "" else ""))


def rule(rule_id: str, name: str, route: str, items: list[str], on: bool = True, **more: object) -> dict:
    return {"id": rule_id, "name": name, "on": on, "route": route, "items": items, **more}


class World:
    """The list as stored, and what the routing and the blocker were last made to follow."""

    def __init__(self) -> None:
        self.rules = [
            rule("a00001", "YouTube", "exit", ["youtube.com", "ytimg.com"]),
            rule("a00002", "Работа", "exit", ["notion.so", "10.20.0.0/16"], on=False),
            rule("a00003", "Соцсети", "exit", ["vk.com", "tiktok.com"]),
            rule("a00004", "Список", "exit", ["a.example"], url="https://example.com/list.txt"),
            rule("b00001", "Свои", "block", ["casino.example"]),
        ]
        self.saves = 0
        self.followed: list[list[str]] = []
        self.refuse_routing = 0          # how many next follows of the routing refuse
        self.refuse_blocking = False
        self.features: frozenset[str] = frozenset()     # what the watch service says it can do
        self.features_asked = 0
        self.single = False
        self.refuse_scoped = 0           # how many next loads of the chain of the rules for groups refuse
        self.scoped_followed: list[list[str]] = []
        self.nudged = 0


class Store:
    def __init__(self, world: World) -> None:
        self.world = world

    async def read(self) -> list[dict]:
        return [dict(item) for item in self.world.rules]

    async def save_all(self, rules: list[dict]) -> None:
        self.world.saves += 1
        self.world.rules = [dict(item) for item in rules]


class Routing:
    def __init__(self, world: World) -> None:
        self.world = world

    async def reapply(self) -> bool:
        if self.world.refuse_routing:
            self.world.refuse_routing -= 1
            raise Refused("после применения DNS не отвечает")
        self.world.followed.append([item["id"] for item in self.world.rules])
        return True


class Blocking:
    def __init__(self, world: World) -> None:
        self.world = world

    async def follow(self) -> None:
        if self.world.refuse_blocking:
            raise Refused("блокировщик не отвечает")


class People:
    """The groups of people, as stored."""

    async def read(self):
        return StoredGroups.of([{"id": "g00001", "name": "Дети", "members": ["kid"]}, {"id": "g00002", "name": "Дом", "members": ["mum"]}])


class Scoped:
    """The watch service's chain of the rules for some groups (rules/routes.ScopedRoutes)."""

    def __init__(self, world: World) -> None:
        self.world = world

    async def follow(self) -> None:
        if self.world.refuse_scoped:
            self.world.refuse_scoped -= 1
            raise Refused("правила для групп не загрузились: сторож отказал")
        self.world.scoped_followed.append([item["id"] for item in self.world.rules if item.get("groups") and item.get("route") != "block"])


class Features:
    def __init__(self, world: World) -> None:
        self.world = world

    async def features(self, timeout: float) -> frozenset[str]:
        self.world.features_asked += 1
        return self.world.features


class Profiles:
    def __init__(self, world: World) -> None:
        self.world = world

    def nudge(self) -> None:
        self.world.nudged += 1


def service(world: World) -> RulesService:
    return RulesService(
        store=Store(world), routing=Routing(world), blocking=Blocking(world), lock_file=Path(tempfile.mkdtemp()) / ".rules.lock", groups=People(),
        scoped=Scoped(world), features=Features(world), single=lambda: world.single, profiles=Profiles(world),
    )


def ask(world: World, changes: list[tuple] = (), order: list[str] | None = None, base: str | None = None) -> object:
    """`changes`: (rule, route) or (rule, route or None, [group ids])."""
    payload: dict = {"changes": [{"rule": change[0], **({"route": change[1].value} if change[1] else {}), **({"groups": change[2]} if len(change) > 2 else {})} for change in changes]}
    if order is not None:
        payload["order"] = order
    if base is not None:
        payload["base"] = base
    try:
        return asyncio.run(service(world).route(RuleRoutesSet(payload=encode(payload)))).wire()
    except Refused as refused:
        return refused


def named(world: World, name: str) -> dict:
    return next(item for item in world.rules if item["name"] == name)


def t_routes() -> None:
    print("— куда ведёт правило: поле правила, id тот же")
    world = World()
    answer = ask(world, [("a00003", RuleRoute.BLOCK)])
    ok(isinstance(answer, dict) and named(world, "Соцсети")["route"] == "block" and named(world, "Соцсети")["id"] == "a00003" and world.saves == 1 and len(world.followed) == 1,
       "«Соцсети» в блок: одно сохранение списка, id тот же, маршрутизация и блокировщик пересобраны", [answer, world.saves])
    ok(answer["rules"] == [{"rule": "a00003", "name": "Соцсети", "route": "block", "groups": []}] and answer["v"] == version_of(world.rules), "ответ: правило, куда ведёт, новая версия", answer)
    ask(world, [("a00003", RuleRoute.DIRECT)])
    ok(named(world, "Соцсети")["route"] == "direct" and named(world, "Соцсети")["on"] is True, "и напрямую")
    ask(world, [("a00003", RuleRoute.OFF)])
    ok(named(world, "Соцсети")["on"] is False and named(world, "Соцсети")["route"] == "direct", "выключено — «куда» запомнено (включат — снова напрямую)")
    ask(world, [("a00003", RuleRoute.EXIT), ("b00001", RuleRoute.OFF), ("a00002", RuleRoute.EXIT)])
    ok(named(world, "Соцсети")["route"] == "exit" and named(world, "Свои")["on"] is False and named(world, "Работа")["on"] is True and world.saves == 4,
       "три правила сразу — одно сохранение", world.saves)
    for rule_id, route, why in (("a00002", RuleRoute.BLOCK, "сеть 10.20.0.0/16"), ("a00002", RuleRoute.DIRECT, "сеть 10.20.0.0/16"), ("a00004", RuleRoute.BLOCK, "по ссылке")):
        answer = ask(World(), [(rule_id, route)])
        ok(isinstance(answer, Refused) and why in answer.error, f"{route.value} нельзя: {why}", answer)
    answer = ask(World(), [("zzzzzz", RuleRoute.EXIT)])
    ok(isinstance(answer, Refused) and "больше нет" in answer.error, "правила с таким id нет — отказ", answer)
    answer = ask(World(), [("a00001", RuleRoute.OFF), ("a00001", RuleRoute.EXIT)])
    ok(isinstance(answer, Refused) and answer.error == "некорректные данные", "одно правило дважды — отказ", answer)
    world = World()
    answer = ask(world, [("a00001", RuleRoute.OFF), ("a00002", RuleRoute.BLOCK)])
    ok(isinstance(answer, Refused) and world.saves == 0 and named(world, "YouTube")["on"] is True, "одно из правил нельзя: ничего не сохраняется", [answer, world.saves])


def t_order() -> None:
    print("\n— порядок")
    world = World()
    ids = [item["id"] for item in world.rules]
    answer = ask(world, order=[ids[4], *ids[:4]])
    ok(isinstance(answer, dict) and [item["id"] for item in world.rules] == [ids[4], *ids[:4]] and answer["ordered"] is True and world.followed,
       "«Свои» наверх: список в новом порядке, мир пересобран", [item["name"] for item in world.rules])
    answer = ask(world, order=ids[:4])
    ok(isinstance(answer, Refused) and "устарел" in answer.error, "в порядке не все правила (страница устарела) — отказ", answer)
    answer = ask(world, [("a00001", RuleRoute.OFF)], base="000000000000")
    ok(isinstance(answer, Refused) and answer.beside.get("conflict") is True and answer.beside.get("v") == version_of(world.rules) and named(world, "YouTube")["on"] is True,
       "версия, которую страница видела, устарела: отказ с новым списком, ничего не сохранено", answer)
    answer = ask(world, [("a00001", RuleRoute.OFF)], base=version_of(world.rules))
    ok(isinstance(answer, dict), "с нынешней версией — принято")
    answer = ask(World())
    ok(isinstance(answer, Refused), "ни правил, ни порядка — отказ")


def t_back() -> None:
    print("\n— маршрутизация не поднялась: прежний список вернулся целиком")
    world = World()
    before = [dict(item) for item in world.rules]
    world.refuse_routing = 1
    answer = ask(world, [("a00003", RuleRoute.DIRECT)], order=[item["id"] for item in reversed(world.rules)])
    ok(isinstance(answer, Refused) and "возвращено как было" in answer.error and world.rules == before and world.saves == 2 and world.followed == [[item["id"] for item in before]],
       "отказ, список сохранён прежним и мир пересобран по нему", [answer, world.saves])
    world = World()
    world.refuse_routing = 2
    answer = ask(world, [("a00003", RuleRoute.DIRECT)])
    ok(isinstance(answer, Refused) and "не применились" in answer.error and world.rules[2]["route"] == "exit", "и вернуть не вышло — сказано прямо, хранится прежний список", answer)
    world = World()
    world.refuse_blocking = True
    answer = ask(world, [("a00003", RuleRoute.BLOCK)])
    ok(isinstance(answer, Refused) and world.rules[2]["route"] == "exit", "блокировщик не принял — тоже всё назад", answer)


def t_covered() -> None:
    print("\n— что покрывает правило выше")
    rules = [
        rule("r1", "Банки напрямую", "direct", ["sber.ru"]),
        rule("r2", "Видео", "exit", ["youtube.com", "sber.ru", "m.sber.ru"]),
        rule("r3", "Соцсети в блок", "block", ["vk.com", "m.youtube.com", "x.example"]),
        rule("r4", "Под доменом", "direct", ["music.youtube.com"]),
        rule("r5", "Выключено", "exit", ["vk.com"], on=False),
        rule("r6", "Ниже выключенного", "exit", ["vk.com"]),
        rule("r7", "Список", "exit", ["youtube.com"], url="https://example.com/l.txt"),
    ]
    routing = routing_covered(rules)
    ok(routing == {"r2": {"sber.ru": "Банки напрямую", "m.sber.ru": "Банки напрямую"}, "r4": {"music.youtube.com": "Видео"}},
       "маршрутизация: правило ниже не перебивает выше (и поддомен под доменом выше); блок выше не убирает имя из маршрутов, выключенное и список по ссылке не в счёт", routing)
    blocking = block_covered(rules)
    ok(blocking == {"r3": {"m.youtube.com": "Видео"}}, "блокировка: «за границу» выше снимает блок с поддомена ниже", blocking)
    ok(shown_covered(rules) == {**routing, **blocking}, "страницам — оба вида")
    acting = without(rules, routing)
    ok(acting[1]["items"] == ["youtube.com"] and acting[0] is rules[0], "действует: без покрытых записей, нетронутые правила — те же")


def t_list() -> None:
    print("\n— один список правил: перенос и два вида")
    sites = [{"id": "a00001", "name": "YouTube", "on": True, "items": ["youtube.com"]},
             {"id": "a00002", "name": "Банки", "on": True, "direct": True, "items": ["sber.ru"]},
             {"id": "a00003", "name": "Список", "on": True, "url": "https://example.com/l.txt", "items": ["a.example"], "updated": 1700000000, "error": "", "skipped": 2},
             {"id": "a00004", "name": "Telegram", "on": False, "auto": "telegram", "items": ["t.me", "149.154.160.0/20"]}]
    blocks = [{"id": "b00001", "name": "Свои", "on": True, "items": ["ads.example"], "kind": "block"},
              {"id": "allow", "name": "Исключения", "on": True, "items": ["ok.example"], "kind": "allow"},
              {"id": "a00001", "name": "Совпал id", "on": False, "items": ["x.example"], "kind": "block"}]
    rules = rules_of(sites, blocks)
    ok([(item["name"], item["route"]) for item in rules] == [("YouTube", "exit"), ("Банки", "direct"), ("Список", "exit"), ("Telegram", "exit"), ("Свои", "block"), ("Совпал id", "block")],
       "перенос: сначала группы сайтов по порядку (с «напрямую»), потом блокировка; старые «Исключения» отброшены")
    ok(len({item["id"] for item in rules}) == len(rules) and rules[-1]["id"] != "a00001", "id группы блокировки, совпавший с id группы сайтов, заменён")
    ok(sites_of(rules) == sites and blocks_of(rules)[0] == blocks[0], "виды возвращают ровно то, из чего собраны")
    again = merged(rules, Part.SITES, [{**sites[0], "on": False}, *sites[1:], {"id": "n00001", "name": "Новая", "on": True, "items": ["new.example"]}])
    ok([item["name"] for item in again] == ["YouTube", "Банки", "Список", "Telegram", "Новая", "Свои", "Совпал id"] and again[0]["on"] is False and blocks_of(again) == blocks_of(rules),
       "сохранение «Сайтов»: на свои места, новая — за последней своей, блокировка не тронута")
    interleaved = [rules[0], rules[4], rules[1]]
    swapped = merged(interleaved, Part.SITES, [sites_of(interleaved)[1], sites_of(interleaved)[0]])
    ok([item["name"] for item in swapped] == ["Банки", "Свои", "YouTube"], "часть занимает свои места в своём порядке, чужое правило между ними стоит где стояло")
    ok([item["route"] for item in merged(rules, Part.BLOCK, [])] == ["exit", "direct", "exit", "exit"], "блокировка пуста — её правила ушли, остальные на местах")
    try:
        merged(rules, Part.SITES, [*sites, {"id": "b00001", "name": "Чужой id", "on": True, "items": ["c.example"]}])
        clash = None
    except Exception as e:
        clash = e
    ok(clash is not None and "другого вида" in str(clash), "новая группа сайтов с id правила блокировки — отказ, не подмена")


def t_scope() -> None:
    print("\n— правило для выбранных групп людей")
    world = World()
    answer = ask(world, [("a00003", RuleRoute.BLOCK, ["g00001"])])
    ok(isinstance(answer, dict) and named(world, "Соцсети")["groups"] == ["g00001"] and named(world, "Соцсети")["route"] == "block"
       and answer["rules"][0]["groups"] == ["Дети"], "«Соцсети» — блок только для «Дети»: группа записана, ответ называет её", answer)
    ask(world, [("a00003", None, [])])
    ok("groups" not in named(world, "Соцсети") and named(world, "Соцсети")["route"] == "block", "«для всех» — пометка снята, «куда» не тронуто")
    answer = ask(World(), [("a00003", RuleRoute.BLOCK, ["g99999"])])
    ok(isinstance(answer, Refused) and "группы людей больше нет" in answer.error, "неизвестная группа — отказ", answer)
    world = World()
    answer = ask(world, [("a00003", RuleRoute.EXIT, ["g00001"])])
    ok(isinstance(answer, Refused) and "после обновления сторожа" in answer.error and world.features_asked == 1 and world.saves == 0,
       "сторож прежней версии: «за границу» для группы — отказ, сказано почему, ничего не сохранено", answer)
    world = World()
    world.features = frozenset({"scoped"})
    world.single = True
    answer = ask(world, [("a00003", RuleRoute.DIRECT, ["g00001"])])
    ok(isinstance(answer, Refused) and "без выходов" in answer.error, "один сервер без выходов: «напрямую» для группы — отказ", answer)
    world = World()
    world.features = frozenset({"scoped"})
    answer = ask(world, [("a00003", RuleRoute.EXIT, ["g00001"])])
    ok(isinstance(answer, dict) and named(world, "Соцсети")["groups"] == ["g00001"] and named(world, "Соцсети")["route"] == "exit"
       and world.scoped_followed == [["a00003"]] and world.nudged == 1 and answer["rules"][0] == {"rule": "a00003", "name": "Соцсети", "route": "exit", "groups": ["Дети"]},
       "сторож умеет: «Соцсети» за границу только для «Дети» — цепочка сторожа загружена, клиенты групп подтолкнуты после замка", answer)
    asked = world.features_asked
    ask(world, [("a00001", RuleRoute.OFF)])
    ok(world.features_asked == asked and world.nudged == 2, "правка, от которой правило для групп не появляется, сторожа не спрашивает")
    answer = ask(world, [("a00003", RuleRoute.DIRECT)])
    ok(isinstance(answer, dict) and named(world, "Соцсети")["route"] == "direct" and named(world, "Соцсети")["groups"] == ["g00001"], "и «напрямую» для той же группы", answer)
    world = World()
    world.features = frozenset({"scoped"})
    world.refuse_scoped = 1
    before = [dict(item) for item in world.rules]
    answer = ask(world, [("a00003", RuleRoute.EXIT, ["g00001"])])
    ok(isinstance(answer, Refused) and "возвращено как было" in answer.error and world.rules == before and world.nudged == 0,
       "сторож не загрузил цепочку — прежний список сохранён и применён снова", answer)
    world = World()
    world.features = frozenset({"scoped"})
    answer = ask(world, [("a00004", RuleRoute.EXIT, ["g00001"])])
    ok(isinstance(answer, Refused) and "по ссылке" in answer.error, "список по ссылке для группы не направить", answer)
    answer = ask(World(), [("a00002", RuleRoute.BLOCK, ["g00001"])])
    ok(isinstance(answer, Refused) and "сеть" in answer.error, "для группы — только сайты, без сетей", answer)
    rules = [rule("r1", "Соцсети детям", "block", ["vk.com"], groups=["g00001"]), rule("r2", "Соцсети всем", "exit", ["vk.com"]), rule("r3", "Всем блок", "block", ["vk.com"])]
    ok(routing_covered(rules) == {} and block_covered(rules) == {"r3": {"vk.com": "Соцсети всем"}},
       "правило для группы никого не перекрывает: ниже «за границу» для всех действует, а блок для всех перекрыт им")
    rules = [rule("r1", "Всем за границу", "exit", ["vk.com"]), rule("r2", "Детям блок", "block", ["vk.com"], groups=["g00001"])]
    ok(block_covered(rules) == {"r2": {"vk.com": "Всем за границу"}}, "правило для всех выше перекрывает правило для группы ниже")
    kept = merged([rule("b1", "Соцсети", "block", ["vk.com"], groups=["g00001"])], Part.BLOCK, [{"id": "b1", "name": "Соцсети!", "on": True, "items": ["vk.com"], "kind": "block"}])
    ok(kept[0]["groups"] == ["g00001"] and kept[0]["name"] == "Соцсети!", "сохранение «Блокируется», которое не знает о группах, их не стирает", kept)


def t_scoped_files() -> None:
    print("\n— что получает сторож: цепочка правил для групп и люди групп")
    from awgscale.agent.rules.scoped import members_text, scoped_names, scoped_rules_text, set_id
    from awgscale.agent.users.models import KnownUser, UserGroup
    rules = [
        rule("s00001", "Детям YouTube напрямую", "direct", ["youtube.com"], groups=["g00001"]),
        rule("a00001", "YouTube", "exit", ["youtube.com", "m.youtube.com"]),
        rule("s00002", "Дому x", "exit", ["x.example"], groups=["g00002", "g00001"]),
        rule("s00003", "Выключено", "exit", ["off.example"], groups=["g00002"], on=False),
        rule("b00001", "Блок детям", "block", ["casino.example"], groups=["g00001"]),
    ]
    ok(scoped_rules_text(rules) == "rs_s00001 direct rg_g00001\nrs_s00002 exit rg_g00002 rg_g00001\n", "по строке на включённое правило для групп, что ведёт куда-то, в порядке списка",
       scoped_rules_text(rules))
    pairs = [pair.line() for pair in scoped_names(rules)]
    ok(pairs == ["rs_s00001 m.youtube.com", "rs_s00001 youtube.com", "rs_s00002 x.example"], "имена: свои и те имена для всех, что под ними и не решены правилом выше", pairs)
    ok(set_id("g00001") == "g00001" and len(set_id("Not An Id!")) == 6 and set_id("Not An Id!") == set_id("Not An Id!"), "id в имени набора: как есть или шесть знаков отпечатка")
    groups = [UserGroup(id="g00001", name="Дети", members=("kid", "gone")), UserGroup(id="g00002", name="Дом", members=("mum", "dad")), UserGroup(name="без id", members=("x",))]
    users = [KnownUser(name="kid", ip="172.16.17.9"), KnownUser(name="mum", ip="172.16.17.12"), KnownUser(name="dad", ip="172.16.17.3")]
    ok(members_text(groups, users) == "rg_g00001 172.16.17.9\nrg_g00002 172.16.17.3 172.16.17.12\n", "люди групп: адреса тех, кого знает сервис пользователей", members_text(groups, users))
    ok(members_text(groups, []) is None, "список пользователей не прочитан — ничего не меняется (не «все ушли»)")

    from awgscale.agent.ports import Outcome
    from awgscale.agent.rules.routes import ScopedMembers, ScopedRoutes
    folder = Path(tempfile.mkdtemp())

    class Watch:
        def __init__(self) -> None:
            self.asked: list[str] = []
            self.refuse = False

        async def scoped(self, timeout: float) -> Outcome:
            self.asked.append("scoped")
            return Outcome(not self.refuse, "сторож отказал" if self.refuse else "")

        async def features(self, timeout: float) -> frozenset[str]:
            return frozenset({"scoped"})

        async def pin(self, timeout: float) -> Outcome:
            self.asked.append("pin")
            return Outcome(not self.refuse)

    class Listed:
        def __init__(self, rules: list[dict]) -> None:
            self.rules = rules

        async def read(self) -> list[dict]:
            return self.rules

        async def preview(self, part: object, records: object) -> list[dict]:
            return self.rules

    watch = Watch()
    listed = Listed([rule("a00001", "Всем", "exit", ["a.example"])])
    chain = ScopedRoutes(listed, watch, folder / "scoped.rules")
    asyncio.run(chain.follow())
    ok(watch.asked == [] and not (folder / "scoped.rules").exists(), "правил для групп нет — сторожа не спрашивают (сторож прежней версии не мешает)")
    listed.rules = rules
    watch.refuse = True
    try:
        asyncio.run(chain.follow())
        refused = None
    except Refused as e:
        refused = e
    ok(refused is not None and "не загрузились" in refused.error and (folder / "scoped.rules").read_text() == scoped_rules_text(rules), "сторож отказал — отказ словами, файл записан", refused)
    watch.refuse = False
    asyncio.run(chain.follow())
    asyncio.run(chain.follow())
    ok(watch.asked == ["scoped", "scoped"], "после отказа — загружается снова и при том же файле; потом без перемен не спрашивают", watch.asked)

    class Groups:
        groups: list = []

        async def read(self) -> StoredGroups:
            return StoredGroups.of([{"id": group.id, "name": group.name, "members": list(group.members)} for group in Groups.groups if group.id])

    Groups.groups = groups
    watch = Watch()
    people = ScopedMembers(Groups(), watch, folder / "scoped.members")
    asyncio.run(people.check(users))
    asyncio.run(people.check(users))
    ok(watch.asked == ["pin"] and (folder / "scoped.members").read_text() == members_text(groups[:2], users), "люди групп записаны, проход сторожа один; без перемен — ни записи, ни прохода", watch.asked)
    Groups.groups = [UserGroup(id="g00001", name="Дети", members=("kid", "mum")), UserGroup(id="g00002", name="Дом", members=("dad",))]
    asyncio.run(people.check(users))
    ok(watch.asked == ["pin", "pin"] and "rg_g00001 172.16.17.9 172.16.17.12" in (folder / "scoped.members").read_text(), "человек перешёл в другую группу — файл и проход (замков не берёт)")
    watch.refuse = True
    Groups.groups = [UserGroup(id="g00001", name="Дети", members=("kid",)), UserGroup(id="g00002", name="Дом", members=("dad", "mum"))]
    asyncio.run(people.check(users))
    watch.refuse = False
    asyncio.run(people.check(users))
    ok(watch.asked == ["pin", "pin", "pin", "pin"], "проход не удался — при следующем взгляде просят снова")
    asyncio.run(people.check([]))
    ok(len(watch.asked) == 4, "список пользователей не прочитан — ничего не трогают")


def t_journal() -> None:
    print("\n— журнал")
    reply = {"ok": True, "rules": [{"rule": "a00003", "name": "Соцсети", "route": "block"}, {"rule": "a00001", "name": "YouTube", "route": "off"}], "ordered": True, "v": "x"}
    said = describe(["rule-route", "eyJ9"], None, reply)
    ok(said == ("Правила: «Соцсети» — блокируется; «YouTube» — выключено; изменён порядок", False), "в журнале — какие правила куда, и что порядок изменён", said)
    ok(describe(["rule-route", "eyJ9"], None, {"ok": True, "rules": [], "ordered": True, "v": "x"}) == ("Правила: изменён порядок", False), "только порядок")
    said = describe(["rule-route", "eyJ9"], None, {"ok": True, "rules": [{"rule": "a", "name": "Соцсети", "route": "block", "groups": ["Дети", "Дом"]}], "v": "x"})
    ok(said == ("Правила: «Соцсети» — блокируется (только: Дети, Дом)", False), "и для каких групп", said)
    ok(describe(["rule-route", "eyJ9"], None, {"ok": False, "error": "нет"}) == (None, False), "отказ в журнал не пишется (его видит тот, кто просил)")


def main() -> int:
    t_routes()
    t_order()
    t_back()
    t_covered()
    t_scope()
    t_list()
    t_scoped_files()
    t_journal()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
