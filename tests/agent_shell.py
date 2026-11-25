#!/usr/bin/env python3
"""The commands only a shell runs — store-init, store-export, cascade-get,
cascade-set, spaces-sync, suspend-sync — as the package has them
(awgscale.agent.shell), on a real, throwaway Postgres: each step of two stories
gives the answer and exit code, the rows in every table, the files left on the
host, the calls and the journal lines written down in tests/golden/agent_shell.json.
And the real thing: `python -m awgscale.agent <command>` prints one JSON line
and exits with the right code.

    PG_DSN="host=127.0.0.1 port=35443 dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=... uv run python tests/agent_shell.py
    GOLDEN_WRITE=1 … tests/agent_shell.py     a deliberate change of behaviour: the stories as they go now become the expectation

Nothing live is touched."""
import asyncio
import base64
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agent-shell-")
import psycopg  # noqa: E402

from awgscale.agent.cascade import conf  # noqa: E402
from awgscale.agent.cascade.models import checked  # noqa: E402
from awgscale.agent.cascade.service import CascadeService  # noqa: E402
from awgscale.agent.cascade.store import StoredCascade  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.shell import Shell, ShellCommand, caller_of  # noqa: E402
from awgscale.agent.spaces.service import SpacesService  # noqa: E402
from awgscale.agent.spaces.store import SpacesInStore  # noqa: E402
from awgscale.agent.store.moving import StoreMoving  # noqa: E402
from awgscale.agent.store.store import Store  # noqa: E402
from awgscale.agent.users.directory import user_of  # noqa: E402
from awgscale.agent.users.pauses import Pauses  # noqa: E402
from awgscale.agent.users.service import UsersService  # noqa: E402
from awgscale.agent.users.store import StoredHandPins, StoredPauses, StoredUserGroups  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402

DSN = os.environ["PG_DSN"]
PASSWORD_FILE = Path(TMP) / "password"
PASSWORD_FILE.write_text(os.environ["PG_PASSWORD"] + "\n")
NEW = Store(DSN, PASSWORD_FILE)
LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
SEED = open(os.path.join(ROOT, "tests", "cascade.conf")).read()
CLIENTS = [{"name": "alice", "ip": "10.9.9.2", "expires_at": None}, {"name": "bob", "ip": "10.9.9.3", "expires_at": None}, {"name": "carol", "ip": "10.9.9.4", "expires_at": None}]
TABLES = ("meta", "suspended", "dns_devices", "site_groups", "site_group_items", "block_groups", "block_group_items", "spaces", "space_members", "user_groups", "user_group_members",
          "invites", "invite_uses")
MARKS = ("ready", "moved:dns", "moved:seen", "moved:cascade")
RULES_MARK = "moved:rules"
GOLDEN = Path(HERE) / "golden" / "agent_shell.json"
# The day in the name of an archived file, and the line of cascade.conf's header that says how to change it.
DAY_RE = re.compile(r"""
    \.until-\d{4}-\d{2}-\d{2}        # archive/seen.json.until-2026-11-19
""", re.VERBOSE)
HOW_TO_CHANGE_RE = re.compile(r"""
    ^(?P<lead>\#\ lost\ with\ the\ next\ change\.\ To\ change:).*$     # # lost with the next change. To change:  python -m awgscale.agent cascade-get / cascade-set
""", re.VERBOSE | re.MULTILINE)


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:2500])


def sql(q, *a):
    with psycopg.connect(DSN, password=os.environ["PG_PASSWORD"], autocommit=True) as c:
        cur = c.execute(q, a)
        return cur.fetchall() if cur.description else None


def reset():
    sql("DROP SCHEMA public CASCADE")
    sql("CREATE SCHEMA public")


def rows():
    """Every row of every table; the moments of the marks are not compared."""
    held = {}
    for table in TABLES:
        try:
            got = sql(f"SELECT * FROM {table} ORDER BY 1, 2")
        except psycopg.Error:
            held[table] = None
            continue
        held[table] = [(key, "T") if table == "meta" and key in MARKS else tuple(row) for key, *rest in got for row in [(key, *rest)]
                       if not (table == "meta" and key == RULES_MARK)]      # the rules came later: their tables and mark are looked at apart
    return json.loads(DAY_RE.sub(".until-<day>", json.dumps(held, default=str, ensure_ascii=False)))


def packed(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


class Side:
    """One path's own folder of the host's files, and what it was asked on the way."""

    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        os.makedirs(os.path.join(self.dir, "lists"))
        self.asked = []
        self.lines = []
        self.watch_fails = set()
        self.ipset = None
        self.rules = []

    def path(self, rel):
        return os.path.join(self.dir, rel)

    def write(self, rel, text):
        with open(self.path(rel), "w") as fh:
            fh.write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False))

    def remove(self, rel):
        with contextlib.suppress(OSError):
            os.remove(self.path(rel))

    def files(self):
        """What is on the host now: every file's name and text (the lock files and the moment in the note aside)."""
        found = {}
        for folder, _, names in os.walk(self.dir):
            for name in names:
                full = os.path.join(folder, name)
                rel = os.path.relpath(full, self.dir)
                if rel.endswith(".lock"):
                    continue
                text = "T" if rel == ".store-moved" else HOW_TO_CHANGE_RE.sub(r"\g<lead> <how>", open(full).read())
                found[DAY_RE.sub(".until-<day>", rel)] = text
        return found

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]

    def said(self, text):
        return HOW_TO_CHANGE_RE.sub(r"\g<lead> <how>", text.replace(self.dir, "<dir>"))

    def host(self, cmd):
        cmd = list(cmd)
        self.asked.append(("run", *cmd))
        if cmd[:2] == ["awg", "show"]:
            return 0, "".join(f"{i:043d}=\t{c['ip']}/32\n" for i, c in enumerate(CLIENTS))
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
        return 1, ""

    def watch(self, command):
        self.asked.append(("watch", command))
        return command not in self.watch_fails, "сбой: " + command if command in self.watch_fails else ""


class New(Side):
    def __init__(self, name):
        super().__init__(name)
        side = self

        class Dir:
            async def list(self, force=False):
                side.asked.append(("list", force))
                return [user_of(c) for c in CLIENTS]

        class Watch:
            async def health(self, timeout):
                return Outcome(*side.watch("health"))

            async def apply(self, timeout):
                return Outcome(*side.watch("apply"))

            async def pin(self, timeout):
                return Outcome(*side.watch("pin"))

            async def load(self, timeout):
                return Outcome(*side.watch("spaces-apply"))

        class Writer:
            async def write(self, entry):
                side.lines.append(entry.line())

        async def host(cmd, stdin=None, timeout=30):
            code, out = side.host(cmd)
            return Result(code, out, "")

        P = lambda rel: Path(side.path(rel))
        journal = Journal(None, Writer())
        moving = StoreMoving(NEW, Path(self.dir), P("lists/dns-tokens.json"))
        cascade = CascadeService(StoredCascade(NEW), Watch(), P("cascade.conf"), P(".panel.lock"))
        spaces = SpacesService(Dir(), SpacesInStore(NEW), StoredUserGroups(NEW), Watch(), host, "awg0", P("spaces.sets"), P(".users.lock"))
        pauses = Pauses(StoredPauses(NEW), host, "awg0", P("suspended.ips"))
        users = UsersService(Dir(), None, StoredUserGroups(NEW), StoredHandPins(NEW), StoredCascade(NEW), Watch(), StoredPauses(NEW), pauses, P("pins.map"), P(".users.lock"))

        async def make():
            shell_commands = Dispatcher()
            shell_commands.register(ShellCommand.STORE_INIT, Handled(moving.init))
            shell_commands.register(ShellCommand.STORE_EXPORT, Handled(moving.export))
            shell_commands.register(ShellCommand.CASCADE_GET, Handled(cascade.show))
            shell_commands.register(ShellCommand.CASCADE_SET, Handled(cascade.replace, journal))
            shell_commands.register(ShellCommand.SPACES_SYNC, Handled(spaces.resync))
            shell_commands.register(ShellCommand.SUSPEND_SYNC, Handled(users.resync_pauses))
            return Shell(Dispatcher(), shell_commands)
        self.shell = LOOP.run_until_complete(make())

    def ask(self, arguments):
        printed, code = LOOP.run_until_complete(self.shell.run(list(arguments), caller_of(os.environ)))
        return code, printed


def answer_of(side, code, printed):
    """What a run said, comparable between the paths: the JSON it printed (or its text), and the exit code."""
    try:
        said = json.loads(side.said(printed).strip().splitlines()[-1])
    except (ValueError, IndexError):
        said = side.said(printed)
    return [code or 0, said]


def seed_files(side):
    side.write("groups.json", {"v": 1, "groups": [{"id": "g00001", "name": "Видео", "on": True, "items": ["youtube.com", "*.example.org"]},
                                                  {"id": "g00002", "name": "Список", "on": False, "items": ["a.example"], "url": "https://lists.example/x.txt", "updated": 1700000000, "skipped": 2, "error": ""},
                                                  {"id": "g00003", "name": "Сети", "on": True, "items": [], "auto": "telegram"}]})
    side.write("block.json", {"v": 1, "groups": [{"id": "b00001", "name": "Мои", "on": True, "items": ["ads.example"], "kind": "block"}]})
    side.write("spaces.json", {"v": 1, "spaces": [{"id": "s00001", "name": "Дом", "on": True, "members": ["alice", "bob"]}]})
    side.write("usergroups.json", {"v": 1, "groups": [{"id": "aaaaaa", "name": "Семья", "space": True, "exit": 3, "filter": "kids", "members": ["alice", "carol"]},
                                                      {"id": "bbbbbb", "name": "Старая", "members": ["bob"], "space": 1, "exit": None}]})
    side.write("suspended.json", {"v": 1, "names": {"carol": 1700000001}})
    side.write("invites.json", {"v": 1, "invites": [{"id": "i00001", "hash": "h1", "group": "aaaaaa", "left": 2, "total": 3, "expires_at": 1900000000, "created": 1700000002, "used": ["dave"]}]})
    side.write("lists/dns-tokens.json", {"tokens": {"tok1": {"name": "tv", "created": 1700000003}}})
    side.write(".seen.json", {"v": 1, "at": 1700000004, "clients": {"alice": None, "bob": 1800000000}, "exits": {"1": ["up", "exit1-nl", 1700000000]}, "leaving": {}})
    side.write("domains-exit.txt", "# сайты\nyoutube.com\nflat.example\n")
    side.write("cascade.conf", SEED)


def forget(*keys):
    for key in keys:
        sql("DELETE FROM meta WHERE key = %s", key)


def story_moving(side):
    """store-init and store-export, from an empty database on. → what each step said and left"""
    steps = []

    def step(name, arguments):
        code, printed = side.ask(arguments)
        steps.append((name, answer_of(side, code, printed), rows(), side.files()))
    reset()
    step("пустая база, файлов нет: переносить нечего", ["store-init"])
    step("store-export из пустой базы", ["store-export"])
    side.write("domains-exit.txt", "# сайты\nyoutube.com\nflat.example\n")
    step("новый сервер: --fresh; плоский список сайтов становится одной группой", ["store-init", "--fresh"])
    step("повторный запуск ничего не делает", ["store-init", "--fresh"])
    step("store-export нового сервера", ["store-export"])
    reset()
    side.remove(".store-moved")
    seed_files(side)
    side.write("spaces.json", "{это не JSON")
    step("файл не читается как JSON: перенос остановлен, в базе ничего", ["store-init"])
    side.write("spaces.json", {"v": 1, "spaces": {"не": "список"}})
    step("файл не того вида", ["store-init"])
    seed_files(side)
    side.write("usergroups.json", {"v": 1, "groups": [{"name": "без id", "members": []}]})
    step("запись без id", ["store-init"])
    seed_files(side)
    side.write("lists/dns-tokens.json", {"tokens": {"a": {"name": "tv", "created": 1}, "b": {"name": "tv", "created": 2}}})
    step("два устройства DNS с одним именем", ["store-init"])
    seed_files(side)
    side.write(".seen.json", "[1, 2]")
    step("наблюдения журнала не того вида", ["store-init"])
    seed_files(side)
    side.write("cascade.conf", 'CLIENT_IF="awg0"\nCLIENT_SUBNET="172.16.17.0/24"\nEXITS=(\n)\n')
    step("cascade.conf без выходов", ["store-init"])
    side.write("cascade.conf", "# пусто\n")
    step("cascade.conf без обязательных строк", ["store-init"])
    seed_files(side)
    step("все файлы на месте: перенос", ["store-init"])
    step("ещё раз: уже перенесено", ["store-init"])
    step("store-export после переноса", ["store-export"])
    forget("moved:dns")
    sql("DELETE FROM dns_devices")
    side.write("lists/dns-tokens.json", {"tokens": {"tok2": {"name": "phone", "created": 1700000009}, "tok1": {"name": "tv", "created": 1700000003}}})
    step("коды DNS пришли в хранилище позже: переносятся один раз", ["store-init"])
    forget("moved:seen", "seen")
    side.write(".seen.json", {"v": 1, "at": 5, "clients": {"zed": None}, "exits": {}, "leaving": {"zed": 9}})
    step("наблюдения журнала пришли позже: переносятся, файл убран в архив", ["store-init"])
    forget("moved:cascade", "cascade")
    side.write("cascade.conf", SEED.replace("exit1-nl", "exit9-xx"))
    step("настройки каскада пришли позже: переносятся из файла", ["store-init"])
    forget("moved:dns", "moved:seen", "seen", "moved:cascade", "cascade")
    sql("DELETE FROM dns_devices")
    side.remove("cascade.conf")
    step("три вида сразу, а файла каскада и наблюдений нет", ["store-init"])
    forget("moved:dns")
    side.write("lists/dns-tokens.json", {"tokens": ["не", "то"]})
    step("коды DNS не того вида: перенос остановлен, отметки нет", ["store-init"])
    side.write("lists/dns-tokens.json", {"tokens": {}})
    step("…исправлено", ["store-init"])
    reset()
    step("база потеряна, а пометка на хосте говорит, что перенос был: не заполнять заново", ["store-init", "--fresh"])
    step("store-export без хранилища", ["store-export"])
    step("store-init с лишним словом", ["store-init", "--force"])
    return steps


def story_settings(side):
    """cascade-get, cascade-set, spaces-sync and suspend-sync on a store that was moved into."""
    steps = []

    def step(name, arguments, by=None):
        side.asked.clear()
        side.lines.clear()
        os.environ.pop("VPN_BY", None)
        if by is not None:
            os.environ["VPN_BY"] = by
        code, printed = side.ask(arguments(side) if callable(arguments) else arguments)
        os.environ.pop("VPN_BY", None)
        steps.append((name, answer_of(side, code, printed), rows(), side.files(), list(side.asked), side.journal()))
    reset()
    side.remove(".store-moved")
    side.remove("archive/seen.json.until-" + time.strftime("%Y-%m-%d", time.gmtime()))
    seed_files(side)
    side.ask(["store-init"])
    document = lambda: checked(conf.parse(SEED).wire()).wire()
    step("cascade-get", ["cascade-get"])
    step("cascade-get --conf: сам файл", ["cascade-get", "--conf"])
    step("cascade-get с лишним словом", ["cascade-get", "--json"])
    step("cascade-set тем же самым", lambda s: ["cascade-set", packed(document())])
    changed = dict(document(), exits=[dict(document()["exits"][0], weight=1), dict(document()["exits"][1], weight=3)])
    step("cascade-set: новые доли; в журнале — от имени системы", lambda s: ["cascade-set", packed(changed)])
    step("cascade-set обратно, от имени бота", lambda s: ["cascade-set", packed(document())], by="bot")
    step("cascade-set снова, от имени панели", lambda s: ["cascade-set", packed(changed)], by="panel")
    side.watch_fails = {"apply"}
    step("правила не применились: всё возвращено", lambda s: ["cascade-set", packed(document())])
    side.watch_fails = {"health"}
    step("сторож не отвечает: ничего не меняется", lambda s: ["cascade-set", packed(document())])
    side.watch_fails = set()
    step("cascade-set: сеть пользователей не меняется", lambda s: ["cascade-set", packed(dict(changed, client_subnet="10.9.0.0/24"))])
    step("cascade-set без выходов", lambda s: ["cascade-set", packed(dict(changed, exits=[]))])
    step("cascade-set: выход убран", lambda s: ["cascade-set", packed(dict(changed, exits=[changed["exits"][1]]))])
    step("cascade-set: не объект", lambda s: ["cascade-set", packed([1])])
    step("cascade-set: не base64", ["cascade-set", "это не base64"])
    step("cascade-set без данных", ["cascade-set"])
    side.write("cascade.conf", "# правка руками\n" + SEED)
    step("cascade-get после правки файла руками", ["cascade-get"])
    step("spaces-sync", ["spaces-sync"])
    side.watch_fails = {"spaces-apply"}
    step("spaces-sync, а сторож пространства не загрузил", ["spaces-sync"])
    side.watch_fails = set()
    step("suspend-sync: пауза возвращена ядру", ["suspend-sync"])
    step("suspend-sync ещё раз", ["suspend-sync"])
    forget("ready")
    step("хранилище без отметки: cascade-get", ["cascade-get"])
    step("…cascade-set", lambda s: ["cascade-set", packed(document())])
    step("…spaces-sync", ["spaces-sync"])
    step("…suspend-sync", ["suspend-sync"])
    step("неизвестная команда", ["нет-такой"])
    return steps


def against_golden(title, story, pieces, golden, write):
    """Each step of the story, told on the package, beside what the expectation says it gives."""
    print("\n— " + title)
    mine = json.loads(json.dumps(story(New("new")), ensure_ascii=False))
    if write:
        golden[title] = mine
        return mine
    expected = golden[title]
    ok([step[0] for step in mine] == [step[0] for step in expected], "те же шаги", [step[0] for step in mine])
    for expected_step, new_step in zip(expected, mine):
        differ = {piece: (expected_step[i + 1], new_step[i + 1]) for i, piece in enumerate(pieces) if expected_step[i + 1] != new_step[i + 1]}
        if "files" in differ:
            differ["files"] = {name: (differ["files"][0].get(name), differ["files"][1].get(name)) for name in set(differ["files"][0]) | set(differ["files"][1]) if differ["files"][0].get(name) != differ["files"][1].get(name)}
        if "rows" in differ:
            differ["rows"] = {table: (differ["rows"][0].get(table), differ["rows"][1].get(table)) for table in TABLES if differ["rows"][0].get(table) != differ["rows"][1].get(table)}
        ok(not differ, f"{expected_step[0]}: " + ", ".join(pieces), differ)
    return mine


def t_real():
    print("\n— python -m awgscale.agent <команда>: как из оболочки")
    side = New("real")
    reset()
    seed_files(side)
    env = dict(os.environ, VPN_STORE_DSN=DSN, VPN_STORE_PASSWORD_FILE=str(PASSWORD_FILE), VPN_CASCADE_DIR=side.dir, VPN_DNS_TOKENS_FILE=side.path("lists/dns-tokens.json"), VPN_RUN_DIR=side.dir,
               VPN_USERS_CACHE_FILE=side.path("clients.json"), VPN_TUNNEL_FILE=side.path("awg0.conf"), VPN_USERS_URL="http://127.0.0.1:1", VPN_WATCH_URL="http://127.0.0.1:1",
               VPN_USERS_TOKEN_FILE=str(PASSWORD_FILE), VPN_WATCH_TOKEN_FILE=str(PASSWORD_FILE), VPN_JOURNAL_SOCKET=side.path("no-journal"), VPN_ADGUARD_CREDENTIALS_FILE=side.path("none"))
    env.pop("VPN_BY", None)

    def shell(*arguments, **more):
        done = subprocess.run([sys.executable, "-m", "awgscale.agent", *arguments], capture_output=True, text=True, env=dict(env, **more), cwd=ROOT, timeout=120)
        return done.returncode, done.stdout
    code, out = shell("store-init")
    ok(code == 0 and json.loads(out) == {"ok": True, "moved": {"sites": 3, "block": 1, "spaces": 1, "ugroups": 2, "suspended": 1, "invites": 1, "dns": 1, "rules": 4}} and out.count("\n") == 1,
       "store-init (так его зовёт deploy): одна строка JSON, код 0", [code, out])
    code, out = shell("store-init")
    ok(code == 0 and json.loads(out) == {"ok": True, "already": True}, "повторно — «уже», код 0", [code, out])
    code, out = shell("cascade-get", "--conf")
    ok(code == 0 and out == open(side.path("cascade.conf")).read() and out.startswith("# Cascade settings"), "cascade-get --conf печатает сам файл, не JSON", [code, out[:200]])
    code, out = shell("store-export")
    ok(code == 0 and sorted(json.loads(out)["files"]) == ["block.json", "dns-tokens.json", "groups.json", "invites.json", "spaces.json", "suspended.json", "usergroups.json"], "store-export: все виды под именами прежних файлов", out[:300])
    code, out = shell("ugroups-get")
    ok(code == 0 and [g["name"] for g in json.loads(out)["groups"]] == ["Семья", "Старая"], "команда сокета из оболочки тоже работает: ugroups-get", [code, out])
    code, out = shell("нет-такой")
    ok(code == 1 and json.loads(out) == {"ok": False, "error": "unknown command"}, "неизвестная команда: отказ и код 1", [code, out])
    code, out = shell("cascade-set", "это не base64")
    ok(code == 1 and json.loads(out) == {"ok": False, "error": "некорректные данные"}, "cascade-set с мусором: отказ и код 1", [code, out])
    code, out = shell("ugroups-get", VPN_STORE_DSN="host=127.0.0.1 port=1 dbname=vpn user=vpn connect_timeout=1")
    said = json.loads(out)
    ok(code == 1 and said["ok"] is False and said["error"] == "Хранилище настроек недоступно" and "store" in said, "база недоступна: «Хранилище настроек недоступно» и код 1, а не падение", [code, out])
    code, out = shell("store-init", VPN_STORE_DSN="host=127.0.0.1 port=1 dbname=vpn user=vpn connect_timeout=1")
    ok(code == 1 and json.loads(out)["error"].startswith("перенос не выполнен: "), "store-init без базы: «перенос не выполнен», код 1", [code, out])
    t_wiring(env, side)


SWEEP = r"""
import asyncio, base64, json, sys
from awgscale.agent.factory import get_dispatcher, get_shell
from awgscale.agent.commands import Command
from awgscale.agent.protocol import Caller

b64 = lambda obj: base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()
ASKED = [["state"], ["clients", b64({"limit": 5})], ["day", "1763300000"], ["traffic"], ["journal"], ["add", "eve", "7d"], ["remove", "eve"], ["expire", "7d", "alice"], ["qr", "alice"], ["diag", "alice"], ["suspend", "on", "alice"], ["user-exit", "1", "alice"],
         ["ugroups-get"], ["ugroups-set", b64({"groups": [], "base": "x"})], ["spaces-get"], ["spaces-set", b64({"spaces": [], "base": "x"})], ["sites-get"], ["sites-set", b64({"groups": [], "base": "x"})],
         ["sites-refresh"], ["mode-set", "geo"], ["check", "example.org"], ["probe", "example.org"], ["block-get"], ["block-set", b64({"groups": [{"name": "Свои", "on": True, "items": ["ads.example"]}]})],
         ["block-lists", b64({"op": "refresh"})], ["dns-list"], ["dns-add", "tv"], ["dns-remove", "tv"], ["invite-list"], ["invite-add", b64({"group": None, "uses": 1, "days": 1})],
         ["invite-remove", "aaaaaa"], ["invite-info", "x" * 22], ["invite-redeem", "x" * 22, "guest"], ["exits-get"], ["exit-add", b64({"address": "198.51.100.7", "name": "exit9"})],
         ["exit-attach", b64({"index": 4, "name": "exit9", "weight": 10})], ["exit-rename", "1", "exit1-x"], ["exit-remove", "1"], ["exit-update", "1"], ["exit-country", "1", "nl"], ["weights-set", b64({"weights": {"1": 50, "3": 50}})], ["country-set", "kz"],
         ["app-me", "172.16.17.2"], ["app-exits"], ["app-owners"], ["app-exit", "172.16.17.2", "auto"], ["app-filter", "172.16.17.2", "auto"], ["app-route", "172.16.17.2", "smart"], ["rule-route", b64({"changes": [{"rule": "zzzzzz", "route": "off"}]})], ["rules-get"]]


async def main():
    dispatcher = get_dispatcher()
    shell = get_shell()
    replies = [[arguments[0], await dispatcher.dispatch(arguments, Caller.PANEL)] for arguments in ASKED]
    for arguments in (["cascade-get"], ["spaces-sync"], ["suspend-sync"], ["store-export"], ["owner-code", "if-none"]):
        replies.append([arguments[0], (await shell.answer(arguments, Caller.SYSTEM))[1]])
    print(json.dumps({"asked": sorted({a[0] for a in ASKED}), "all": sorted(c.value for c in Command), "replies": replies}, ensure_ascii=False))

asyncio.run(main())
"""


def t_wiring(env, side):
    print("\n— настоящая сборка агента: каждая команда отвечает словами")
    tools = side.path("bin")
    os.makedirs(tools)
    for tool in ("awg", "ip", "ipset", "iptables", "curl", "dig"):       # the host's own tools are never reached
        with open(os.path.join(tools, tool), "w") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(tools, tool), 0o755)
    nowhere = "http://127.0.0.1:1"
    done = subprocess.run([sys.executable, "-c", SWEEP], capture_output=True, text=True, cwd=ROOT, timeout=300,
                          env=dict(env, PATH=tools + ":" + env["PATH"], VPN_RESOLVER_URL=nowhere, VPN_EXITS_URL=nowhere, VPN_LOKI_URL=nowhere, VPN_COUNTRY_LOOKUP_URL=nowhere, VPN_ADGUARD_URL=nowhere + "/control",
                                   VPN_RESOLVER_TOKEN_FILE=str(PASSWORD_FILE), VPN_EXITS_TOKEN_FILE=str(PASSWORD_FILE), VPN_LISTS_DIR=side.path("lists"), VPN_RESOLVER_YAML=side.path("AdGuardHome.yaml"),
                                   VPN_ADGUARD_FILTERS_DIR=side.dir, VPN_ADGUARD_QUERYLOG_FILE=side.path("querylog.json"), VPN_DNS_STATE_FILE=side.path("dns-state.json")))
    try:
        swept = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        swept = {"asked": [], "all": ["?"], "replies": [["?", {"error": done.stderr[-600:]}]]}
    ok(swept["asked"] == swept["all"] and len(swept["all"]) == 50, "спрошена каждая из 50 команд сокета (и пять команд оболочки)", [swept["asked"], swept["all"]])
    broken = [(command, reply) for command, reply in swept["replies"] if not isinstance(reply.get("ok"), bool) or str(reply.get("error", "")).startswith("ошибка агента")]
    ok(not broken and "Traceback" not in done.stderr, "база на месте, остальных сервисов нет: у каждой команды — ответ или отказ словами, ни одной «ошибки агента» (части собраны друг с другом верно)",
       [broken, done.stderr[-800:]])
    said = dict((command, reply) for command, reply in swept["replies"])
    ok(said["state"].get("ok") is True and said["state"]["watch"] == {"ok": False} and said["state"]["store_ok"] is True and said["ugroups-get"].get("ok") is True
       and said["block-set"].get("error", "").startswith("блокировщик не принял изменения") and said["add"].get("error", "").startswith("не удалось создать пользователя"),
       "…state отдан и без сервисов; block-set дошёл до блокировщика; add — до сервиса пользователей", [said["block-set"], said["add"]])
    ok(said["owner-code"].get("ok") is True and len(said["owner-code"].get("token", "")) == 22 and said["owner-code"].get("owners") == [],
       "owner-code: в настоящей сборке код владельца выдан и сохранён в базе", said["owner-code"])


def main():
    write = os.environ.get("GOLDEN_WRITE") == "1"
    golden = {} if write else json.loads(GOLDEN.read_text())
    mine = against_golden("store-init и store-export: перенос настроек в базу", story_moving, ("answer", "rows", "files"), golden, write)
    moved = next(step for step in mine if step[0] == "все файлы на месте: перенос")
    ok(moved[1] == [0, {"ok": True, "moved": {"sites": 3, "block": 1, "spaces": 1, "ugroups": 2, "suspended": 1, "invites": 1, "dns": 1, "rules": 4}}] and ["ready", "T"] in moved[2]["meta"]
       and ".seen.json" not in moved[3] and any(name.startswith("archive/seen.json.until-") for name in moved[3]) and moved[3].get("cascade.conf.prev") == SEED and moved[3]["cascade.conf"] != SEED,
       "…и это видно: записей столько, отметка стоит, файл наблюдений убран в архив, cascade.conf собран заново, прежний рядом", [moved[1], sorted(moved[3])])
    refused = [step for step in mine if step[1][0] == 1]
    ok(len(refused) == 13 and all(step[1][1].get("ok") is False for step in refused) and all(step[2]["meta"] is None or ["ready", "T"] in step[2]["meta"] or step[2]["meta"] == [] for step in refused),
       "тринадцать отказов: в каждом база осталась как была (пустой — пустой)", [step[0] for step in refused])
    mine = against_golden("cascade-get, cascade-set, spaces-sync, suspend-sync", story_settings, ("answer", "rows", "files", "asked", "journal"), golden, write)
    if write:
        GOLDEN.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n")
        print(f"written: {GOLDEN}")
        return 0
    by = {step[0]: step[5] for step in mine}
    ok([line["who"] for line in by["cascade-set: новые доли; в журнале — от имени системы"]] == ["system"] and [line["who"] for line in by["cascade-set обратно, от имени бота"]] == ["bot"]
       and by["cascade-set: новые доли; в журнале — от имени системы"][0]["text"] == "Доли выходов: exit1-nl 25%, exit2-nl 75%",
       "…cascade-set из оболочки пишет в журнал доли; автор — тот, кого назвал VPN_BY, иначе система", by)
    ok(caller_of({"VPN_BY": "bot"}).value == "bot" and caller_of({"VPN_BY": "panel"}).value == "panel" and caller_of({"VPN_BY": "кто-то"}).value == "" == caller_of({}).value == caller_of({"VPN_BY": ""}).value,
       "VPN_BY называет автора только панелью или ботом; всё остальное — система (как на сокете)")
    t_real()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
