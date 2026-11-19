#!/usr/bin/env python3
"""The exits as servers (awgscale.agent.exits): exits-get, exit-add, exit-attach,
exit-rename, exit-remove and exit-update sent through the real dispatcher give
the answers, leave the settings, files, groups and pins, ask the exits service
and the watch service the things and write the journal lines kept in
tests/golden/agent_exits.json. The number chosen for a new exit and the list of
working exits come out as kept there too, on random worlds (a fixed seed). The
root password of a new exit reaches the exits service and nothing else: not the
answer, the journal, the log, the stored settings or a repr.

    uv run python tests/agent_exits.py
    PG_DSN=… PG_PASSWORD=… uv run python tests/agent_exits.py     (also: the groups' records in a real database)
    GOLDEN_WRITE=1 uv run python tests/agent_exits.py             writes the golden file again

Nothing live is touched."""
import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import random
import re
import sys
import tempfile
from pathlib import Path

import httpx
from pydantic import SecretStr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agent-exits-")
GOLDEN_FILE = os.path.join(HERE, "golden", "agent_exits.json")
WRITING = os.environ.get("GOLDEN_WRITE") == "1"
GOLDEN = {} if WRITING else json.load(open(GOLDEN_FILE))

from awgscale.agent import errors  # noqa: E402
from awgscale.agent.cascade import conf  # noqa: E402
from awgscale.agent.cascade.models import checked  # noqa: E402
from awgscale.agent.cascade.service import CascadeService  # noqa: E402
from awgscale.agent.clients.exits import AddBody, ExitsClient, Registry  # noqa: E402
from awgscale.agent.clients.http import ServiceClient  # noqa: E402
from awgscale.agent.commands import Command, parse  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.exits.gate import ExitsService  # noqa: E402
from awgscale.agent.exits.pins import KeptOnExit, StoredGroupExits  # noqa: E402
from awgscale.agent.exits.service import ExitServers  # noqa: E402
from awgscale.agent.exits.working import StateFiles  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.protocol import Caller, SocketRequest  # noqa: E402
from awgscale.agent.users.directory import user_of  # noqa: E402

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
SEED = open(os.path.join(ROOT, "tests", "cascade.conf")).read()
CANARY = "c4nary-R00t-pa55"


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:1200])


def plain(value):
    """As JSON keeps it: a world's temporary directory and the how-to line of the compiled file's header named by what they are."""
    text = json.dumps(value, ensure_ascii=False)
    text = re.sub(re.escape(TMP) + r"/[^/'\"]+", "<world>", text)
    text = re.sub(r"To change:  [^\\]*", "To change:  <how>", text)
    return json.loads(text)


def kept(key, value):
    """Is `value` what the package gave here when the golden file was written? (GOLDEN_WRITE=1: it is written now)"""
    if WRITING:
        GOLDEN[key] = plain(value)
        return True
    return GOLDEN.get(key) == plain(value)


def digest(values):
    return hashlib.sha256(json.dumps(plain(values), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def golden_written():
    if WRITING:
        os.makedirs(os.path.dirname(GOLDEN_FILE), exist_ok=True)
        with open(GOLDEN_FILE, "w") as fh:
            json.dump(GOLDEN, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")


def seed_settings():
    return conf.parse(SEED).wire()


def b64(data):
    return base64.b64encode(json.dumps(data).encode()).decode()


def read(path):
    try:
        return open(path).read()
    except OSError:
        return None


class World:
    """One world: the settings, the files, the groups, and stand-ins for the two services."""

    def __init__(self, name, cascade=None, nodes=None):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        self.run_dir = os.path.join(self.dir, "run")
        os.mkdir(self.run_dir)
        self.conf = os.path.join(self.dir, "cascade.conf")
        self.pins = os.path.join(self.dir, "pins.map")
        self.panel_lock = os.path.join(self.dir, ".panel.lock")
        self.users_lock = os.path.join(self.dir, ".users.lock")
        self.cascade = cascade or seed_settings()
        with open(self.conf, "w") as fh:
            fh.write(conf.text_of(checked(self.cascade)))
        self.nodes = nodes if nodes is not None else {"1": {"address": "45.10.20.1", "managed": True, "ssh_port": 22}, "3": {"address": "45.10.20.3", "managed": False, "ssh_port": 22}}
        self.clients = [{"name": "alice", "ip": "10.9.9.2", "expires_at": None}, {"name": "bob", "ip": "10.9.9.3", "expires_at": None}, {"name": "carol", "ip": "10.9.9.4", "expires_at": None}]
        self.groups = []
        self.hand = {}
        self.exits_refuse = None          # the exits service says no, in these words
        self.exits_away = None            # …or cannot be asked: the words the agent has for it
        self.watch_fails = set()          # commands of the watch service that fail
        self.store_down = False
        self.asked = []
        self.lines = []
        self.saved = []                   # every document that was stored

    def working(self, **states):
        for index, state in states.items():
            with open(os.path.join(self.run_dir, f"exit{index}.state"), "w") as fh:
                fh.write(state + "\n")

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]

    def exits_answer(self):
        if self.exits_away:
            return False, self.exits_away
        if self.exits_refuse:
            return False, self.exits_refuse
        return True, ""

    def registry(self):
        return {"ok": True, "nodes": json.loads(json.dumps(self.nodes)), "job": None, "busy": False, "key": "ssh-ed25519 AAAA entry"}

    def snapshot(self):
        return (json.loads(json.dumps(self.cascade)), read(self.conf), json.loads(json.dumps(self.groups)), dict(self.hand), read(self.pins))


class NewPath:
    def __init__(self, world):
        w = world

        class Cascade:
            async def load(self):
                if w.store_down:
                    raise errors.StoreDown("нет связи")
                return checked(json.loads(json.dumps(w.cascade)))

            async def save(self, settings):
                w.cascade = settings.wire()
                w.saved.append(json.dumps(w.cascade))

        class Watch:
            def told(self, command):
                w.asked.append(("watch", command))
                return Outcome(command not in w.watch_fails, "не вышло" if command in w.watch_fails else "")

            async def health(self, timeout):
                return self.told("health")

            async def apply(self, timeout):
                return self.told("apply")

            async def pin(self, timeout):
                return self.told("pin")

        class Exits:
            async def registry(self):
                w.asked.append(("exits", "nodes"))
                done, said = w.exits_answer()
                if not done:
                    raise errors.Refused(said)
                return Registry.model_validate(w.registry())

            async def add(self, index, name, weight, address, ssh_port, password):
                w.asked.append(("exits", "add", index, name, weight, address, ssh_port, None if password is None else password.get_secret_value()))
                return Outcome(*w.exits_answer())

            async def remove(self, index, iface, name):
                w.asked.append(("exits", "remove", index, iface, name))
                return Outcome(*w.exits_answer())

            async def update(self, index, reboot, name):
                w.asked.append(("exits", "update", index, reboot, name))
                return Outcome(*w.exits_answer())

        class Groups:
            async def records(self):
                return [g for g in json.loads(json.dumps(w.groups)) if isinstance(g, dict)]

            async def save(self, records):
                w.groups = records
                w.saved.append(json.dumps(records))

        class Hand:
            async def read(self):
                return dict(w.hand)

            async def save(self, pins):
                w.hand = dict(pins)

        class Dir:
            async def list(self, force=False):
                return [user_of(c) for c in w.clients]

        class Writer:
            async def write(self, entry):
                w.lines.append(entry.line())

        cascade = CascadeService(Cascade(), Watch(), Path(w.conf), Path(w.panel_lock))
        kept = KeptOnExit(Groups(), Hand(), Dir(), Watch(), Path(w.pins), Path(w.users_lock))
        service = ExitServers(cascade, Cascade(), Exits(), StateFiles(Path(w.run_dir), Path(w.conf)), kept)
        journal = Journal(None, Writer())

        async def make():
            dispatcher = Dispatcher()
            dispatcher.register(Command.EXITS_GET, Handled(service.show))
            dispatcher.register(Command.EXIT_ADD, Handled(service.add, journal, with_secret=True))
            dispatcher.register(Command.EXIT_ATTACH, Handled(service.attach, journal))
            dispatcher.register(Command.EXIT_RENAME, Handled(service.rename, journal))
            dispatcher.register(Command.EXIT_REMOVE, Handled(service.remove, journal))
            dispatcher.register(Command.EXIT_UPDATE, Handled(service.update, journal))
            return dispatcher
        self.dispatcher = LOOP.run_until_complete(make())

    def ask(self, arguments, secret=None):
        return LOOP.run_until_complete(self.dispatcher.dispatch(arguments, Caller.PANEL, None if secret is None else SecretStr(secret)))


def t_commands():
    print("\n— команды через диспетчер: как прежде")

    def pinned(w):
        w.groups = [{"id": "aaaaaa", "name": "A", "exit": 4, "members": ["alice"]}, {"id": "bbbbbb", "name": "B", "exit": 1, "members": ["bob"]}, "мусор"]
        w.hand = {"carol": 4, "bob": 3}

    steps = [
        ("exits-get", lambda w: w.working(**{"1": "up", "3": "up"}), ["exits-get"], None),
        ("exit-add: с паролем, свободный номер", lambda w: None, ["exit-add", b64({"address": "45.10.20.9", "name": "exit3-de", "weight": 20})], "r00t pw"),
        ("exit-add: без пароля и без доли (равная часть), свой порт", lambda w: None, ["exit-add", b64({"address": "45.10.20.9", "name": "exit3-de", "ssh_port": 2222})], None),
        ("exit-add: сервер уже записан — прежний номер", lambda w: None, ["exit-add", b64({"address": "45.10.20.3", "name": "again"})], None),
        ("exit-add: имя занято", lambda w: None, ["exit-add", b64({"address": "45.10.20.9", "name": "exit1-nl"})], None),
        ("exit-add: имя с пробелом", lambda w: None, ["exit-add", b64({"address": "45.10.20.9", "name": "bad name"})], None),
        ("exit-add: нулевая доля", lambda w: None, ["exit-add", b64({"address": "45.10.20.9", "name": "ok", "weight": 0})], None),
        ("exit-add: доля не числом", lambda w: None, ["exit-add", b64({"address": "45.10.20.9", "name": "ok", "weight": "5"})], None),
        ("exit-add: адрес не строкой", lambda w: None, ["exit-add", b64({"address": 5, "name": "ok"})], None),
        ("exit-add: порт не числом", lambda w: None, ["exit-add", b64({"address": "45.10.20.9", "name": "ok", "ssh_port": "22"})], None),
        ("exit-add: не JSON-объект", lambda w: None, ["exit-add", b64([1, 2])], None),
        ("exit-add: сервис отказал своими словами", lambda w: setattr(w, "exits_refuse", "другая операция с выходами ещё идёт — дождитесь её окончания"), ["exit-add", b64({"address": "45.10.20.9", "name": "exit3-de"})], "r00t pw"),
        ("exit-add: сервис не отвечает", lambda w: (setattr(w, "exits_refuse", None), setattr(w, "exits_away", "сервис выходов не отвечает")), ["exit-add", b64({"address": "45.10.20.9", "name": "exit3-de"})], None),
        ("exits-get: сервис не отвечает", lambda w: None, ["exits-get"], None),
        ("exit-add: хранилище недоступно", lambda w: (setattr(w, "exits_away", None), setattr(w, "store_down", True)), ["exit-add", b64({"address": "45.10.20.9", "name": "exit3-de"})], None),
        ("exit-attach: хранилище недоступно", lambda w: None, ["exit-attach", b64({"index": 4, "name": "exit3-de", "weight": 20})], None),
        ("exit-attach: сторож не отвечает", lambda w: (setattr(w, "store_down", False), w.watch_fails.add("health")), ["exit-attach", b64({"index": 4, "name": "exit3-de", "weight": 20})], None),
        ("exit-attach: правила не применились — возвращено", lambda w: (w.watch_fails.discard("health"), w.watch_fails.add("apply")), ["exit-attach", b64({"index": 4, "name": "exit3-de", "weight": 20})], None),
        ("exit-attach: новый выход берёт свою долю, остальные делят остаток", lambda w: w.watch_fails.clear(), ["exit-attach", b64({"index": 4, "name": "exit3-de", "weight": 20})], None),
        ("exit-attach: тот же ещё раз — ничего не меняется", lambda w: None, ["exit-attach", b64({"index": 4, "name": "exit3-de", "weight": 50})], None),
        ("exit-attach: уже есть, другое имя — только имя", lambda w: None, ["exit-attach", b64({"index": 4, "name": "exit3-fr"})], None),
        ("exit-attach: номер вне пределов", lambda w: None, ["exit-attach", b64({"index": 51, "name": "x"})], None),
        ("exit-attach: доля 100", lambda w: None, ["exit-attach", b64({"index": 5, "name": "x", "weight": 100})], None),
        ("exit-attach: имя занято другим выходом", lambda w: None, ["exit-attach", b64({"index": 5, "name": "exit1-nl", "weight": 10})], None),
        ("exit-rename: новое имя", lambda w: None, ["exit-rename", "4", "exit-de"], None),
        ("exit-rename: то же имя", lambda w: None, ["exit-rename", "4", "exit-de"], None),
        ("exit-rename: занятое имя", lambda w: None, ["exit-rename", "4", "exit1-nl"], None),
        ("exit-rename: нет такого выхода", lambda w: None, ["exit-rename", "9", "zz"], None),
        ("exit-update: без перезагрузки", lambda w: w.working(**{"1": "down", "3": "down", "4": "up"}), ["exit-update", "4"], None),
        ("exit-update: перезагрузка единственного работающего — отложена", lambda w: None, ["exit-update", "4", "reboot"], None),
        ("exit-update: перезагрузка, когда работают другие", lambda w: w.working(**{"1": "up"}), ["exit-update", "4", "reboot"], None),
        ("exit-update: нет такого выхода", lambda w: None, ["exit-update", "7"], None),
        ("exit-update: сервис отказал", lambda w: setattr(w, "exits_refuse", "на сервере, настроенном вручную, обновлять нечего"), ["exit-update", "3"], None),
        ("exit-remove: единственный работающий — сначала вопрос", lambda w: (setattr(w, "exits_refuse", None), pinned(w), w.working(**{"1": "down", "3": "down", "4": "up"})), ["exit-remove", "4"], None),
        ("exit-remove: нет такого выхода", lambda w: None, ["exit-remove", "9", "force"], None),
        ("exit-remove: правила не применились — возвращено, закрепления целы", lambda w: w.watch_fails.add("apply"), ["exit-remove", "4", "force"], None),
        ("exit-remove: с подтверждением; группа и пользователь возвращены на «авто»", lambda w: w.watch_fails.clear(), ["exit-remove", "4", "force"], None),
        ("exit-remove: связь на входе осталась — сказано в ответе", lambda w: (w.working(**{"1": "up", "3": "up"}), setattr(w, "exits_away", "сервис выходов сейчас не может выполнить запрос")), ["exit-remove", "3"], None),
        ("exit-remove: последний выход — никогда", lambda w: setattr(w, "exits_away", None), ["exit-remove", "1", "force"], None),
        ("exits-get после всего", lambda w: None, ["exits-get"], None),
    ]
    world = World("steps")
    path = NewPath(world)
    for number, (name, prepare, arguments, secret) in enumerate(steps):
        prepare(world)
        world.asked.clear()
        world.lines.clear()
        mine = path.ask(arguments, secret)
        seen = (mine, world.snapshot(), world.asked, world.journal())
        key = f"step {number:02d}: {name}"
        same = kept(key, seen)
        ok(same, f"{name}: тот же ответ, те же настройки, файлы и закрепления, те же обращения к сервисам, та же строка журнала",
           None if same else [(i, a, b) for i, (a, b) in enumerate(zip(GOLDEN.get(key, []), plain(seen))) if a != b][:1])
    ok([e["index"] for e in world.cascade["exits"]] == [1], "в конце остался один выход — первый", world.cascade["exits"])


def t_single_server():
    print("\n— один сервер получает первый выход")
    single = conf.parse(open(os.path.join(ROOT, "cascade", "cascade.conf")).read()).wire()
    world = World("single", cascade=single)
    path = NewPath(world)
    added = path.ask(["exit-add", b64({"address": "45.10.20.9", "name": "first"})], "r00t pw")
    ok(added.get("ok") is True and isinstance(added.get("index"), int), "добавление начато без доли: первому выходу её не нужно", added)
    index = added.get("index")
    attached = path.ask(["exit-attach", b64({"index": index, "name": "first", "weight": 99})])
    now = world.cascade
    ok(attached.get("ok") is True and not now.get("single_server") and [e["index"] for e in now["exits"]] == [index],
       "первый выход принят: сервер стал каскадом с одним выходом", [attached, now])
    ok(now["probe_targets"] and now["dns_via_exit"] and now["udp_via_exit"], "у каскада есть что пинговать через выход, DNS и звонки идут через него", now)
    with open(world.conf) as fh:
        text = fh.read()
    ok("SINGLE_SERVER" not in text and f'"{index}:awgk{index}:' in text, "собранный cascade.conf: без SINGLE_SERVER, с выходом", text[-300:])


def random_cascade(rnd):
    """Settings with a few exits whose interfaces and addresses do not always follow from their numbers (as on the live server)."""
    indexes = rnd.sample(range(1, 12), rnd.randint(1, 8))
    exits = []
    for index in indexes:
        other = rnd.choice([index, index, rnd.randint(1, 12)])
        exits.append({"index": index, "iface": f"awgk{other}", "tip": f"10.77.{other}.2", "name": f"exit{index}", "weight": rnd.randint(1, 60)})
    if len({e["iface"] for e in exits}) != len(exits):
        return None
    return checked(dict(seed_settings(), exits=exits)).wire()


def t_numbers_and_shares():
    print("\n— номер нового выхода, его доля и доли остальных: на случайных настройках")
    rnd = random.Random(11)
    outcomes = []
    tried = 0
    attached = 0
    while tried < 400:
        cascade = random_cascade(rnd)
        if cascade is None:
            continue
        tried += 1
        nodes = {str(i): {"address": f"45.10.20.{i}", "managed": rnd.random() < 0.5} for i in rnd.sample(range(1, 14), rnd.randint(0, 6))}
        world = World("new-n", json.loads(json.dumps(cascade)), json.loads(json.dumps(nodes)))
        address = rnd.choice(["45.10.20.99", "45.10.20." + str(rnd.randint(1, 13))])
        payload = {"address": address, "name": rnd.choice(["fresh", "exit" + str(rnd.randint(1, 12))])}
        if rnd.random() < 0.6:
            payload["weight"] = rnd.choice([1, 10, 33, 50, 99, 100, 0])
        arguments = ["exit-add", b64(payload)]
        mine = NewPath(world).ask(arguments, "pw")
        outcomes.append([mine, list(world.asked)])
        if mine.get("ok") and len(cascade["exits"]) < 8 and mine["index"] not in [e["index"] for e in cascade["exits"]]:
            attached += 1
            arguments = ["exit-attach", b64({"index": mine["index"], "name": "fresh-one", "weight": rnd.choice([1, 5, 20, 50, 90, 99])})]
            mine = NewPath(world).ask(arguments)
            outcomes.append([mine, world.snapshot(), world.journal()])
    ok(kept("numbers_digest", digest(outcomes)) and attached > 50, f"400 случайных настроек и записей сервиса ({attached} с подключением): номер, ответ, доли и собранный файл — те же", [digest(outcomes), attached])
    doc = seed_settings()
    held = dict(doc, exits=[{"index": 1, "iface": "awgk1", "tip": "10.77.1.2", "name": "a", "weight": 2}, {"index": 3, "iface": "awgk2", "tip": "10.77.2.2", "name": "b", "weight": 1}])
    world = World("held", checked(held).wire(), {})
    reply = NewPath(world).ask(["exit-add", b64({"address": "45.10.20.50", "name": "c"})])
    ok(reply.get("index") == 4, "номер 2 свободен как номер, но его интерфейс и сеть заняты выходом 3 — новому выходу достаётся 4", reply)
    world = World("small", checked(dict(doc, exits=[{"index": 1, "iface": "awgk1", "tip": "10.77.1.2", "name": "a", "weight": 98}, {"index": 2, "iface": "awgk2", "tip": "10.77.2.2", "name": "b", "weight": 1}])).wire(), {})
    NewPath(world).ask(["exit-attach", b64({"index": 3, "name": "c", "weight": 99})])
    ok([e["weight"] for e in world.cascade["exits"]] == [1, 1, 99], "новый выход берёт 99% — остальным остаётся хотя бы по одному, ни у кого не ноль", world.cascade["exits"])


def t_working():
    print("\n— какие выходы сейчас работают")
    rnd = random.Random(5)
    outcomes = []
    for _ in range(500):
        cascade = None
        while cascade is None:
            cascade = random_cascade(rnd)
        world = World("up", cascade)
        if rnd.random() < 0.1:
            os.unlink(world.conf)
        for exit_ in cascade["exits"]:
            if rnd.random() < 0.8:
                world.working(**{str(exit_["index"]): rnd.choice(["up", "up", "down", "unknown", ""])})
        world.working(**{"49": "up"})                    # a file of an exit the routing does not know
        mine = StateFiles(Path(world.run_dir), Path(world.conf)).up()
        rnd.choice([e["index"] for e in cascade["exits"]])            # the draw the seed's sequence was kept with
        outcomes.append(sorted(mine))
    ok(kept("working_digest", digest(outcomes)), "500 случайных наборов файлов состояния: работающие выходы те же", digest(outcomes))


class Service:
    """The exits service on the wire: remembers every request it was sent and answers as it is told to."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.body = {"ok": True}
        self.drop = False
        self.nodes = {"1": {"address": "45.10.20.1", "managed": True, "ssh_port": 22, "added": "2026-11-15T16:00:00+00:00"}}

    def handle(self, request):
        self.requests.append((request.method, request.url.path, request.headers.get("authorization"), request.content.decode()))
        if self.drop:
            raise httpx.ConnectError("refused")
        if request.url.path == "/v1/nodes" and self.status == 200:
            job = {"id": "j1", "kind": "add", "state": "done", "index": 1, "name": "exit1-nl", "address": "45.10.20.1", "steps": [], "started": "2026-11-15T16:00:00+00:00", "finished": "2026-11-15T16:03:00+00:00"}
            return httpx.Response(200, json={"ok": True, "nodes": self.nodes, "job": job, "busy": False, "key": "ssh-ed25519 AAAA entry"})
        return httpx.Response(self.status, json=self.body)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.text = io.StringIO()

    def emit(self, record):
        self.text.write(self.format(record) + "\n" + repr(record.args) + "\n")


def t_password():
    print("\n— пароль root нового выхода: только сервису выходов")
    token = Path(TMP) / "exits-token"
    token.write_text("t0ken\n")
    wire = Service()
    gate = ExitsService(ExitsClient(ServiceClient("http://exits.test", token, httpx.MockTransport(wire.handle))))
    world = World("pw")

    class Cascade:
        async def load(self):
            return checked(json.loads(json.dumps(world.cascade)))

        async def save(self, settings):
            world.saved.append(json.dumps(settings.wire()))

    class Writer:
        async def write(self, entry):
            world.lines.append(entry.line())

    class Nobody:
        async def release(self, index):
            pass

    service = ExitServers(CascadeService(Cascade(), None, Path(world.conf), Path(world.panel_lock)), Cascade(), gate, StateFiles(Path(world.run_dir), Path(world.conf)), Nobody())
    journal = Journal(None, Writer())

    async def make():
        dispatcher = Dispatcher()
        dispatcher.register(Command.EXIT_ADD, Handled(service.add, journal, with_secret=True))
        dispatcher.register(Command.EXITS_GET, Handled(service.show))
        dispatcher.register(Command.EXIT_UPDATE, Handled(service.update, journal))
        return dispatcher
    dispatcher = LOOP.run_until_complete(make())
    capture = Capture()
    logging.getLogger().addHandler(capture)
    logging.getLogger().setLevel(logging.DEBUG)
    line = SocketRequest.model_validate({"args": ["exit-add", b64({"address": "45.10.20.9", "name": "exit3-de", "weight": 20})], "by": "panel", "secret": CANARY})
    request = line.command()
    secret = line.secret_for(request)
    replies = []

    def ask():
        replies.append(LOOP.run_until_complete(dispatcher.run(request, line.caller(), secret)))
        return replies[-1]

    reply = ask()
    sent = [r for r in wire.requests if r[1] == "/v1/add"]
    ok(reply == {"ok": True, "index": 4, "name": "exit3-de", "address": "45.10.20.9"} and len(sent) == 1 and json.loads(sent[0][3]) == {"index": 4, "name": "exit3-de", "weight": 20, "address": "45.10.20.9", "ssh_port": 22, "password": CANARY}
       and sent[0][2] == "Bearer t0ken", "пароль дошёл до сервиса выходов в теле запроса — и это единственное место", [reply, sent])
    wire.status, wire.body = 400, {"ok": False, "error": "другая операция с выходами ещё идёт — дождитесь её окончания"}
    refused = ask()
    wire.status, wire.body = 422, {"detail": [{"type": "value_error", "loc": ["body", "address"], "msg": "Value error, адрес сервера: нужен внешний IPv4", "input": {"password": CANARY}}]}
    unfit = ask()
    wire.status, wire.body = 503, {"ok": False, "error": "сервис сейчас не может выполнить запрос", "detail": CANARY}
    away = ask()
    wire.drop = True
    silent = ask()
    wire.drop, wire.status, wire.body = False, 200, {"ok": True}
    ok(refused == {"ok": False, "error": "другая операция с выходами ещё идёт — дождитесь её окончания"} and unfit == {"ok": False, "error": "адрес сервера: нужен внешний IPv4"}
       and away == {"ok": False, "error": "сервис выходов сейчас не может выполнить запрос"} and silent == {"ok": False, "error": "сервис выходов не отвечает"},
       "отказ сервиса — его словами (400 и 422), «не может» и «не отвечает» — как прежде", [refused, unfit, away, silent])
    logging.getLogger().removeHandler(capture)
    places = {
        "ответы": json.dumps(replies, ensure_ascii=False),
        "журнал": "\n".join(world.lines),
        "лог": capture.text.getvalue(),
        "сохранённые настройки": "\n".join(world.saved),
        "repr запроса с сокета": repr(line.model_copy(update={"secret": secret})) + repr(secret) + str(secret),
        "repr и обычный вид тела запроса": repr(AddBody(index=2, name="x", weight=1, address="45.10.20.9", ssh_port=22, password=secret)) + str(AddBody(index=2, name="x", weight=1, address="45.10.20.9", ssh_port=22, password=secret).model_dump()),
        "аргументы команды (они идут в журнал)": " ".join(request.to_args()) + repr(request),
    }
    leaked = [place for place, text in places.items() if CANARY in text]
    ok(not leaked and len(world.lines) == 1 and "exit3-de" in world.lines[0], "пароля нет в ответах, журнале, логе, сохранённых настройках и ни в одном repr; в журнале — одна строка о начатом подключении", leaked)
    other = SocketRequest.model_validate({"args": ["exit-update", "1"], "by": "panel", "secret": CANARY})
    ok(other.secret_for(other.command()) is None and SocketRequest.model_validate({"args": ["exit-add", "e30="], "secret": "x" * 201}).secret_for(parse(["exit-add", "e30="])) is None,
       "секрет принимается только с exit-add и только разумной длины; с другой командой он отбрасывается")
    world.working(**{"1": "up", "3": "up"})
    shown = LOOP.run_until_complete(dispatcher.dispatch(["exits-get"], Caller.PANEL))
    ok(shown["nodes"]["1"] == {"address": "45.10.20.1", "ssh_port": 22, "managed": True, "added": 1794758400} and shown["job"]["started"] == 1794758400 and shown["job"]["finished"] == 1794758580 and shown["key"].startswith("ssh-ed25519"),
       "exits-get: время сервиса (ISO) странице отдаётся как прежде — секундами", shown)
    wire.status, wire.body = 500, {"detail": "boom"}
    shown = LOOP.run_until_complete(dispatcher.dispatch(["exits-get"], Caller.PANEL))
    update = LOOP.run_until_complete(dispatcher.dispatch(["exit-update", "1"], Caller.PANEL))
    ok(shown == {"ok": False, "error": "сервис выходов сейчас не может выполнить запрос"} == update, "сервис ответил ошибкой — «сейчас не может выполнить запрос», и для списка, и для обновления", [shown, update])


def t_groups_in_database():
    print("\n— группы, закреплённые за выходом: в настоящей базе")
    import psycopg

    from awgscale.agent.store.documents import Kind
    from awgscale.agent.store.store import Store
    from awgscale.agent.users.store import StoredHandPins

    password_file = Path(TMP) / "db-password"
    password_file.write_text(os.environ["PG_PASSWORD"] + "\n")
    with psycopg.connect(os.environ["PG_DSN"], password=os.environ["PG_PASSWORD"], autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    store = Store(os.environ["PG_DSN"], password_file)
    groups = [{"id": "aaaaaa", "name": "A", "space": False, "exit": 4, "filter": "std", "members": ["alice"]}, {"id": "bbbbbb", "name": "B", "space": True, "exit": 1, "filter": "kids", "members": ["bob", "carol"]}]
    world = World("db")

    class Dir:
        async def list(self, force=False):
            return [user_of(c) for c in world.clients]

    class Watch:
        async def pin(self, timeout):
            world.asked.append(("watch", "pin"))
            return Outcome(True)

    async def scenario():
        async with store.transaction() as tx:
            await tx.create_schema()
        with psycopg.connect(os.environ["PG_DSN"], password=os.environ["PG_PASSWORD"], autocommit=True) as connection:
            connection.execute("INSERT INTO meta (key, value) VALUES ('ready', '1')")       # what store-init leaves in a database the settings were moved into
        async with store.transaction() as tx:
            await tx.put(Kind.USER_GROUPS, groups)
        hand = StoredHandPins(store)
        await hand.save({"carol": 4, "bob": 3})
        kept = KeptOnExit(StoredGroupExits(store), hand, Dir(), Watch(), Path(world.pins), Path(world.users_lock))
        await kept.release(4)
        first = (await StoredGroupExits(store).records(), await hand.read(), read(world.pins), list(world.asked))
        await kept.release(9)
        return first, (await StoredGroupExits(store).records(), await hand.read(), list(world.asked))
    first, second = LOOP.run_until_complete(scenario())
    expected = [dict(groups[0], exit=None), groups[1]]
    ok(first[0] == expected and first[1] == {"bob": 3} and first[2] == "10.9.9.3 3\n10.9.9.4 1\n" and first[3] == [("watch", "pin")],
       "группа и пользователь, закреплённые за удалённым выходом, вернулись на «авто»; остальные записи групп целы до поля; файл закреплений собран и применён", first)
    ok(second[0] == expected and second[1] == {"bob": 3}, "за выходом никто не закреплён — в базе ничего не меняется", second)


def main():
    t_commands()
    t_numbers_and_shares()
    t_working()
    t_password()
    t_single_server()
    if os.environ.get("PG_DSN"):
        t_groups_in_database()
    golden_written()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
