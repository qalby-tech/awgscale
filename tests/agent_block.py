#!/usr/bin/env python3
"""Blocking as it lives in the package (awgscale.agent.block, awgscale.agent.adguard),
given a stand-in AdGuard Home that answers over real HTTP: block-get, block-set
and block-lists sent through the real dispatcher give the answers, ask AdGuard
the things, leave the groups in the store and write the journal lines kept in
tests/golden/agent_block.json. The per-group filtering profiles come out as kept
there too, on random groups (a fixed seed) and through the stand-in.

    uv run python tests/agent_block.py
    GOLDEN_WRITE=1 uv run python tests/agent_block.py     writes the golden file again

Nothing live is touched: no real AdGuard, no name is looked up."""
import asyncio
import base64
import copy
import hashlib
import json
import os
import random
import re
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agent-block-")
GOLDEN_FILE = os.path.join(HERE, "golden", "agent_block.json")
WRITING = os.environ.get("GOLDEN_WRITE") == "1"
GOLDEN = {} if WRITING else json.load(open(GOLDEN_FILE))

from awgscale.agent.adguard import group_lists, profiles  # noqa: E402
from awgscale.agent.adguard.models import GROUP_LISTS_FILE, THREATS_LIST_URL  # noqa: E402
from awgscale.agent.adguard.api import AdGuard  # noqa: E402
from awgscale.agent.adguard.client import AdGuardClient  # noqa: E402
from awgscale.agent.block import entries, links  # noqa: E402
from awgscale.agent.block.counters import AdGuardCounters  # noqa: E402
from awgscale.agent.block.service import BlockService
from awgscale.agent.payloads import decode
from awgscale.agent.rules.service import RulesService
from awgscale.agent.rules.store import Part, blocks_of, merged, rules_of, sites_of
from awgscale.agent.block.store import own_groups
from awgscale.agent.sites.models import SiteGroup  # noqa: E402
from awgscale.agent.block.store import own_groups  # noqa: E402
from awgscale.agent.commands import Command  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.errors import Refused, StoreRefused  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent.store import documents  # noqa: E402
from awgscale.agent.users.directory import user_of  # noqa: E402
from awgscale.agent.users.models import StoredGroups, UserGroup  # noqa: E402

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
ADS = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt"
ADAWAY = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_2.txt"


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:1200])


def plain(value):
    """As JSON keeps it, a world's temporary directory named by what it is."""
    return json.loads(re.sub(re.escape(TMP) + r"/[^/'\"]+", "<world>", json.dumps(value, ensure_ascii=False)))


def kept(key, value):
    """Is `value` what the package gave here when the golden file was written? (GOLDEN_WRITE=1: it is written now)"""
    if WRITING:
        GOLDEN[key] = plain(value)
        return True
    return GOLDEN.get(key) == plain(value)


def digest(values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def golden_written():
    if WRITING:
        os.makedirs(os.path.dirname(GOLDEN_FILE), exist_ok=True)
        with open(GOLDEN_FILE, "w") as fh:
            json.dump(GOLDEN, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")


RESOLVED = {           # the made-up DNS of the "is the link's host public" checks
    "v4.example": ["93.184.215.14"],
    "v6.example": ["2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
    "both.example": ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c", "8.8.8.8"],
    "inner6.example": ["93.184.215.14", "fd00::5"],
    "inner4.example": ["10.0.0.5", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
    "loop.example": ["127.0.0.1"],
}


def made_up_getaddrinfo(host, port, family=0, *a, **k):
    if host not in RESOLVED:
        raise socket.gaierror(-2, "Name or service not known")
    found = [(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in RESOLVED[host]]
    return [answer for answer in found if family in (0, answer[0])]


def b64(obj):
    return base64.b64encode(json.dumps(obj).encode()).decode()


class FakeAdGuard:
    """AdGuard Home's control API, as far as the agent uses it, over real HTTP."""

    def __init__(self):
        self.filters = [{"id": 1, "url": ADS, "name": "AdGuard DNS filter", "enabled": True, "rules_count": 190777, "last_updated": "2026-11-12T05:00:00Z"},
                        {"id": 2, "url": ADAWAY, "name": "AdAway Default Blocklist", "enabled": False, "rules_count": 0, "last_updated": ""},
                        {"id": 1700000001, "url": "https://lists.example.org/mine.txt", "name": "Свой", "enabled": True, "rules_count": 12, "last_updated": "2026-11-13T05:00:00Z"}]
        self.safebrowsing = True
        self.rules = []
        self.clients = []
        self.calls = []
        self.fail = {}                    # path → (status, body): the next requests to it are refused
        self.stats = {"num_dns_queries": 48211, "num_blocked_filtering": 7990, "num_replaced_safebrowsing": 3, "avg_processing_time": 0.0112,
                      "top_blocked_domains": [{"ads.example": 900}, {"Bad Name!": 800}, {"track.example": 400}, {"a.example": 30}, {"b.example": 20}, {"c.example": 10},
                                              {"d.example": 5}, {"e.example": 4}],
                      "top_clients": [{"172.16.17.3": 9000}]}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def serve(self, method):
                path = self.path[len("/control/"):]
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length)) if length else None
                if self.headers.get("Authorization") != "Basic " + base64.b64encode(b"admin:s3cret").decode():
                    return self.reply(401, b"unauthorized")
                if method == "POST":
                    fake.calls.append((path, payload))
                if path in fake.fail:
                    code, body = fake.fail[path]
                    return self.reply(code, body.encode())
                self.reply(*fake.answer(method, path, payload))

            def do_GET(self):
                self.serve("GET")

            def do_POST(self):
                self.serve("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/control"

    def answer(self, method, path, payload):
        if path == "filtering/status":
            return 200, {"filters": copy.deepcopy(self.filters), "user_rules": self.rules}
        if path == "safebrowsing/status":
            return 200, {"enabled": self.safebrowsing}
        if path == "stats":
            return 200, self.stats
        if path == "clients":
            return 200, {"clients": copy.deepcopy(self.clients)}
        if path == "filtering/set_url":
            for f in self.filters:
                if f["url"] == payload["url"]:
                    f.update(name=payload["data"]["name"], enabled=payload["data"]["enabled"])
            return 200, {}
        if path == "filtering/add_url":
            self.filters.append({"id": 1700000000 + len(self.filters) + 1, "url": payload["url"], "name": payload["name"], "enabled": True, "rules_count": 7, "last_updated": "now"})
            return 200, {}
        if path == "filtering/remove_url":
            self.filters = [f for f in self.filters if f["url"] != payload["url"]]
            return 200, {}
        if path == "filtering/refresh":
            return 200, {"updated": 1}
        if path == "filtering/set_rules":
            self.rules = payload["rules"]
            return 200, {}
        if path in ("safebrowsing/enable", "safebrowsing/disable"):
            self.safebrowsing = path.endswith("enable")
            return 200, {}
        if path == "clients/add":
            taken = {i for c in self.clients for i in c["ids"]}
            if taken & set(payload["ids"]):
                return 400, b"client already exists: another client uses the same ID\n"
            self.clients.append(payload)
            return 200, {}
        if path == "clients/update":
            if payload["name"] not in {c["name"] for c in self.clients}:
                return 400, b"client not found\n"
            taken = {i for c in self.clients if c["name"] != payload["name"] for i in c["ids"]}
            if taken & set(payload["data"]["ids"]):
                return 400, b"another client uses the same ID\n"
            self.clients = [dict(payload["data"]) if c["name"] == payload["name"] else c for c in self.clients]
            return 200, {}
        if path == "clients/delete":
            self.clients = [c for c in self.clients if c["name"] != payload["name"]]
            return 200, {}
        return 404, b"not found"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class World:
    """One world for a path: its own AdGuard, its own store, its own files."""

    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        self.adguard = FakeAdGuard()
        self.cred = os.path.join(self.dir, "adguard-admin.txt")
        open(self.cred, "w").write("user: admin\npassword: s3cret\n")
        self.filters_dir = os.path.join(self.dir, "filters")
        os.mkdir(self.filters_dir)
        open(os.path.join(self.filters_dir, "2.txt"), "w").write("! a comment\n||one.example^\n\n# another\n||two.example^\n  ||three.example^\n")
        self.groups = [{"id": "b00001", "name": "Свои", "on": True, "kind": "block", "items": ["casino.example", "ads.example"]},
                       {"id": "allow", "name": "Исключения", "on": True, "kind": "allow", "items": ["ok.example"]},
                       {"id": "b00002", "name": "Выключенная", "on": False, "kind": "block", "items": ["off.example"]}]
        self.stopped = {"1": 5123, "1700000001": 7, "safe": 3}
        self.public = True
        self.lines = []
        self.store_refuses = False
        self.sites = []                 # the groups of sites, for the rules' tests (the steps before them have none)
        self.members = {}               # {group id: addresses of its people}
        self.rules = None

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]


class NewPath:
    def __init__(self, world):
        w = world
        self.world = world

        class Groups:
            async def read(self):
                return own_groups(copy.deepcopy(w.groups))

            def check(self, groups):
                if w.store_refuses:
                    raise StoreRefused("block: запись без своего id")
                documents.check(documents.Kind.BLOCK, groups)

            async def save(self, groups):
                w.groups = copy.deepcopy(groups)

        class Tops:
            async def stopped_by_list(self):
                return w.stopped

        class Writer:
            async def write(self, entry):
                w.lines.append(entry.line())

        async def is_public(host):
            return w.public

        class RulesList:
            """The whole list over the world: its own order once one was stored, else the groups of sites, then the block groups."""

            async def read(self):
                if w.rules is None:
                    return rules_of(json.loads(json.dumps(w.sites)), own_groups(json.loads(json.dumps(w.groups))))
                return merged(merged(w.rules, Part.SITES, json.loads(json.dumps(w.sites))), Part.BLOCK, own_groups(json.loads(json.dumps(w.groups))))

            async def preview(self, part, records):
                return merged(await self.read(), part, list(records))

            async def save_all(self, rules):
                w.rules = json.loads(json.dumps(list(rules)))
                w.sites = sites_of(w.rules)
                w.groups = blocks_of(w.rules)

        class NoRouting:
            async def reapply(self):
                return False

        adguard = AdGuard(AdGuardClient(w.adguard.url, Path(w.cred)))
        class People:
            async def read(self):
                return StoredGroups.of([{"id": "g00001", "name": "Дети", "members": []}, {"id": "g00002", "name": "Гости", "members": []}])

        class Members:
            async def addresses(self, users=None):
                return {group: list(ips) for group, ips in w.members.items()}

        service = BlockService(adguard, Groups(), AdGuardCounters(adguard), Tops(), Path(w.filters_dir), is_public, rules=RulesList() if w.sites else None, members=Members())
        self.service = service
        journal = Journal(None, Writer())

        async def make():
            dispatcher = Dispatcher()
            dispatcher.register(Command.BLOCK_GET, Handled(service.show))
            dispatcher.register(Command.BLOCK_SET, Handled(service.save, journal))
            dispatcher.register(Command.BLOCK_LISTS, Handled(service.change_lists, journal))
            rules = RulesService(store=RulesList(), routing=NoRouting(), blocking=service, lock_file=Path(w.dir) / ".rules.lock", groups=People())
            dispatcher.register(Command.RULES_GET, Handled(rules.show))
            dispatcher.register(Command.RULE_ROUTE, Handled(rules.route, journal))
            return dispatcher
        self.dispatcher = LOOP.run_until_complete(make())

    def ask(self, arguments):
        return LOOP.run_until_complete(self.dispatcher.dispatch(arguments, Caller.PANEL))


def scrubbed(value, known):
    """An answer or a stored list with the ids made for new groups replaced by where they stand: both paths draw them at random."""
    if isinstance(value, list):
        return [scrubbed(item, known) for item in value]
    if isinstance(value, dict):
        out = {key: scrubbed(item, known) for key, item in value.items()}
        if isinstance(out.get("v"), str) and "groups" in out:
            out["v"] = "<v>"                # the fingerprint of the groups: new ones have random ids
        if isinstance(out.get("id"), str) and "items" in out and out["id"] not in known:
            out["id"] = "<new>"
        return out
    return value


ASIDE: dict = {}


def keep_aside(w):
    """A site in two groups is allowed now (the first in the order decides): what the step changes is put back after it."""
    ASIDE.update(groups=copy.deepcopy(w.groups), rules=list(w.adguard.rules))


def put_back(w):
    """…so the steps after see the world they always saw."""
    if ASIDE:
        w.groups = ASIDE["groups"]
        w.adguard.rules = ASIDE["rules"]


def t_entries():
    print("\n— записи и ссылки: те же правила")
    rnd = random.Random(11)
    pieces = ["example.com", "*.example.com", "a*.example.co.uk", "*", "bad domain", "10.0.0.0/8", "300.1.1.1", "1.2.3.4/40", "http://Site.Example/", "x" * 70 + ".com", "",
              "sub.example.com.", "ex*ample.com", "*.com", 5, None, "тест.рф", "https://a.example/path?x=1", "a.example", "a.example"]
    outcomes = []
    for _ in range(4000):
        items = [rnd.choice(pieces) for _ in range(rnd.randint(0, 6))]
        try:
            new = (entries.clean_entries(items), None)
        except Refused as e:
            new = (None, e.error)
        outcomes.append(plain(new))
    ok(kept("entries_digest", digest(outcomes)), "4000 случайных наборов записей: тот же итог или тот же отказ", digest(outcomes))
    labels = ["Имя", "  два   слова ", "", " ", "x" * 61, "x" * 60, "a\x07b", "tab\tinside", 5, None, "x" * 41]
    cleaned = [[entries.clean_label(text), entries.clean_label(text, 40)] for text in labels]
    ok(kept("labels", cleaned), "названия: тот же итог", cleaned)
    urls = ["https://lists.example.org/a.txt", "http://lists.example.org/a.txt", "https://10.0.0.1/a.txt", "https://lists.example.org", "https://lists.example.org/",
            "https://user:pw@lists.example.org/a.txt", "https://lists.example.org:8443/a.txt", "https://lists.example.org:443/a.txt?x=1", "https://LISTS.Example.Org./A.txt",
            "https://lists.example.org/a.txt#frag", "https://lists.example.org/a b.txt", "file:///etc/passwd", "/etc/passwd", "https://localhost/a.txt", "https://[::1]/a.txt",
            "https://lists.example.org:bad/a.txt", 5, None, "https://lists.example.123/a.txt", "https://" + "a" * 500 + ".org/x"]
    taken = [[link.url, link.host] if link else None for link in (links.clean_list_url(url) for url in urls)]
    ok(kept("list_urls", taken), "ссылки на списки: принимаются и отклоняются те же", taken)

    groups_cases = []
    stored = [{"id": "b00001", "name": "Свои", "on": True, "kind": "block", "items": ["a.example"]}, {"id": "b00002", "name": "Вторая", "on": False, "kind": "block", "items": []}]
    names = ["Свои", "Новая", "", "x" * 61, 7]
    for _ in range(3000):
        raw = []
        for _ in range(rnd.randint(0, 4)):
            raw.append({"id": rnd.choice(["b00001", "b00002", "zzzzzz", None, 5]), "name": rnd.choice(names), "on": rnd.choice([True, False, 1, None]),
                        "items": [rnd.choice(["a.example", "b.example", "*.c.example", "10.0.0.0/8", "bad domain", "d.example"]) for _ in range(rnd.randint(0, 3))],
                        **({"kind": rnd.choice(["block", "allow", "x"])} if rnd.random() < 0.3 else {})})
        if rnd.random() < 0.05:
            raw = rnd.choice([None, "x", [1], [{}] * 61])
        groups_cases.append(raw)
    outcomes = []
    for raw in groups_cases:
        try:
            new = (entries.clean_groups(copy.deepcopy(raw), stored, "block"), None)
        except Refused as e:
            new = (None, e.error)
        outcomes.append(plain((scrubbed(new[0], {"b00001", "b00002"}), new[1])))
    ok(kept("groups_digest", digest(outcomes)), "3000 случайных наборов групп: те же группы или тот же отказ", digest(outcomes))


def t_commands():
    print("\n— команды через диспетчер: как прежде")
    known = {"b00001", "b00002", "allow"}
    mine = "https://lists.example.org/mine.txt"
    long_refusal = "couldn't fetch filter from url: " + "x" * 300

    def fail(path, code, body):
        return lambda w: w.adguard.fail.update({path: (code, body)})

    def heal(w):
        w.adguard.fail.clear()

    steps = [
        ("block-get", None, ["block-get"]),
        ("block-set: новая группа, шаблон, выключенная группа", None, ["block-set", b64({"groups": [
            {"id": "b00001", "name": " Свои  переименованы ", "on": True, "items": ["casino.example", "*.Ads.Example", "http://new.example/x"]},
            {"name": "Новая", "on": True, "items": ["n.example"]}, {"id": "b00002", "name": "Выключенная", "on": False, "items": ["off.example"]}]})]),
        ("block-set: включить список (нужно обновление), выключить опасные сайты", None, ["block-set", b64({"groups": [], "lists": {"2": True, "1": True, "99": True}, "safebrowsing": False})]),
        ("block-set: «Исключения» со старой страницы не принимаются", None, ["block-set", b64({"groups": [{"id": "allow", "name": "Исключения", "on": True, "kind": "allow", "items": ["ok.example"]},
                                                                                                    {"name": "Одна", "on": 1, "items": ["one.example"]}], "lists": "x"})]),
        ("block-set: запись в двух группах", keep_aside, ["block-set", b64({"groups": [{"name": "А", "on": True, "items": ["dup.example"]}, {"name": "Б", "on": True, "items": ["dup.example"]}]})]),
        ("block-set: сеть в группе", put_back, ["block-set", b64({"groups": [{"name": "А", "on": True, "items": ["10.0.0.0/8"]}]})]),
        ("block-set: групп нет вовсе", None, ["block-set", b64({"lists": {"2": False}})]),
        ("block-set: не JSON", None, ["block-set", base64.b64encode(b"{not json").decode()]),
        ("block-set: хранилище не примет", lambda w: setattr(w, "store_refuses", True), ["block-set", b64({"groups": [{"name": "А", "on": True, "items": ["a.example"]}]})]),
        ("block-set: блокировщик отказал в правилах", lambda w: (setattr(w, "store_refuses", False), fail("filtering/set_rules", 400, "bad rules\n")(w)),
         ["block-set", b64({"groups": [{"name": "А", "on": True, "items": ["a.example"]}]})]),
        ("block-get: блокировщик отвечает ошибкой", lambda w: (heal(w), fail("filtering/status", 500, "boom")(w)), ["block-get"]),
        ("block-lists: блокировщик отвечает ошибкой", None, ["block-lists", b64({"op": "refresh"})]),
        ("block-get: счётчики недоступны — списки всё равно показаны", lambda w: (heal(w), fail("stats", 500, "boom")(w)), ["block-get"]),
        ("block-lists: добавить", heal, ["block-lists", b64({"op": "add", "name": " Мой  второй ", "url": "https://Lists.Example.org/second.txt"})]),
        ("block-lists: тот же список ещё раз", None, ["block-lists", b64({"op": "add", "name": "Другое имя", "url": "https://lists.example.org/second.txt"})]),
        ("block-lists: занятое название", None, ["block-lists", b64({"op": "add", "name": "реклама", "url": "https://lists.example.org/third.txt"})]),
        ("block-lists: название как у «Опасных сайтов»", None, ["block-lists", b64({"op": "add", "name": "Опасные сайты", "url": "https://lists.example.org/third.txt"})]),
        ("block-lists: без названия", None, ["block-lists", b64({"op": "add", "name": " ", "url": "https://lists.example.org/third.txt"})]),
        ("block-lists: не ссылка", None, ["block-lists", b64({"op": "add", "name": "Третий", "url": "http://lists.example.org/third.txt"})]),
        ("block-lists: адрес из ссылки не найден", lambda w: setattr(w, "public", False), ["block-lists", b64({"op": "add", "name": "Третий", "url": "https://lists.example.org/third.txt"})]),
        ("block-lists: блокировщик не смог скачать", lambda w: (setattr(w, "public", True), fail("filtering/add_url", 400, long_refusal)(w)),
         ["block-lists", b64({"op": "add", "name": "Третий", "url": "https://lists.example.org/third.txt"})]),
        ("block-lists: блокировщик отказал без слов", fail("filtering/add_url", 502, ""), ["block-lists", b64({"op": "add", "name": "Третий", "url": "https://lists.example.org/third.txt"})]),
        ("block-lists: переименовать", heal, ["block-lists", b64({"op": "rename", "id": 1700000001, "name": "Переименован"})]),
        ("block-lists: переименовать в занятое", None, ["block-lists", b64({"op": "rename", "id": 1700000001, "name": "adaway"})]),
        ("block-lists: переименовать в своё же", None, ["block-lists", b64({"op": "rename", "id": 1700000001, "name": "ПЕРЕИМЕНОВАН"})]),
        ("block-lists: переименовать встроенный", None, ["block-lists", b64({"op": "rename", "id": 1, "name": "Нельзя"})]),
        ("block-lists: номер текстом", None, ["block-lists", b64({"op": "remove", "id": "1700000001"})]),
        ("block-lists: убрать", None, ["block-lists", b64({"op": "remove", "id": 1700000001})]),
        ("block-lists: убрать тот, которого нет", None, ["block-lists", b64({"op": "remove", "id": 1700000001})]),
        ("block-lists: обновить", None, ["block-lists", b64({"op": "refresh"})]),
        ("block-lists: обновить не вышло", fail("filtering/refresh", 500, "upstream timeout"), ["block-lists", b64({"op": "refresh"})]),
        ("block-lists: неизвестное действие", heal, ["block-lists", b64({"op": "explode"})]),
        ("block-lists: JSON, но не объект", None, ["block-lists", b64(["op", "refresh"])]),
    ]
    world = World("new")
    new_path = NewPath(world)
    for number, (what, prepare, arguments) in enumerate(steps):
        if prepare:
            prepare(world)
        before_calls, before_lines = len(world.adguard.calls), len(world.lines)
        reply = new_path.ask(arguments)
        seen = (scrubbed(reply, known), world.adguard.calls[before_calls:], scrubbed(world.groups, known), world.adguard.rules, world.adguard.safebrowsing,
                [f["name"] + ("+" if f["enabled"] else "-") for f in world.adguard.filters], world.journal()[before_lines:])
        key = f"step {number:02d}: {what}"
        same = kept(key, seen)
        ok(same, f"{what}: тот же ответ, те же обращения к блокировщику, то же в хранилище и в журнале",
           None if same else [(i, a, b) for i, (a, b) in enumerate(zip(GOLDEN.get(key, []), plain(seen))) if a != b][:1])
    first = NewPath(World("check")).ask(["block-get"])
    ok(first["ok"] and [(entry["title"], entry["custom"], entry["on"], entry["rules"], entry["day"]) for entry in first["lists"]]
       == [("Реклама", False, True, 190777, 5123), ("AdAway", False, False, 3, 0), ("Свой", True, True, 12, 7)]
       and [g["id"] for g in first["groups"]] == ["b00001", "b00002"] and first["safebrowsing"] == {"on": True, "day": 3}
       and [t["domain"] for t in first["top"]] == ["ads.example", "track.example", "a.example", "b.example", "c.example"] and first["max_lists"] == 8,
       "что именно показывается: названия списков, размер выключенного списка из файла, свои группы без «Исключений», только имена и счётчики", first)
    world.adguard.stop()
    reply = new_path.ask(["block-get"])
    ok(not reply["ok"] and reply["error"].startswith("блокировщик не отвечает: "), "блокировщика нет вовсе — отказ с теми же словами в начале", reply)
    cred = World("nocred")
    os.unlink(cred.cred)
    reply = NewPath(cred).ask(["block-get"])
    ok(not reply["ok"] and kept("no password file", reply), "файла с паролем блокировщика нет — тот же отказ", reply)
    cred.adguard.stop()


def t_profiles():
    print("\n— фильтрация по группам")
    rnd = random.Random(5)
    names = [f"user{i}" for i in range(7)]
    outcomes = []
    for _ in range(3000):
        clients = [{"name": n, "ip": f"10.9.9.{i + 2}"} for i, n in enumerate(names) if rnd.random() < 0.8]
        if rnd.random() < 0.1:
            clients = []
        groups = [{"id": f"g{i:05d}", "name": f"G{i}", "filter": rnd.choice(["std", "off", "kids", None, "kids"]), "members": rnd.sample(names + ["ghost"], rnd.randint(0, 3))}
                  for i in range(rnd.randint(0, 3))]
        new = profiles.wanted([UserGroup.of(g) for g in groups], [user_of(c) for c in clients])
        wire = None if new is None else [p.wire() for p in new]
        outcome = [wire]
        if new:
            mixed = dict(wire[0], ids=list(reversed(wire[0]["ids"])), **{rnd.choice(profiles.COMPARED_SWITCHES): rnd.choice([True, False, None])})
            outcome += [profiles.signature(new), profiles.same(new[0].wire(), mixed)]
        outcomes.append(plain(outcome))
    ok(kept("profiles_digest", digest(outcomes)), "3000 случайных наборов групп: те же клиенты блокировщика, та же отметка, то же сравнение", digest(outcomes))

    clients = [{"name": "alice", "ip": "10.9.9.2"}, {"name": "bob", "ip": "10.9.9.3"}, {"name": "carol", "ip": "10.9.9.4"}]
    kids = {"id": "kkkkk1", "name": "Дети", "filter": "kids", "members": ["alice", "bob"]}
    off = {"id": "ooooo1", "name": "Без", "filter": "off", "members": ["carol"]}
    rounds = [("с нуля", [kids, off], clients), ("ещё раз — ничего не меняется", [kids, off], clients), ("участник перешёл в другую группу", [dict(kids, members=["alice"]), dict(off, members=["carol", "bob"])], clients),
              ("детский режим снят", [dict(kids, filter="std"), off], clients), ("пользователей не прочитать", [kids, off], []), ("групп нет", [], clients)]
    t_profiles_whole(clients, kids)
    world = World("p-new")

    class Groups:
        async def read(self):
            return StoredGroups.of([])

    class Silent:
        async def write(self, *a, **k):
            pass

    memory = profiles.ProfileMemory()
    new_profiles = profiles.AdGuardProfiles(AdGuard(AdGuardClient(world.adguard.url, Path(world.cred))), Groups(), Silent(), Path(world.dir) / ".users.lock", memory)
    for what, groups, listed in rounds:
        mark = len(world.adguard.calls)
        new_error = LOOP.run_until_complete(new_profiles.sync([UserGroup.of(g) for g in groups], [user_of(c) for c in listed]))
        seen = (new_error, world.adguard.calls[mark:], world.adguard.clients, memory.signature)
        ok(kept(f"profiles_sync, {what}", seen), f"profiles_sync, {what}: те же обращения к блокировщику, те же клиенты, та же отметка", seen)
    world.adguard.clients = [{"name": "grp-zzzzz9", "ids": ["10.9.9.2"], "use_global_settings": False, "filtering_enabled": True}]
    world.adguard.fail["clients/delete"] = (400, "cannot delete\n")
    world.adguard.fail["clients/update"] = (400, "cannot update\n")
    memory.signature = None
    new_error = LOOP.run_until_complete(new_profiles.sync([UserGroup.of(kids)], [user_of(c) for c in clients]))
    ok(new_error and kept("profiles_sync refused", new_error) and memory.signature is None, "блокировщик отказал — та же причина, отметка не поставлена", new_error)
    world.adguard.stop()
    new_error = LOOP.run_until_complete(new_profiles.sync([UserGroup.of(kids)], [user_of(c) for c in clients]))
    ok(new_error.startswith("блокировщик не отвечает ("), "блокировщика нет — «не отвечает», ничего не меняется", new_error)


def t_profiles_whole(clients, kids):
    print("\n— весь трафик через выход: DNS через выход, защита та же")
    users = [user_of(c) for c in clients + [{"name": "dave", "ip": "10.9.9.5"}]]
    kids_group = UserGroup.of(kids)
    pro = UserGroup.of({"id": "ppppp1", "name": "Pro", "filter": "pro", "members": ["carol"]})
    plain = profiles.wanted([kids_group, pro], users, {"dave": "proplus"})
    split = profiles.wanted([kids_group, pro], users, {"dave": "proplus"}, {"alice", "carol", "dave", "ghost"})
    by_name = {p.name: p for p in split}
    ok(sorted(by_name) == ["grp-kkkkk1", "grp-kkkkk1-all", "grp-pro-all", "grp-proplus-all"],
       "каждый клиент делится на себя и двойника -all; пустой не заводится", sorted(by_name))
    ok(by_name["grp-kkkkk1"].ids == ["10.9.9.3"] and by_name["grp-kkkkk1-all"].ids == ["10.9.9.2"], "ребёнок с «весь трафик» — в детском двойнике, не в обычном", split)
    twin = by_name["grp-kkkkk1-all"].wire()
    base = next(p for p in plain if p.name == "grp-kkkkk1").wire()
    ok({k: v for k, v in twin.items() if k not in ("name", "ids", "upstreams", "upstreams_cache_enabled", "upstreams_cache_size")}
       == {k: v for k, v in base.items() if k not in ("name", "ids", "upstreams")}, "у двойника та же защита (родительский контроль, безопасный поиск)", twin)
    ok(twin["upstreams"] == list(profiles.WHOLE_UPSTREAMS) and twin["upstreams_cache_enabled"] and twin["upstreams_cache_size"] > 0,
       "двойник спрашивает резолверы выхода, со своим кэшем", twin)
    ok(all(p.upstreams == [] and "upstreams_cache_enabled" not in p.wire() for p in plain), "без «весь трафик» клиенты как прежде: DNS по режиму сети")
    addresses = [a for p in split for a in p.ids]
    ok(len(addresses) == len(set(addresses)), "адрес — не больше чем в одном клиенте", addresses)
    every = profiles.wanted([], users, {}, {"bob"})
    ok([p.name for p in every] == ["grp-all"] and every[0].use_global_settings and every[0].ids == ["10.9.9.3"] and every[0].upstreams,
       "защита как у всех — клиент grp-all с общими настройками и DNS через выход", every)
    ok(profiles.wanted([], [], {}, {"bob"}) is None, "пользователей не прочитать — ничего не трогаем")
    ok(profiles.wanted([], users, {}, {"ghost"}) == [], "выбор того, кого нет, ничего не заводит")
    held = dict(twin, upstreams=[])
    ok(not profiles.same(twin, held) and not profiles.same(twin, dict(twin, upstreams_cache_enabled=False)) and profiles.same(twin, dict(twin)),
       "клиент с теми же адресами, но другими DNS — не тот же (будет исправлен)")
    ok(profiles.same(base, dict(base, upstreams=None)), "upstreams: null у блокировщика — то же, что пусто")


def t_profiles_routes():
    print("\n— группы, которым правило шлёт сайты за границу или напрямую: свои клиенты блокировщика со своими DNS")
    from awgscale.agent.sites.scoped import GroupDns, RouteDns
    from awgscale.agent.users.models import KnownUser, UserGroup as Group
    users = [KnownUser(name=name, ip=f"10.9.9.{i}") for i, name in enumerate(["anna", "boris", "kid", "pro", "plain", "guest"], start=2)]
    kids = Group.of({"id": "kkkkk1", "name": "Дети", "filter": "kids", "kids_lists": ["social"], "members": ["kid"]})
    pros = Group.of({"id": "ppppp1", "name": "Pro", "filter": "pro", "members": ["pro", "anna"]})
    plain = Group.of({"id": "sssss1", "name": "Обычные", "members": ["plain", "boris"]})
    routes = {"kkkkk1": RouteDns(upstreams=("https://8.8.8.8/dns-query", "[/kids.example/]https://1.1.1.1/dns-query"), direct_lines=("[/home.example/]https://8.8.8.8/dns-query",)),
              "sssss1": RouteDns(upstreams=("https://8.8.8.8/dns-query", "[/x.example/]https://1.1.1.1/dns-query"), direct_lines=())}
    before = profiles.wanted([kids, pros, plain], users, {"anna": "proplus"}, {"boris"})
    same_without = profiles.wanted([kids, pros, plain], users, {"anna": "proplus"}, {"boris"}, {})
    ok(before == same_without, "без правил для групп — клиенты те же, что были")
    split = profiles.wanted([kids, pros, plain], users, {"anna": "proplus"}, {"boris", "kid"}, routes)
    by_name = {p.name: p for p in split}
    ok(sorted(by_name) == ["grp-kkkkk1-rkkkkk1-all", "grp-pro", "grp-proplus", "grp-rsssss1", "grp-rsssss1-all"],
       "дети (весь трафик) — двойник варианта; Pro — как был (его группе правило не шлёт); обычные — grp-r<группа> и его двойник", sorted(by_name))
    ok(by_name["grp-rsssss1"].ids == ["10.9.9.6"] and by_name["grp-rsssss1"].use_global_settings and by_name["grp-rsssss1"].upstreams == list(routes["sssss1"].upstreams)
       and by_name["grp-rsssss1"].upstreams_cache_enabled, "человек группы без своей защиты: защита как у всех, DNS — своего списка, свой кэш", by_name["grp-rsssss1"])
    ok(by_name["grp-rsssss1-all"].ids == ["10.9.9.3"] and by_name["grp-rsssss1-all"].upstreams == list(profiles.WHOLE_UPSTREAMS),
       "весь трафик через выход в такой группе: резолверы выхода (и ни одной строки «напрямую» — у группы их нет)", by_name["grp-rsssss1-all"])
    twin = by_name["grp-kkkkk1-rkkkkk1-all"]
    ok(twin.parental_enabled and twin.upstreams == [*profiles.WHOLE_UPSTREAMS, "[/home.example/]https://8.8.8.8/dns-query"],
       "ребёнок с «весь трафик»: детская защита, резолверы выхода, но имена «напрямую» его группы — напрямую", twin)
    split = profiles.wanted([kids, pros, plain], users, {}, set(), routes)
    by_name = {p.name: p for p in split}
    ok(by_name["grp-kkkkk1-rkkkkk1"].upstreams == list(routes["kkkkk1"].upstreams) and by_name["grp-kkkkk1-rkkkkk1"].ids == ["10.9.9.4"] and "grp-kkkkk1" not in by_name,
       "ребёнок: вариант детского клиента с DNS своего списка", sorted(by_name))
    addresses = [a for p in split for a in p.ids]
    ok(len(addresses) == len(set(addresses)), "адрес — не больше чем в одном клиенте", addresses)
    ok(profiles.wanted([plain], [], {}, set(), routes) is None, "пользователей не прочитать — ничего не трогаем")

    needs = group_lists.needs_of([kids, pros, plain], {"anna": "proplus", "boris": "pro"}, ["kkkkk1", "sssss1"])
    ok({"grp-kkkkk1-rkkkkk1", "grp-kkkkk1-rkkkkk1-all", "grp-pro-rsssss1", "grp-pro-rsssss1-all"} <= set(needs) and "grp-proplus-rsssss1" not in needs
       and needs["grp-kkkkk1-rkkkkk1"] == needs["grp-kkkkk1"] and needs["grp-pro-rsssss1"] == needs["grp-pro"],
       "списки групп называют и варианты: детский клиент группы, уровни её людей; всё — с двойниками", sorted(needs))
    ok(group_lists.needs_of([kids, pros, plain], {"anna": "proplus"}) == group_lists.needs_of([kids, pros, plain], {"anna": "proplus"}, []), "без таких групп — как было")

    class Rules:
        reads = 0
        rules = [{"id": "s00001", "name": "Детям напрямую", "on": True, "route": "direct", "groups": ["kkkkk1"], "items": ["home.example"]},
                 {"id": "a00001", "name": "Видео", "on": True, "route": "exit", "items": ["youtube.com"]}]

        async def read(self):
            Rules.reads += 1
            return Rules.rules

    class Routing:
        mode_now = "list"

        def mode(self):
            from awgscale.agent.commands import RouteMode
            return RouteMode(Routing.mode_now)

        def hand_names(self):
            return ["bank.example"]

    dns = GroupDns(Rules(), Routing())
    made = LOOP.run_until_complete(dns.of())
    kids_dns = made.get("kkkkk1")
    ok(list(made) == ["kkkkk1"] and kids_dns is not None and any("youtube.com" in line for line in kids_dns.upstreams)
       and any("home.example" in line and "8.8.8.8" in line for line in kids_dns.upstreams) and any("bank.example" in line for line in kids_dns.upstreams)
       and kids_dns.direct_lines == ("[/home.example/]https://8.8.8.8/dns-query https://8.8.4.4/dns-query",),
       "DNS группы: её список целиком (за границу — через выход, напрямую — напрямую, и ручной файл), строки «напрямую» для двойников", made)
    again = LOOP.run_until_complete(dns.of())
    ok(again is made, "тот же список, режим и ручной файл — не пересобирается")
    Routing.mode_now = "geo"
    ok(LOOP.run_until_complete(dns.of()) is not made, "режим сменился — собрано заново")


def t_block_lifted():
    print("\n— правило для группы «за границу»/«напрямую» выше блока: снимает блок только с её людей")
    world = World("lifted")

    class Rules:
        def __init__(self, rules):
            self.rules = rules

        async def read(self):
            return self.rules

        async def preview(self, part, records):
            return merged(self.rules, part, list(records))

    class Members:
        async def addresses(self, users=None):
            return {"g00001": ["172.16.17.9", "172.16.17.5"], "g00002": ["172.16.17.7"]}

    async def lines(rules):
        service = BlockService(adguard=AdGuard(AdGuardClient(world.adguard.url, Path(world.cred))), groups=None, counters=None, tops=None, filters_dir=Path(world.dir),
                               rules=Rules(rules), members=Members())
        await service.follow()
        return list(world.adguard.rules)

    plain = [{"id": "b00001", "name": "Блок", "on": True, "route": "block", "items": ["vk.com", "tiktok.com"]}]
    ok(LOOP.run_until_complete(lines(plain)) == ["||vk.com^", "||tiktok.com^"], "без правил для групп — как было")
    rules = [{"id": "s00001", "name": "Детям vk", "on": True, "route": "direct", "groups": ["g00001"], "items": ["vk.com"]}, *plain,
             {"id": "b00002", "name": "Блок гостям", "on": True, "route": "block", "groups": ["g00001", "g00002"], "items": ["m.vk.com", "x.example"]}]
    got = LOOP.run_until_complete(lines(rules))
    ok(got == ["||vk.com^$client=~'172.16.17.5'|~'172.16.17.9'", "||tiktok.com^", "||x.example^$client='172.16.17.5'|'172.16.17.7'|'172.16.17.9'"],
       "для всех: блок всем, кроме людей группы (m.vk.com ниже перекрыт блоком vk.com); остальное — как было", got)
    rules[1] = dict(rules[1], items=["tiktok.com"])
    got = LOOP.run_until_complete(lines(rules))
    ok(got == ["||tiktok.com^", "||m.vk.com^$client='172.16.17.7'", "||x.example^$client='172.16.17.5'|'172.16.17.7'|'172.16.17.9'"],
       "блок для групп ниже правила для одной из них: без людей той группы", got)
    below = [*plain, rules[0]]
    ok(LOOP.run_until_complete(lines(below)) == ["||vk.com^", "||tiktok.com^"], "правило для группы ниже блока ничего не снимает")


def t_group_lists():
    print("\n— списки групп: уровни Pro / Pro++ / Ultimate, дополнительные списки детского режима, «Угрозы»")
    Source = group_lists.GroupSource
    header = ["[Adblock Plus]", "! Title: HaGeZi's list", "! Expires: 8 hours", ""]
    texts = {
        Source.PRO: header + ["||track.example^", "||Ads.Example.COM^", "||track.example^", "||x.example^$important", "@@||allowed.example^", "||*.wild.example^",
                              "/regex/", "0.0.0.0 hosts.example", "# comment", "  ||spaced.example^  ", "||trailing.example^ ! note", "||under_score.example^"],
        Source.PROPLUS: header + ["||track.example^", "||ads.example.com^", "||plus.example^"],
        Source.ULTIMATE: header + ["||track.example^", "||plus.example^", "||ultimate.example^"],
        Source.NSFW: header + ["||adult.example^", "||track.example^"],
        Source.GAMBLING: header + ["||casino.example^"],
        Source.BYPASS: header + ["||doh.example^"],
        Source.SOCIAL: header + ["||social.example^"],
    }
    text = {source: "\r\n".join(lines) + "\n" for source, lines in texts.items()}

    ok(list(group_lists.rule_names(text[Source.PRO])) == ["track.example", "ads.example.com", "track.example", "spaced.example", "under_score.example"],
       "из списка взяты только простые ||имя^ — заголовок, модификаторы, исключения, шаблоны и прочее отброшены (CRLF не мешает)", list(group_lists.rule_names(text[Source.PRO])))

    def group(group_id, filter, members=("alice",), **more):
        return UserGroup.of(dict({"id": group_id, "name": group_id, "filter": filter, "members": list(members)}, **more))

    groups = [group("aaaaa1", "pro"), group("aaaaa2", "pro", ["bob"]), group("aaaaa3", "ultimate"), group("kkkkk1", "kids", kids_lists=["social", "nsfw"]),
              group("kkkkk2", "kids"), group("kkkkk3", "kids", [], kids_lists=["gambling"]), group("eeeee1", "proplus", []), group("sssss1", "std")]
    needs = group_lists.needs_of(groups)
    ok(needs == {"grp-kkkkk1": (Source.NSFW, Source.SOCIAL), "grp-kkkkk1-all": (Source.NSFW, Source.SOCIAL), "grp-pro": (Source.PRO,), "grp-pro-all": (Source.PRO,),
                 "grp-ultimate": (Source.ULTIMATE,), "grp-ultimate-all": (Source.ULTIMATE,)},
       "что кому нужно: один клиент на уровень, детской группе — её списки, и всегда их двойникам -all; без участников, без списков и обычные — ничего", needs)
    ok(group_lists.signature(needs) == "grp-kkkkk1=nsfw,social grp-kkkkk1-all=nsfw,social grp-pro=pro grp-pro-all=pro grp-ultimate=ultimate grp-ultimate-all=ultimate",
       "подпись нужд — одной строкой", group_lists.signature(needs))
    own_needs = group_lists.needs_of([group("sssss1", "std")], {"dave": "proplus", "erin": "kids", "fred": "std", "gina": "proplus"})
    ok(own_needs == {"grp-proplus": (Source.PROPLUS,), "grp-proplus-all": (Source.PROPLUS,)},
       "уровень, выбранный человеком самим (без группы этого уровня), получает свой список HaGeZi", own_needs)
    ok(group_lists.needs_of([group("aaaaa1", "pro")], {"dave": "pro"}) == group_lists.needs_of([group("aaaaa1", "pro")]), "выбор совпал с группой — список тот же, по разу")
    ok(group_lists.sources_of(needs) == [Source.PRO, Source.ULTIMATE, Source.NSFW, Source.SOCIAL], "скачивается только нужное, каждый список по разу", group_lists.sources_of(needs))
    ok(group_lists.needs_of([group("kkkkk1", "kids")]) == {}, "детская группа из старой записи (без списков) ничего не требует — как прежде")

    def made(needs, sources=None):
        blocked = {}
        for source in sources or group_lists.sources_of(needs):
            group_lists.merged_into(blocked, text[source], group_lists.client_bits(needs, source), 1)
        return "".join(group_lists.file_lines(needs, blocked)).splitlines()

    nested = {"grp-kkkkk1": (Source.NSFW,), "grp-pro": (Source.PRO,), "grp-proplus": (Source.PROPLUS,), "grp-ultimate": (Source.ULTIMATE,)}
    lines = made(nested)
    ok(lines[:2] == ["! Title: Списки групп", "! Needs: grp-kkkkk1=nsfw grp-pro=pro grp-proplus=pro.plus grp-ultimate=ultimate"], "в заголовке — для чего файл сделан", lines[:2])
    ok(lines[2:] == ["||track.example^$client='grp-kkkkk1'|'grp-pro'|'grp-proplus'|'grp-ultimate'",
                     "||ads.example.com^$client='grp-pro'|'grp-proplus'",
                     "||spaced.example^$client='grp-pro'",
                     "||under_score.example^$client='grp-pro'",
                     "||plus.example^$client='grp-proplus'|'grp-ultimate'",
                     "||ultimate.example^$client='grp-ultimate'",
                     "||adult.example^$client='grp-kkkkk1'"],
       "каждое имя один раз, со всеми клиентами, которым его блокировать (уровни вложены друг в друга)", lines[2:])
    try:
        group_lists.merged_into({}, text[Source.SOCIAL], 1, 2)
        refused = None
    except group_lists.ListRefused as e:
        refused = str(e)
    ok(refused == "в списке 1 правил, меньше 2", "список, в котором правил меньше порога, отклонён", refused)

    folder = Path(tempfile.mkdtemp(prefix="group-lists-", dir=TMP))
    target = folder / "userfilters" / "group-lists.txt"
    content = [line + "\n" for line in lines]
    ok(group_lists.written_if_changed(target, iter(content)) is True and target.read_text() == "".join(content) and (target.stat().st_mode & 0o777) == 0o644,
       "файла не было — записан целиком, читаем для резолвера (0644), папка создана", oct(target.stat().st_mode))
    inode = target.stat().st_ino
    ok(group_lists.written_if_changed(target, iter(content)) is False and target.stat().st_ino == inode, "то же содержимое — файл не переписан", target.stat().st_ino)
    ok(group_lists.made_for(target) == "grp-kkkkk1=nsfw grp-pro=pro grp-proplus=pro.plus grp-ultimate=ultimate" and group_lists.made_for(folder / "none.txt") is None,
       "для чего сделан файл, читается из его заголовка", group_lists.made_for(target))
    ok(group_lists.written_if_changed(target, iter(content + ["||new.example^\n"])) is True and sorted(p.name for p in target.parent.iterdir()) == ["group-lists.txt"],
       "другое содержимое — файл заменён, временных файлов рядом не осталось", sorted(p.name for p in target.parent.iterdir()))

    # the download: https only, a size cap, an answer that is not 200 refused
    asked = []

    def answer(request):
        asked.append(request.url.path)
        if request.url.path.endswith("/social.txt"):
            return httpx.Response(404)
        if request.url.path.endswith("/ultimate.txt"):
            return httpx.Response(200, content=b"||a.example^\n" * 1000)
        return httpx.Response(200, content=text[Source.PRO].encode())

    def download(base, source, cap=100_000):
        try:
            return LOOP.run_until_complete(group_lists.ListDownloader(base, cap, httpx.MockTransport(answer)).text(source))
        except group_lists.ListRefused as e:
            return "refused: " + str(e)

    ok(download("https://lists.example/adblock/", Source.PRO) == text[Source.PRO] and asked == ["/adblock/pro.txt"], "скачан по https, по имени файла списка", asked)
    asked.clear()
    ok(download("http://lists.example/adblock/", Source.PRO) == "refused: ссылка на список не https" and asked == [], "ссылка не https — отказ, ничего не запрошено", asked)
    ok(download("https://lists.example/adblock/", Source.SOCIAL) == "refused: social: HTTP 404", "ответ не 200 — отказ", None)
    ok(download("https://lists.example/adblock/", Source.ULTIMATE, 5000) == "refused: ultimate: больше 5000 байт", "больше предела по размеру — отказ", None)

    # the keeper, against AdGuard's API (the stand-in over HTTP)
    world = World("group-lists")
    stored = {"groups": [{"id": "aaaaa1", "name": "Про", "filter": "pro", "members": ["alice"]}, {"id": "kkkkk1", "name": "Дети", "filter": "kids", "members": ["bob"]}]}
    downloads = []
    broken = {}
    written = []

    class Groups:
        async def read(self):
            return StoredGroups.of(copy.deepcopy(stored["groups"]))

    class Downloads:
        async def text(self, source):
            downloads.append(source.value)
            if source in broken:
                raise broken[source]
            return text[source]

    class Notes:
        async def write(self, text, bad=False, kind=None, who=None):
            written.append((text, bad))

    memory = group_lists.GroupListsMemory()
    target = folder / "kept" / "group-lists.txt"
    keeper = group_lists.GroupBlockLists(AdGuard(AdGuardClient(world.adguard.url, Path(world.cred))), Groups(), Notes(), Downloads(), target, 1, memory)

    def keep(refresh=False):
        before = len(world.adguard.calls)
        downloads.clear()
        error = LOOP.run_until_complete(keeper.keep(refresh))
        return error, [path for path, _ in world.adguard.calls[before:]]

    def held():
        return [f for f in world.adguard.filters if f["url"] == str(target)]

    def rules():
        return target.read_text().splitlines()[2:]

    error, calls = keep()
    ok(error is None and calls == ["filtering/add_url"] and world.adguard.calls[-1][1] == {"name": "Списки групп", "url": str(target), "whitelist": False}
       and downloads == ["pro"] and memory.applied == "grp-pro=pro grp-pro-all=pro", "группа уровня Pro с участниками: скачан только список Pro, файл записан и подключён в AdGuard по локальному пути", [error, calls, downloads])
    ok(rules()[0] == "||track.example^$client='grp-pro'|'grp-pro-all'" and len(rules()) == 4, "в файле правила только для grp-pro (и его двойника -all)", rules())
    error, calls = keep()
    ok(error is None and calls == [] and downloads == [], "ещё раз: файл сделан для тех же нужд, список подключён — ничего не скачано и не изменено", [error, calls, downloads])
    inode = target.stat().st_ino
    error, calls = keep(refresh=True)
    ok(error is None and calls == [] and downloads == ["pro"] and target.stat().st_ino == inode, "плановое обновление, список не изменился — файл не переписан, AdGuard не обновляет", [error, calls])
    stored["groups"][1]["kids_lists"] = ["social", "bypass"]
    error, calls = keep()
    ok(error is None and calls == ["filtering/refresh"] and downloads == ["pro", "doh-vpn-proxy-bypass", "social"] and "||social.example^$client='grp-kkkkk1'|'grp-kkkkk1-all'" in rules(),
       "детской группе включили списки — файл сделан заново с ними, AdGuard обновил списки", [error, calls, downloads])
    texts[Source.PRO].append("||new-pro.example^")
    text[Source.PRO] = "\n".join(texts[Source.PRO]) + "\n"
    error, calls = keep(refresh=True)
    ok(error is None and calls == ["filtering/refresh"] and "||new-pro.example^$client='grp-pro'|'grp-pro-all'" in rules(), "список изменился — файл заменён, AdGuard обновил списки", [error, calls])
    texts[Source.PRO].append("||owed.example^")
    text[Source.PRO] = "\n".join(texts[Source.PRO]) + "\n"
    world.adguard.fail["filtering/refresh"] = (500, "busy")
    error, calls = keep(refresh=True)
    ok(error and "||owed.example^$client='grp-pro'|'grp-pro-all'" in rules() and calls == ["filtering/refresh"], "файл заменён, а обновить AdGuard не вышло — ошибка", [error, calls])
    world.adguard.fail.clear()
    written.clear()
    memory.complained_at = 0.0
    error, calls = keep()
    ok(error is None and calls == ["filtering/refresh"], "в следующий раз файл тот же, но обновление AdGuard за ним числится — сделано", [error, calls])
    before = target.read_text()
    broken[Source.SOCIAL] =group_lists.ListRefused("в списке 0 правил, меньше 1")
    error, calls = keep(refresh=True)
    ok(error == "в списке 0 правил, меньше 1" and calls == [] and target.read_text() == before and held(), "один из списков не годится — отказ, файл в работе цел, список в AdGuard остался", [error, calls])
    ok(written == [("Списки групп не применяются: в списке 0 правил, меньше 1", True)], "отказ записан в журнал как плохая новость", written)
    error, calls = keep(refresh=True)
    ok(error and len(written) == 1, "второй отказ подряд в журнал не идёт (не чаще раза в час)", written)
    broken.clear()
    world.adguard.filters = [dict(f, enabled=False) if f["url"] == str(target) else f for f in world.adguard.filters]
    error, calls = keep()
    ok(error is None and calls == ["filtering/set_url", "filtering/refresh"] and held()[0]["enabled"] is True, "список выключили в AdGuard — включён снова и правила загружены", [error, calls])
    world.adguard.filters = [f for f in world.adguard.filters if f["url"] != str(target)]
    error, calls = keep()
    ok(error is None and calls == ["filtering/add_url"], "список убрали из AdGuard руками — подключён снова", [error, calls])

    # a group save puts it right in the background (through the per-group clients' sync)
    profile_memory = profiles.ProfileMemory()
    with_lists = profiles.AdGuardProfiles(AdGuard(AdGuardClient(world.adguard.url, Path(world.cred))), Groups(), Notes(), folder / ".users.lock", profile_memory, keeper)
    users = [user_of({"name": "alice", "ip": "10.9.9.2"}), user_of({"name": "bob", "ip": "10.9.9.3"})]

    async def save_and_wait(records):
        stored["groups"] = records
        typed = [UserGroup.of(record) for record in records]
        error = await with_lists.sync(typed, users)
        for _ in range(100):
            await asyncio.sleep(0.02)
            if memory.applied == group_lists.signature(group_lists.needs_of(typed)):
                break
        return error

    pro, kids = copy.deepcopy(stored["groups"])
    before = len(world.adguard.calls)
    downloads.clear()
    error = LOOP.run_until_complete(save_and_wait([dict(pro, filter="std"), dict(kids, kids_lists=[])]))
    ok(error is None and not held() and not target.exists() and memory.applied == "" and "filtering/remove_url" in [p for p, _ in world.adguard.calls[before:]] and downloads == [],
       "списки больше никому не нужны — список убран из AdGuard, файл удалён, ничего не скачано", [p for p, _ in world.adguard.calls[before:]])
    error = LOOP.run_until_complete(save_and_wait([dict(pro, members=[]), dict(kids, kids_lists=[])]))
    ok(error is None and not held() and downloads == [], "группа уровня без участников списка не требует", downloads)
    error = LOOP.run_until_complete(save_and_wait([dict(pro, filter="ultimate"), dict(kids, kids_lists=["nsfw"])]))
    ok(error is None and held() and target.exists() and downloads == ["ultimate", "nsfw"] and memory.applied == "grp-kkkkk1=nsfw grp-kkkkk1-all=nsfw grp-ultimate=ultimate grp-ultimate-all=ultimate"
       and [c["name"] for c in world.adguard.clients] == ["grp-kkkkk1", "grp-ultimate"],
       "сохранение с группой Ultimate и детской с NSFW — клиенты заведены, нужные списки скачаны и подключены в фоне", [downloads, [c["name"] for c in world.adguard.clients]])

    # failures: the blocker away with nothing to free is not news; a list that cannot be downloaded at first is
    stored["groups"] = []
    LOOP.run_until_complete(keeper.keep())
    memory.complained_at = 0.0
    written.clear()
    world.adguard.fail["filtering/status"] = (500, "boom")
    error, calls = keep()
    ok(error is None and written == [] and memory.applied == "", "списки никому не нужны, файла нет, AdGuard не отвечает — тишина в журнале", [error, written])
    world.adguard.fail.clear()
    stored["groups"] = [{"id": "aaaaa1", "name": "Про", "filter": "pro", "members": ["alice"]}]
    broken[Source.PRO] = httpx.ConnectError("no route to host")
    error, calls = keep()
    ok(error == "список не скачан: no route to host" and calls == [] and not target.exists() and written == [("Списки групп не применяются: список не скачан: no route to host", True)],
       "файла ещё нет и скачать не вышло — в AdGuard ничего не подключено, сказано в журнале", [error, calls, written])
    broken.clear()

    # «Угрозы»: put in when missing, by its link, for everybody
    threats = group_lists.ThreatsList(AdGuard(AdGuardClient(world.adguard.url, Path(world.cred))), THREATS_LIST_URL)
    before = len(world.adguard.calls)
    added = LOOP.run_until_complete(threats.ensure())
    ok(added is True and world.adguard.calls[before:] == [("filtering/add_url", {"name": "Угрозы", "url": THREATS_LIST_URL, "whitelist": False})], "«Угрозы» нет — подключён по ссылке", world.adguard.calls[before:])
    world.adguard.filters = [dict(f, enabled=False) if f["url"] == THREATS_LIST_URL else f for f in world.adguard.filters]
    before = len(world.adguard.calls)
    ok(LOOP.run_until_complete(threats.ensure()) is False and world.adguard.calls[before:] == [], "«Угрозы» есть (пусть и выключен админом) — ничего не меняется", world.adguard.calls[before:])

    # the block tab: both are lists of the panel's own, not attached by the admin
    keep()
    new_path = NewPath(world)
    world.adguard.filters = [dict(f, url=GROUP_LISTS_FILE) if f["url"] == str(target) else f for f in world.adguard.filters]
    shown = new_path.ask(["block-get"])
    mine = {entry["url"]: entry for entry in shown.get("lists", []) if entry["url"] in (GROUP_LISTS_FILE, THREATS_LIST_URL)}
    ok(mine.get(GROUP_LISTS_FILE, {}).get("title") == "Списки групп" and mine.get(THREATS_LIST_URL, {}).get("title") == "Угрозы"
       and not any(entry["custom"] for entry in mine.values()) and len(mine) == 2,
       "вкладка блокировок: «Списки групп» и «Угрозы» названы так и не считаются своими списками админа", mine)
    for url, entry in mine.items():
        removed = new_path.ask(["block-lists", b64({"op": "remove", "id": entry["id"]})])
        ok(removed == {"ok": False, "error": "такого списка нет"} and any(f["url"] == url for f in world.adguard.filters), f"убрать «{entry['title']}» из панели нельзя", removed)
    world.adguard.stop()


def t_public_host():
    print("\n— адрес ссылки на список: обычный ли он (одна проверка с сайтами, IPv4 и IPv6)")
    real = socket.getaddrinfo
    socket.getaddrinfo = made_up_getaddrinfo
    try:
        for host, public in (("v4.example", True), ("v6.example", True), ("both.example", True), ("inner6.example", False), ("inner4.example", False),
                             ("loop.example", False), ("nxdomain.example", False)):
            seen = LOOP.run_until_complete(links.host_is_public(host))
            ok(seen is public, f"{host}: {'обычный' if public else 'не обычный'} адрес", seen)
    finally:
        socket.getaddrinfo = real


def t_rules():
    print("\n— правила одним списком: настоящая блокировка")
    world = World("rules")
    world.sites = [{"id": "a00001", "name": "Соцсети", "on": True, "items": ["vk.com", "tiktok.com"]},
                   {"id": "a00002", "name": "Видео", "on": True, "items": ["youtube.com"]}]
    world.groups = [g for g in world.groups if g.get("kind") != "allow"]
    path = NewPath(world)
    shown = path.ask(["block-get"])
    stale = path.ask(["block-set", b64({"groups": [dict(g, on=False) for g in shown["groups"]], "base": "000000000000"})])
    ok(stale.get("ok") is False and stale.get("conflict") is True and stale.get("v") == shown["v"] and world.groups[0]["on"] is True,
       "вкладка «Блокируется», открытая давно, со старой версией: отказ «изменилось в другом окне» с новым списком, ничего не сохранено", stale.get("error"))
    ok(path.ask(["block-set", b64({"groups": shown["groups"], "base": shown["v"]})]).get("ok") is True, "с версией, которую страница видела, — сохраняется")
    lists_before = json.loads(json.dumps(world.adguard.filters))
    safe_before = world.adguard.safebrowsing
    route = lambda *changes, order=None: path.ask(["rule-route", b64({"changes": [{"rule": r, "route": w} for r, w in changes], **({"order": order} if order else {})})])
    reply = route(("a00001", "block"))
    moved = next((group for group in world.groups if group["id"] == "a00001"), None)
    ok(reply.get("ok") and moved and moved["on"] and moved["kind"] == "block" and moved["items"] == ["vk.com", "tiktok.com"] and not any(g["id"] == "a00001" for g in world.sites),
       "«Соцсети» в блок: та же запись, тот же id, теперь среди блокировки", [reply, world.groups])
    ok("||vk.com^" in world.adguard.rules and "||casino.example^" in world.adguard.rules and "||off.example^" not in world.adguard.rules,
       "правила блокировщика: и новые, и прежние; выключенной группы — нет", world.adguard.rules)
    ok(world.adguard.filters == lists_before and world.adguard.safebrowsing == safe_before, "списки блокировщика и проверка опасных сайтов — как были")
    reply = path.ask(["block-set", b64({"groups": [*path.ask(["block-get"])["groups"], {"name": "Видео в блок", "on": True, "items": ["m.youtube.com"]}]})])
    ok(reply.get("ok") and reply.get("covered") == {next(g["id"] for g in world.groups if g["name"] == "Видео в блок"): {"m.youtube.com": "Видео"}} and "||m.youtube.com^" not in world.adguard.rules,
       "блок m.youtube.com ниже правила «Видео» (youtube.com за границу): перекрыт — блокировщик его не получает, страница видит почему", [reply.get("covered"), world.adguard.rules])
    ids = [rule["id"] for rule in path.ask(["rules-get"])["rules"]]
    blocked_video = next(g["id"] for g in world.groups if g["name"] == "Видео в блок")
    reply = route(order=[blocked_video, *[i for i in ids if i != blocked_video]])
    ok(reply.get("ok") and "||m.youtube.com^" in world.adguard.rules, "поставлен наверх — блокируется", world.adguard.rules)
    reply = route(("a00001", "exit"))
    ok(reply.get("ok") and any(g["id"] == "a00001" and g["on"] for g in world.sites) and "||vk.com^" not in world.adguard.rules,
       "«Соцсети» обратно за границу: их правила из блокировщика ушли", world.adguard.rules)
    print("\n— блок для выбранных групп людей: правило с адресами их людей")
    world.members = {"g00001": ["172.16.17.9", "172.16.17.5"], "g00002": []}
    reply = path.ask(["rule-route", b64({"changes": [{"rule": "a00001", "route": "block", "groups": ["g00001", "g00002"]}]})])
    lines = [line for line in world.adguard.rules if "vk.com" in line]
    ok(reply.get("ok") and lines == ["||vk.com^$client='172.16.17.5'|'172.16.17.9'"],
       "«Соцсети» — блок для «Дети» (двое) и пустой группы: одно правило с адресами двоих", [reply, lines])
    calls = len(world.adguard.calls)
    LOOP.run_until_complete(path.service.follow(None, force=False))
    ok(len(world.adguard.calls) == calls, "опрос без перемен: блокировщика не беспокоят")
    world.members = {"g00001": ["172.16.17.5"], "g00002": ["172.16.17.7"]}
    LOOP.run_until_complete(path.service.follow(None, force=False))
    lines = [line for line in world.adguard.rules if "vk.com" in line]
    ok(lines == ["||vk.com^$client='172.16.17.5'|'172.16.17.7'"], "человек перешёл в другую группу — правило следует за ним", lines)
    world.members = {"g00001": [], "g00002": []}
    LOOP.run_until_complete(path.service.follow(None, force=False))
    ok(not any("vk.com" in line for line in world.adguard.rules), "в группах никого — правило никого не блокирует (и всех тоже)", world.adguard.rules)
    world.members = {}
    path.ask(["rule-route", b64({"changes": [{"rule": "a00001", "route": "exit", "groups": []}]})])
    world.adguard.fail["filtering/set_rules"] = (400, "bad rules\n")
    reply = route(("a00001", "block"))
    ok(not reply.get("ok") and any(g["id"] == "a00001" for g in world.sites) and "400" in reply.get("error", "") and "прежние правила сохранены" in reply.get("error", ""),
       "блокировщик не принял правила (и при возврате тоже): хранится прежний список, причина названа", reply)


def main():
    t_entries()
    t_public_host()
    t_commands()
    t_profiles()
    t_group_lists()
    t_profiles_routes()
    t_block_lifted()
    t_rules()
    golden_written()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
