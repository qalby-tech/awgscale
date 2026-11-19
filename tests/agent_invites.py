#!/usr/bin/env python3
"""The invitations' commands that live in the package (awgscale.agent.invites):
invite-list, -add, -remove, -info and -redeem sent through the real dispatcher
give the answers, store the invitations and groups, ask the users service, the
watch service and the rest of the agent the things in the order, and write the
journal lines recorded in tests/golden/agent_invites.json.

    uv run python tests/agent_invites.py
    GOLDEN_WRITE=1 uv run python tests/agent_invites.py    (records the golden file anew)

Nothing live is touched."""
import asyncio
import base64
import json
import os
import secrets
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="agent-invites-")
from awgscale.agent import errors  # noqa: E402
from awgscale.agent.commands import Command  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.invites.handling import HandledFor  # noqa: E402
from awgscale.agent.invites.models import hash_of  # noqa: E402
from awgscale.agent.invites.notes import JournalNotes  # noqa: E402
from awgscale.agent.invites.service import InvitesService  # noqa: E402
from awgscale.agent.invites.store import unexpired  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent.spaces.models import StoredSpaces  # noqa: E402
from awgscale.agent.spaces.service import SpacesService  # noqa: E402
from awgscale.agent.users.directory import user_of  # noqa: E402
from awgscale.agent.users.models import StoredGroups  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
START = int(time.time()) - 60             # a minute back: a moment just gone (now - 1) still counts as the hour the suite began
REAL_TOKEN_HEX = secrets.token_hex
REAL_TOKEN_URLSAFE = secrets.token_urlsafe
HOUR = 3600


GOLDEN_FILE = os.path.join(HERE, "golden", "agent_invites.json")
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


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:1500])


def key(n):
    return f"{n:043d}="


def token(n):
    return f"t{n:021d}"


def packed(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


def copy(value):
    return json.loads(json.dumps(value))


class Counted:
    """Random things the commands ask for, made predictable: the n-th is always the same."""

    def __init__(self, shape):
        self.n = 0
        self.shape = shape

    def __call__(self, nbytes):
        self.n += 1
        return self.shape(self.n)


def hours(value):
    """Moments depend on the clock: compared by the hour since the suite began."""
    if isinstance(value, dict):
        return {k: (v - START) // HOUR if k in ("expires_at", "created") and isinstance(v, (int, float)) else hours(v) for k, v in value.items()}
    if isinstance(value, list):
        return [hours(v) for v in value]
    return value


class World:
    """The users, groups, spaces and invitations, the services around them."""

    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        self.sets = os.path.join(self.dir, "spaces.sets")
        self.lock = os.path.join(self.dir, ".users.lock")
        self.invite_lock = os.path.join(self.dir, ".invite.lock")
        self.clients = [{"name": "alice", "ip": "10.9.9.2", "expires_at": None}, {"name": "bob", "ip": "10.9.9.3", "expires_at": None}]
        self.groups = [{"id": "aaaaaa", "name": "Дом", "space": True, "exit": 3, "filter": "kids", "members": ["alice"]},
                       {"id": "bbbbbb", "name": "Гости", "space": False, "exit": None, "filter": "std", "members": []}]
        self.spaces = [{"id": "s00001", "name": "S", "on": True, "members": ["alice", "bob"]}]
        self.invites = []
        self.adding = "ok"                # how the users service answers a new user: ok | refuse | raise | quiet (says no, yet the user is there)
        self.list_empty = False
        self.no_config = False
        self.store_down = False
        self.asked = []
        self.lines = []

    def allowed_ips(self):
        return "".join(f"{key(i)}\t{c['ip']}/32\n" for i, c in enumerate(self.clients))

    def listed(self, force):
        self.asked.append(("list", force))
        return [] if self.list_empty else copy(self.clients)

    def add(self, name):
        """→ did the service say yes (raises when it is told to)."""
        self.asked.append(("add", name))
        if self.adding == "raise":
            raise RuntimeError("нет")
        if self.adding in ("ok", "quiet"):
            self.clients.append({"name": name, "ip": f"10.9.9.{len(self.clients) + 2}", "expires_at": None})
        return self.adding == "ok"

    def config(self, name):
        self.asked.append(("config", name))
        if self.no_config or name not in [c["name"] for c in self.clients]:
            return None
        return {"ok": True, "name": name, "conf": "[Interface]\n", "uri": "vpn://x", "conf_png": "AA", "uri_png": "BB"}

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]


class NewPath:
    def __init__(self, world):
        w = world

        class Dir:
            async def list(self, force=False):
                return [user_of(c) for c in w.listed(force)]

        class Invites:
            async def read(self):
                return unexpired(copy(w.invites), datetime.fromtimestamp(int(time.time()), UTC))

            async def save(self, invitations):
                w.invites = [invitation.record() for invitation in invitations]

        class Groups:
            async def read(self):
                return StoredGroups.of(copy(w.groups))

            async def save(self, records):
                w.groups = copy(list(records))

        class Spaces:
            async def read(self):
                return StoredSpaces.of(copy(w.spaces))

            async def save(self, records):
                w.spaces = copy(list(records))

        class Users:
            async def set_expiry(self, names, term):
                return Outcome(True)

            async def config(self, name):
                return w.config(name)

        class Newcomers:
            async def ready(self):
                if w.store_down:
                    raise errors.StoreDown("нет связи")

            async def forget(self, name):
                w.asked.append(("forget", (name,)))
                w.asked.append(("seen_back", name))

            async def add(self, name):
                return w.add(name)

            async def pin(self, groups, users):
                w.asked.append(("pins", [g["id"] for g in groups], [u.name for u in users]))

            async def filter(self, groups, users):
                w.asked.append(("profiles", [g["id"] for g in groups], [u.name for u in users]))

        class Watch:
            async def health(self, timeout):
                return Outcome(True)

            async def apply(self, timeout):
                return Outcome(True)

            async def pin(self, timeout):
                w.asked.append(("watch", "pin"))
                return Outcome(True)

        class Rules:
            async def load(self, timeout):
                w.asked.append(("watch", "spaces-apply"))
                return Outcome(True)

        class Writer:
            async def write(self, entry):
                w.lines.append(entry.line())

        async def host(cmd, stdin=None, timeout=30):
            return Result(0, w.allowed_ips(), "")

        spaces = SpacesService(Dir(), Spaces(), Groups(), Rules(), host, "awg0", Path(w.sets), Path(w.lock))
        service = InvitesService(Invites(), Groups(), Dir(), Users(), Newcomers(), Watch(), Spaces(), spaces, JournalNotes(Journal(None, Writer())), Path(w.lock), Path(w.invite_lock))

        async def make():
            dispatcher = Dispatcher()
            dispatcher.register(Command.INVITE_LIST, Handled(service.list))
            dispatcher.register(Command.INVITE_ADD, HandledFor(service.add))
            dispatcher.register(Command.INVITE_REMOVE, HandledFor(service.remove))
            dispatcher.register(Command.INVITE_INFO, Handled(service.info))
            dispatcher.register(Command.INVITE_REDEEM, Handled(service.redeem))
            return dispatcher
        self.dispatcher = LOOP.run_until_complete(make())

    def ask(self, arguments):
        return LOOP.run_until_complete(self.dispatcher.dispatch(arguments, Caller.PANEL))


def read(path):
    try:
        return open(path).read()
    except OSError:
        return None


def expire_all(w):
    for invitation in w.invites:
        invitation["expires_at"] = int(time.time()) - 1


def t_commands():
    print("\n— команды через диспетчер")
    steps = [
        ("invite-list: пусто", lambda w: None, ["invite-list"]),
        ("invite-add: без группы, на два устройства", lambda w: None, ["invite-add", packed({"group": None, "uses": 2, "days": 3})]),
        ("invite-add: в группу", lambda w: None, ["invite-add", packed({"group": "aaaaaa", "uses": 3, "days": 30})]),
        ("invite-add: такой группы нет", lambda w: None, ["invite-add", packed({"group": "zzzzzz", "uses": 1, "days": 1})]),
        ("invite-add: группа не строкой", lambda w: None, ["invite-add", packed({"group": 5, "uses": 1, "days": 1})]),
        ("invite-add: устройств слишком много", lambda w: None, ["invite-add", packed({"group": None, "uses": 11, "days": 1})]),
        ("invite-add: дни не числом", lambda w: None, ["invite-add", packed({"group": None, "uses": 1, "days": True})]),
        ("invite-add: не JSON", lambda w: None, ["invite-add", base64.b64encode(b"[]").decode()]),
        ("invite-list: два", lambda w: None, ["invite-list"]),
        ("invite-info: годная ссылка без группы", lambda w: None, ["invite-info", token(1)]),
        ("invite-info: годная ссылка в группу", lambda w: None, ["invite-info", token(2)]),
        ("invite-info: чужая ссылка", lambda w: None, ["invite-info", token(99)]),
        ("invite-redeem: чужая ссылка — ничего не запущено", lambda w: None, ["invite-redeem", token(99), "eve"]),
        ("invite-redeem: имя занято", lambda w: None, ["invite-redeem", token(1), "bob"]),
        ("invite-redeem: список пользователей не прочитать", lambda w: setattr(w, "list_empty", True), ["invite-redeem", token(1), "eve"]),
        ("invite-redeem: без группы", lambda w: setattr(w, "list_empty", False), ["invite-redeem", token(1), "eve"]),
        ("invite-redeem: в группу с выходом, фильтром и пространством", lambda w: None, ["invite-redeem", token(2), "frank"]),
        ("invite-redeem: сервис отказал, пользователя нет — использование возвращено", lambda w: setattr(w, "adding", "refuse"), ["invite-redeem", token(2), "gina"]),
        ("invite-redeem: сервис упал, пользователя нет — использование возвращено", lambda w: setattr(w, "adding", "raise"), ["invite-redeem", token(2), "gina"]),
        ("invite-redeem: сервис сказал «нет», а пользователь есть — он создан", lambda w: setattr(w, "adding", "quiet"), ["invite-redeem", token(2), "gina"]),
        ("invite-redeem: создан, но кода нет", lambda w: (setattr(w, "adding", "ok"), setattr(w, "no_config", True)), ["invite-redeem", token(2), "hank"]),
        ("invite-redeem: использований больше нет", lambda w: setattr(w, "no_config", False), ["invite-redeem", token(2), "ivan"]),
        ("invite-info: использований больше нет", lambda w: None, ["invite-info", token(2)]),
        ("invite-redeem: последнее использование первой ссылки", lambda w: None, ["invite-redeem", token(1), "ivan"]),
        ("invite-list: исчерпанные не показаны", lambda w: None, ["invite-list"]),
        ("invite-add: ещё одно, в группу без особенностей", lambda w: None, ["invite-add", packed({"group": "bbbbbb", "uses": 10, "days": 1})]),
        ("invite-redeem: хранилище недоступно — никто не создан", lambda w: setattr(w, "store_down", True), ["invite-redeem", token(3), "jane"]),
        ("invite-redeem: в группу без выхода, фильтра и пространства", lambda w: setattr(w, "store_down", False), ["invite-redeem", token(3), "jane"]),
        ("invite-redeem: пользователей уже предел", lambda w: w.clients.extend({"name": f"u{i}", "ip": "10.0.0.1", "expires_at": None} for i in range(240)), ["invite-redeem", token(3), "kate"]),
        ("invite-redeem: группы приглашения больше нет", lambda w: (setattr(w, "clients", w.clients[:8]), setattr(w, "groups", w.groups[:1])), ["invite-redeem", token(3), "kate"]),
        ("invite-list: без приглашения в исчезнувшую группу", lambda w: None, ["invite-list"]),
        ("invite-remove: такого нет", lambda w: None, ["invite-remove", "zzzzzz"]),
        ("invite-remove: отозвано", lambda w: None, ["invite-remove", "000003"]),
        ("invite-add: после отзыва", lambda w: None, ["invite-add", packed({"group": None, "uses": 1, "days": 2})]),
        ("invite-info: срок вышел", expire_all, ["invite-info", token(4)]),
        ("invite-redeem: срок вышел", lambda w: None, ["invite-redeem", token(4), "kate"]),
        ("invite-list: просроченные не показаны", lambda w: None, ["invite-list"]),
    ]
    new_world = World("new")
    new_path = NewPath(new_world)
    tokens = Counted(token)
    ids = Counted(lambda n: f"{n:06x}")
    for name, prepare, arguments in steps:
        prepare(new_world)
        new_world.asked.clear()
        new_world.lines.clear()
        secrets.token_urlsafe = tokens
        secrets.token_hex = ids
        mine = new_path.ask(arguments)
        secrets.token_urlsafe = REAL_TOKEN_URLSAFE
        secrets.token_hex = REAL_TOKEN_HEX
        seen, expected = as_golden(name, {"answer": hours(mine), "invites": hours(new_world.invites), "groups": new_world.groups, "users": [c["name"] for c in new_world.clients],
                                          "file": read(new_world.sets), "asked": new_world.asked, "journal": new_world.journal()})
        ok(seen == expected, f"{name}: записанный ответ, приглашения и группы, обращения по порядку, строка журнала", [seen, expected])
    made = [c["name"] for c in new_world.clients]
    ok(made == ["alice", "bob", "eve", "frank", "gina", "hank", "ivan", "jane"] and new_world.groups[0]["members"] == ["alice", "frank", "gina", "hank"],
       "создано шестеро гостей; в группу попали те, чья ссылка вела в неё", [made, new_world.groups])
    stored = json.dumps(new_world.invites)
    ok(all(token(n) not in stored for n in range(1, 6)) and hash_of(token(4)) in stored, "в хранилище нет ни одной ссылки — только отпечатки", stored[:300])
    ok(read(new_world.sets) is not None and "sp_gaaaaaa " in read(new_world.sets), "файл пространств пересобран, когда гость вошёл в группу-пространство", read(new_world.sets))
    write_golden()


def main():
    t_commands()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
