#!/usr/bin/env python3
"""The cascade's settings and the shares in the package (awgscale.agent.cascade):
documents read, checked, compiled and refused as tests/golden/agent_cascade.json
records — and the command weights-set, sent through the real dispatcher, gives
the recorded answers, settings, file, questions to the watch service and
journal lines.

    uv run python tests/agent_cascade.py
    GOLDEN_WRITE=1 uv run python tests/agent_cascade.py    (records the golden file anew)

Nothing live is touched."""
import asyncio
import base64
import hashlib
import json
import os
import random
import re
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agent-cascade-")
from awgscale.agent import errors  # noqa: E402
from awgscale.agent.cascade import conf  # noqa: E402
from awgscale.agent.cascade.models import checked  # noqa: E402
from awgscale.agent.cascade.service import CascadeService  # noqa: E402
from awgscale.agent.commands import Command  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent.shares import shares_percent, shares_with  # noqa: E402
from awgscale.agent.shell import CascadeSet  # noqa: E402

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
SEED = open(os.path.join(ROOT, "tests", "cascade.conf")).read()
GOLDEN_FILE = os.path.join(HERE, "golden", "agent_cascade.json")
WRITE = os.environ.get("GOLDEN_WRITE") == "1"
GOLDEN = {} if WRITE else json.load(open(GOLDEN_FILE))


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:700])


def as_golden(key, value):
    """→ (the value as JSON keeps it, what the golden file has for it); GOLDEN_WRITE=1 records it instead."""
    value = json.loads(json.dumps(value, ensure_ascii=False))
    if WRITE:
        GOLDEN[key] = value
    return value, GOLDEN.get(key)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def text_seen(text):
    """The compiled file, without the line that says how to change it (its wording is not behaviour)."""
    return re.sub(r"To change:.*", "To change: <how>", text)


def b64(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


def said(fn, *a):
    """→ ("value", the result) or ("refused", the words)."""
    try:
        return "value", fn(*a)
    except ValueError as e:
        return "refused", str(e)
    except Exception as e:
        return "error", type(e).__name__


def t_shares():
    print("\n— доли")
    rnd = random.Random(11)
    results = []
    for _ in range(5000):
        weights = [rnd.randint(0, 100) for _ in range(rnd.randint(0, 6))]
        percent = rnd.randint(1, 99)
        results.append([shares_percent(weights), shares_with(weights, percent)])
    seen, expected = as_golden("shares", digest(results))
    ok(seen == expected, "5000 наборов долей: проценты и доля нового выхода — как записано", [seen, expected])
    ok(shares_percent([1, 1]) == [50, 50] and shares_percent([]) == [], "простые случаи долей", [shares_percent([1, 1]), shares_percent([])])


def mutations(doc, rnd):
    """Documents near a good one: some still good, most wrong in one place."""
    values = [None, True, False, 0, 1, 51, 100, 101, -1, "", "awg0", "awgk9", "x;y", "10.77.9.2", "10.77.1", "172.16.17.0/24", "172.16.17.0", "exit9-de", 'a"b', "a b", [], ["1.1.1.1"], ["nope"], {}]
    for _ in range(3000):
        new = json.loads(json.dumps(doc))
        if rnd.random() < 0.25:                    # a change that is fine: another share, another name, UDP the other way
            new["exits"][rnd.randrange(len(new["exits"]))].update(weight=rnd.randint(1, 100), name=rnd.choice(["exit9-de", "a.b_c-1"]))
            new["udp_via_exit"] = rnd.random() < 0.5
            new["extra_ru_nets"] = rnd.choice([[], ["10.0.0.0/8"], ["10.0.0.0/8", "192.0.2.1"]])
            yield new
            continue
        for _ in range(rnd.randint(1, 2)):
            where = rnd.random()
            if where < 0.5 and isinstance(new.get("exits"), list) and new["exits"]:
                target = rnd.choice(new["exits"])
                if isinstance(target, dict):
                    target[rnd.choice(["index", "iface", "tip", "name", "weight"])] = rnd.choice(values)
            elif where < 0.6:
                exits = doc["exits"]
                new["exits"] = rnd.choice([[], None, "x", exits * 2, exits * 5, [exits[0]], [1]])
            else:
                new[rnd.choice(list(doc))] = rnd.choice(values)
        yield new


def t_documents():
    print("\n— документ настроек и файл")
    parsed = conf.parse(SEED).wire()
    doc = checked(parsed).wire()
    seen, expected = as_golden("seed", {"parsed": parsed, "checked": doc})
    ok(seen == expected, "файл из репозитория читается и проверяется как записано", [seen, expected])
    text = conf.text_of(checked(doc))
    seen, expected = as_golden("seed text", text_seen(text))
    ok(seen == expected and conf.parse(text).wire() == doc, "…и собирается обратно в записанный файл, который читается в тот же документ", [seen, expected])
    ok("# lost with the next change. To change:  python -m awgscale.agent cascade-get / cascade-set\n" in text, "заголовок файла называет команду пакета, которой его меняют", text.splitlines()[:3])
    ok(json.dumps(checked(doc).wire(), ensure_ascii=False, sort_keys=True) == json.dumps(doc, ensure_ascii=False, sort_keys=True), "в базе документ лежит в прежнем виде, байт в байт")
    rnd = random.Random(5)
    good = 0
    differ = 0
    results = []
    for new in mutations(doc, rnd):
        mine = said(lambda d: checked(d).wire(), new)
        if mine[0] == "value":
            good += 1
            text = conf.text_of(checked(new))
            differ += conf.parse(text).wire() != mine[1]
            results.append([mine, text_seen(text)])
        else:
            results.append([mine])
    seen, expected = as_golden("mutations", digest(results))
    ok(seen == expected and good > 50 and differ == 0, f"3000 изменённых документов: приняты ({good}) и отклонены как записано, теми же словами; принятые собираются в записанный файл и читаются обратно в себя", [seen, expected, differ])
    odd = ['CLIENT_IF=awg0\nCLIENT_SUBNET=\'172.16.17.0/24\'\nEXITS=( "1:awgk1:10.77.1.2" 3:awgk2:10.77.3.2:two:  # note\n "5:awgk5:10.77.5.2:five:7" )\nUDP_VIA_EXIT=1 # yes\nEXTRA_RU_NETS="10.0.0.0/8  192.0.2.1"\nVIA_EXIT_SUBNETS=("" "172.30.99.0/24")\n',
           'CLIENT_IF="awg0"\nCLIENT_SUBNET="10.9.0.0/24"\n']
    seen, expected = as_golden("odd files", [conf.parse(text).wire() for text in odd])
    ok(seen == expected, "файлы, написанные рукой (комментарии, кавычки, без имени и доли, пустые элементы), читаются как записано", [seen, expected])


class World:
    """The stored settings, the watch service, the file, the journal."""

    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        self.conf = os.path.join(self.dir, "cascade.conf")
        self.lock = os.path.join(self.dir, ".panel.lock")
        self.stored = checked(conf.parse(SEED).wire()).wire()
        self.store_down = False
        self.watch = {"health": True, "apply": True}
        self.asked = []
        self.lines = []
        with open(self.conf, "w") as fh:
            fh.write(conf.text_of(checked(self.stored)))

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]


class NewPath:
    """weights-set through the real dispatcher of the package."""

    def __init__(self, world):
        self.world = world
        world_ = world

        class Stored:
            async def load(self):
                if world_.store_down:
                    raise errors.StoreDown("нет связи")
                return checked(world_.stored)

            async def save(self, settings):
                world_.stored = settings.wire()

        class Watch:
            async def ask(self, command):
                world_.asked.append(command)
                return Outcome(world_.watch[command], "" if world_.watch[command] else "iptables: сбой")

            async def health(self, timeout):
                return await self.ask("health")

            async def apply(self, timeout):
                return await self.ask("apply")

            async def pin(self, timeout):
                return Outcome(True)

        class Writer:
            async def write(self, entry):
                world_.lines.append(entry.line())

        self.stored = Stored()
        self.watch = Watch()
        self.writer = Writer()
        service = CascadeService(self.stored, self.watch, Path(world.conf), Path(world.lock))
        journal = Journal(None, self.writer)
        self.dispatcher = LOOP.run_until_complete(self.make(Handled(service.set_weights, journal), Handled(service.set_country, journal)))

    async def make(self, weights, country):
        dispatcher = Dispatcher()
        dispatcher.register(Command.WEIGHTS_SET, weights)
        dispatcher.register(Command.COUNTRY_SET, country)
        return dispatcher

    def ask(self, payload, by=Caller.PANEL):
        return LOOP.run_until_complete(self.dispatcher.dispatch(["weights-set", payload], by))

    def ask_country(self, country, by=Caller.PANEL):
        return LOOP.run_until_complete(self.dispatcher.dispatch(["country-set", country], by))


def t_country():
    """country-set through the real dispatcher: stored, compiled, applied, journaled — or put back."""
    print("\n— country-set через диспетчер")
    world = World("country")
    path = NewPath(world)
    reply = path.ask_country("kz")
    with open(world.conf) as fh:
        text = fh.read()
    ok(reply.get("ok") and world.stored.get("home_country") == "kz" and 'HOME_COUNTRY="kz"' in text and world.asked == ["health", "apply"],
       "kz: stored, compiled into the file, the rules applied", [reply, world.asked])
    ok([entry.get("text") for entry in world.journal()] == ["Домашняя страна: KZ"], "the journal says it", world.journal())
    ok(path.ask_country("kz").get("already") and len(world.journal()) == 1, "the same country again: nothing changes, nothing journaled")
    world.watch["apply"] = False
    reply = path.ask_country("by")
    with open(world.conf) as fh:
        text = fh.read()
    ok(not reply.get("ok") and "прежние настройки возвращены" in reply.get("error", "") and world.stored.get("home_country") == "kz" and 'HOME_COUNTRY="kz"' in text,
       "a list the watch service could not load: the country stays kz, in the store and in the file", reply)
    world.watch["apply"] = True
    reply = path.ask_country("ru")
    with open(world.conf) as fh:
        text = fh.read()
    ok(reply.get("ok") and "home_country" not in world.stored and "HOME_COUNTRY" not in text, "back to Russia: no trace of it, in the store or the file")


def t_command():
    print("\n— weights-set через диспетчер")
    steps = [
        ("доля одного выхода", lambda w: None, {"weights": {"1": 3}, "base": {"1": 2, "3": 1}}),
        ("та же доля ещё раз", lambda w: None, {"weights": {"1": 3}, "base": {"1": 3, "3": 1}}),
        ("страница показывала старые доли", lambda w: None, {"weights": {"1": 1}, "base": {"1": 2, "3": 1}}),
        ("проценты по всем выходам", lambda w: None, {"weights": {"1": 80, "3": 20}}),
        ("сумма не 100", lambda w: None, {"weights": {"1": 70, "3": 40}}),
        ("нет такого выхода", lambda w: None, {"weights": {"9": 2}}),
        ("доля ноль", lambda w: None, {"weights": {"1": 0}}),
        ("доля строкой", lambda w: None, {"weights": {"1": "2"}}),
        ("пусто", lambda w: None, {"weights": {}}),
        ("не объект", lambda w: None, [1, 2]),
        ("сторож не отвечает", lambda w: w.watch.update(health=False), {"weights": {"1": 5}}),
        ("правила не применились", lambda w: w.watch.update(health=True, apply=False), {"weights": {"1": 5}}),
        ("хранилище недоступно", lambda w: (w.watch.update(apply=True), setattr(w, "store_down", True)), {"weights": {"1": 5}}),
    ]
    new_world = World("new")
    new_path = NewPath(new_world)
    for name, prepare, payload in steps:
        prepare(new_world)
        new_world.asked.clear()
        new_world.lines.clear()
        mine = new_path.ask(b64(payload))
        seen, expected = as_golden("weights-set: " + name, {"answer": mine, "stored": new_world.stored, "asked": new_world.asked,
                                                             "file": text_seen(open(new_world.conf).read()), "journal": new_world.journal()})
        ok(seen == expected, f"{name}: записанный ответ, настройки и файл, обращения к сторожу, строка журнала", [seen, expected])
    by_bot = NewPath(World("bot"))
    by_bot.ask(b64({"weights": {"1": 3}}), Caller.BOT)
    by_system = NewPath(World("system"))
    by_system.ask(b64({"weights": {"1": 3}}), Caller.SYSTEM)
    ok(by_bot.world.journal()[0]["who"] == "bot" and by_system.world.journal()[0]["who"] == "system", "в журнале — кто просил: бот, или сама система")
    all_off = NewPath(World("all-off"))
    refused = all_off.ask(b64({"weights": {"1": 0, "3": 0}}))
    one_on = NewPath(World("one-on"))
    kept = one_on.ask(b64({"weights": {"1": 0, "3": 100}}))
    ok(refused.get("ok") is False and refused.get("error") == "хотя бы один выход должен остаться включённым" and not all_off.world.asked
       and kept.get("ok") is True and [exit_["weight"] for exit_ in kept["cascade"]["exits"]] == [0, 100],
       "выключить можно любой выход, но не все сразу: сторож не спрошен, настройки прежние", [refused, kept])
    ok(shares_with([0, 3, 1], 20) == ([0, 60, 20], 20), "новый выход: выключенный остаётся выключенным, доли делят включённые", shares_with([0, 3, 1], 20))

    class Broken:
        async def load(self):
            raise RuntimeError("неожиданное")

        async def save(self, settings):
            pass
    world = World("broken")
    service = CascadeService(Broken(), None, Path(world.conf), Path(world.lock))
    dispatcher = LOOP.run_until_complete(NewPath.make(None, Handled(service.set_weights), Handled(service.set_country)))
    stderr = sys.stderr
    sys.stderr = open(os.devnull, "w")
    try:
        reply = LOOP.run_until_complete(dispatcher.dispatch(["weights-set", b64({"weights": {"1": 3}})]))
    finally:
        sys.stderr = stderr
    ok(reply == {"ok": False, "error": "ошибка агента: RuntimeError('неожиданное')"}, "неожиданная ошибка в команде — ответ прежнего вида, а не обрыв", reply)


def t_exit_countries():
    """An exit's country in the settings: only a real ISO 3166-1 alpha-2 code, lower case; cascade-set keeps the stored ones."""
    print("\n— страна выхода в настройках")
    doc = checked(conf.parse(SEED).wire()).wire()

    def with_country(country):
        return dict(doc, exits=[dict(doc["exits"][0], country=country), *doc["exits"][1:]])

    error = "выход 1: страна — код ISO 3166 из двух строчных латинских букв, например nl"
    refused = {repr(country): said(checked, with_country(country)) for country in (1, ["nl"], True, "", "NL", "Nl", "nld", "n", "zz", "xx", "eu", "uk", " nl", "nl\n")}
    ok(all(answer == ("refused", error) for answer in refused.values()), "не строка, верхний регистр, не две буквы, неизвестный код (zz, xx, eu, uk) — отказ", refused)
    taken = [checked(with_country(country)).exits[0].country for country in ("nl", "de", "gb", "us", "ax")]
    ok(taken == ["nl", "de", "gb", "us", "ax"] and checked(with_country(None)).exits[0].country is None and "country" not in checked(with_country(None)).wire()["exits"][0],
       "настоящие коды приняты; null — страны нет, и в документе её нет", taken)

    print("\n— cascade-set и страны выходов")
    world = World("cascade-set")
    path = NewPath(world)
    stored = dict(world.stored, exits=[dict(world.stored["exits"][0], country="nl"), dict(world.stored["exits"][1], country="de")])
    world.stored = checked(stored).wire()
    service = CascadeService(path.stored, path.watch, Path(world.conf), Path(world.lock))
    journal = Journal(None, path.writer)
    handled = Handled(service.replace, journal)

    def cascade_set(document):
        return LOOP.run_until_complete(handled(CascadeSet(payload=b64(document)), Caller.SYSTEM, None))

    world.asked.clear()
    plain = dict(world.stored, exits=[{k: v for k, v in exit_.items() if k != "country"} for exit_ in world.stored["exits"]])
    plain["exits"][0]["weight"] = 5
    reply = cascade_set(plain)
    ok(reply.get("ok") and [exit_.get("country") for exit_ in world.stored["exits"]] == ["nl", "de"] and world.stored["exits"][0]["weight"] == 5 and world.asked == ["health", "apply"],
       "документ без стран у выходов: доли изменены, страны сохранены те, что были (по номеру выхода)", [reply, world.stored["exits"]])
    world.asked.clear()
    world.lines.clear()
    again = cascade_set(plain)
    ok(again.get("already") and world.journal() == [], "тот же документ без стран ещё раз — ничего не изменилось", [again, world.journal()])
    changed = dict(world.stored, exits=[dict(world.stored["exits"][0], country="fi"), dict(world.stored["exits"][1], country=None)])
    reply = cascade_set(changed)
    ok(reply.get("ok") and reply.get("countries_only") is True and [exit_.get("country") for exit_ in world.stored["exits"]] == ["fi", None] and "country" not in world.stored["exits"][1]
       and world.asked == [], "страны заданы явно (null — снять): сохранены как есть, сторож не спрошен — правил они не меняют", [reply, world.stored["exits"], world.asked])
    names = [exit_["name"] for exit_ in world.stored["exits"]]
    ok([entry.get("text") for entry in world.journal()] == [f"Страны выходов: {names[0]} FI, {names[1]} —"],
       "в журнале — страны выходов, а не «Доли выходов»", world.journal())
    renumbered = dict(world.stored, exits=[{k: v for k, v in world.stored["exits"][0].items() if k != "country"}])
    renumbered["exits"][0]["weight"] = 1
    reply = cascade_set(dict(renumbered, exits=[dict(renumbered["exits"][0], index=7, iface="awgk7", tip="10.77.7.2")]))
    ok(reply.get("ok") and world.stored["exits"] == [{k: v for k, v in renumbered["exits"][0].items()} | {"index": 7, "iface": "awgk7", "tip": "10.77.7.2"}],
       "у выхода с новым номером сохранённой страны нет — она не переносится с другого", world.stored["exits"])


def main():
    t_shares()
    t_documents()
    t_command()
    t_country()
    t_exit_countries()
    if WRITE:
        with open(GOLDEN_FILE, "w") as fh:
            json.dump(GOLDEN, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
