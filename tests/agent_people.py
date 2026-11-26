#!/usr/bin/env python3
"""The users' commands — add, remove, diag, ugroups-set — as the package has
them (awgscale.agent.users): sent through the real dispatcher, each step's
answer, stored documents, compiled files, calls to the other services (in their
order) and journal lines are the ones recorded in tests/golden/agent_people.json
(recorded while the package was still checked against the single-file agent it
replaced, and equal to what that gave).

    uv run python tests/agent_people.py
    GOLDEN_WRITE=1 uv run python tests/agent_people.py   record again (after a deliberate change)

Nothing live is touched."""
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import random
import secrets
import sys
import tempfile
import time
import io
from datetime import UTC, datetime
from ipaddress import IPv4Address
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agent-people-")
GOLDEN_FILE = os.path.join(HERE, "golden", "agent_people.json")
WRITE = os.environ.get("GOLDEN_WRITE") == "1"

from awgscale.agent import errors  # noqa: E402
from awgscale.agent.adguard.api import AdGuard  # noqa: E402
from awgscale.agent.adguard.client import AdGuardDown, AdGuardRefused  # noqa: E402
from awgscale.agent.adguard.profiles import AdGuardProfiles, ProfileMemory  # noqa: E402
from awgscale.agent.clients.http import ServiceDown, ServiceRefused  # noqa: E402
from awgscale.agent.clients.users import RemovalsAnswer  # noqa: E402
from awgscale.agent.exits.working import CompiledExits  # noqa: E402
from awgscale.agent.commands import Command  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent.seen.service import SeenNotes  # noqa: E402
from awgscale.agent.spaces.models import StoredSpaces  # noqa: E402
from awgscale.agent.spaces.service import SpacesService  # noqa: E402
from awgscale.agent.tunnel import Tunnel  # noqa: E402
from awgscale.agent.users.accounts import AccountsService  # noqa: E402
from awgscale.agent.users.diagnosis import DiagnosisService  # noqa: E402
from awgscale.agent.users.directory import UsersService as UsersGateway, user_of  # noqa: E402
from awgscale.agent.users.forgetting import Forgetting  # noqa: E402
from awgscale.agent.users.models import StoredGroups, version_of  # noqa: E402
from awgscale.agent.users.pauses import Pauses  # noqa: E402
from awgscale.agent.users.pins import Pinning  # noqa: E402
from awgscale.agent.users.regrouping import GroupsService  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402
from awgscale.users.models import Created, Removal, Status as UserStatus  # noqa: E402
from awgscale.watch.conf import RouteMode  # noqa: E402
from awgscale.watch.health import ExitState  # noqa: E402
from awgscale.watch.models import ExitView, Status, Work  # noqa: E402

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
START = int(time.time())
CONF = 'CLIENT_IF="awg0"\nEXITS=(\n  "1:awgk1:10.77.1.2:exit1-nl:2"\n  "3:awgk3:10.77.3.2:exit2-de"\n)\n'


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:2500])


def key(n):
    return f"{n:043d}="


def packed(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


def moment(seconds):
    return None if seconds is None else datetime.fromtimestamp(seconds, UTC)


class World:
    """One world: the store, the users service, the tunnel, the kernel's set, the watch service, the blocker."""

    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        self.pins = os.path.join(self.dir, "pins.map")
        self.sets = os.path.join(self.dir, "spaces.sets")
        self.ips = os.path.join(self.dir, "suspended.ips")
        self.lock = os.path.join(self.dir, ".users.lock")
        self.conf = os.path.join(self.dir, "cascade.conf")
        with open(self.conf, "w") as fh:
            fh.write(CONF)
        self.clients = [{"name": n, "ip": f"10.9.9.{i + 2}", "expires_at": None} for i, n in enumerate(["alice", "bob", "carol", "dave"])]
        self.next_ip = 10
        self.groups = []
        self.spaces = []
        self.invites = []
        self.hand = {}
        self.paused = {}
        self.seen = {}                    # None: the notes were never moved into the store
        self.store_down = False
        self.users_down = False
        self.users_refuse = None
        self.fail_removal = {}            # name → the error the users service gives instead of removing
        self.ipset = None
        self.rules = []
        self.spaces_rc = None             # set: the watch service refuses to load the spaces, with these words
        self.status = None                # what the watch service says of the exits; None: it does not answer
        self.dumps = []                   # what `awg show awg0 dump` prints, one after another
        self.ag = {}                      # the blocker's clients by name
        self.ag_down = False
        self.ag_refuse = set()
        self.ag_top = []
        self.asked = []
        self.lines = []
        self.ids = 0

    # ── the store ────────────────────────────────────────────────────────────
    def get(self, doc):
        if self.store_down:
            raise ConnectionError("нет связи")
        return json.loads(json.dumps(getattr(self, {"ugroups": "groups", "spaces": "spaces", "invites": "invites", "suspended": "paused"}[doc])))

    def put(self, doc, value):
        if self.store_down:
            raise ConnectionError("нет связи")
        setattr(self, {"ugroups": "groups", "spaces": "spaces", "invites": "invites", "suspended": "paused"}[doc], json.loads(json.dumps(value)))

    def stored(self):
        return json.loads(json.dumps([self.groups, self.spaces, self.invites, self.hand, sorted(self.paused), {k: v for k, v in (self.seen or {}).items() if k != "leaving"},
                                      sorted((self.seen or {}).get("leaving") or {})]))

    # ── the host's tools ─────────────────────────────────────────────────────
    def host(self, cmd):
        cmd = list(cmd)
        self.asked.append(("run", *cmd))
        if cmd[:2] == ["awg", "show"] and cmd[3] == "allowed-ips":
            return 0, "".join(f"{key(int(c['ip'].split('.')[-1]))}\t{c['ip']}/32\n" for c in self.clients)
        if cmd[:2] == ["awg", "show"] and cmd[3] == "dump":
            return 0, self.dumps.pop(0) if len(self.dumps) > 1 else (self.dumps[0] if self.dumps else "")
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

    # ── the watch service ────────────────────────────────────────────────────
    def watch(self, command):
        """→ (done, what it said)"""
        self.asked.append(("watch", command))
        if command == "spaces-apply" and self.spaces_rc:
            return False, self.spaces_rc
        return True, ""

    # ── the blocker ──────────────────────────────────────────────────────────
    def adguard_get(self, path):
        """→ the answer; raises ConnectionError when it is away."""
        self.asked.append(("adguard", path))
        if self.ag_down:
            raise ConnectionError("refused")
        if path == "stats":
            return {"top_clients": json.loads(json.dumps(self.ag_top))}
        return {"clients": [dict(c) for c in self.ag.values()]}

    def adguard_post(self, path, payload):
        """→ None, or the words of a refusal; raises ConnectionError when it is away."""
        self.asked.append(("adguard", path, payload.get("name")))
        if self.ag_down:
            raise ConnectionError("refused")
        if path in self.ag_refuse:
            return "отказ блокировщика\n  на " + path
        if path == "clients/delete":
            self.ag.pop(payload["name"], None)
        if path == "clients/add":
            self.ag[payload["name"]] = dict(payload)
        if path == "clients/update":
            self.ag.pop(payload["name"], None)
            self.ag[payload["data"]["name"]] = dict(payload["data"])
        return None

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]

    def files(self):
        return [read(self.pins), read(self.sets), read(self.ips), None if self.ipset is None else sorted(self.ipset), self.rules]


class FakeUsers:
    """The users service's typed client."""

    def __init__(self, world):
        self.w = world

    async def add(self, name, expires):
        w = self.w
        w.asked.append(("users", "add", name, expires))
        if w.users_down:
            raise ServiceDown("нет")
        if w.users_refuse:
            raise ServiceRefused(w.users_refuse)
        w.next_ip += 1
        expires_at = None if expires is None else START + 7 * 86400
        w.clients.append({"name": name, "ip": f"10.9.9.{w.next_ip}", "expires_at": expires_at})
        return Created(name=name, ip=IPv4Address(f"10.9.9.{w.next_ip}"), expires_at=moment(expires_at))

    async def remove(self, names):
        w = self.w
        w.asked.append(("users", "remove", tuple(names)))
        if w.users_down:
            raise ServiceDown("нет")
        if w.users_refuse:
            raise ServiceRefused(w.users_refuse)
        results = []
        for name in names:
            if name in w.fail_removal:
                results.append(Removal(name=name, status=UserStatus.FAILED, error=w.fail_removal[name]))
            elif name in [c["name"] for c in w.clients]:
                w.clients = [c for c in w.clients if c["name"] != name]
                results.append(Removal(name=name, status=UserStatus.REMOVED))
            else:
                results.append(Removal(name=name, status=UserStatus.NOT_FOUND))
        return RemovalsAnswer(ok=all(r.status is UserStatus.REMOVED for r in results), results=results)


class Agent:
    def __init__(self, world):
        self.world = world
        w = world

        class Dir:
            async def list(self, force=False):
                w.asked.append(("list", force))
                return [user_of(c) for c in w.clients]

        def down(work):
            try:
                return work()
            except ConnectionError as e:
                raise errors.StoreDown(str(e)) from None

        class Groups:
            async def read(self):
                return StoredGroups.of(down(lambda: w.get("ugroups")))

            async def save(self, records):
                down(lambda: w.put("ugroups", list(records)))

        class Spaces:
            async def read(self):
                return StoredSpaces.of(down(lambda: w.get("spaces")))

            async def save(self, records):
                down(lambda: w.put("spaces", list(records)))

        class Invites:
            async def read(self):
                return down(lambda: w.get("invites"))

            async def save(self, records):
                down(lambda: w.put("invites", list(records)))

        class Hand:
            async def read(self):
                down(lambda: w.get("spaces"))
                return dict(w.hand)

            async def save(self, pins):
                w.hand = dict(pins)

            async def whole(self):
                return set(getattr(w, "whole", set()))

            async def save_whole(self, names):
                w.whole = set(names)

        class PauseStore:
            async def read(self):
                return {name: moment(since) for name, since in down(lambda: w.get("suspended")).items()}

            async def save(self, pauses):
                down(lambda: w.put("suspended", {name: int(since.timestamp()) for name, since in pauses.items()}))

        class Seen:
            async def load(self):
                down(lambda: w.get("spaces"))
                if w.seen is None:
                    raise errors.StoreDown("наблюдения журнала ещё не перенесены в базу (store-init)")
                return json.loads(json.dumps(w.seen))

            async def save(self, notes):
                w.seen = json.loads(json.dumps(notes))

        class Whole:
            @contextlib.asynccontextmanager
            async def whole(self):
                yield

        class Ready:
            async def ping(self):
                down(lambda: w.get("spaces"))

        class Watch:
            async def health(self, timeout):
                return Outcome(*w.watch("health"))

            async def apply(self, timeout):
                return Outcome(*w.watch("apply"))

            async def pin(self, timeout):
                return Outcome(*w.watch("pin"))

            async def load(self, timeout):
                return Outcome(*w.watch("spaces-apply"))

            async def status(self, timeout):
                w.asked.append(("watch", "status-json"))
                return w.status

        class AdGuardClient:
            async def get(self, path, timeout=5):
                try:
                    return w.adguard_get(path)
                except ConnectionError as e:
                    raise AdGuardDown(str(e), "ConnectionError") from None

            async def post(self, path, payload, timeout=10):
                try:
                    refused = w.adguard_post(path, payload)
                except ConnectionError as e:
                    raise AdGuardDown(str(e), "ConnectionError") from None
                if refused:
                    raise AdGuardRefused(400, "Bad Request", refused)

        class Exits:
            def weights(self):
                return CompiledExits(Path(w.conf)).weights()

        class Writer:
            async def write(self, entry):
                w.lines.append(entry.line())

        async def host(cmd, stdin=None, timeout=30):
            code, out = w.host(cmd)
            return Result(code, out, "")

        async def no_sleep(seconds):
            w.asked.append(("sleep", seconds))

        journal = Journal(None, Writer())
        adguard = AdGuard(AdGuardClient())
        pauses = Pauses(PauseStore(), host, "awg0", Path(w.ips))
        pinning = Pinning(Hand(), Watch(), Path(w.pins))
        spaces = SpacesService(Dir(), Spaces(), Groups(), Watch(), host, "awg0", Path(w.sets), Path(w.lock))
        profiles = AdGuardProfiles(adguard, Groups(), journal, Path(w.lock), ProfileMemory())
        forgetting = Forgetting(Dir(), Hand(), PauseStore(), pauses, Spaces(), Groups(), pinning, profiles, spaces, Whole(), Path(w.lock))
        seen = SeenNotes(Seen(), Whole(), journal, Path(w.lock))
        accounts = AccountsService(Ready(), Dir(), UsersGateway(FakeUsers(w)), forgetting, seen, Watch())
        groups = GroupsService(Dir(), Groups(), Invites(), Exits(), pinning, Spaces(), spaces, profiles, Whole(), Path(w.lock))
        diagnosis = DiagnosisService(Dir(), Tunnel(host, "awg0"), PauseStore(), Watch(), adguard, no_sleep)

        async def make():
            dispatcher = Dispatcher()
            dispatcher.register(Command.ADD, Handled(accounts.add, journal))
            dispatcher.register(Command.REMOVE, Handled(accounts.remove, journal))
            dispatcher.register(Command.UGROUPS_SET, Handled(groups.replace, journal, before=groups.stored_before))
            dispatcher.register(Command.DIAG, Handled(diagnosis.check))
            return dispatcher
        self.dispatcher = LOOP.run_until_complete(make())

    def ask(self, arguments):
        self.world.ids = 0
        secrets.token_hex = lambda nbytes=None: self.world_id()
        return LOOP.run_until_complete(self.dispatcher.dispatch(arguments, Caller.PANEL))

    def world_id(self):
        self.world.ids += 1
        return f"{self.world.ids:06x}"


REAL_TOKEN_HEX = secrets.token_hex


def read(path):
    try:
        return open(path).read()
    except OSError:
        return None


REAL_SLEEP = time.sleep


CLOCK = {"now": START}


def same_second():
    """The commands look at the clock (how long ago, how long is left): each step is asked early in a second, and its moments are counted from it."""
    while time.time() % 1 > 0.25:
        REAL_SLEEP(0.02)
    CLOCK["now"] = int(time.time())


def scrubbed(value, world, base="start"):
    """What a run cannot repeat, replaced by what it is: the moments (as seconds from the start of the run, or with
    base="now" from the step's clock, which is what the step's own moments are counted from) and the world's temporary directory."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)) and abs(value - START) < 90 * 86400:
        return f"<start{int(value - START):+d}>" if base == "start" else f"<now{int(value - CLOCK['now']):+d}>"
    if isinstance(value, str):
        return value.replace(world.dir, "<world>").replace(TMP, "<tmp>")
    if isinstance(value, dict):
        return {str(key): scrubbed(item, world, base) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrubbed(item, world, base) for item in value]
    return value


def seen_after(reply, world, base="start"):
    """One step as the golden file keeps it."""
    return scrubbed(json.loads(json.dumps({"reply": reply, "stored": world.stored(), "files": world.files(), "asked": world.asked, "journal": world.journal(),
                                           "blocker": world.ag, "clients": world.clients}, ensure_ascii=False, default=str)), world, base)


GOLDEN = {} if WRITE else json.load(open(GOLDEN_FILE))
RECORDED = {}


def as_recorded(key, seen, text):
    """The step against what is recorded under its key (or, with GOLDEN_WRITE=1, recorded)."""
    RECORDED[key] = seen
    if WRITE:
        return
    expected = GOLDEN.get(key)
    same = seen == expected
    ok(same, text, None if same else {part: [expected.get(part) if isinstance(expected, dict) else expected, seen[part]] for part in seen
                                      if not isinstance(expected, dict) or expected.get(part) != seen[part]})


def steps_on(steps, world, section, what, clock=True, base="start"):
    """Each step through the dispatcher: the answer, the stored documents, the files, the calls and the journal as recorded.
    clock=False: the steps do not look at the clock, and are not made to wait for the start of a second;
    base: what the moments are counted from (see scrubbed)."""
    agent = Agent(world)
    for name, prepare, arguments in steps:
        if clock:
            same_second()
        prepare(world)
        world.asked.clear()
        world.lines.clear()
        reply = agent.ask(arguments(world) if callable(arguments) else arguments)
        as_recorded(f"{section}: {name}", seen_after(reply, world, base), f"{name}: {what}")
    return world


def leftovers(w):
    """A user "eve" was here before and left everything behind (removed by the users service's own job)."""
    w.groups = [{"id": "aaaaaa", "name": "Семья", "space": True, "exit": 3, "filter": "kids", "members": ["alice", "eve", "bob"]},
                {"id": "bbbbbb", "name": "Гости", "space": False, "exit": None, "filter": "std", "members": ["carol"]}]
    w.spaces = [{"id": "s00001", "name": "Дом", "on": True, "members": ["eve", "carol", "dave"]}, {"id": "s00002", "name": "Дача", "on": False, "members": ["alice"]}]
    w.hand = {"eve": 1, "dave": 3}
    w.paused = {"eve": START - 50, "carol": START - 40}
    w.seen = {"v": 1, "at": START - 5, "clients": {"alice": None}, "exits": {}, "leaving": {"eve": START + 500, "zed": START + 400}}
    w.ipset = {"10.9.9.4"}
    w.rules = [["-i", "awg0", "-m", "set", "--match-set", "cascade_susp", "src", "-j", "DROP"]]
    w.ag = {"grp-aaaaaa": {"name": "grp-aaaaaa", "ids": ["10.9.9.2", "10.9.9.3"], "use_global_settings": False, "filtering_enabled": True, "parental_enabled": True,
                           "safebrowsing_enabled": True, "safe_search": {"enabled": True}}}


def t_add():
    print("\n— add: новый пользователь")
    what = "ответ, хранилище, файлы, обращения по порядку и строка журнала — как записано"
    steps = [
        ("новый пользователь без срока", lambda w: None, ["add", "eve"]),
        ("новый пользователь со сроком", lambda w: None, ["add", "fay", "7d"]),
        ("имя занято", lambda w: None, ["add", "alice"]),
        ("сервис пользователей отказал", lambda w: setattr(w, "users_refuse", "свободных адресов нет: " + "x" * 300), ["add", "gus"]),
        ("сервис пользователей не отвечает", lambda w: (setattr(w, "users_refuse", None), setattr(w, "users_down", True)), ["add", "gus"]),
        ("наблюдения ещё не перенесены в базу", lambda w: (setattr(w, "users_down", False), setattr(w, "seen", None)), ["add", "gus"]),
        ("хранилище недоступно: пользователь не создаётся", lambda w: (setattr(w, "seen", {}), setattr(w, "store_down", True)), ["add", "hal"]),
    ]
    steps_on(steps, World("add"), "add", what)
    new_world = World("namesake")
    leftovers(new_world)
    steps_on([("тёзка ушедшего: группа, пространство, выбор выхода, пауза и пометка — ничего не унаследовано", lambda w: None, ["add", "eve"])], new_world, "add", what)
    ok(new_world.groups[0]["members"] == ["alice", "bob"] and new_world.spaces[0]["members"] == ["carol", "dave"] and new_world.hand == {"dave": 3} and sorted(new_world.paused) == ["carol"]
       and sorted(new_world.seen["leaving"]) == ["zed"] and ("users", "add", "eve", None) in new_world.asked and ("watch", "spaces-apply") in new_world.asked and new_world.ipset == {"10.9.9.4"},
       "…и это видно: тёзки нет ни в группе, ни в пространстве, ни среди закреплённых и приостановленных; правила пространств перезагружены", [new_world.stored(), new_world.asked])


def t_remove():
    print("\n— remove: удаление")
    what = "ответ, хранилище, файлы, обращения по порядку и строка журнала — как записано"

    def world_with_eve(name):
        w = World(name)
        leftovers(w)
        w.clients.append({"name": "eve", "ip": "10.9.9.9", "expires_at": START + 3600})
        w.ipset = {"10.9.9.4", "10.9.9.9"}
        w.ag["grp-aaaaaa"]["ids"] = ["10.9.9.2", "10.9.9.3", "10.9.9.9"]
        return w
    steps = [
        ("один: был в группе-пространстве с фильтром и закреплением, в пространстве, с ручным выходом и на паузе", lambda w: None, ["remove", "eve"]),
        ("двое, имя повторено", lambda w: None, ["remove", "carol", "dave", "carol"]),
        ("частично: одного сервис не удалил", lambda w: setattr(w, "fail_removal", {"bob": "туннель не ответил"}), ["remove", "alice", "bob"]),
        ("никого: сервис назвал причину", lambda w: None, ["remove", "bob"]),
        ("уже удалён сроком: не найден, но всё о нём забыто", lambda w: (setattr(w, "fail_removal", {}), w.groups[0]["members"].append("ghost"), w.hand.update(ghost=1)), ["remove", "ghost"]),
        ("сервис отказал целиком", lambda w: setattr(w, "users_refuse", "идёт другая операция"), ["remove", "bob"]),
        ("сервис не отвечает", lambda w: (setattr(w, "users_refuse", None), setattr(w, "users_down", True)), ["remove", "bob"]),
        ("хранилище недоступно: никого не удаляют", lambda w: (setattr(w, "users_down", False), setattr(w, "store_down", True)), ["remove", "bob"]),
        ("последний", lambda w: setattr(w, "store_down", False), ["remove", "bob"]),
    ]
    new_world = steps_on(steps, world_with_eve("remove"), "remove", what)
    ok(new_world.clients == [] and new_world.groups[0]["members"] == [] and new_world.hand == {} and new_world.paused == {}, "…в конце не осталось никого и ничего о них", new_world.stored())


def kids_lists_fit(world):
    """Only a group in the children's mode has add-on lists, and only known ones."""
    return all(group.get("filter") == "kids" and isinstance(group["kids_lists"], list) and set(group["kids_lists"]) <= {"nsfw", "gambling", "bypass", "social"}
               for group in world.groups if "kids_lists" in group)


def t_groups():
    print("\n— ugroups-set: группы пользователей")
    what = "ответ, хранилище, файлы, обращения по порядку и строка журнала — как записано"
    v = version_of

    def sent(groups, base=None):
        return lambda w: ["ugroups-set", packed({"groups": groups, "base": v(w.groups) if base is None else base})]
    A = {"id": "000001", "name": "Семья", "members": ["alice", "bob", "nobody", "alice"]}
    B = {"id": "000002", "name": "Гости", "members": ["carol"]}
    invite = lambda group: {"id": "i0000" + group[-1], "hash": "h", "group": group, "left": 1, "total": 1, "expires_at": START + 9999, "created": START, "used": []}
    steps = [
        ("две новые группы", lambda w: None, sent([{"name": "  Семья ", "members": ["alice", "bob", "nobody", "alice"]}, {"name": "Гости", "members": ["carol"], "space": 1}])),
        ("то же самое ещё раз: ничего не меняется", lambda w: None, sent([dict(A), dict(B, space=True)])),
        ("переименование и перенос пользователя", lambda w: None, sent([dict(A, name="Родня", members=["alice"]), dict(B, space=True, members=["carol", "bob"])])),
        ("группа закреплена за выходом, другой — детская фильтрация", lambda w: None, sent([dict(A, exit=3), dict(B, space=True, members=["carol", "bob"], filter="kids")])),
        ("страница без полей exit и filter: сохранённые остаются", lambda w: None, sent([dict(A), dict(B, space=True, members=["carol", "bob"])])),
        ("фильтрация «без блокировки», закрепление снято, пространство выключено", lambda w: None, sent([dict(A, exit=None, filter="off"), dict(B, members=["carol", "bob"], filter="std")])),
        ("закрепление за выходом, которого нет", lambda w: None, sent([dict(A, exit=7), dict(B)])),
        ("закрепление не числом", lambda w: None, sent([dict(A, exit="3"), dict(B)])),
        ("закрепление — true", lambda w: None, sent([dict(A, exit=True), dict(B)])),
        ("сохранённое закрепление за исчезнувшим выходом становится «авто»", lambda w: w.groups[0].update(exit=9), sent([dict(A, exit=9, filter="off"), dict(B, members=["carol", "bob"])])),
        ("неизвестный режим фильтрации", lambda w: None, sent([dict(A, filter="strict"), dict(B)])),
        ("режим фильтрации списком (сбой сохранён как был)", lambda w: None, sent([dict(A, filter=["kids"]), dict(B)])),
        ("id списком у группы без поля filter (сбой сохранён как был)", lambda w: None, sent([{"id": ["x"], "name": "Новая", "members": []}])),
        ("id числом — группа получает новый id", lambda w: None, sent([dict(A, filter="off"), dict(B, members=["carol", "bob"]), {"id": 5, "name": "Третья", "members": ["dave"]}])),
        ("чужой и повторный id — тоже новые", lambda w: None, sent([dict(A, filter="off"), dict(A, name="Копия", members=[]), {"id": "zzzzzz", "name": "Чужая", "members": []}])),
        ("основано на старом списке: отказ с нынешним", lambda w: None, sent([dict(A)], base="000000000000")),
        ("без base: тоже отказ", lambda w: None, lambda w: ["ugroups-set", packed({"groups": []})]),
        ("имя пустое", lambda w: None, sent([dict(A, name="  ")])),
        ("имя длиннее 40", lambda w: None, sent([dict(A, name="я" * 41)])),
        ("имя не строкой", lambda w: None, sent([dict(A, name=5)])),
        ("два одинаковых имени (регистр не важен)", lambda w: None, sent([dict(A, name="Дом"), dict(B, name="ДОМ")])),
        ("имя «Без группы»", lambda w: None, sent([dict(A, name="без Группы")])),
        ("состав не списком", lambda w: None, sent([dict(A, members="alice")])),
        ("в составе не имя", lambda w: None, sent([dict(A, members=["alice", "bad name"])])),
        ("в составе 254", lambda w: None, sent([dict(A, members=[f"u{i}" for i in range(254)])])),
        ("пользователь в двух группах", lambda w: None, sent([dict(A, members=["alice"]), dict(B, members=["bob", "alice"])])),
        ("группа не объектом", lambda w: None, sent([dict(A), "Гости"])),
        ("groups не списком", lambda w: None, lambda w: ["ugroups-set", packed({"groups": {"a": 1}, "base": v(w.groups)})]),
        ("31 группа", lambda w: None, sent([{"name": f"G{i}", "members": []} for i in range(31)])),
        ("не объект в base64", lambda w: None, lambda w: ["ugroups-set", packed([1, 2])]),
        ("группа-пространство, а сторож пространства не загрузил", lambda w: setattr(w, "spaces_rc", "nft: сбой " + "y" * 300), sent([dict(A, space=True, members=["alice", "bob"], filter="off"), dict(B)])),
        ("детская фильтрация, а блокировщик отказал", lambda w: (setattr(w, "spaces_rc", None), setattr(w, "ag_refuse", {"clients/add", "clients/update"})), sent([dict(A, space=True, members=["alice", "bob"], filter="kids"), dict(B)])),
        ("блокировщик не отвечает", lambda w: (setattr(w, "ag_refuse", set()), setattr(w, "ag_down", True)), sent([dict(A, space=True, members=["alice", "bob"], filter="off"), dict(B)])),
        ("блокировщик вернулся", lambda w: setattr(w, "ag_down", False), sent([dict(A, space=True, members=["alice", "bob"], filter="kids"), dict(B)])),
        ("приглашения в группы есть; удаляется одна группа — её приглашение пропадает, остальные целы",
         lambda w: setattr(w, "invites", [invite("000001"), invite("000002"), dict(invite("000002"), id="i00009", group=None), dict(invite("000001"), id="i00008", expires_at=START - 5)]),
         sent([dict(B)])),
        ("список пользователей пуст (сбой чтения): закрепления и фильтрация не трогаются", lambda w: (setattr(w, "saved", w.clients), setattr(w, "clients", [])), sent([dict(B, exit=1, filter="kids", members=["carol"])])),
        ("хранилище недоступно", lambda w: (setattr(w, "clients", w.saved), setattr(w, "store_down", True)), sent([dict(B)], base="x")),
        ("все группы удалены", lambda w: setattr(w, "store_down", False), sent([])),
    ]
    new_world = World("groups")
    kids_fit = []
    for name, prepare, arguments in steps:
        steps_on([(name, prepare, arguments)], new_world, "ugroups-set", what, clock=False)
        kids_fit.append(kids_lists_fit(new_world))
    ok(all(kids_fit), "списки детского режима — только у детских групп и только известные", kids_fit)
    new_path = Agent(new_world)
    ok(new_path.ask(["ugroups-set", "это не base64"]) == {"ok": False, "error": "bad request"} == new_path.ask(["ugroups-set"]) and new_world.asked[-1:] != [("list", True)],
       "не base64 вовсе или без данных: до команды не доходит — «bad request», как на сокете")
    ok(new_world.groups == [] and new_world.invites == [{**invite("000002"), "id": "i00009", "group": None}], "…в конце групп нет, осталось только приглашение без группы", new_world.stored())

    # random payloads
    rnd = random.Random(5)
    names = ["alice", "bob", "carol", "dave", "nobody", "bad name", 7]
    new_world = World("random")
    new_path = Agent(new_world)
    prints = []
    kids_fit = []
    saved = 0
    for i in range(400):
        groups = []
        for j in range(rnd.randint(0, 4)):
            group = {"name": rnd.choice(["Семья", "Гости", "семья", " Дом  2 ", "", "Без группы", 5, "X" * rnd.choice([3, 41])])}
            if rnd.random() < 0.8:
                group["members"] = rnd.sample(names[:5], rnd.randint(0, 3)) if rnd.random() < 0.9 else rnd.choice([names, "alice", None])
            if rnd.random() < 0.7:
                group["id"] = rnd.choice([g["id"] for g in new_world.groups] + ["zzzzzz", 5, None, "000001"])
            if rnd.random() < 0.4:
                group["exit"] = rnd.choice([None, 1, 3, 7, "1", True, 1.0])
            if rnd.random() < 0.4:
                group["filter"] = rnd.choice(["std", "kids", "off", "x", None])
            if rnd.random() < 0.4:
                group["space"] = rnd.choice([True, False, 1, "", "да"])
            groups.append(group if rnd.random() < 0.97 else "мусор")
        new_world.asked.clear()
        new_world.lines.clear()
        base = v(new_world.groups) if rnd.random() < 0.9 else "0" * 12
        mine = new_path.ask(["ugroups-set", packed({"groups": groups, "base": base})])
        saved += bool(mine.get("ok"))
        kids_fit.append(kids_lists_fit(new_world))
        seen = json.dumps(seen_after(mine, new_world), ensure_ascii=False, sort_keys=True)
        prints.append(hashlib.sha256(seen.encode()).hexdigest()[:12])
    RECORDED["ugroups-set: 400 random"] = prints
    if not WRITE:
        recorded = GOLDEN.get("ugroups-set: 400 random") or []
        first = next((i for i, (a, b) in enumerate(zip(prints, recorded)) if a != b), None if len(prints) == len(recorded) else min(len(prints), len(recorded)))
        ok(first is None and all(kids_fit) and 60 < saved < 380,
           f"400 случайных наборов групп подряд ({saved} сохранено): ответы, хранилище, файлы, обращения и журнал — как записано (отпечатки)", first)


def t_no_doh():
    print("\n— ugroups-set: «Без чужого DNS»")
    world = World("no-doh")
    agent = Agent(world)

    def save(*groups):
        world.lines.clear()
        return agent.ask(["ugroups-set", packed({"groups": list(groups), "base": version_of(world.groups)})])
    A = {"id": "000001", "name": "Семья", "members": ["alice", "bob"]}
    B = {"id": "000002", "name": "Гости", "filter": "pro", "members": ["carol"]}
    first = save(dict(A), dict(B))
    stored = json.dumps(world.groups, sort_keys=True)
    ok(first.get("ok") and all("no_doh" not in group for group in world.groups + first["groups"]) and world.ag == {"grp-pro": world.ag.get("grp-pro")},
       "без переключателя: в хранилище и в ответе его нет — группы как прежде", [world.groups, sorted(world.ag)])
    on = save(dict(A, no_doh=True), dict(B, no_doh=False))
    ok(on.get("ok") and world.groups[0].get("no_doh") is True and "no_doh" not in world.groups[1] and on["groups"][0].get("no_doh") is True
       and sorted(world.ag) == ["grp-pro", "grp-std-doh"] and world.ag["grp-std-doh"]["tags"] == ["device_printer"],
       "обычной группе включён: сохранён, показан, её люди — в клиенте блокировщика с меткой списка шифрованных DNS", [world.groups, sorted(world.ag)])
    ok(any("без чужого DNS — включено" in str(line) for line in world.lines), "в журнале: «без чужого DNS — включено»", world.lines)
    kept = save(dict(A), dict(B))
    ok(kept.get("ok") and world.groups[0].get("no_doh") is True and sorted(world.ag) == ["grp-pro", "grp-std-doh"],
       "страница без поля no_doh (старая, приложение): сохранённое остаётся", world.groups)
    refused = save(dict(A, no_doh="yes"), dict(B))
    ok(not refused.get("ok") and "Без чужого DNS" in refused.get("error", "") and world.groups[0].get("no_doh") is True, "не true/false — отказ, ничего не меняется", refused)
    off = save(dict(A, no_doh=False), dict(B))
    ok(off.get("ok") and json.dumps(world.groups, sort_keys=True) == stored and sorted(world.ag) == ["grp-pro"],
       "выключен: группа хранится ровно как до включения, клиент grp-std-doh убран", [world.groups, sorted(world.ag)])
    ok(any("без чужого DNS — выключено" in str(line) for line in world.lines), "в журнале: «без чужого DNS — выключено»", world.lines)


def dump(*peers):
    """`awg show awg0 dump`: the interface's line, then a line per peer (address, endpoint, handshake, rx, tx)."""
    lines = ["PRIVATE\tPUBLIC\t51820\toff"]
    for address, endpoint, handshake, rx, tx in peers:
        lines.append("\t".join([key(1), "PSK", endpoint, f"{address}/32", str(handshake), str(rx), str(tx), "off"]))
    return "\n".join(lines) + "\n"


def status(*exits):
    made = datetime.fromtimestamp(START, UTC)
    views = [ExitView(index=index, name=name, iface=f"awgk{index}", state=ExitState(state), handshake=made, rx=1, tx=2, clients=[IPv4Address(c) for c in clients], probe_ms=12.5, changed=None)
             for index, name, state, clients in exits]
    work = Work(started=made, applied=made, last_tick=made, tick_seconds=0.5, ticks=3, tick_failed=0)
    return Status(time=made, exits=views, udp_via_exit=True, route_mode=RouteMode.LIST, force_exit_nets=1, ru_networks=2, force_exit_ips=3, force_direct_ips=4, watch=work)


def t_diag():
    print("\n— diag: что сервер знает об одном пользователе")
    what = "ответ и обращения по порядку — как записано"
    ip = "10.9.9.2"
    up = status((1, "exit1-nl", "up", [ip]), (3, "exit2-de", "down", []))

    def world(w, dumps, st=up, **more):
        w.dumps = list(dumps)
        w.status = st
        w.ag_top = [{"10.9.9.3": 5}, {ip: 40}, {ip: 2}]
        w.ag_down = False
        w.paused = {}
        w.store_down = False
        w.clients[0]["expires_at"] = None
        for name, value in more.items():
            setattr(w, name, value)
    recent = lambda: CLOCK["now"] - 20
    steps = [
        ("нет такого пользователя", lambda w: world(w, []), ["diag", "ghost"]),
        ("ключа нет в туннеле", lambda w: world(w, [dump(("10.9.9.3", "1.2.3.4:5", recent(), 1, 2))]), ["diag", "alice"]),
        ("ключ пропал между двумя замерами", lambda w: world(w, [dump((ip, "1.2.3.4:5", recent(), 1, 2)), dump()]), ["diag", "alice"]),
        ("на связи, идёт трафик, выход работает, DNS наш", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1000, 2000)), dump((ip, "203.0.113.7:51820", recent(), 400000, 900000))]), ["diag", "alice"]),
        ("на связи, трафика почти нет", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1000, 2000)), dump((ip, "203.0.113.7:51820", recent(), 1100, 2100))]), ["diag", "alice"]),
        ("приостановлен", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))], paused={"alice": CLOCK["now"] - 9}), ["diag", "alice"]),
        ("ни разу не подключался", lambda w: world(w, [dump((ip, "(none)", 0, 0, 0))]), ["diag", "alice"]),
        ("не на связи пять часов", lambda w: world(w, [dump((ip, "[2001:db8::1]:4500", CLOCK["now"] - 5 * 3600 - 100, 10, 20))]), ["diag", "alice"]),
        ("его выход недоступен", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))], st=status((1, "exit1-nl", "down", [ip]), (3, "exit2-de", "up", []))), ["diag", "alice"]),
        ("все выходы недоступны", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))], st=status((1, "exit1-nl", "down", []), (3, "exit2-de", "unknown", []))), ["diag", "alice"]),
        ("не закреплён ни за одним выходом", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))], st=status((1, "exit1-nl", "up", ["10.9.9.3"]))), ["diag", "alice"]),
        ("выходов нет вовсе", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))], st=status()), ["diag", "alice"]),
        ("сторож не отвечает", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))], st=None), ["diag", "alice"]),
        ("не спрашивает наш DNS при большом трафике", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 3_000_000, 3_000_000))], ag_top=[{"10.9.9.3": 5}]), ["diag", "alice"]),
        ("не спрашивает наш DNS, но и трафика мало", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 3_000, 3_000))], ag_top=[]), ["diag", "alice"]),
        ("блокировщик не отвечает", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))], ag_down=True), ["diag", "alice"]),
        ("счётчик блокировщика не числом", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))], ag_top=[{ip: "много"}]), ["diag", "alice"]),
        ("срок истекает через два дня", lambda w: (world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))]), w.clients[0].update(expires_at=CLOCK["now"] + 2 * 86400 + 5000)), ["diag", "alice"]),
        ("срок через месяц", lambda w: (world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))]), w.clients[0].update(expires_at=CLOCK["now"] + 30 * 86400 + 5000)), ["diag", "alice"]),
        ("срок уже вышел", lambda w: (world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))]), w.clients[0].update(expires_at=CLOCK["now"] - 500)), ["diag", "alice"]),
        ("хранилище недоступно", lambda w: world(w, [dump((ip, "203.0.113.7:51820", recent(), 1, 2))], store_down=True), ["diag", "alice"]),
    ]
    new_world = steps_on(steps, World("diag"), "diag", what, base="now")
    path = Agent(new_world)
    same_second()
    world(new_world, [dump((ip, "203.0.113.7:51820", recent(), 1000, 2000)), dump((ip, "203.0.113.7:51820", recent(), 400000, 900000))])
    got = path.ask(["diag", "alice"])
    texts = [item["text"] for item in got.get("items", [])]
    ok(got.get("endpoint") == "203.0.113.7" and [item["state"] for item in got["items"]] == ["ok", "ok", "ok", "ok"] and "Сейчас идёт трафик: ↓ 3.6 Мбит/с, ↑ 1.6 Мбит/с" in texts
       and "Пользуется нашим DNS: 42 запросов за сутки" in texts and "Выход exit1-nl работает" in texts,
       "…и это видно: адрес без порта, трафик в мегабитах, число запросов DNS — и ни одного названия сайта", got)
    ok("PSK" not in json.dumps(got) and key(1) not in json.dumps(got), "ни ключа, ни общего секрета туннеля в ответе нет")


def main():
    t_add()
    t_remove()
    real_stderr = sys.stderr
    sys.stderr = io.StringIO()              # two steps fail on purpose (recorded as they were): their tracebacks are not this run's news
    try:
        t_groups()
    finally:
        sys.stderr = real_stderr
    t_no_doh()
    t_diag()
    secrets.token_hex = REAL_TOKEN_HEX
    if WRITE:
        os.makedirs(os.path.dirname(GOLDEN_FILE), exist_ok=True)
        with open(GOLDEN_FILE, "w") as fh:
            json.dump(RECORDED, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print(f"recorded {len(RECORDED)} steps in {GOLDEN_FILE}")
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
