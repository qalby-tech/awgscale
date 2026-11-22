#!/usr/bin/env python3
"""Where a rule leads (agent/rules): a group of sites goes abroad, is blocked, or is off — and moves between the
list of sites and the blocking through the saves the pages use, the second step undone when the first was refused.

    uv run python tests/agent_rules.py
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from awgscale.agent.commands import BlockSet, RuleRoute, RuleRoutesSet, SitesSet
from awgscale.agent.errors import Refused
from awgscale.agent.journal.texts import describe
from awgscale.agent.payloads import decode, encode
from awgscale.agent.rules.service import RulesService
from awgscale.agent.rules.store import Part, blocks_of, merged, rules_of, sites_of
from awgscale.agent.sites.groups import version_of_sites
from awgscale.agent.sites.models import SiteGroup

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


class World:
    """Both lists, kept as the real saves keep them: a version for the sites, fresh ids for what comes in."""

    def __init__(self) -> None:
        self.sites = [
            SiteGroup(id="a00001", name="YouTube", on=True, items=("youtube.com", "ytimg.com")),
            SiteGroup(id="a00002", name="Работа", on=False, items=("notion.so", "10.20.0.0/16")),
            SiteGroup(id="a00003", name="Соцсети", on=True, items=("vk.com", "*.tiktok.com")),
            SiteGroup(id="a00004", name="Список", on=True, url="https://example.com/list.txt", items=("a.example",)),
        ]
        self.blocks: list[dict] = [{"id": "b00001", "name": "Свои", "on": True, "items": ["casino.example"], "kind": "block"}]
        self.refuse_sites = False
        self.refuse_blocks = False
        self.sites_saves_left: int | None = None        # after this many saves of the sites, every next one is refused
        self.saves: list[str] = []
        self.counter = 0

    def fresh(self) -> str:
        self.counter += 1
        return f"n{self.counter:05d}"


class Sites:
    def __init__(self, world: World) -> None:
        self.world = world

    async def read(self) -> list[SiteGroup]:
        return list(self.world.sites)

    async def save(self, request: SitesSet) -> object:
        world = self.world
        data = decode(request.payload)
        world.saves.append("sites")
        if data.get("base") != version_of_sites(world.sites):
            raise Refused("сайты изменились в другом окне", conflict=True)
        if world.sites_saves_left is not None:
            if world.sites_saves_left <= 0:
                raise Refused("не удалось применить")
            world.sites_saves_left -= 1
        if world.refuse_sites:
            raise Refused("не удалось применить")
        known = {group.id for group in world.sites}
        groups = []
        for sent in data["groups"]:
            group_id = sent.get("id") if sent.get("id") in known else world.fresh()
            groups.append(SiteGroup(**{**sent, "id": group_id, "items": tuple(sent.get("items") or ())}))
        world.sites = groups

        class Shown:
            @staticmethod
            def wire() -> dict:
                return {"ok": True, "groups": [group.wire() for group in groups]}

        return Shown()


class Blocks:
    def __init__(self, world: World) -> None:
        self.world = world

    async def read(self) -> list[dict]:
        return [dict(group) for group in self.world.blocks]

    async def save(self, request: BlockSet) -> object:
        world = self.world
        data = decode(request.payload)
        world.saves.append("block")
        if world.refuse_blocks:
            raise Refused("блокировщик не принял изменения")
        known = {group.get("id") for group in world.blocks}
        world.blocks = [{**sent, "id": sent.get("id") if sent.get("id") in known else world.fresh(), "kind": "block"} for sent in data["groups"]]

        class Shown:
            groups = world.blocks

        return Shown()


def service(world: World) -> RulesService:
    lock = Path(tempfile.mkdtemp()) / ".rules.lock"
    return RulesService(sites_store=Sites(world), block_store=Blocks(world), sites=Sites(world), block=Blocks(world), lock_file=lock)


def routes(world: World, *changes: tuple[str, RuleRoute]) -> object:
    payload = encode({"changes": [{"rule": rule, "route": where.value} for rule, where in changes]})
    try:
        return asyncio.run(service(world).route(RuleRoutesSet(payload=payload))).wire()
    except Refused as refused:
        return refused


def route(world: World, rule: str, where: RuleRoute) -> object:
    """One change; → the rule's own answer (or the refusal)."""
    answer = routes(world, (rule, where))
    return answer if isinstance(answer, Refused) else {"ok": answer["ok"], **answer["rules"][0]}


def site(world: World, name: str) -> SiteGroup | None:
    return next((group for group in world.sites if group.name == name), None)


def block(world: World, name: str) -> dict | None:
    return next((group for group in world.blocks if group.get("name") == name), None)


def main() -> int:
    print("— включить и выключить в своём списке")
    world = World()
    answer = route(world, "a00001", RuleRoute.OFF)
    ok(isinstance(answer, dict) and answer == {"ok": True, "rule": "a00001", "now": "a00001", "name": "YouTube", "route": "off"} and site(world, "YouTube").on is False and world.saves == ["sites"],
       "«YouTube» выключено: одно сохранение сайтов, id тот же", answer)
    answer = route(world, "a00001", RuleRoute.OFF)
    ok(isinstance(answer, dict) and world.saves == ["sites"], "уже выключено: ничего не сохраняется", world.saves)
    route(world, "a00001", RuleRoute.EXIT)
    ok(site(world, "YouTube").on is True and [group.name for group in world.sites][0] == "YouTube", "снова за границу, на своём месте в списке")
    answer = route(world, "b00001", RuleRoute.OFF)
    ok(isinstance(answer, dict) and block(world, "Свои")["on"] is False and world.saves[-1] == "block", "группа блокировки выключена: одно сохранение блокировки", answer)

    print("\n— из сайтов в блокировку и обратно")
    world = World()
    answer = route(world, "a00003", RuleRoute.BLOCK)
    moved = block(world, "Соцсети")
    ok(isinstance(answer, dict) and moved and moved["on"] is True and moved["items"] == ["vk.com", "*.tiktok.com"] and site(world, "Соцсети") is None,
       "«Соцсети» перенесены в блокировку: там включены с теми же записями, из сайтов убраны", [answer, world.blocks])
    ok(world.saves == ["block", "sites"] and answer["now"] == moved["id"] and answer["route"] == "block",
       "сначала блокировка, потом сайты; ответ называет новый id", [world.saves, answer])
    answer = route(world, moved["id"], RuleRoute.EXIT)
    back = site(world, "Соцсети")
    ok(isinstance(answer, dict) and back and back.on and back.items == ("vk.com", "*.tiktok.com") and block(world, "Соцсети") is None and answer["now"] == back.id,
       "и обратно за границу: снова в сайтах, включены, из блокировки убраны", [answer, world.sites])
    ok(world.saves[-2:] == ["sites", "block"], "сначала сайты, потом блокировка (из неё — убрать перенесённое)", world.saves)
    off_block = route(world, "a00002", RuleRoute.BLOCK)
    ok(isinstance(off_block, Refused) and "сеть 10.20.0.0/16" in off_block.error and site(world, "Работа") is not None,
       "группа с сетью в блокировку не переносится — сказано, какая сеть мешает", off_block)
    by_link = route(world, "a00004", RuleRoute.BLOCK)
    ok(isinstance(by_link, Refused) and "по ссылке" in by_link.error, "список по ссылке в блокировку не переносится", by_link)

    print("\n— напрямую")
    world = World()
    answer = route(world, "a00003", RuleRoute.DIRECT)
    ok(isinstance(answer, dict) and site(world, "Соцсети").direct is True and site(world, "Соцсети").on is True and world.saves == ["sites"],
       "«Соцсети» напрямую: в своём списке, включены, помечены «напрямую» — одно сохранение сайтов", [answer, world.saves])
    route(world, "a00003", RuleRoute.OFF)
    ok(site(world, "Соцсети").on is False and site(world, "Соцсети").direct is True, "выключены — пометка остаётся (включат — снова напрямую)")
    route(world, "a00003", RuleRoute.EXIT)
    ok(site(world, "Соцсети").on is True and site(world, "Соцсети").direct is False, "за границу — пометка снята")
    answer = route(world, "b00001", RuleRoute.DIRECT)
    ok(isinstance(answer, dict) and site(world, "Свои") and site(world, "Свои").direct is True and block(world, "Свои") is None,
       "группа блокировки напрямую: переехала в сайты с пометкой", [answer, world.sites])
    world = World()
    world.refuse_sites = True
    answer = route(world, "a00001", RuleRoute.DIRECT)
    ok(isinstance(answer, Refused) and site(world, "YouTube").direct is False, "сайты отказали в «напрямую» для включённой группы — это отказ, а не «и так сохранено»", answer)
    for rule, why in (("a00002", "сеть 10.20.0.0/16"), ("a00004", "по ссылке")):
        answer = route(World(), rule, RuleRoute.DIRECT)
        ok(isinstance(answer, Refused) and why in answer.error and "напрямую" in answer.error, f"напрямую нельзя: {why}", answer)

    print("\n— второй шаг отказал: первый отменён")
    world = World()
    world.refuse_sites = True
    answer = route(world, "a00003", RuleRoute.BLOCK)
    ok(isinstance(answer, Refused) and "возвращено как было" in answer.error and block(world, "Соцсети") is None and site(world, "Соцсети") is not None and world.saves == ["block", "sites", "block"],
       "сайты не приняли: из блокировки «Соцсети» убраны обратно, в сайтах остались", [answer, world.saves])
    world = World()
    world.refuse_blocks = True
    answer = route(world, "b00001", RuleRoute.EXIT)
    ok(isinstance(answer, Refused) and site(world, "Свои") is None and block(world, "Свои") is not None and world.saves == ["sites", "block", "sites", "block"] and "возвращено как было" in answer.error,
       "блокировка не приняла: из сайтов «Свои» убраны обратно, в блокировке остались (её откат видит по хранилищу, что она и так прежняя)", [answer, world.saves])
    world = World()
    world.refuse_blocks = True
    answer = route(world, "a00003", RuleRoute.BLOCK)
    ok(isinstance(answer, Refused) and site(world, "Соцсети") is not None and world.saves == ["block"], "первый шаг отказал: второго не было, всё как было", [answer, world.saves])
    print("\n— несколько правил сразу: сайты сохраняются один раз")
    world = World()
    answer = routes(world, ("a00001", RuleRoute.OFF), ("a00002", RuleRoute.EXIT), ("a00003", RuleRoute.BLOCK), ("b00001", RuleRoute.EXIT))
    ok(isinstance(answer, dict) and world.saves == ["block", "sites", "block"],
       "четыре правила (два переключены, одно в блокировку, одно из неё): блокировка, сайты один раз, блокировка", [answer, world.saves])
    ok(site(world, "YouTube").on is False and site(world, "Работа").on is True and site(world, "Свои").on is True and block(world, "Соцсети")["on"] is True
       and block(world, "Свои") is None and site(world, "Соцсети") is None, "каждое правило там, куда его послали", [world.sites, world.blocks])
    nows = {rule["rule"]: rule["now"] for rule in answer["rules"]}
    ok(nows["a00001"] == "a00001" and nows["a00003"] == block(world, "Соцсети")["id"] and nows["b00001"] == site(world, "Свои").id,
       "ответ называет новый id у перенесённых и прежний у остальных", nows)
    world = World()
    answer = routes(world, ("a00001", RuleRoute.OFF), ("b00001", RuleRoute.OFF))
    ok(isinstance(answer, dict) and world.saves == ["block", "sites"], "переключения в обоих списках: по одному сохранению каждого", world.saves)
    world = World()
    answer = routes(world, ("a00001", RuleRoute.OFF), ("a00002", RuleRoute.BLOCK))
    ok(isinstance(answer, Refused) and world.saves == [] and site(world, "YouTube").on is True, "одно из правил нельзя: ничего не сохраняется", [answer, world.saves])
    answer = routes(World(), ("a00001", RuleRoute.OFF), ("a00001", RuleRoute.EXIT))
    ok(isinstance(answer, Refused) and answer.error == "некорректные данные", "одно правило дважды — отказ", answer)

    world = World()
    world.refuse_blocks = True
    world.sites_saves_left = 1
    answer = route(world, "b00001", RuleRoute.EXIT)
    ok(isinstance(answer, Refused) and "и в «Сайтах», и в «Блокируется»" in answer.error and site(world, "Свои") is not None and block(world, "Свои") is not None,
       "и откат не удался: отказ прямо говорит, что правило теперь в обоих списках", answer)

    answer = route(World(), "zzzzzz", RuleRoute.EXIT)
    ok(isinstance(answer, Refused) and "больше нет" in answer.error, "правила с таким id нет — отказ", answer)

    print("\n— один список правил: перенос и два вида")
    sites = [{"id": "a00001", "name": "YouTube", "on": True, "items": ["youtube.com"]},
             {"id": "a00002", "name": "Банки", "on": True, "direct": True, "items": ["sber.ru"]},
             {"id": "a00003", "name": "Список", "on": True, "url": "https://example.com/l.txt", "items": ["a.example"], "updated": 1700000000, "error": "", "skipped": 2},
             {"id": "a00004", "name": "Telegram", "on": False, "auto": "telegram", "items": ["t.me", "149.154.160.0/20"]}]
    blocks = [{"id": "b00001", "name": "Свои", "on": True, "items": ["ads.example"], "kind": "block"},
              {"id": "allow", "name": "Исключения", "on": True, "items": ["ok.example"], "kind": "allow"},
              {"id": "a00001", "name": "Совпал id", "on": False, "items": ["x.example"], "kind": "block"}]
    rules = rules_of(sites, blocks)
    ok([(rule["name"], rule["route"]) for rule in rules] == [("YouTube", "exit"), ("Банки", "direct"), ("Список", "exit"), ("Telegram", "exit"), ("Свои", "block"), ("Совпал id", "block")],
       "перенос: сначала группы сайтов по порядку (с «напрямую»), потом блокировка; старые «Исключения» отброшены", [(rule["name"], rule["route"]) for rule in rules])
    ok(len({rule["id"] for rule in rules}) == len(rules) and rules[-1]["id"] != "a00001", "id группы блокировки, совпавший с id группы сайтов, заменён", [rule["id"] for rule in rules])
    ok(sites_of(rules) == sites and blocks_of(rules)[0] == blocks[0], "виды возвращают ровно то, из чего собраны (и список по ссылке с датой, и Telegram)", sites_of(rules))
    again = merged(rules, Part.SITES, [{**sites[0], "on": False}, *sites[1:], {"id": "n00001", "name": "Новая", "on": True, "items": ["new.example"]}])
    ok([rule["name"] for rule in again] == ["YouTube", "Банки", "Список", "Telegram", "Новая", "Свои", "Совпал id"] and again[0]["on"] is False and blocks_of(again) == blocks_of(rules),
       "сохранение «Сайтов»: на свои места, новая — за последней своей, блокировка не тронута", [rule["name"] for rule in again])
    interleaved = [rules[0], rules[4], rules[1]]
    swapped = merged(interleaved, Part.SITES, [sites_of(interleaved)[1], sites_of(interleaved)[0]])
    ok([rule["name"] for rule in swapped] == ["Банки", "Свои", "YouTube"], "часть занимает свои места в своём порядке, чужое правило между ними стоит где стояло", [rule["name"] for rule in swapped])
    fewer = merged(rules, Part.BLOCK, [])
    ok([rule["route"] for rule in fewer] == ["exit", "direct", "exit", "exit"], "блокировка пуста — её правила ушли, остальные на местах")
    try:
        merged(rules, Part.SITES, [*sites, {"id": "b00001", "name": "Чужой id", "on": True, "items": ["c.example"]}])
        clash = None
    except Exception as e:
        clash = e
    ok(clash is not None and "другого вида" in str(clash), "новая группа сайтов с id правила блокировки — отказ, не подмена", clash)
    ok(merged([], Part.BLOCK, [blocks[0]]) == [rule_of_block_expected := {**{k: v for k, v in blocks[0].items() if k != "kind"}, "route": "block"}] and rule_of_block_expected,
       "в пустой список часть встаёт целиком")

    print("\n— журнал")
    reply = {"ok": True, "rules": [{"rule": "a00003", "now": "n00001", "name": "Соцсети", "route": "block"}, {"rule": "a00001", "now": "a00001", "name": "YouTube", "route": "off"}]}
    said = describe(["rule-route", "eyJ9"], None, reply)
    ok(said == ("Правила: «Соцсети» — блокируется; «YouTube» — выключено", False), "в журнале — какие правила и куда они теперь ведут", said)
    ok(describe(["rule-route", "eyJ9"], None, {"ok": False, "error": "нет"}) == (None, False), "отказ в журнал не пишется (его видит тот, кто просил)")

    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
