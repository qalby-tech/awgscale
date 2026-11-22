#!/usr/bin/env python3
"""The users' commands that live in the package (awgscale.agent.users):
ugroups-get, user-exit, expire, qr and suspend sent through the real dispatcher
give the answers, leave the files, ask the other services the things and write
the journal lines recorded in tests/golden/agent_users.json; the pins file and
the lists' fingerprints come out as recorded; the list of users kept in a file
is read and written in the shape it has always had.

    uv run python tests/agent_users.py
    GOLDEN_WRITE=1 uv run python tests/agent_users.py    (records the golden file anew)

Nothing live is touched."""
import asyncio
import hashlib
import json
import os
import random
import sys
import tempfile
import time
from datetime import UTC, datetime
from ipaddress import IPv4Address
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agent-users-")

from awgscale.agent.cascade import conf  # noqa: E402
from awgscale.agent.cascade.models import checked  # noqa: E402
from awgscale.agent.clients.http import ServiceDown  # noqa: E402
from awgscale.agent.commands import Command  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent.users import pins  # noqa: E402
from awgscale.agent.users.directory import Directory, user_of  # noqa: E402
from awgscale.agent.users.pauses import Pauses  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402
from awgscale.agent.users.models import StoredGroups, UserGroup, version_of  # noqa: E402
from awgscale.agent.users.service import UsersService  # noqa: E402
from awgscale.users.models import UserView  # noqa: E402

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
SEED = open(os.path.join(ROOT, "tests", "cascade.conf")).read()
NOW = 1_800_000_000


GOLDEN_FILE = os.path.join(HERE, "golden", "agent_users.json")
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
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:900])


def t_pure():
    print("\n— закрепления и отпечатки списков")
    rnd = random.Random(3)
    names = [f"user{i}" for i in range(8)]
    differ = 0
    results = []
    for _ in range(3000):
        clients = [{"name": n, "ip": f"10.9.9.{i + 2}", "expires_at": None} for i, n in enumerate(names) if rnd.random() < 0.8]
        if rnd.random() < 0.1:
            clients = []
        groups = [{"id": f"g{i:05d}", "name": f"G{i}", "exit": rnd.choice([None, None, 1, 3]), "space": rnd.random() < 0.3, "members": rnd.sample(names, rnd.randint(0, 3))} for i in range(rnd.randint(0, 3))]
        by_hand = {n: rnd.choice([1, 3, 7]) for n in rnd.sample(names + ["ghost"], rnd.randint(0, 3))}
        group_pins = pins.group_pins([UserGroup.of(g) for g in groups])
        results.append([pins.pins_text([UserGroup.of(g) for g in groups], [user_of(c) for c in clients], by_hand), group_pins, version_of(groups)])
        differ += version_of(groups) != StoredGroups.of(groups).version
    seen, expected = as_golden("pure", digest(results))
    ok(seen == expected and differ == 0, "3000 случайных наборов: файл закреплений, закрепления групп и отпечаток списка — как записано", [seen, expected, differ])


class World:
    """The users, their groups and pins, the services around them."""

    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        self.pins = os.path.join(self.dir, "pins.map")
        self.lock = os.path.join(self.dir, ".users.lock")
        self.clients = [{"name": "alice", "ip": "10.9.9.2", "expires_at": None}, {"name": "bob", "ip": "10.9.9.3", "expires_at": NOW + 86400}, {"name": "carol", "ip": "10.9.9.4", "expires_at": None}]
        self.groups = [{"id": "aaaaaa", "name": "A", "space": False, "exit": 3, "filter": "std", "members": ["bob", "alice", "gone"]},
                       {"id": "bbbbbb", "name": "B", "space": True, "exit": None, "filter": "kids", "members": ["carol"]}]
        self.hand = {}
        self.cascade = checked(conf.parse(SEED).wire()).wire()
        self.users_down = False
        self.users_refuse = None
        self.store_down = False
        self.asked = []
        self.lines = []
        self.paused = {}                  # the store: name → Unix seconds
        self.ipset = None                 # the kernel's set; None while it does not exist
        self.rules = []
        self.ips = os.path.join(self.dir, "suspended.ips")
        self.commands = []

    def host(self, cmd):
        """ipset and iptables, as far as the pauses use them. → (code, stdout)"""
        cmd = list(cmd)
        self.commands.append(cmd)
        if cmd[:2] == ["ipset", "list"]:
            if self.ipset is None:
                return 1, ""
            return 0, "create cascade_susp hash:ip\n" + "".join(f"add cascade_susp {ip}\n" for ip in sorted(self.ipset))
        if cmd[:2] == ["ipset", "create"]:
            self.ipset = set() if self.ipset is None else self.ipset
            return 0, ""
        if cmd[:2] == ["ipset", "add"]:
            self.ipset.add(cmd[3])
            return 0, ""
        if cmd[:2] == ["ipset", "del"]:
            self.ipset.discard(cmd[3])
            return 0, ""
        if cmd[:4] == ["iptables", "-w", "-t", "raw"]:
            if cmd[4] == "-C":
                return (0 if cmd[6:] in self.rules else 1), ""
            if cmd[4] == "-I":
                self.rules.insert(0, cmd[7:])
                return 0, ""
            if cmd[4] == "-D":
                self.rules.remove(cmd[6:])
                return 0, ""
        return 1, ""

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]

    def config(self, name):
        if self.users_down or name not in [c["name"] for c in self.clients]:
            return None
        return {"ok": True, "name": name, "conf": "[Interface]\n", "uri": "vpn://x", "conf_png": "AA", "uri_png": "BB"}


class NewPath:
    def __init__(self, world):
        self.world = world
        w = world

        class Dir:
            async def list(self, force=False):
                w.asked.append(("list", force))
                return [user_of(c) for c in w.clients]

        class Users:
            async def set_expiry(self, names, term):
                w.asked.append(("expiry", tuple(names), term))
                if w.users_down:
                    return Outcome(False, "сервис пользователей не отвечает")
                if w.users_refuse:
                    return Outcome(False, w.users_refuse)
                return Outcome(True)

            async def config(self, name):
                w.asked.append(("config", name))
                return w.config(name)

        class Groups:
            async def read(self):
                return StoredGroups.of(json.loads(json.dumps(w.groups)))

        class Hand:
            async def read(self):
                return dict(w.hand)

            async def save(self, pins_):
                w.hand = dict(pins_)

            async def whole(self):
                return set(getattr(w, "whole", set()))

            async def save_whole(self, names):
                w.whole = set(names)

        class Cascade:
            async def load(self):
                if w.store_down:
                    from awgscale.agent.errors import StoreDown
                    raise StoreDown("нет связи")
                return checked(w.cascade)

            async def save(self, settings):
                pass

        class Watch:
            async def health(self, timeout):
                return Outcome(True)

            async def apply(self, timeout):
                return Outcome(True)

            async def pin(self, timeout):
                w.asked.append(("watch", "pin"))
                return Outcome(True)

        class Writer:
            async def write(self, entry):
                w.lines.append(entry.line())

        class PauseStore:
            async def read(self):
                return {name: datetime.fromtimestamp(since, UTC) for name, since in w.paused.items()}

            async def save(self, pauses_):
                w.paused = {name: int(since.timestamp()) for name, since in pauses_.items()}

        async def host(cmd, stdin=None, timeout=30):
            code, out = w.host(cmd)
            return Result(code, out, "")

        pauses_ = Pauses(PauseStore(), host, "awg0", Path(world.ips))
        service = UsersService(Dir(), Users(), Groups(), Hand(), Cascade(), Watch(), PauseStore(), pauses_, Path(world.pins), Path(world.lock))
        journal = Journal(None, Writer())

        async def make():
            dispatcher = Dispatcher()
            dispatcher.register(Command.UGROUPS_GET, Handled(service.show_groups))
            dispatcher.register(Command.USER_EXIT, Handled(service.choose_exit, journal))
            dispatcher.register(Command.EXPIRE, Handled(service.set_term, journal))
            dispatcher.register(Command.QR, Handled(service.device_code))
            dispatcher.register(Command.SUSPEND, Handled(service.pause, journal))
            return dispatcher
        self.dispatcher = LOOP.run_until_complete(make())

    def ask(self, arguments):
        return LOOP.run_until_complete(self.dispatcher.dispatch(arguments, Caller.PANEL))


def in_runs(commands):
    """The host's commands, each run of the same command (ipset add …, ipset add …) sorted: their order inside it follows a set's."""
    out = []
    for command in commands:
        if out and out[-1][0][:2] == command[:2]:
            out[-1].append(command)
        else:
            out.append([command])
    return [command for run in out for command in sorted(run)]


def read(path):
    try:
        return open(path).read()
    except OSError:
        return None


def t_commands():
    print("\n— команды через диспетчер")
    steps = [
        ("ugroups-get", lambda w: None, ["ugroups-get"]),
        ("user-exit: на выход", lambda w: None, ["user-exit", "1", "carol", "carol"]),
        ("user-exit: из закреплённой группы", lambda w: None, ["user-exit", "1", "alice"]),
        ("user-exit: авто", lambda w: None, ["user-exit", "auto", "alice"]),
        ("user-exit: нет такого выхода", lambda w: None, ["user-exit", "7", "carol"]),
        ("user-exit: нет такого пользователя", lambda w: None, ["user-exit", "1", "nobody", "carol"]),
        ("user-exit: хранилище недоступно", lambda w: setattr(w, "store_down", True), ["user-exit", "3", "bob"]),
        ("user-exit: пользователей не прочитать", lambda w: (setattr(w, "store_down", False), setattr(w, "saved_clients", w.clients), setattr(w, "clients", [])), ["user-exit", "auto", "carol"]),
        ("expire: новый срок", lambda w: setattr(w, "clients", w.saved_clients), ["expire", "7d", "bob", "alice", "bob"]),
        ("expire: без срока", lambda w: None, ["expire", "none", "bob"]),
        ("expire: нет такого пользователя", lambda w: None, ["expire", "1d", "bob", "ghost"]),
        ("expire: сервис отказал", lambda w: setattr(w, "users_refuse", "не вышло: " + "x" * 300), ["expire", "1d", "bob"]),
        ("expire: сервис не отвечает", lambda w: (setattr(w, "users_refuse", None), setattr(w, "users_down", True)), ["expire", "1d", "bob"]),
        ("qr: сервис не отвечает", lambda w: None, ["qr", "bob"]),
        ("qr: есть", lambda w: setattr(w, "users_down", False), ["qr", "bob"]),
        ("qr: нет такого", lambda w: None, ["qr", "ghost"]),
        ("suspend: двоих", lambda w: None, ["suspend", "on", "alice", "bob", "alice"]),
        ("suspend: ещё раз того же", lambda w: None, ["suspend", "on", "alice"]),
        ("suspend: снять с одного", lambda w: None, ["suspend", "off", "alice"]),
        ("suspend: нет такого пользователя", lambda w: None, ["suspend", "on", "ghost", "bob"]),
        ("suspend: приостановленный исчез из списка", lambda w: setattr(w, "clients", [c for c in w.clients if c["name"] != "bob"]), ["suspend", "on", "carol"]),
        ("suspend: снять с последнего", lambda w: None, ["suspend", "off", "carol"]),
        ("ugroups-get после всего", lambda w: None, ["ugroups-get"]),
    ]
    new_world = World("new")
    new_path = NewPath(new_world)
    for name, prepare, arguments in steps:
        prepare(new_world)
        new_world.asked.clear()
        new_world.lines.clear()
        new_world.commands.clear()
        mine = new_path.ask(arguments)
        kernel = [None if new_world.ipset is None else sorted(new_world.ipset), new_world.rules, in_runs(new_world.commands), read(new_world.ips)]
        now = time.time()
        fresh = all(abs(since - now) <= 60 for since in new_world.paused.values())
        seen, expected = as_golden(name, {"answer": mine, "kernel": kernel, "paused": sorted(new_world.paused), "hand": new_world.hand, "pins": read(new_world.pins),
                                          "asked": new_world.asked, "journal": new_world.journal()})
        ok(seen == expected and fresh, f"{name}: записанный ответ, закрепления и файл, обращения к сервисам, строка журнала", [seen, expected])


class Client:
    """The users service's client, as far as the directory needs it."""

    def __init__(self):
        self.views = [UserView(name="ann", ip=IPv4Address("10.9.8.2"), expires_at=None, created_at=datetime.fromtimestamp(NOW, UTC)),
                      UserView(name="bob", ip=IPv4Address("10.9.8.3"), expires_at=datetime.fromtimestamp(NOW + 3600, UTC), created_at=datetime.fromtimestamp(NOW, UTC))]
        self.down = False
        self.asked = 0

    async def list(self):
        self.asked += 1
        if self.down:
            raise ServiceDown("нет")
        return self.views


def t_directory():
    print("\n— список пользователей и файл рядом с ним")
    folder = tempfile.mkdtemp(prefix="dir-", dir=TMP)
    cache = Path(folder) / "run" / "panel-clients.json"
    tunnel = Path(folder) / "awg0.conf"
    tunnel.write_text("[Interface]\nPrivateKey = x\n\n[Peer]\n#_Name = zoe\nPublicKey = k\nAllowedIPs = 10.9.8.9/32\n\n[Peer]\n#_Name = yan\nAllowedIPs = 10.9.8.8/32, ::/0\n")
    client = Client()
    directory = Directory(client, cache, tunnel)
    run = LOOP.run_until_complete
    client.down = True
    ok([(u.name, str(u.ip)) for u in run(directory.list())] == [("zoe", "10.9.8.9"), ("yan", "10.9.8.8")],
       "сервис не отвечает, списка ещё не было — пользователи берутся из файла туннеля")
    client.down = False
    fresh = run(directory.list())
    kept = json.loads(cache.read_text())
    ok([u.name for u in fresh] == ["ann", "bob"] and client.asked == 2 and kept == [{"name": "ann", "ip": "10.9.8.2", "expires_at": None, "created_at": NOW}, {"name": "bob", "ip": "10.9.8.3", "expires_at": NOW + 3600, "created_at": NOW}],
       "список спрошен и положен в файл в прежнем виде (моменты — секунды Unix)", kept)
    run(directory.list())
    ok(client.asked == 2, "в течение минуты сервис заново не спрашивается")
    run(directory.list(force=True))
    ok(client.asked == 3, "…а когда нужен свежий — спрашивается")
    with open(cache, "w") as fh:
        json.dump([{"name": "old1", "ip": "10.9.8.7", "expires_at": NOW, "created_at": NOW - 5}], fh)
    got = run(directory.list())
    ok([(u.name, str(u.ip), int(u.expires_at.timestamp())) for u in got] == [("old1", "10.9.8.7", NOW)] and client.asked == 3, "файл прежнего вида читается как свой")
    os.utime(cache, (time.time() - 120, time.time() - 120))
    client.down = True
    ok([u.name for u in run(directory.list())] == ["old1"] and [c["name"] for c in json.loads(cache.read_text())] == ["old1"], "список устарел, а сервис не отвечает — показываются последние известные, а не «никого»")


def main():
    t_pure()
    t_commands()
    t_directory()
    write_golden()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
