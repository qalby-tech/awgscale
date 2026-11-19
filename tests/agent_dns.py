#!/usr/bin/env python3
"""The smart-DNS devices as they live in the package (awgscale.agent.dns):
dns-list, dns-add and dns-remove sent through the real dispatcher give the
answers, leave the codes in the store and the file for the DNS gate, and write
the journal lines recorded in tests/golden/agent_dns.json.

    uv run python tests/agent_dns.py
    GOLDEN_WRITE=1 uv run python tests/agent_dns.py    (records the golden file anew)

Nothing live is touched."""
import asyncio
import json
import os
import stat
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="agent-dns-")
from awgscale.agent.commands import Command  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.dns.models import Device  # noqa: E402
from awgscale.agent.dns.service import DnsDevicesService  # noqa: E402
from awgscale.agent.errors import StoreDown  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent.store import documents  # noqa: E402

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
NOW = int(time.time())
TV = "tok-aaaaaaaaaaaaaaaaaaaaaaaa"


GOLDEN_FILE = os.path.join(HERE, "golden", "agent_dns.json")
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
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:1200])


class World:
    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        self.tokens_file = os.path.join(self.dir, "lists", "dns-tokens.json")
        os.mkdir(os.path.dirname(self.tokens_file))
        self.state_file = os.path.join(self.dir, "state.json")
        self.tokens = {TV: {"name": "tv", "created": NOW - 86400}}
        self.store_down = False
        self.lines = []

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]


class NewPath:
    def __init__(self, world):
        w = world

        class Devices:
            async def read(self):
                if w.store_down:
                    raise StoreDown("нет связи")
                return {token: Device(name=record["name"], created=datetime.fromtimestamp(record["created"], UTC)) for token, record in w.tokens.items()}

            async def save(self, devices):
                value = {token: {"name": device.name, "created": int(device.created.timestamp())} for token, device in devices.items()}
                documents.check(documents.Kind.DNS, value)
                w.tokens = value

        class Writer:
            async def write(self, entry):
                w.lines.append(entry.line())

        service = DnsDevicesService(Devices(), Path(w.tokens_file), Path(w.state_file))
        journal = Journal(None, Writer())

        async def make():
            dispatcher = Dispatcher()
            dispatcher.register(Command.DNS_LIST, Handled(service.show))
            dispatcher.register(Command.DNS_ADD, Handled(service.add, journal))
            dispatcher.register(Command.DNS_REMOVE, Handled(service.remove, journal))
            return dispatcher
        self.dispatcher = LOOP.run_until_complete(make())

    def ask(self, arguments):
        return LOOP.run_until_complete(self.dispatcher.dispatch(arguments, Caller.PANEL))


def scrubbed(value, world):
    """Codes are drawn at random and the moments are the clock's: replaced by what they are."""
    known = {TV}
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str) and key in world.tokens and key not in known:
                key = "<code>"
            if key == "token" and item not in known:
                item = "<code>" if isinstance(item, str) and len(item) == 24 else item
            if key == "created" and isinstance(item, int) and abs(item - NOW) < 120:
                item = "<now>"
            out[key] = scrubbed(item, world)
        return out
    if isinstance(value, (list, tuple)):
        return [scrubbed(item, world) for item in value]
    if isinstance(value, int) and not isinstance(value, bool) and abs(value - NOW) < 30 * 86400:
        return f"<now{value - NOW:+d}>"
    return value


def file_seen(world):
    try:
        text = open(world.tokens_file).read()
    except OSError:
        return None
    return scrubbed(json.loads(text), world), text.count("\n"), oct(os.stat(world.tokens_file).st_mode & 0o777)


def t_commands():
    print("\n— команды через диспетчер")

    def state(w, content):
        if content is None:
            if os.path.exists(w.state_file):
                os.unlink(w.state_file)
        else:
            open(w.state_file, "w").write(content if isinstance(content, str) else json.dumps(content))

    seen_state = {"seen": {"tv": {"last": NOW - 600, "ip": "198.51.100.7", "queries": 1200}, "phone": {"last": NOW - 5, "ip": "198.51.100.9", "queries": 3}},
                  "allowed": {"198.51.100.7": NOW + 600, "198.51.100.9": NOW - 1}}
    steps = [
        ("dns-list: шлюз ещё ничего не записал", lambda w: state(w, None), ["dns-list"]),
        ("dns-list: файл шлюза испорчен", lambda w: state(w, "{not json"), ["dns-list"]),
        ("dns-add", lambda w: state(w, seen_state), ["dns-add", "phone"]),
        ("dns-add: такое имя уже есть", None, ["dns-add", "tv"]),
        ("dns-add: ещё одно", None, ["dns-add", "Zed_1"]),
        ("dns-list: кто что спрашивал и кого сейчас пускают", None, ["dns-list"]),
        ("dns-remove", None, ["dns-remove", "tv"]),
        ("dns-remove: нет такого", None, ["dns-remove", "tv"]),
        ("dns-list: после отзыва", None, ["dns-list"]),
        ("dns-list: хранилище недоступно", lambda w: setattr(w, "store_down", True), ["dns-list"]),
        ("dns-add: хранилище недоступно", None, ["dns-add", "late"]),
        ("dns-remove: хранилище недоступно", None, ["dns-remove", "phone"]),
    ]
    new_world = World("new")
    new_path = NewPath(new_world)
    for what, prepare, arguments in steps:
        if prepare:
            prepare(new_world)
        before_lines = len(new_world.lines)
        reply = new_path.ask(arguments)
        seen, expected = as_golden(what, [scrubbed(reply, new_world), scrubbed(new_world.tokens, new_world), file_seen(new_world),
                                          scrubbed(new_world.journal()[before_lines:], new_world)])
        ok(seen == expected, f"{what}: записанный ответ, коды в хранилище, файл для шлюза, строки журнала",
           None if seen == expected else [(i, a, b) for i, (a, b) in enumerate(zip(seen, expected or [])) if a != b][:1] or [seen, expected])
    listed = NewPath(new_world)
    new_world.store_down = False
    reply = listed.ask(["dns-list"])
    by_name = {device["name"]: device for device in reply["devices"]}
    ok([device["name"] for device in reply["devices"]] == ["Zed_1", "phone"] and by_name["phone"]["queries"] == 3 and by_name["phone"]["admitted"] is False
       and by_name["Zed_1"]["last"] is None and by_name["Zed_1"]["queries"] == 0 and set(by_name["phone"]) == {"name", "token", "created", "last", "ip", "queries", "admitted"},
       "что именно показывается: по именам, с числом запросов; истёкший допуск — не допуск; ничего сверх этого", reply)
    codes = [device["token"] for device in reply["devices"]]
    ok(all(len(code) == 24 for code in codes) and len(set(codes)) == 2 and json.load(open(new_world.tokens_file)) == {"tokens": new_world.tokens}
       and stat.S_IMODE(os.stat(new_world.tokens_file).st_mode) == 0o600, "коды случайные и разные; файл для шлюза — только коды, права 600")
    ok(new_path.ask(["dns-add", "-bad"]) == {"ok": False, "error": "bad request"} and new_path.ask(["dns-remove", "a b"]) == {"ok": False, "error": "bad request"},
       "негодное имя не доходит до команды")


def t_lock():
    print("\n— два добавления сразу")
    world = World("race")
    path = NewPath(world)

    async def both():
        return await asyncio.gather(path.dispatcher.dispatch(["dns-add", "one"], Caller.PANEL), path.dispatcher.dispatch(["dns-add", "two"], Caller.PANEL))
    replies = LOOP.run_until_complete(both())
    ok(all(reply["ok"] for reply in replies) and sorted(record["name"] for record in world.tokens.values()) == ["one", "tv", "two"]
       and len(json.load(open(world.tokens_file))["tokens"]) == 3, "оба устройства сохранены: читают, меняют и пишут по очереди", world.tokens)


def main():
    t_commands()
    t_lock()
    write_golden()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
