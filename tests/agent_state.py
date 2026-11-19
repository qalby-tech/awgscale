#!/usr/bin/env python3
"""The state and what a look at it does on the way, as the package has them
(awgscale.agent.state, .seen, .tops): `state` sent through the real dispatcher
on stand-ins gives the answer, asks the other services, leaves the files and
stored notes and writes the journal lines recorded in tests/golden/agent_state.json;
the notes of the last look, the day's tops and an exit's system figures on
seeded random inputs come out as recorded too. (Recorded while the package was
still checked against the single-file agent it replaced, and equal to what
that gave; the looks at `state` were recorded again when the state came to
count the users instead of listing them: the list is a page of `clients` now.)

    uv run python tests/agent_state.py
    GOLDEN_WRITE=1 uv run python tests/agent_state.py   record again (after a deliberate change)

Nothing live is touched."""
import asyncio
import base64
import contextlib
import hashlib
import http.server
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agent-state-")
GOLDEN_FILE = os.path.join(HERE, "golden", "agent_state.json")
WRITE = os.environ.get("GOLDEN_WRITE") == "1"

from awgscale.agent import errors  # noqa: E402
from awgscale.agent.adguard.api import AdGuard  # noqa: E402
from awgscale.agent.adguard.client import AdGuardDown  # noqa: E402
from awgscale.agent.adguard.profiles import AdGuardProfiles, ProfileMemory  # noqa: E402
from awgscale.agent.clients.gates import WatchService  # noqa: E402
from awgscale.agent.clients.http import ServiceClient  # noqa: E402
from awgscale.agent.clients.users import UsersClient  # noqa: E402
from awgscale.agent.clients.watch import WatchClient  # noqa: E402
from awgscale.agent.commands import Command, RouteMode  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.panel.looks import PanelState  # noqa: E402
from awgscale.agent.exits.working import CompiledExits  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent.seen.models import ExitSeen, Notes  # noqa: E402
from awgscale.agent.seen.notes import next_notes  # noqa: E402
from awgscale.agent.seen.service import SeenNotes  # noqa: E402
from awgscale.agent.sites.models import SiteGroup  # noqa: E402
from awgscale.agent.spaces.models import StoredSpaces  # noqa: E402
from awgscale.agent.state.dns import DnsDay  # noqa: E402
from awgscale.agent.state.ledger import Ledger  # noqa: E402
from awgscale.agent.state.service import Sampling, StateService  # noqa: E402
from awgscale.agent.state.system import Entry, ExitSystems  # noqa: E402
from awgscale.agent.tops import service as tops  # noqa: E402
from awgscale.agent.tops.sites import site_of  # noqa: E402
from awgscale.agent.tunnel import Tunnel  # noqa: E402
from awgscale.agent.users.directory import Directory, user_of  # noqa: E402
from awgscale.agent.users.models import StoredGroups  # noqa: E402
from awgscale.agent.users.pauses import Pauses  # noqa: E402
from awgscale.agent.users.service import UsersService  # noqa: E402
from awgscale.agent.wire import unix  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402
from awgscale.users.models import UserView  # noqa: E402
from awgscale.watch.conf import RouteMode as WatchMode  # noqa: E402
from awgscale.watch.health import ExitState  # noqa: E402
from awgscale.watch.models import ExitView, Status, Work  # noqa: E402


def moment_of_seconds(seconds):
    """The world keeps the moments of the profile memory as seconds, 0 for "never"; the package as a datetime or None."""
    return datetime.fromtimestamp(seconds, UTC) if seconds else None


def seconds_of_moment(moment):
    return moment.timestamp() if moment is not None else 0.0

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
NOW = 1_800_000_000


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:1500])


GOLDEN = {} if WRITE else json.load(open(GOLDEN_FILE))
RECORDED = {}
# "Выход exit2-de снова работает (не отвечал 0 с)": how long an exit was down is
# counted on the wall clock from a moment the suite set just before, so a slow run
# may say 1 s; both sides are compared with that number of seconds left out.
JUST_NOW_DOWN = re.compile(r"\(не отвечал (?P<seconds>[01]) с\)")


def without_just_now(value):
    return json.loads(JUST_NOW_DOWN.sub("(не отвечал <0-1> с)", json.dumps(value, ensure_ascii=False)))


def as_recorded(key, seen, text):
    """`seen` against what is recorded under `key` (or, with GOLDEN_WRITE=1, recorded)."""
    seen = json.loads(json.dumps(seen, ensure_ascii=False, default=str))
    RECORDED[key] = seen
    if WRITE:
        return
    seen = without_just_now(seen)
    expected = without_just_now(GOLDEN.get(key))
    same = seen == expected
    shown = None
    if not same and isinstance(seen, dict) and isinstance(expected, dict):
        shown = {part: [expected.get(part), seen.get(part)] for part in set(seen) | set(expected) if expected.get(part) != seen.get(part)}
    elif not same:
        shown = [expected, seen]
    ok(same, text, shown)


def prints_recorded(key, prints, text):
    """A long seeded run kept as one short fingerprint per round: says which round first differs from the recorded ones."""
    RECORDED[key] = prints
    if WRITE:
        return
    recorded = GOLDEN.get(key) or []
    first = next((i for i, (a, b) in enumerate(zip(prints, recorded)) if a != b), None if len(prints) == len(recorded) else min(len(prints), len(recorded)))
    ok(first is None, text, first)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:12]


def exit_seen(record):
    state = record.get("state")
    return ExitSeen(index=record.get("index"), name=record.get("name"), state=ExitState(state) if state in ("up", "down") else ExitState.UNKNOWN)


def t_seen_pure():
    print("\n— замеченное между опросами: что запомнить и о чём написать")
    rnd = random.Random(11)
    names = [f"user{i}" for i in range(6)]
    prints = []
    events_seen = 0
    for run in range(400):
        new_notes = {}
        story = []
        now = NOW + rnd.randint(0, 1000)
        for step in range(12):
            now += rnd.choice([1, 5, 60, 300, 700, 4000, 200000])
            clients = [{"name": n, "ip": f"10.9.9.{i + 2}", "expires_at": rnd.choice([None, None, now + 100, now + 800, now + 5000, now - 50])} for i, n in enumerate(names) if rnd.random() < 0.8]
            if rnd.random() < 0.12:
                clients = []
            exits = [{"index": i, "name": f"exit{i}", "state": rnd.choice(["up", "up", "up", "down", "unknown", None, ""])} for i in (1, 3, 4) if rnd.random() < 0.9]
            if rnd.random() < 0.1:
                exits = []
            if rnd.random() < 0.25:                    # the agent removes somebody itself in between
                if new_notes:
                    new_notes["leaving"] = dict(new_notes.get("leaving") or {}, **{random.Random(run * 100 + step).choice(names): now + 600})
            ahead, events = next_notes(Notes.of(json.loads(json.dumps(new_notes))), [user_of(c) for c in clients], [exit_seen(e) for e in exits], datetime.fromtimestamp(now, UTC))
            mine = ahead.record()
            my_events = [(event.text, event.bad, event.kind.value) for event in events]
            events_seen += len(my_events)
            story.append([mine, my_events])
            new_notes = mine
        prints.append(fingerprint(story))
    ok(events_seen > 500, f"400 случайных историй по 12 опросов: событий хватает ({events_seen})")
    prints_recorded("seen: 400 random", prints, "…записанное и события в каждой — как записано (отпечатки)")


# ── the day's tops ───────────────────────────────────────────────────────────

HOSTS = ["www.youtube.com", "rr3.googlevideo.com", "www.google.com", "google.com", "mail.yandex.ru", "example.co.uk", "sub.site.com.ru", "news.site.io", "localhost", "t.me", "api.telegram.org",
         "bad_host!", "ozon.ru", "x.com", "deep.sub.portal.de", "xn--80a.xn--p1ai", "shop.example.su", "", "connectivitycheck.gstatic.com"]


def log_line(rnd, moment):
    entry = {"T": moment.isoformat().replace("+00:00", "Z") if rnd.random() < 0.5 else moment.astimezone().isoformat(timespec="microseconds") + ("" if rnd.random() < 0.5 else "")[:0],
             "QH": rnd.choice(HOSTS) + rnd.choice(["", "", "."]), "QT": rnd.choice(["A", "A", "A", "AAAA", "HTTPS"]), "IP": rnd.choice(["172.16.17.5", "172.16.17.9", "172.16.17.1", "10.0.0.5", "нет"])}
    if rnd.random() < 0.5:
        entry["T"] = moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{rnd.randint(0, 999999999):09d}+00:00"
    if rnd.random() < 0.25:
        entry["Result"] = {"IsFiltered": True, "Reason": rnd.choice([3, 4, 7]), "Rules": rnd.choice([[{"FilterListID": rnd.choice([0, 1, 17])}], [], None, [None]])}
        return json.dumps(entry, separators=(",", ":"))
    if rnd.random() < 0.1:
        entry["Result"] = {}
    line = json.dumps(entry, separators=(",", ":"))
    return line if rnd.random() < 0.97 else line[: len(line) // 2]


def t_tops():
    print("\n— посещаемое за сутки: счёт по журналу запросов")
    rnd = random.Random(7)
    hosts = [rnd.choice(HOSTS[:-2]) for _ in range(40)] + ["a.b.c.co.uk", "WWW.Google.RU.", "x", "googleapis.com", "my.own-site.org", "cdn.example.com.ua"]
    ok(site_of("www.google.com") == "Google Поиск" and site_of("google.com") is None and site_of("a.b.c.co.uk") == "c.co.uk",
       "к какому сайту относится имя: самый длинный известный хвост, фон не считается, у двухуровневых зон — три метки")
    as_recorded("tops: site_of", {host: site_of(host) for host in hosts}, "…и для каждого из случайных имён — как записано")
    folder = tempfile.mkdtemp(prefix="tops-", dir=TMP)
    prints = []
    counted = 0
    for run in range(60):
        now = NOW + run * 1000 + rnd.random()
        log = os.path.join(folder, f"querylog{run}.json")
        moments = sorted(now - rnd.random() * rnd.choice([3600, 80000, 90000, 200000]) for _ in range(rnd.randint(0, 400)))
        lines = [log_line(rnd, datetime.fromtimestamp(m, UTC)) for m in moments]
        cut = rnd.randint(0, len(lines)) if rnd.random() < 0.5 else 0
        if cut:
            with open(log + ".1", "w") as fh:
                fh.write("".join(line + "\n" for line in lines[:cut]))
        with open(log, "w") as fh:
            fh.write("".join(line + "\n" for line in lines[cut:]) + ("" if rnd.random() < 0.8 else "\n\n"))
        conf = os.path.join(folder, f"cascade{run}.conf")
        if rnd.random() < 0.8:
            with open(conf, "w") as fh:
                fh.write('CLIENT_IF="awg0"\nCLIENT_SUBNET="172.16.17.0/24"\n' if rnd.random() < 0.7 else 'CLIENT_SUBNET=10.0.0.0/24\n')
        mode = rnd.choice(["list", "list", "geo"])
        groups = [{"id": "g00001", "name": "A", "on": True, "items": ["youtube.com", "*.telegram.org", "149.154.160.0/20", "site.io"]}, {"id": "g00002", "name": "B", "on": rnd.random() < 0.5, "items": ["ozon.ru", "x.com"]}]
        listed = set(tops.resolver_names(tops.flat_entries([SiteGroup.of(g) for g in groups])))
        try:
            conf_text = open(conf).read()
        except OSError:
            conf_text = None
        mine = tops.count(Path(log), datetime.fromtimestamp(now, UTC), tops.users_test(conf_text), tops.route_by(RouteMode(mode), listed)).record()
        counted += mine["total"] + sum(mine["blocked_lists"].values())
        prints.append(fingerprint(mine))
    ok(counted > 2000, f"60 случайных журналов: учтённых строк хватает ({counted})")
    prints_recorded("tops: 60 random", prints, "…итоги, верхушки по обоим путям и счёт по спискам блокировки в каждом — как записано (отпечатки)")
    blob = json.dumps(mine)
    ok("172.16.17" not in blob and set(mine) == {"at", "hours", "total", "sites", "blocked_lists", "exit", "exit_total", "direct", "direct_total"},
       "в подсчитанном только названия и числа: ни одного адреса пользователя", blob[:300])


# ── the state itself ─────────────────────────────────────────────────────────

CONF = 'CLIENT_IF="awg0"\nCLIENT_SUBNET="10.9.9.0/24"\nEXITS=(\n  "1:awgk1:10.77.1.2:exit1-nl:2"\n  "3:awgk3:10.77.3.2:exit2-de"\n)\n'
SUSP_RULE = ["-i", "awg0", "-m", "set", "--match-set", "cascade_susp", "src", "-j", "DROP"]


def key(n):
    return f"{n:043d}="


def at(seconds):
    return None if seconds is None else datetime.fromtimestamp(seconds, UTC)


class ExitServer:
    """A real HTTP server that stands in for the small service on an exit."""

    def __init__(self):
        self.reply = {"code": 200, "body": json.dumps({"load1": 0.5, "cpus": 2, "mem_total": 4000, "mem_used": 1000, "uptime": 77, "disk_pct": 31, "hostname": "x"}).encode()}
        self.hits = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                server.hits.append((self.client_address[0], self.path))
                self.send_response(server.reply["code"])
                self.send_header("Content-Length", str(len(server.reply["body"])))
                self.end_headers()
                self.wfile.write(server.reply["body"])
        self.http = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()


EXIT = ExitServer()


class World:
    """The world around the agent: the store, the users and watch services, the host's tools and files, the blocker."""

    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        for sub in ("proc", "net/awg0", "net/ens3/statistics", "net/awgk1/statistics", "net/awgk3/statistics", "run"):
            os.makedirs(os.path.join(self.dir, sub))
        put = lambda rel, text: open(os.path.join(self.dir, rel), "w").write(text)
        put("proc/meminfo", "MemTotal:        2000000 kB\nMemFree:          100000 kB\nMemAvailable:    1200000 kB\n")
        put("proc/loadavg", "0.42 0.30 0.20 1/200 999\n")
        put("proc/uptime", "86400.55 1000.00\n")
        put("net/ens3/statistics/rx_bytes", "111\n")
        put("net/ens3/statistics/tx_bytes", "222\n")
        put("net/awgk1/statistics/rx_bytes", "333\n")
        put("net/awgk1/statistics/tx_bytes", "мусор\n")
        put("cascade.conf", CONF)
        put("domains-exit.txt", "# сайты\nyoutube.com\n*.telegram.org\n149.154.160.0/20\n\nsite.io\n")
        put("domains-direct.txt", "bank.example\n")
        put("adguard-admin.txt", "user: admin\npassword: secret-of-the-blocker\n")
        put("token", "token-of-a-service\n")
        self.path = lambda rel: os.path.join(self.dir, rel)
        self.views = [UserView(name="alice", ip=IPv4Address("10.9.9.2"), expires_at=None, created_at=at(NOW)),
                      UserView(name="bob", ip=IPv4Address("10.9.9.3"), expires_at=datetime(2030, 1, 2, 3, 4, 5, 678901, UTC), created_at=at(NOW)),
                      UserView(name="carol", ip=IPv4Address("10.9.9.4"), expires_at=None, created_at=at(NOW)),
                      UserView(name="dave", ip=IPv4Address("10.9.9.12"), expires_at=None, created_at=at(NOW))]
        self.users_down = False
        self.groups = [{"id": "aaaaaa", "name": "Семья", "space": False, "exit": 3, "filter": "kids", "members": ["alice", "bob"]}]
        self.spaces = [{"id": "s00001", "name": "Дом", "on": True, "members": ["alice"]}]
        self.sites = [{"id": "g00001", "name": "Видео", "on": True, "items": ["youtube.com", "site.io"]}, {"id": "g00002", "name": "Список", "on": True, "items": ["a.example"], "url": "https://lists.example/x.txt"}]
        self.paused = {"carol": NOW - 100, "ghost": NOW - 50}
        self.hand = {"dave": 1}
        self.seen = {}
        self.store_down = False
        self.status = "normal"            # what the watch service answers: a Status, None for no answer
        self.dump = ""
        self.listen_port = "51820\n"
        self.ipset = None
        self.rules = []
        self.ag = {}
        self.ag_down = False
        self.ag_stats = {"num_dns_queries": 1200, "num_blocked_filtering": 300, "num_replaced_safebrowsing": 4, "avg_processing_time": 0.01234,
                         "top_blocked_domains": [{"ads.example": 50}, {"Bad Name!": 9}, {"track.example": 7}], "top_clients": [{"10.9.9.2": 700}, {"10.9.9.3": 500}]}
        self.asked = []
        self.lines = []
        self.profile_seen = {"sig": None, "at": 0.0, "err_at": 0.0, "fail_at": 0.0}
        self.exit_asked = {}

    def exits(self, first="up", second="down"):
        views = [ExitView(index=1, name="exit1-nl", iface="awgk1", state=ExitState(first), handshake=datetime(2029, 5, 6, 7, 8, 9, 123456, UTC), rx=10, tx=20,
                          clients=[IPv4Address("10.9.9.2"), IPv4Address("10.9.9.3")], probe_ms=41.5, changed=datetime(2029, 5, 6, 7, 0, 0, 500000, UTC)),
                 ExitView(index=3, name="exit2-de", iface="awgk3", state=ExitState(second), handshake=None, rx=0, tx=0, clients=[IPv4Address("10.9.9.4")], probe_ms=None, changed=None)]
        work = Work(started=datetime(2029, 5, 1, 0, 0, 0, 250000, UTC), applied=None, last_tick=datetime(2029, 5, 6, 7, 8, 10, 750000, UTC), tick_seconds=0.37, ticks=900, tick_failed=2)
        return Status(time=at(NOW), exits=views, udp_via_exit=True, route_mode=WatchMode.FOREIGN, force_exit_nets=5, ru_networks=9000, force_exit_ips=70, force_direct_ips=8, watch=work)

    def watch_status(self):
        return self.exits() if self.status == "normal" else self.status

    def get(self, doc):
        if self.store_down:
            raise ConnectionError("нет связи")
        return json.loads(json.dumps(getattr(self, {"ugroups": "groups", "spaces": "spaces", "sites": "sites", "suspended": "paused"}[doc])))

    def put(self, doc, value):
        if self.store_down:
            raise ConnectionError("нет связи")
        setattr(self, {"ugroups": "groups", "spaces": "spaces", "sites": "sites", "suspended": "paused"}[doc], json.loads(json.dumps(value)))

    def host(self, cmd):
        cmd = list(cmd)
        self.asked.append(("run", *cmd))
        if cmd[:2] == ["awg", "show"]:
            if cmd[3] == "endpoints":
                return 0, f"{key(9)}\t203.0.113.{cmd[2][-1]}:443\n" if cmd[2] == "awgk1" else ""
            if cmd[3] == "dump":
                return 0, self.dump
            if cmd[3] == "listen-port":
                return 0, self.listen_port
        if cmd[:5] == ["ip", "-4", "route", "show", "default"]:
            return 0, "default via 203.0.113.1 dev ens3 proto static\n"
        if cmd[:5] == ["ip", "-4", "-o", "addr", "show"]:
            return 0, "7: awgk1    inet 127.0.0.2/32 scope global awgk1\n" if cmd[5] == "awgk1" else ""
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

    def adguard_get(self, path):
        self.asked.append(("adguard", path))
        if self.ag_down:
            raise ConnectionError("refused")
        if path == "stats":
            return json.loads(json.dumps(self.ag_stats))
        return {"clients": [dict(c) for c in self.ag.values()]}

    def adguard_post(self, path, payload):
        self.asked.append(("adguard", path, payload.get("name")))
        if self.ag_down:
            raise ConnectionError("refused")
        if path == "clients/delete":
            self.ag.pop(payload["name"], None)
        if path == "clients/add":
            self.ag[payload["name"]] = dict(payload)

    def journal(self):
        return [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines]

    def left(self):
        """Everything a look may have changed: the stored documents, the files, the kernel's set, the blocker."""
        read = lambda rel: read_file(self.path(rel))
        kept = json.loads(read("run/top-sites.json") or "{}")
        return [self.groups, self.spaces, self.paused, self.hand, self.seen, read("pins.map"), read("suspended.ips"), None if self.ipset is None else sorted(self.ipset), self.rules, self.ag,
                {k: v for k, v in kept.items() if k != "at"}, json.loads(read("run/panel-clients.json") or "null")]


def read_file(path):
    try:
        return open(path).read()
    except OSError:
        return None


class Agent:
    def __init__(self, world):
        self.world = world
        w = world

        def serve(request):
            if request.headers.get("authorization") != "Bearer token-of-a-service":
                return httpx.Response(401, json={"ok": False, "error": "нет токена"})
            if request.url.path == "/v1/status":
                w.asked.append(("watch", "status-json"))
                status = w.watch_status()
                return httpx.Response(503, json={"ok": False, "error": "занят"}) if status is None else httpx.Response(200, json=status.model_dump(mode="json"))
            if request.url.path == "/v1/pin":
                w.asked.append(("watch", "pin"))
                return httpx.Response(200, json={"ok": True})
            if request.url.path == "/v1/users":
                w.asked.append(("users", "list"))
                if w.users_down:
                    raise httpx.ConnectError("refused")
                return httpx.Response(200, json={"ok": True, "users": [view.model_dump(mode="json") for view in w.views]})
            return httpx.Response(404, json={})
        service = lambda: ServiceClient("http://service", Path(w.path("token")), transport=httpx.MockTransport(serve))

        def down(work):
            try:
                return work()
            except ConnectionError as e:
                raise errors.StoreDown(str(e)) from None

        class Groups:
            async def read(self):
                return StoredGroups.of(down(lambda: w.get("ugroups")))

        class Spaces:
            async def read(self):
                return StoredSpaces.of(down(lambda: w.get("spaces")))

        class Sites:
            async def read(self):
                return [SiteGroup.of(record) for record in down(lambda: w.get("sites"))]

        class Hand:
            async def read(self):
                down(lambda: w.get("spaces"))
                return dict(w.hand)

            async def save(self, pins):
                w.hand = dict(pins)

        class PauseStore:
            async def read(self):
                return {name: at(since) for name, since in down(lambda: w.get("suspended")).items()}

            async def save(self, pauses):
                down(lambda: w.put("suspended", {name: int(since.timestamp()) for name, since in pauses.items()}))

        class Seen:
            async def load(self):
                down(lambda: w.get("spaces"))
                return json.loads(json.dumps(w.seen))

            async def save(self, notes):
                w.seen = json.loads(json.dumps(notes))

        class Whole:
            @contextlib.asynccontextmanager
            async def whole(self):
                yield

        class AdGuardClient:
            async def get(self, path, timeout=5):
                try:
                    return w.adguard_get(path)
                except ConnectionError as e:
                    raise AdGuardDown(f"<urlopen error {e}>" if path == "stats" else str(e), "ConnectionError") from None

            async def post(self, path, payload, timeout=10):
                try:
                    w.adguard_post(path, payload)
                except ConnectionError as e:
                    raise AdGuardDown(str(e), "ConnectionError") from None

        class Writer:
            async def write(self, entry):
                w.lines.append(entry.line())

        class Memory:
            signature = property(lambda self: w.profile_seen["sig"], lambda self, value: w.profile_seen.update(sig=value))
            asked_at = property(lambda self: moment_of_seconds(w.profile_seen["at"]), lambda self, value: w.profile_seen.update(at=seconds_of_moment(value)))
            complained_at = property(lambda self: moment_of_seconds(w.profile_seen["err_at"]), lambda self, value: w.profile_seen.update(err_at=seconds_of_moment(value)))
            failed_at = property(lambda self: moment_of_seconds(w.profile_seen["fail_at"]), lambda self, value: w.profile_seen.update(fail_at=seconds_of_moment(value)))

        async def host(cmd, stdin=None, timeout=30):
            code, out = w.host(cmd)
            return Result(code, out, "")

        P = lambda rel: Path(w.path(rel))
        journal = Journal(None, Writer())
        watch = WatchService(WatchClient(service()))
        directory = Directory(UsersClient(service()), P("run/panel-clients.json"), P("awg0.conf"))
        adguard = AdGuard(AdGuardClient())
        pauses = Pauses(PauseStore(), host, "awg0", P("suspended.ips"))
        users = UsersService(directory, None, Groups(), Hand(), None, watch, PauseStore(), pauses, P("pins.map"), P(".users.lock"))
        profiles = AdGuardProfiles(adguard, Groups(), journal, P(".users.lock"), Memory())
        seen = SeenNotes(Seen(), Whole(), journal, P(".users.lock"))
        top_sites = tops.TopSites(P("querylog.json"), P("run/top-sites.json"), P("cascade.conf"), P("route.mode"), Sites())
        state = StateService(
            watch=watch, exits=CompiledExits(P("cascade.conf")), tunnel=Tunnel(host, "awg0"), entry=Entry(host, P("proc"), P("net")), directory=directory, pins=users, seen=seen,
            profiles=profiles, exit_servers=ExitSystems(host, EXIT.port, w.exit_asked), pause_store=PauseStore(), pauses=pauses, hand_pins=Hand(), groups=Groups(), sites=Sites(), spaces=Spaces(),
            dns=DnsDay(top_sites, adguard), client_iface="awg0", domains_exit=P("domains-exit.txt"), domains_direct=P("domains-direct.txt"), lock_file=P(".users.lock"),
            sampling=Sampling(every=timedelta(hours=1), online_for=timedelta(seconds=180), keep_day_every=timedelta(hours=1)), ledger=Ledger(P("run/day.json")))
        self.state = state

        async def make():
            dispatcher = Dispatcher()
            dispatcher.register(Command.STATE, Handled(state.show))
            dispatcher.register(Command.CLIENTS, Handled(state.clients))
            return dispatcher
        self.dispatcher = LOOP.run_until_complete(make())

    def ask(self):
        """`state` after a change (a new look: the users counted), and every user as one page of `clients`."""
        self.state.changed()
        answer = LOOP.run_until_complete(self.dispatcher.dispatch(["state"], Caller.PANEL))
        query = base64.b64encode(json.dumps({"limit": 100}).encode()).decode()
        page = LOOP.run_until_complete(self.dispatcher.dispatch(["clients", query], Caller.PANEL))
        return answer, page


def cached(view):
    """A user as the directory keeps it in its file (run/panel-clients.json): moments as whole seconds."""
    return {"name": view.name, "ip": str(view.ip), "expires_at": unix(view.expires_at), "created_at": unix(view.created_at)}


def state_seen(reply, page, world, hits):
    """One look as the golden file keeps it (the state, and its users as `clients` pages them): what the agent's own machine and clock give is replaced by what it is."""
    reply = json.loads(json.dumps(reply))
    page = json.loads(json.dumps(page))
    if isinstance(page.get("time"), (int, float)):
        page["time"] = "<now>"
    if isinstance(reply.get("time"), (int, float)):
        reply["time"] = "<now>"
    entry = reply.get("entry")
    if isinstance(entry, dict):
        entry.update({part: f"<{part}>" for part in ("hostname", "cpus", "disk_pct") if part in entry})
    return clock_scrubbed({"reply": reply, "clients": page, "left": world.left(), "asked": world.asked, "journal": world.journal(), "exit_hits": hits})


def clock_scrubbed(value):
    """The moments of the agent's own clock (the notes of the last look) as "<now>"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and abs(value - time.time()) < 3600:
        return "<now>"
    if isinstance(value, dict):
        return {key: clock_scrubbed(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clock_scrubbed(item) for item in value]
    return value


def same_second():
    """The look counts from the clock: it is asked early in a second."""
    while time.time() % 1 > 0.25:
        time.sleep(0.02)


def dump(*peers):
    lines = ["PRIVATE-KEY-OF-THE-TUNNEL\tPUBLIC\t51820\toff"]
    for address, endpoint, handshake, rx, tx in peers:
        lines.append("\t".join([key(1), "PRESHARED-KEY", endpoint, f"{address}/32", str(handshake), str(rx), str(tx), "off"]))
    return "\n".join(lines) + "\n"


def numbers_only(reply, page):
    """Is every moment and counter of the answer and of its page of users a number, as the panel and the bot read them."""
    number = lambda value: type(value) in (int, float)
    fits = type(reply["time"]) is int and type(page["time"]) is int and reply["watch"].get("last_tick", 0) is not None and number(reply["watch"].get("last_tick", 0))
    for client in page["clients"]:
        fits = fits and type(client["handshake"]) is int and type(client["rx"]) is int and type(client["tx"]) is int and (client["expires_at"] is None or type(client["expires_at"]) is int)
    for exit_ in reply["exits"]:
        fits = fits and type(exit_["handshake"]) is int and type(exit_["iface_rx"]) is int and type(exit_["iface_tx"]) is int and type(exit_["rx"]) is int and (exit_["changed"] is None or number(exit_["changed"]))
    return fits


def t_state():
    print("\n— state: ответ, обращения и следы — как записано")
    full = dump(("10.9.9.2", "198.51.100.7:40000", NOW - 30, 1000, 2000), ("10.9.9.3", "(none)", 0, 0, 0), ("10.9.9.4", "[2001:db8::5]:777", NOW - 9000, 5, 6), ("10.9.9.2", "198.51.100.8:1", NOW - 10, 7, 8))
    log_now = time.time()
    visit = lambda host, ip="10.9.9.2", ago=600, **more: json.dumps({"T": at(log_now - ago).isoformat(), "QH": host, "QT": "A", "IP": ip, **more}, separators=(",", ":")) + "\n"
    log = (visit("old.example", ago=90000) + visit("www.youtube.com") + visit("rr1.googlevideo.com", "10.9.9.3") + visit("bank.example") + visit("bank.example", "10.9.9.3") + visit("site.io")
           + visit("ads.example", Result={"IsFiltered": True, "Reason": 3, "Rules": [{"FilterListID": 17}]}) + visit("ya.ru", "10.9.9.1") + visit("ya.ru", "192.0.2.1"))

    def fresh(w):
        for rel in ("run/panel-clients.json",):
            with contextlib.suppress(OSError):
                os.remove(w.path(rel))

    def no_cache(w):
        fresh(w)
        with contextlib.suppress(OSError):
            os.remove(w.path("run/top-sites.json"))

    def first(w):
        w.dump = full
        with open(w.path("querylog.json"), "w") as fh:
            fh.write(log)
    steps = [
        ("первый взгляд: всё на месте", first),
        ("второй взгляд сразу за первым", lambda w: None),
        ("список пользователей спрошен заново", fresh),
        ("срок вышел у одного пользователя, выход упал, другой поднялся", lambda w: (fresh(w), setattr(w, "views", [v for v in w.views if v.name != "bob"]), setattr(w, "status", w.exits("down", "up")))),
        ("сторож не отвечает", lambda w: (fresh(w), setattr(w, "status", None))),
        ("сторож вернулся", lambda w: (fresh(w), setattr(w, "status", w.exits("up", "up")))),
        ("хранилище недоступно: паузы возвращаются из файла", lambda w: (fresh(w), setattr(w, "store_down", True), setattr(w, "ipset", None), setattr(w, "rules", []))),
        ("хранилище вернулось", lambda w: (fresh(w), setattr(w, "store_down", False))),
        ("сервис пользователей не отвечает: последние известные", lambda w: (fresh(w), open(w.path("run/panel-clients.json"), "w").write(json.dumps([cached(v) for v in w.views])),
                                                                     os.utime(w.path("run/panel-clients.json"), (NOW, NOW)), setattr(w, "users_down", True))),
        ("сервис пользователей вернулся, блокировщик не отвечает", lambda w: (fresh(w), setattr(w, "users_down", False), setattr(w, "ag_down", True), w.profile_seen.update(at=0.0, fail_at=0.0))),
        ("блокировщик вернулся, его клиенты потеряны", lambda w: (fresh(w), setattr(w, "ag_down", False), setattr(w, "ag", {}), w.profile_seen.update(at=0.0, fail_at=0.0))),
        ("в туннеле никого, порт не назван", lambda w: (fresh(w), setattr(w, "dump", dump()), setattr(w, "listen_port", "\n"))),
        ("никто не приостановлен, закреплений нет", lambda w: (fresh(w), setattr(w, "dump", full), setattr(w, "listen_port", "51820\n"), setattr(w, "paused", {}), setattr(w, "hand", {}), w.groups[0].update(exit=None, filter="std"))),
        ("ни одного пользователя (сбой чтения)", lambda w: (fresh(w), setattr(w, "saved", w.views), setattr(w, "views", []))),
        ("выход отвечает ошибкой о себе; режим «всё зарубежное»; счёт посещаемого заново",
         lambda w: (no_cache(w), setattr(w, "views", w.saved), EXIT.reply.update(code=500), w.exit_asked.clear(), open(w.path("route.mode"), "w").write("geo\n"))),
        ("от прежней версии остался счёт посещаемого без новых полей", lambda w: (fresh(w), EXIT.reply.update(code=200), open(w.path("run/top-sites.json"), "w").write(json.dumps({"at": int(time.time()), "sites": [{"name": "x.org", "count": 3}], "total": 3})))),
        ("журнал запросов не читается как JSON-объекты", lambda w: (no_cache(w), open(w.path("querylog.json"), "w").write('[1,"IsFiltered":true]\n' + '"QT":"A"\n'))),
    ]
    new_world = World("state")
    new_path = Agent(new_world)
    answers = []
    pages = []
    journals = []
    asked = []
    for name, prepare in steps:
        prepare(new_world)
        new_world.asked.clear()
        new_world.lines.clear()
        hits = len(EXIT.hits)
        same_second()
        mine, page = new_path.ask()
        new_hits = EXIT.hits[hits:]
        answers.append(mine)
        pages.append(page)
        journals.append([line["text"] for line in new_world.journal()])
        asked.append(list(new_world.asked))
        as_recorded(f"state: {name}", state_seen(mine, page, new_world, new_hits), f"{name}: ответ, обращения по порядку, хранилище и файлы, строки журнала — как записано")

    print("\n— state: вид на проводе")
    first_answer = answers[0]
    first_page = pages[0]["clients"]
    ok(all(numbers_only(answer, page) for answer, page in zip(answers, pages)) and len(answers) == len(steps), "во всех ответах время, рукопожатия, счётчики и сроки — числа, не строки и не даты")
    alice = next(c for c in first_page if c["name"] == "alice")
    bob = next(c for c in first_page if c["name"] == "bob")
    ok(alice == {"name": "alice", "suspended": False, "ip": "10.9.9.2", "expires_at": None, "endpoint": "198.51.100.8:1", "handshake": NOW - 10, "rx": 7, "tx": 8, "live": True, "exit": 1, "pin": None,
                    "group": "aaaaaa", "online": True, "down_bps": 0.0, "up_bps": 0.0}
       and bob["online"] is False and bob["expires_at"] == int(datetime(2030, 1, 2, 3, 4, 5, 678901, UTC).timestamp()) and bob["handshake"] == 0 and bob["endpoint"] is None and bob["live"] is True,
       "пользователь: срок из ISO с долями секунды — целые секунды Unix, рукопожатие — секунды, «никогда» — 0", [alice, bob])
    ok([c["name"] for c in first_page] == ["alice", "bob", "carol", "dave"] and first_page[2]["suspended"] is True and first_page[3]["pin"] == 1 and first_page[3]["live"] is False
       and pages[0]["total"] == 4 and pages[0]["offset"] == 0,
       "пользователи (страница clients) по порядку адресов (.12 после .4); пауза и ручной выход видны; о ком туннель молчит — live=false", pages[0])
    up, down_ = first_answer["exits"]
    ok(up == {"index": 1, "name": "exit1-nl", "iface": "awgk1", "state": "up", "handshake": int(datetime(2029, 5, 6, 7, 8, 9, 123456, UTC).timestamp()), "rx": 10, "tx": 20, "users": 2, "online": 1, "probe_ms": 41.5,
              "changed": datetime(2029, 5, 6, 7, 0, 0, 500000, UTC).timestamp(), "weight": 2, "endpoint": "203.0.113.1:443", "iface_rx": 333, "iface_tx": 0,
              "sys": {"load1": 0.5, "cpus": 2, "mem_total": 4000, "mem_used": 1000, "uptime": 77, "disk_pct": 31}}
       and down_["handshake"] == 0 and down_["changed"] is None and down_["weight"] == 1 and down_["endpoint"] is None and "sys" not in down_ and (down_["users"], down_["online"]) == (1, 1),
       "выход: рукопожатие — целые секунды, смена состояния — секунды с долей, счётчики интерфейса — числа, пользователи сосчитаны, сведения о сервере — только у работающего", [up, down_])
    ok(first_answer["watch"] == {"started": datetime(2029, 5, 1, 0, 0, 0, 250000, UTC).timestamp(), "applied": 0, "last_tick": datetime(2029, 5, 6, 7, 8, 10, 750000, UTC).timestamp(), "tick_seconds": 0.37, "ticks": 900,
                                 "tick_failed": 2, "ok": True} and abs(first_answer["time"] - time.time()) < 120,
       "сторож: last_tick — секунды с долей, «ещё не применял» — 0; time — часы самого агента", first_answer["watch"])
    ok(first_answer["entry"] == {"hostname": os.uname().nodename, "load1": 0.42, "cpus": os.cpu_count() or 1, "mem_total": 2000000 * 1024, "mem_used": 800000 * 1024, "uptime": 86400,
                                 "disk_pct": first_answer["entry"]["disk_pct"], "wan_if": "ens3", "wan_rx": 111, "wan_tx": 222, "listen_port": 51820, "client_if_up": True},
       "вход: нагрузка, память, время работы, внешний интерфейс и его счётчики", first_answer["entry"])
    ok(first_answer["routing"] == {"mode": "geo", "udp_via_exit": True, "exit_nets": 5, "exit_domains": 3, "exit_patterns": 1, "direct_domains": 1, "exit_ips": 70, "direct_ips": 8} and first_answer["ru_networks"] == 9000,
       "маршрутизация: только счётчики", first_answer["routing"])
    dns = first_answer["dns"]
    ok(dns["ok"] is True and (dns["queries"], dns["blocked"], dns["safe"], dns["avg_ms"]) == (1200, 300, 4, 12.3) and dns["top_blocked"] == [{"domain": "ads.example", "count": 50}, {"domain": "track.example", "count": 7}]
       and dns["top_clients"] == [{"ip": "10.9.9.2", "count": 700}, {"ip": "10.9.9.3", "count": 500}] and dns["top_sites"] == [{"name": "YouTube", "count": 2}, {"name": "bank.example", "count": 2}, {"name": "site.io", "count": 1}]
       and dns["top_exit"] == [{"name": "YouTube", "count": 1}, {"name": "site.io", "count": 1}] and dns["top_direct"] == [{"name": "bank.example", "count": 2}, {"name": "YouTube", "count": 1}] and dns["top_sites_hours"] == 24,
       "DNS: итоги, заблокированные имена, счёт по адресам и посещаемое по названиям", dns)
    blob = json.dumps([answers, pages], ensure_ascii=False)
    ok(set(first_answer) == {"ok", "time", "entry", "exits", "watch", "ru_networks", "routing", "users", "dns", "ugroups_v", "sites_v", "spaces_v", "store_ok"}
       and set(dns) == {"ok", "queries", "blocked", "safe", "avg_ms", "top_blocked", "top_clients", "top_sites", "top_sites_total", "top_exit", "top_exit_total", "top_direct", "top_direct_total", "top_sites_hours"},
       "в ответе ровно эти поля: ничего о том, кто какие сайты открывает, не добавилось")
    ok(not any(secret in blob for secret in ("PRIVATE-KEY-OF-THE-TUNNEL", "PRESHARED-KEY", key(1), key(9), "secret-of-the-blocker", "token-of-a-service")),
       "ни ключей туннеля, ни общего секрета, ни пароля блокировщика, ни токена сервиса в ответах (и в страницах пользователей) нет")
    ok(journals[0] == [] and journals[3][:2] == ["Пользователь bob удалён не через панель", "Выход exit1-nl недоступен"] and journals[3][2].startswith("Выход exit2-de снова работает (не отвечал ") and len(journals[5]) == 1
       and ("adguard", "clients/add", "grp-aaaaaa") in asked[0] and ("watch", "pin") in asked[0] and ("run", "ipset", "add", "cascade_susp", "10.9.9.4", "-exist") in asked[0]
       and ("users", "list") in asked[0] and ("users", "list") not in asked[1] and ("adguard", "clients/add", "grp-aaaaaa") in asked[10],
       "по дороге: закрепления и фильтр группы приведены в порядок, пауза возвращена ядру, ушедший и выходы — в журнале", [journals, asked[0]])
    off = answers[4]
    ok(off["watch"] == {"ok": False} and off["exits"] == [] and off["routing"]["mode"] == "list" and off["ru_networks"] == 0, "сторож молчит: watch.ok=false, выходы неизвестны (а не «исчезли»)", off["watch"])
    no_store = answers[6]
    ok(no_store["store_ok"] is False and no_store["ugroups_v"] is None and no_store["sites_v"] is None and no_store["spaces_v"] is None and new_world.ipset is not None and no_store["users"]["users"] == 3 and len(pages[6]["clients"]) == 3,
       "хранилище недоступно: состояние всё равно отдано, отпечатков нет, паузы возвращены ядру из файла", [no_store["store_ok"], new_world.ipset])
    ok(answers[9]["dns"]["ok"] is False and answers[9]["dns"]["error"] and answers[9]["dns"]["top_sites"], "блокировщик молчит: dns.ok=false с причиной, посещаемое всё равно показано", answers[9]["dns"])
    relayed = [json.dumps(PanelState.model_validate(json.loads(json.dumps(answer))).wire(), ensure_ascii=False) for answer in answers]
    ok(relayed == [json.dumps(answer, ensure_ascii=False) for answer in answers], "панель принимает каждый из этих ответов и отдаёт его дальше байт в байт",
       [pair for pair in zip(relayed, (json.dumps(answer, ensure_ascii=False) for answer in answers)) if pair[0] != pair[1]][:1])


def t_exit_systems():
    print("\n— сведения о сервере выхода")
    w = World("exit")
    new_memory = {}

    async def host(cmd, stdin=None, timeout=30):
        code, out = w.host(cmd)
        return Result(code, out, "")
    new = ExitSystems(host, EXIT.port, new_memory)
    polls = []
    hits = len(EXIT.hits)
    plan = [(1000, 200, "awgk1"), (1010, 200, "awgk1"), (1030, 500, "awgk1"), (1060, 200, "awgk1"), (1095, 200, "awgk1"), (1200, "junk", "awgk1"), (1300, "list", "awgk1"), (1400, "empty", "awgk1"), (1000, 200, "awgk3"), (2000, 200, "awgk1")]
    for now, code, iface in plan:
        good = json.dumps({"load1": 0.5, "cpus": 2, "mem_total": 4000, "mem_used": 1000, "uptime": 77, "disk_pct": 31, "extra": "no", "bad": None, "flag": True}).encode()
        EXIT.reply.update(code=code if isinstance(code, int) else 200, body={"junk": b"not json", "list": b"[1, 2]", "empty": b'{"load1": "high", "cpus": true}'}.get(code, good))
        before = len(EXIT.hits)
        got = LOOP.run_until_complete(new.of(iface, now))
        polls.append([now, code, iface, None if got is None else got.wire(), len(EXIT.hits) - before])
    ok(len(EXIT.hits) - hits == 7 and all(source == "127.0.0.2" and path == "/v1/system" for source, path in EXIT.hits[hits:]),
       "десять опросов подряд (ответил, в пределах 20 с, ошибка, минута молчания, мусор, не объект, не числа, нет адреса): 7 запросов, все с адреса туннеля", EXIT.hits[hits:])
    as_recorded("exit systems: ten polls", polls, "…сведения и число запросов в каждом опросе — как записано")
    EXIT.reply.update(code=200)


def t_memory():
    print("\n— клиенты сервисов: сертификаты читаются один раз")
    import ssl
    from awgscale.agent.adguard.client import AdGuardClient
    from awgscale.agent.clients.http import Method
    from awgscale.agent.clients.tls import tls_context
    w = World("memory")
    tls_context()
    real = ssl.create_default_context
    loads = []
    ssl.create_default_context = lambda *a, **k: (loads.append(1), real(*a, **k))[1]
    try:
        service = ServiceClient(f"http://127.0.0.1:{EXIT.port}", Path(w.path("token")))
        blocker = AdGuardClient(f"http://127.0.0.1:{EXIT.port}/v1", Path(w.path("adguard-admin.txt")))

        async def host(cmd, stdin=None, timeout=30):
            return Result(0, "7: awgk1    inet 127.0.0.2/32 scope global awgk1\n", "")
        figures = ExitSystems(host, EXIT.port, {})

        async def many():
            answers = []
            for _ in range(25):
                answers.append(await service.ask(Method.GET, "/v1/system"))
                answers.append(await blocker.get("system"))
                answers.append((await figures.of("awgk1", now=time.time() + 1000 * len(answers))).wire())
            return answers
        answers = LOOP.run_until_complete(many())
    finally:
        ssl.create_default_context = real
    ok(len(answers) == 75 and all(answer.get("load1") == 0.5 for answer in answers) and loads == [],
       "75 настоящих запросов тремя клиентами — список удостоверяющих центров не перечитывается ни разу (иначе агент растёт на мегабайт с каждым опросом состояния)", len(loads))


def main():
    t_seen_pure()
    t_tops()
    t_state()
    t_exit_systems()
    t_memory()
    if WRITE:
        os.makedirs(os.path.dirname(GOLDEN_FILE), exist_ok=True)
        with open(GOLDEN_FILE, "w") as fh:
            json.dump(RECORDED, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print(f"recorded {len(RECORDED)} entries in {GOLDEN_FILE}")
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
