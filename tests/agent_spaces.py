#!/usr/bin/env python3
"""The spaces' commands that live in the package (awgscale.agent.spaces):
spaces-get and spaces-set sent through the real dispatcher give the answers,
store the records, write the file for the watch service, ask the things and
write the journal lines recorded in tests/golden/agent_spaces.json; the file
compiled from random spaces and groups and the names typed by the admin, as
cleaned, come out as recorded.

    uv run python tests/agent_spaces.py
    GOLDEN_WRITE=1 uv run python tests/agent_spaces.py    (records the golden file anew)

Nothing live is touched."""
import asyncio
import base64
import hashlib
import json
import os
import random
import secrets
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="agent-spaces-")
from awgscale.agent import errors  # noqa: E402
from awgscale.agent.commands import Command  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent import labels as naming  # noqa: E402
from awgscale.agent.spaces.models import StoredSpaces  # noqa: E402
from awgscale.agent.spaces.service import SpacesService  # noqa: E402
from awgscale.agent.spaces.sets import keys_by_address, sets_text  # noqa: E402
from awgscale.agent.users.directory import user_of  # noqa: E402
from awgscale.agent.users.models import StoredGroups, version_of  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
REAL_TOKEN_HEX = secrets.token_hex


GOLDEN_FILE = os.path.join(HERE, "golden", "agent_spaces.json")
WRITE = os.environ.get("GOLDEN_WRITE") == "1"
GOLDEN = {} if WRITE else json.load(open(GOLDEN_FILE))


def as_golden(key, value):
    """→ (the value as JSON keeps it, what the golden file has for it); GOLDEN_WRITE=1 records it instead."""
    value = json.loads(json.dumps(value, ensure_ascii=False))
    if WRITE:
        GOLDEN[key] = value
    return value, GOLDEN.get(key)


def write_golden():
    if WRITE:
        with open(GOLDEN_FILE, "w") as fh:
            json.dump(GOLDEN, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:1200])


def key(n):
    return f"{n:043d}="


def packed(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


class Ids:
    """The ids handed out, made predictable: the n-th asked for is always the same."""

    def __init__(self):
        self.n = 0

    def __call__(self, nbytes):
        self.n += 1
        return f"{self.n:06x}"


class World:
    """The users, the spaces and groups, the services around them."""

    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        self.sets = os.path.join(self.dir, "spaces.sets")
        self.lock = os.path.join(self.dir, ".users.lock")
        self.clients = [{"name": n, "ip": f"10.9.9.{i + 2}", "expires_at": None} for i, n in enumerate(["alice", "bob", "carol", "dave"])]
        self.keys = {c["ip"]: key(i) for i, c in enumerate(self.clients)}
        self.spaces = [{"id": "s00001", "name": "Дом", "on": True, "members": ["alice", "bob", "gone"]}]
        self.groups = [{"id": "aaaaaa", "name": "A", "space": True, "exit": None, "filter": "std", "members": ["carol", "dave"]}]
        self.watch_says = None            # set: the watch service refuses with these words
        self.store_down = False
        self.asked = []
        self.lines = []

    def allowed_ips(self):
        return "".join(f"{k}\t{ip}/32\n" for ip, k in self.keys.items()) + "garbage\tline\n"

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]


class NewPath:
    def __init__(self, world):
        w = world

        class Dir:
            async def list(self, force=False):
                w.asked.append(("list", force))
                return [user_of(c) for c in w.clients]

        class Spaces:
            async def read(self):
                if w.store_down:
                    raise errors.StoreDown("нет связи")
                return StoredSpaces.of(json.loads(json.dumps(w.spaces)))

            async def save(self, records):
                w.spaces = json.loads(json.dumps(list(records)))

        class Groups:
            async def read(self):
                return StoredGroups.of(json.loads(json.dumps(w.groups)))

        class Rules:
            async def load(self, timeout):
                w.asked.append(("watch", "spaces-apply"))
                return Outcome(w.watch_says is None, w.watch_says or "")

        class Writer:
            async def write(self, entry):
                w.lines.append(entry.line())

        async def host(cmd, stdin=None, timeout=30):
            w.asked.append(("run", tuple(cmd)))
            return Result(0, w.allowed_ips(), "")

        self.service = SpacesService(Dir(), Spaces(), Groups(), Rules(), host, "awg0", Path(w.sets), Path(w.lock))
        journal = Journal(None, Writer())
        service = self.service

        async def make():
            dispatcher = Dispatcher()
            dispatcher.register(Command.SPACES_GET, Handled(service.show))
            dispatcher.register(Command.SPACES_SET, Handled(service.replace, journal, before=service.stored_before))
            return dispatcher
        self.dispatcher = LOOP.run_until_complete(make())

    def ask(self, arguments):
        return LOOP.run_until_complete(self.dispatcher.dispatch(arguments, Caller.PANEL))

    def compile(self, spaces, groups):
        return LOOP.run_until_complete(self.service.compile(spaces, groups))


def read(path):
    try:
        return open(path).read()
    except OSError:
        return None


def t_pure():
    print("\n— названия, отпечатки и файл для сторожа")
    rnd = random.Random(5)
    alphabet = ["a", "Я", " ", "  ", "\t", "\n", "\x00", "\x7f", "x" * 30, "«Дом»", ""]
    labels = []
    for _ in range(3000):
        text = rnd.choice([None, 5, ["x"]]) if rnd.random() < 0.05 else "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 6)))
        limit = rnd.choice([40, 60])
        labels.append(naming.clean_label(text, limit))
    seen, expected = as_golden("labels", digest(labels))
    ok(seen == expected, "3000 случайных названий: принято, отвергнуто и вычищено как записано", [seen, expected])
    names = [f"user{i}" for i in range(7)]
    differ = 0
    seen_lines = 0
    compiled = []
    new_world = World("pure-new")
    new_path = NewPath(new_world)
    for _ in range(400):
        clients = [{"name": n, "ip": f"10.9.9.{i + 2}", "expires_at": None} for i, n in enumerate(names) if rnd.random() < 0.85]
        keys = {c["ip"]: key(i) for i, c in enumerate(clients) if rnd.random() < 0.9}
        spaces = [{"id": rnd.choice([f"s{i:05d}", "BAD", 7]), "name": f"S{i}", "on": rnd.random() < 0.7, "members": rnd.sample(names + ["ghost"], rnd.randint(0, 5))} for i in range(rnd.randint(0, 3))]
        groups = [{"id": f"g{i:05d}", "name": f"G{i}", "space": rnd.random() < 0.5, "members": rnd.sample(names, rnd.randint(0, 4))} for i in range(rnd.randint(0, 3))]
        says = rnd.choice([None, None, "nft: сбой", " " + "x" * 300, ""])
        new_world.clients = clients
        new_world.keys = keys
        new_world.watch_says = says
        new_world.groups = groups
        mine = new_path.compile(json.loads(json.dumps(spaces)), groups)
        compiled.append([mine, read(new_world.sets)])
        seen_lines += len((read(new_world.sets) or "").splitlines())
        addresses = {c["name"]: c["ip"] for c in clients}
        differ += sets_text(spaces, groups, addresses, keys_by_address(new_world.allowed_ips())) != read(new_world.sets)
    seen, expected = as_golden("compiled", digest(compiled))
    ok(seen == expected and differ == 0 and seen_lines > 200, f"400 случайных наборов пространств и групп: файл для сторожа и ответ о применении — как записано ({seen_lines} строк)", [seen, expected, differ])
    groups = [{"id": "gggggg", "name": "G", "space": True, "members": ["user0", "user1"]}]
    new_world.clients = [{"name": "user0", "ip": "10.9.9.2", "expires_at": None}, {"name": "user1", "ip": "10.9.9.3", "expires_at": None}]
    new_world.keys = {"10.9.9.2": key(1), "10.9.9.3": key(2)}
    new_world.groups = groups
    new_world.watch_says = None
    mine = new_path.compile([], None)
    ok(mine is None and read(new_world.sets) == f"sp_ggggggg 10.9.9.2,{key(1)} 10.9.9.3,{key(2)}\n",
       "группы не переданы — берутся из хранилища; набор группы зовётся sp_g<id>", read(new_world.sets))


def t_commands():
    print("\n— команды через диспетчер")
    full = [{"id": "s00001", "name": "  Дом   и сад ", "on": True, "members": ["bob", "alice", "bob", "ghost"]},
            {"id": "zzzzzz", "name": "Игры", "on": False, "members": ["carol"]}, {"name": "Новое", "on": 1, "members": []}]
    steps = [
        ("spaces-get", lambda w: None, lambda w: ["spaces-get"]),
        ("spaces-set: переименование, новые, повтор и неизвестный в составе", lambda w: None, lambda w: ["spaces-set", packed({"base": version_of(w.spaces), "spaces": full})]),
        ("spaces-set: на старом отпечатке — отказ со свежим списком", lambda w: None, lambda w: ["spaces-set", packed({"base": "000000000000", "spaces": []})]),
        ("spaces-set: без отпечатка", lambda w: None, lambda w: ["spaces-set", packed({"spaces": []})]),
        ("spaces-set: не список", lambda w: None, lambda w: ["spaces-set", packed({"base": version_of(w.spaces), "spaces": {"a": 1}})]),
        ("spaces-set: больше тридцати", lambda w: None, lambda w: ["spaces-set", packed({"base": version_of(w.spaces), "spaces": [{"name": f"n{i}"} for i in range(31)]})]),
        ("spaces-set: запись не того вида", lambda w: None, lambda w: ["spaces-set", packed({"base": version_of(w.spaces), "spaces": ["x"]})]),
        ("spaces-set: пустое название", lambda w: None, lambda w: ["spaces-set", packed({"base": version_of(w.spaces), "spaces": [{"name": "  "}]})]),
        ("spaces-set: негодный состав", lambda w: None, lambda w: ["spaces-set", packed({"base": version_of(w.spaces), "spaces": [{"name": "X", "members": ["-bad"]}]})]),
        ("spaces-set: состав слишком велик", lambda w: None, lambda w: ["spaces-set", packed({"base": version_of(w.spaces), "spaces": [{"name": "X", "members": [f"u{i}" for i in range(65)]}]})]),
        ("spaces-set: не JSON", lambda w: None, lambda w: ["spaces-set", base64.b64encode(b"[1]").decode()]),
        ("spaces-set: сторож отказал — сохранено, но не применено", lambda w: setattr(w, "watch_says", "nft: сбой"),
         lambda w: ["spaces-set", packed({"base": version_of(w.spaces), "spaces": [dict(w.spaces[0], on=False)] + w.spaces[1:]})]),
        ("spaces-set: удаление и выключение", lambda w: setattr(w, "watch_says", None), lambda w: ["spaces-set", packed({"base": version_of(w.spaces), "spaces": [dict(w.spaces[1], on=True)]})]),
        ("spaces-get: участника больше нет", lambda w: setattr(w, "clients", [c for c in w.clients if c["name"] != "carol"]), lambda w: ["spaces-get"]),
        ("spaces-get: хранилище недоступно", lambda w: setattr(w, "store_down", True), lambda w: ["spaces-get"]),
        ("spaces-set: хранилище недоступно", lambda w: None, lambda w: ["spaces-set", packed({"base": "x", "spaces": []})]),
    ]
    new_world = World("new")
    new_path = NewPath(new_world)
    for name, prepare, arguments in steps:
        prepare(new_world)
        new_world.asked.clear()
        new_world.lines.clear()
        secrets.token_hex = Ids()
        mine = new_path.ask(arguments(new_world))
        secrets.token_hex = REAL_TOKEN_HEX
        seen, expected = as_golden(name, {"answer": mine, "spaces": new_world.spaces, "file": read(new_world.sets), "asked": new_world.asked, "journal": new_world.journal()})
        ok(seen == expected, f"{name}: записанный ответ, записи и файл, обращения, строка журнала", [seen, expected])
    ok(new_world.spaces == [{"id": "000001", "name": "Игры", "on": True, "members": ["carol"]}], "в итоге хранится одно включённое пространство", new_world.spaces)


def main():
    t_pure()
    t_commands()
    write_golden()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
