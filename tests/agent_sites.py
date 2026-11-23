#!/usr/bin/env python3
"""The sites' commands that live in the package (awgscale.agent.sites): sites-get,
sites-set, sites-refresh, mode-set, check and probe sent through the real
dispatcher give the answers, leave the files, ask the watch service, the
resolver's keeper, the blocker and the host's tools the things in the order and
write the journal lines kept in tests/golden/agent_sites.json. The parts with no
world around them — the entry rules, the lists' parser, the compilers — come out
as kept there too, on thousands of generated inputs (a fixed seed).

    uv run python tests/agent_sites.py
    PG_DSN="host=127.0.0.1 port=35444 dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=... uv run python tests/agent_sites.py     (also the store, on a throwaway Postgres)
    GOLDEN_WRITE=1 uv run python tests/agent_sites.py     writes the golden file again

Nothing live is touched: no name is resolved, nothing is downloaded."""
import asyncio
import hashlib
import json
import os
import random
import socket
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agent-sites-")
GOLDEN_FILE = os.path.join(HERE, "golden", "agent_sites.json")
WRITING = os.environ.get("GOLDEN_WRITE") == "1"
GOLDEN = {} if WRITING else json.load(open(GOLDEN_FILE))

from awgscale.agent import errors  # noqa: E402
from awgscale.agent.commands import Command, RouteMode  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.journal.service import Journal  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent.sites import entries, groups as site_groups, lists, probe, routing  # noqa: E402
from awgscale.agent.sites.models import MAX_ENTRIES, SiteGroup  # noqa: E402
from awgscale.agent.sites.service import SitesService  # noqa: E402
from awgscale.agent.payloads import decode  # noqa: E402
from awgscale.agent.rules.service import RulesService
from awgscale.agent.users.models import StoredGroups  # noqa: E402
from awgscale.agent.rules.store import Part, blocks_of, merged, rules_of, sites_of  # noqa: E402
from awgscale.agent.block.store import StoredBlockGroups as OwnBlockGroups  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
NOW = 1_800_000_000
REAL_GETADDRINFO = socket.getaddrinfo
routing.RETRY_SECONDS = 0           # nobody waits for a resolver that is a stand-in


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + (seen if isinstance(seen, str) else repr(seen))[:900])


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


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


# ── the parts with no world around them ──────────────────────────────────────

LABELS = ["example", "a", "cdn", "x-y", "api", "*", "a*", "*b", "-bad", "bad-", "under_score", "123", "com", "org", "net", "co", "uk", "xn--p1ai", "localhost", ""]


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


def some_name(rnd):
    return ".".join(rnd.choice(LABELS) for _ in range(rnd.randint(1, 5)))


def some_entry(rnd):
    kind = rnd.random()
    if kind < 0.6:
        return some_name(rnd)
    if kind < 0.8:
        return f"{rnd.randint(0, 300)}.{rnd.randint(0, 260)}.{rnd.randint(0, 255)}.{rnd.randint(0, 255)}" + rnd.choice(["", "", "/8", "/16", "/24", "/33", "/7", "/x"])
    return rnd.choice(["https://", "http://", "  ", "*.", ""]) + some_name(rnd) + rnd.choice(["", ".", "/path", ":443", " "])


def good_entry(rnd):
    kind = rnd.random()
    name = ".".join(rnd.choice(["a", "cdn", "api", "example", "x-y", "shop"]) for _ in range(rnd.randint(1, 3))) + "." + rnd.choice(["com", "org", "example", "co.uk"])
    if kind < 0.7:
        return name
    if kind < 0.85:
        return rnd.choice(["*", "api.*", "a*"]) + "." + name
    return f"{rnd.randint(1, 223)}.{rnd.randint(0, 255)}.{rnd.randint(0, 255)}.0" + rnd.choice(["", "/16", "/24"])


def stored_group(rnd, number):
    record = {"id": f"g{number:05d}", "name": f"Группа {number}", "on": rnd.random() < 0.7}
    if rnd.random() < 0.25:
        record.update(url=f"https://lists.example.org/{number}.txt", items=[good_entry(rnd) for _ in range(rnd.randint(0, 6))],
                      updated=rnd.choice([None, NOW - 5]), error=rnd.choice(["", "не скачался: ответ 404"]), skipped=rnd.randint(0, 3))
    else:
        record["items"] = list(dict.fromkeys(good_entry(rnd) for _ in range(rnd.randint(0, 6))))
        if rnd.random() < 0.15:
            record["auto"] = "telegram"
    return record


def new_checked_entries(items):
    try:
        return entries.checked_entries(items, MAX_ENTRIES), None
    except ValueError as e:
        return None, str(e)


def counter():
    state = {"n": 0}

    def new_id(taken):
        state["n"] += 1
        return f"n{state['n']:05d}"
    return new_id


def new_checked_groups(raw, old, kinds=None, with_lists=False):
    try:
        checked = site_groups.checked_groups(raw, [SiteGroup.of(record) for record in old], counter(), kinds, with_lists)
    except ValueError as e:
        return None, str(e)
    return [group.wire() | ({"new": True} if group.id in checked.new_lists else {}) for group in checked.groups], None


def sent_group(rnd, old):
    kind = rnd.random()
    if kind < 0.05:
        return rnd.choice(["строка", 5, None, []])
    record = {"name": rnd.choice(["Мои", "  Сайты   работы ", "", "x" * 61, "Игры\x07", None, 7, "ИИ"]) if rnd.random() < 0.3 else f"Группа {rnd.randint(1, 9)}",
              "on": rnd.choice([True, False, 1, 0, None, "да"])}
    if old and rnd.random() < 0.6:
        record["id"] = rnd.choice(old)["id"]
    elif rnd.random() < 0.2:
        record["id"] = rnd.choice(["zzzzzz", 5, None])
    if rnd.random() < 0.2:
        record["url"] = rnd.choice(["https://lists.example.org/new.txt", "https://lists.example.org/1.txt", "http://plain.example.org/l.txt", "https://10.0.0.1/l.txt", 5, ""])
    if rnd.random() < 0.9:
        record["items"] = [rnd.choice([good_entry(rnd), good_entry(rnd), good_entry(rnd), some_entry(rnd), 5]) for _ in range(rnd.randint(0, 5))] if rnd.random() < 0.95 else "строка"
    if rnd.random() < 0.3:
        record["kind"] = rnd.choice(["block", "allow", "other", None])
    return record


def t_pure():
    print("\n— правила записей, разбор списков, сборка файлов: на случайных данных")
    rnd = random.Random(7)
    outcomes = [new_checked_entries([some_entry(rnd)]) for _ in range(20000)]
    ok(kept("entries_digest", digest(outcomes)), "20000 случайных записей: принята или отклонена, в том же виде и с теми же словами", digest(outcomes))
    outcomes = []
    for _ in range(3000):
        items = [rnd.choice([some_entry(rnd), good_entry(rnd)]) for _ in range(rnd.randint(0, 8))]
        outcomes.append(new_checked_entries(items))
    for odd in ("строка", None, [5], [None], ["a.com"] * 501, [f"d{i}.example.com" for i in range(501)], [f"d{i}.example.com" for i in range(500)]):
        outcomes.append(new_checked_entries(odd))
    ok(kept("entry_lists_digest", digest(outcomes)), "3000 списков записей и негодные списки (не список, не строка, больше предела) — как прежде", digest(outcomes))
    outcomes = []
    for _ in range(5000):
        listed = [good_entry(rnd) for _ in range(rnd.randint(0, 8))]
        host = rnd.choice(["", "www.", "a.b.", "api.x."]) + rnd.choice(listed + ["other.example"]).replace("*", "q")
        outcomes.append([entries.resolver_names(listed), [entries.covers(entry, host) for entry in listed]])
    ok(kept("names_digest", digest(outcomes)), "5000 наборов: имена для блокировщика (шаблон расширен, сети убраны) и точное значение записи — как прежде", digest(outcomes))

    taken = []
    for url in ["https://a.example.org/l.txt", "http://a.example.org/l.txt", "https://1.2.3.4/x", "https://a.example.org:443/x?y=1", "https://a.example.org:8443/x",
                "https://u:p@a.example.org/x", "https://a.example.org/", "https://a.example.org/x#f", "https://A.Example.ORG./x", "  https://a.example.org/x  ",
                "https://a.example.123/x", "https://localhost/x", None, 5, "https://a.example.org/" + "x" * 600, "https://[::1]/x", "https://a.example.org:bad/x",
                "file:///etc/passwd", "https://a.example.org/x y", "https://a.example.org/x?<y>", "HTTPS://a.example.org/x", "https://a.example.org//"]:
        link = lists.link_of(url)
        taken.append([link.url, link.host] if link else None)
    ok(kept("list_urls", taken), "ссылки на списки: принята или отклонена, и в каком виде — как прежде", taken)
    tokens = ["example.com", "*.a.example.org", "0.0.0.0 ads.example", "127.0.0.1 x.example y.example", "||rule.example^", "||bad/x^", "# c", "! c", "1.2.3.4", "10.0.0.0/8",
              "10.1.0.0/16", "a b c", "localhost.localdomain", "com", "x.123", "  UP.Example.COM.  ", "foo.example # tail", "||a.b^$third", "300.1.1.1", "", "a_b.com", "1.2.3.4/33", "8.8.8.8 dns.example"]
    outcomes = []
    for _ in range(5000):
        text = "\n".join(rnd.choice(tokens) for _ in range(rnd.randint(0, 14)))
        parsed = lists.parse_list(text)
        outcomes.append([parsed.entries, parsed.skipped])
    ok(kept("lists_digest", digest(outcomes)), "5000 файлов списков: записи и число пропущенных строк — как прежде", digest(outcomes))

    lossless = 0
    outcomes = []
    for _ in range(3000):
        stored = [stored_group(rnd, number) for number in range(rnd.randint(0, 6))]
        typed = [SiteGroup.of(record) for record in stored]
        lossless += [group.wire() for group in typed] == stored
        flat = site_groups.flat_entries(typed)
        exit_text = "".join(entry + "\n" for entry in flat) + rnd.choice(["", "# комментарий\n\n", "  \n"])
        direct_text = rnd.choice(["", "direct.example\n# x\nother.example\n"])
        outcomes.append([flat, site_groups.automatic_on(typed), site_groups.version_of_sites(typed), routing.ipset_text(exit_text, direct_text)]
                        + [routing.upstreams(flat, mode) for mode in RouteMode])
    ok(lossless == 3000 and kept("compiled_digest", digest(outcomes)),
       "3000 наборов групп: запись в хранилище без потерь, плоский список, переключатель автосетей, отпечаток, файл блокировщика, настройки DNS в обоих режимах — как прежде", [lossless, digest(outcomes)])
    many = [f"name{i}.example.com" for i in range(4000)]
    ok(len(json.dumps(routing.upstreams(many, RouteMode.LIST)).encode()) > routing.UPSTREAM_BYTES and not routing.upstreams_fit(many, RouteMode.LIST) and routing.upstreams_fit(many, RouteMode.FOREIGN)
       and routing.UPSTREAM_BYTES == 60000, "предел блокировщика: 4000 имён по списку не помещаются, в режиме «всё зарубежное» — помещаются")

    outcomes = []
    for _ in range(6000):
        stored = [stored_group(rnd, number) for number in range(rnd.randint(0, 4))]
        raw = [sent_group(rnd, stored) for _ in range(rnd.randint(0, 5))] if rnd.random() < 0.97 else rnd.choice(["строка", None, {}])
        for kinds, with_lists in ((None, True), (None, False), (("block", "allow"), False)):
            without_lists = [record for record in stored if "url" not in record] if kinds else stored
            outcomes.append(new_checked_groups(json.loads(json.dumps(raw)), without_lists, kinds, with_lists))
    ok(kept("sent_groups_digest", digest(outcomes)), "6000 присланных наборов групп × три вида (сайты со списками, без списков, блокировка): принято или отклонено, тем же текстом", digest(outcomes))
    big = [{"name": f"Г{i}", "items": []} for i in range(61)]
    full = [{"name": f"Г{i}", "items": [f"d{i}-{j}.example.com" for j in range(101)]} for i in range(5)]
    five_lists = [{"name": f"С{i}", "url": f"https://lists.example.org/{i}.txt"} for i in range(5)]
    refusals = [new_checked_groups(raw, [], None, True) for raw in (big, full, five_lists)]
    ok(all(refusal[0] is None and refusal[1] for refusal in refusals) and kept("limits", refusals), "пределы: 61 группа, 505 записей, пять списков по ссылке — отказ теми же словами", refusals)

    conf = 'EXITS=(\n  "1:203.0.113.10:10.77.1.2:nl:2"\n  "2:203.0.113.20:10.77.2.2:"\n "7:198.51.100.7:10.77.7.2:de"\n)\n# "9:x:y:z"\n'
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=TMP))
    (run_dir / "exit1.state").write_text("up\n")
    (run_dir / "exit2.state").write_text("down\n")
    conf_file = run_dir / "cascade.conf"
    conf_file.write_text(conf)
    ok(lists.exits_up(conf, run_dir) == ["10.77.1.2"], "через какие выходы качать список: только те, что сейчас работают", lists.exits_up(conf, run_dir))
    ways = [(way.via.value, way.name, way.source) for way in probe.ways_of(conf)]
    ok(ways == [("direct", "Напрямую", None), ("exit", "nl", "10.77.1.2"), ("exit", "203.0.113.20", "10.77.2.2"), ("exit", "de", "10.77.7.2"), ("exit", "z", "y")],
       "пути проверки сайта: напрямую и каждый выход из cascade.conf, по имени или адресу", ways)


# ── one world ────────────────────────────────────────────────────────────────

SEED_GROUPS = [
    {"id": "a00001", "name": "ИИ", "on": True, "items": ["chatgpt.com", "claude.ai", "*.perplexity.ai"]},
    {"id": "a00002", "name": "Telegram", "on": True, "items": ["telegram.org", "149.154.160.0/20"], "auto": "telegram"},
    {"id": "a00003", "name": "Работа", "on": False, "items": ["notion.so", "api.*.cdn.example.net"]},
    {"id": "a00004", "name": "Список друга", "on": True, "url": "https://lists.example.org/friend.txt", "items": [f"site{i}.example" for i in range(30)],
     "updated": NOW - 86400, "error": "", "skipped": 2},
]
FRIEND_LIST = "".join(f"site{i}.example\n" for i in range(28)) + "new1.example\n0.0.0.0 new2.example\n# comment\nbad line here\n"


class World:
    def __init__(self, name):
        self.dir = tempfile.mkdtemp(prefix=name + "-", dir=TMP)
        self.path = {key: os.path.join(self.dir, file) for key, file in (
            ("domains_exit", "domains-exit.txt"), ("domains_direct", "domains-direct.txt"), ("domains_direct_rules", "domains-direct-rules.txt"), ("nets_auto", "nets-auto.txt"), ("nets_auto_off", "nets-auto.off"),
            ("domains_ipset", "domains.ipset"), ("domains_scoped", "domains-scoped.txt"), ("domains_shared", "shared/domains-exit.txt"), ("resolver_yaml", "AdGuardHome.yaml"), ("route_mode", "route.mode"),
            ("cascade_conf", "cascade.conf"), ("lock", ".panel.lock"))}
        self.run_dir = os.path.join(self.dir, "run")
        os.mkdir(self.run_dir)
        for index in (1, 3):
            open(os.path.join(self.run_dir, f"exit{index}.state"), "w").write("up\n")
        open(self.path["cascade_conf"], "w").write('EXITS=(\n  "1:203.0.113.10:10.77.1.2:exit1-nl:2"\n  "3:203.0.113.30:10.77.3.2:exit2-nl:1"\n)\n')
        open(self.path["resolver_yaml"], "w").write("dns:\n  ipset: []\n  ipset_file: /old/place\n  other: 1\n")
        open(self.path["nets_auto"], "w").write("# telegram\n91.108.4.0/22\n149.154.160.0/20\nnot-a-net\n")
        self.groups = json.loads(json.dumps(SEED_GROUPS))
        self.block = [{"id": "b00001", "name": "Свои", "on": True, "kind": "block", "items": ["casino.example", "ads.example"]}]
        flat = site_groups.flat_entries([SiteGroup.of(record) for record in self.groups])
        open(self.path["domains_exit"], "w").write("".join(entry + "\n" for entry in flat))
        self.stored_mode = "list"
        self.rules = None               # the list's own order, once a change of the canvas stored one
        self.events = []
        self.lines = []
        self.fail = set()               # which things refuse now: "health", "stop", "start", "domains", "apply:<mode>", "dns_config", "answers", "store", "mode-store", "blocker"
        self.files = {"https://lists.example.org/friend.txt": (0, "200", FRIEND_LIST, ""), "https://lists.example.org/new.txt": (0, "200", "one.example\ntwo.example\n", "")}
        self.addresses = {"lists.example.org": "93.184.216.34", "example.com": "8.8.8.8", "inner.example.com": "10.0.0.5"}
        self.verdict = {"reason": "NotFilteredNotFound"}
        self.filters = [{"id": 1, "url": "https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt", "name": "AdGuard DNS filter"}, {"id": 7, "url": "https://x.example/l.txt", "name": "Мой список"}]
        self.curl = (0, "200 0.25")
        self.ids = 0

    def new_id(self, taken):
        self.ids += 1
        return f"n{self.ids:05d}"

    def resolve(self, host, port, *a, **k):
        address = self.addresses.get(host)
        self.events.append(("resolve", host))
        if address is None:
            raise socket.gaierror(-2, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    def mode_now(self):
        return read(self.path["route_mode"]).strip() if read(self.path["route_mode"]) else "list"

    def tool(self, cmd):
        """ip, dig and curl, as far as the sites use them. → (code, stdout, stderr)"""
        cmd = list(cmd)
        if cmd[0] == "ip":
            return 0, "7: awg0    inet 172.16.17.1/24 scope global awg0\n", ""
        if cmd[0] == "dig":
            self.events.append(("dig", cmd[-2], cmd[-3]))
            return 0, ("" if "answers" in self.fail and cmd[-2] == "ya.ru" else "77.88.55.242\n"), ""
        if cmd[0] == "curl" and "-o" in cmd and cmd[cmd.index("-o") + 1] != "/dev/null":
            url = cmd[-1]
            self.events.append(("download", url, cmd[cmd.index("--interface") + 1], cmd[cmd.index("--resolve") + 1]))
            code, status, body, err = self.files.get(url, (0, "404", "", ""))
            if code == 0 and status == "200":
                open(cmd[cmd.index("-o") + 1], "w").write(body)
            return code, status, err
        if cmd[0] == "curl":
            source = cmd[cmd.index("--interface") + 1] if "--interface" in cmd else None
            self.events.append(("open", cmd[-1], source, cmd[cmd.index("--resolve") + 1]))
            return self.curl[0], self.curl[1], ""
        self.events.append(("unknown", cmd))
        return 1, "", ""

    def watch(self, command):
        self.events.append(("watch", command, self.mode_now(), "ipset_file: " + self.path["domains_ipset"] in read(self.path["resolver_yaml"])))
        key = "apply:" + self.mode_now() if command == "apply" else command
        return (1, "сторож отказал: " + command) if key in self.fail else (0, "")

    def keeper(self, command):
        self.events.append(("keeper", command))
        return (1, "unit failed") if command in self.fail else (0, "")

    def blocker_post(self, path, payload):
        self.events.append(("blocker", path, json.dumps(payload, sort_keys=True, ensure_ascii=False)))
        if path in self.fail or "blocker" in self.fail:
            raise OSError("блокировщик отказал")

    def blocker_get(self, path):
        self.events.append(("blocker-get", path))
        if "blocker" in self.fail:
            raise OSError("блокировщик не отвечает")
        return dict(self.verdict) if path.startswith("filtering/check_host") else {"filters": self.filters}

    def seen(self):
        """What a command leaves: the files, the store, and what was asked — in order,
        but for the names resolved ahead (they are asked side by side, in no order)."""
        asked = []
        ahead = []
        for event in self.events:
            if event[0] == "dig" and event[1] != "ya.ru" or event[0] == "open":
                ahead.append(event)
                continue
            if ahead:
                asked.append(("side by side", sorted(ahead, key=repr)))
                ahead = []
            asked.append(event)
        if ahead:
            asked.append(("side by side", sorted(ahead, key=repr)))
        files = {key: (read(path) or "").replace(self.dir, "<dir>") if read(path) is not None else None for key, path in self.path.items() if key not in ("lock", "cascade_conf")}
        if not files.get("domains_direct_rules"):
            files.pop("domains_direct_rules", None)      # written since the groups sent directly; empty, it changes nothing the steps kept
        if not files.get("domains_scoped"):
            files.pop("domains_scoped", None)            # written since the rules for some groups; empty, it changes nothing the steps kept
        leftovers = sorted(os.listdir(self.run_dir))
        return {"files": files, "groups": self.groups, "mode": self.stored_mode, "asked": asked, "journal": [{k: v for k, v in json.loads(line).items() if k != "t"} for line in self.lines],
                "run_dir": leftovers}


def first_difference(old, new, where=""):
    """Where two observations (the kept one and this one) part, and how: short enough to read."""
    if type(old) is not type(new):
        return f"{where}: {old!r:.300} ≠ {new!r:.300}"
    if isinstance(old, dict):
        for key in list(old) + [k for k in new if k not in old]:
            if old.get(key) != new.get(key):
                return first_difference(old.get(key), new.get(key), f"{where}.{key}")
    if isinstance(old, (list, tuple)):
        for index, (one, other) in enumerate(zip(old, new)):
            if one != other:
                return first_difference(one, other, f"{where}[{index}]")
        if len(old) != len(new):
            return f"{where}: {len(old)} ≠ {len(new)} элементов; лишнее: {(old[len(new):] or new[len(old):])!r:.400}"
    return f"{where}: {old!r:.300} ≠ {new!r:.300}"


def read(path):
    try:
        return open(path).read()
    except OSError:
        return None


class NewPath:
    def __init__(self, world):
        w = world

        async def tool(cmd, stdin=None, timeout=30):
            return Result(*w.tool(cmd))

        def down_if(key):
            if key in w.fail:
                raise errors.StoreDown("нет связи")

        class Sites:
            async def read(self):
                down_if("store")
                return [SiteGroup.of(record) for record in json.loads(json.dumps(w.groups))]

            async def save(self, groups_):
                down_if("store")
                w.groups = [group.wire() for group in groups_]

        class Mode:
            async def read(self):
                down_if("store")
                return RouteMode(w.stored_mode)

            async def save(self, mode):
                down_if("mode-store")
                w.stored_mode = mode.value

        class Block:
            async def read(self):
                down_if("store")
                return json.loads(json.dumps(w.block))

        class Watch:
            async def health(self, timeout):
                return Outcome(*self.told("health"))

            async def apply(self, timeout):
                return Outcome(*self.told("apply"))

            async def domains(self, timeout):
                return Outcome(*self.told("domains"))

            def told(self, command):
                code, said = w.watch(command)
                return code == 0, said

        class Keeper:
            async def stop(self, timeout):
                code, said = w.keeper("stop")
                return Outcome(code == 0, said)

            async def start(self, timeout):
                code, said = w.keeper("start")
                return Outcome(code == 0, said)

        class Blocker:
            async def post(self, path, payload, timeout=10):
                w.blocker_post(path, payload)

            async def get(self, path, timeout=5):
                return w.blocker_get(path)

        class Writer:
            async def write(self, entry):
                w.lines.append(entry.line())

        class RulesList:
            """The whole list over the world: the groups of sites, then the block groups — or, once a change of the
            canvas stored an order of its own, that order (the pages' saves keep it, as the store does)."""

            async def read(self):
                if w.rules is None:
                    return rules_of(json.loads(json.dumps(w.groups)), json.loads(json.dumps(w.block)))
                return merged(merged(w.rules, Part.SITES, json.loads(json.dumps(w.groups))), Part.BLOCK, json.loads(json.dumps(w.block)))

            async def preview(self, part, records):
                return merged(await self.read(), part, list(records))

            async def save_all(self, rules):
                w.rules = json.loads(json.dumps(list(rules)))
                w.groups = sites_of(w.rules)
                w.block = blocks_of(w.rules)

        class NoBlocking:
            async def follow(self):
                pass

        files = routing.RoutingFiles(**{key: Path(w.path[key]) for key in ("domains_exit", "domains_direct", "domains_direct_rules", "nets_auto", "nets_auto_off", "domains_ipset", "domains_scoped", "domains_shared", "resolver_yaml", "route_mode")})
        blocker = Blocker()
        tuning = routing.ResolverTuning(blocker, tool, "awg0", files.route_mode)
        service = SitesService(
            store=Sites(), modes=Mode(), routing=routing.Routing(files, Watch(), Keeper(), tuning),
            lists=lists.ListReader(tool, Path(w.path["cascade_conf"]), Path(w.run_dir)), blocker=blocker, block_groups=Block(), prober=probe.CurlProber(tool),
            cascade_conf=Path(w.path["cascade_conf"]), lock_file=Path(w.path["lock"]), make_id=w.new_id, clock=lambda: datetime.fromtimestamp(NOW, UTC),
            rules=RulesList())
        journal = Journal(None, Writer())

        async def make():
            dispatcher = Dispatcher()
            dispatcher.register(Command.SITES_GET, Handled(service.show))
            dispatcher.register(Command.SITES_SET, Handled(service.save, journal, before=service.stored_records))
            dispatcher.register(Command.SITES_REFRESH, Handled(service.refresh, journal, before=service.stored_records))
            dispatcher.register(Command.MODE_SET, Handled(service.set_mode, journal))
            dispatcher.register(Command.CHECK, Handled(service.check))
            dispatcher.register(Command.PROBE, Handled(service.probe))
            class People:
                async def read(self):
                    return StoredGroups.of([{"id": "g00001", "name": "Дети", "members": ["kid"]}, {"id": "g00002", "name": "Дом", "members": []}])

            class Features:
                async def features(self, timeout):
                    return frozenset({"scoped"})

            rules = RulesService(store=RulesList(), routing=service, blocking=NoBlocking(), lock_file=Path(w.dir) / ".rules.lock", groups=People(), features=Features())
            dispatcher.register(Command.RULES_GET, Handled(rules.show))
            dispatcher.register(Command.RULE_ROUTE, Handled(rules.route, journal))
            return dispatcher
        self.dispatcher = LOOP.run_until_complete(make())

    def ask(self, arguments):
        return LOOP.run_until_complete(self.dispatcher.dispatch(arguments, Caller.PANEL))


def b64(obj):
    import base64
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


def sites_set(change):
    """sites-set as the page sends it: the stored groups with `change` made to them, based on what is stored now."""
    def arguments(w):
        sent = json.loads(json.dumps(w.groups))
        change(sent)
        return ["sites-set", b64({"groups": sent, "base": version_of(w.groups)})]
    return arguments


def version_of(records):
    return site_groups.version_of_sites([SiteGroup.of(record) for record in records])


def unduplicated(w):
    """The step before put a site into two groups, which is allowed now (the first in the order decides): it is taken out
    again by hand, so the steps after see the world they always saw."""
    if "telegram.org" in w.groups[1]["items"]:
        w.groups[1]["items"].remove("telegram.org")


def fail(*keys):
    return lambda w: w.fail.update(keys)


def mend(*keys):
    return lambda w: w.fail.difference_update(keys)


def both(*changes):
    return lambda w: [change(w) for change in changes]


def set_(name, value):
    return lambda w: setattr(w, name, value)


def t_commands():
    print("\n— команды через диспетчер: как прежде")
    nothing = lambda w: None
    shrunk = "".join(f"site{i}.example\n" for i in range(5))
    steps = [
        ("sites-get", nothing, ["sites-get"]),
        ("sites-set: переименование — маршруты не трогаются", nothing, sites_set(lambda g: g[0].update(name="Нейросети"))),
        ("sites-set: новая запись — применено", nothing, sites_set(lambda g: g[0]["items"].append("mistral.ai"))),
        ("sites-set: включена группа", nothing, sites_set(lambda g: g[2].update(on=True))),
        ("sites-set: выключена группа с автосетями", nothing, sites_set(lambda g: g[1].update(on=False))),
        ("sites-set: и включена обратно", nothing, sites_set(lambda g: g[1].update(on=True))),
        ("sites-set: новая группа, старая удалена", nothing, sites_set(lambda g: (g.pop(2), g.insert(0, {"name": " Новая  группа ", "on": True, "items": ["https://Fresh.Example/path", "*.fresh.example", "10.20.0.0/16"]})))),
        ("sites-set: устаревшая основа", nothing, lambda w: ["sites-set", b64({"groups": [], "base": "000000000000"})]),
        ("sites-set: не JSON", nothing, lambda w: ["sites-set", "bm90IGpzb24="]),
        ("sites-set: группы не список", nothing, lambda w: ["sites-set", b64({"groups": "строка", "base": version_of(w.groups)})]),
        ("sites-set: негодная запись", nothing, sites_set(lambda g: g[1]["items"].append("не домен"))),
        ("sites-set: запись в двух группах", nothing, sites_set(lambda g: g[1]["items"].append("telegram.org"))),
        ("sites-set: удалена группа с автосетями", unduplicated, sites_set(lambda g: g.pop(2))),
        ("sites-set: новый список по ссылке", nothing, sites_set(lambda g: g.append({"name": "Новый список", "on": True, "url": "https://lists.example.org/new.txt"}))),
        ("sites-set: тот же список второй раз", nothing, sites_set(lambda g: g.append({"name": "Дубль", "on": True, "url": "https://lists.example.org/new.txt"}))),
        ("sites-set: список не скачался (404)", nothing, sites_set(lambda g: g.append({"name": "Нет такого", "on": True, "url": "https://lists.example.org/none.txt"}))),
        ("sites-set: список — не список адресов", lambda w: w.files.update({"https://lists.example.org/junk.txt": (0, "200", "<html>\nnot a list at all\n" * 30, "")}),
         sites_set(lambda g: g.append({"name": "Мусор", "on": True, "url": "https://lists.example.org/junk.txt"}))),
        ("sites-set: в списке больше 3000 адресов", lambda w: w.files.update({"https://lists.example.org/huge.txt": (0, "200", "".join(f"h{i}.example\n" for i in range(3001)), "")}),
         sites_set(lambda g: g.append({"name": "Огромный", "on": True, "url": "https://lists.example.org/huge.txt"}))),
        ("sites-set: curl не смог", lambda w: w.files.update({"https://lists.example.org/err.txt": (35, "000", "", "curl: (35) TLS connect error\n")}),
         sites_set(lambda g: g.append({"name": "Сбой", "on": True, "url": "https://lists.example.org/err.txt"}))),
        ("sites-set: имя из ссылки ведёт на внутренний адрес", lambda w: w.addresses.update({"inner.example.org": "192.168.1.1"}),
         sites_set(lambda g: g.append({"name": "Внутренний", "on": True, "url": "https://inner.example.org/l.txt"}))),
        ("sites-set: нет работающего выхода", lambda w: [os.remove(os.path.join(w.run_dir, name)) for name in os.listdir(w.run_dir)],
         sites_set(lambda g: g.append({"name": "Без выхода", "on": True, "url": "https://lists.example.org/other.txt"}))),
        ("sites-set: ссылка не https", lambda w: [open(os.path.join(w.run_dir, f"exit{i}.state"), "w").write("up\n") for i in (1, 3)],
         sites_set(lambda g: g.append({"name": "Плохая ссылка", "on": True, "url": "http://lists.example.org/l.txt"}))),
        ("sites-set: сторож не отвечает — ничего не сохранено, прежний набор возвращён", fail("health"), sites_set(lambda g: g[0]["items"].append("blocked-by-health.example"))),
        ("sites-set: блокировщик не остановился", both(mend("health"), fail("stop")), sites_set(lambda g: g[0]["items"].append("stop.example"))),
        ("sites-set: блокировщик не запустился", both(mend("stop"), fail("start")), sites_set(lambda g: g[0]["items"].append("start.example"))),
        ("sites-set: сторож не сбросил наборы", both(mend("start"), fail("domains")), sites_set(lambda g: g[0]["items"].append("domains.example"))),
        ("sites-set: DNS не настроился", both(mend("domains"), fail("dns_config")), sites_set(lambda g: g[0]["items"].append("dns.example"))),
        ("sites-set: DNS не отвечает после применения", both(mend("dns_config"), fail("answers")), sites_set(lambda g: g[0]["items"].append("silent.example"))),
        ("sites-set: хранилище недоступно", both(mend("answers"), fail("store")), lambda w: ["sites-set", b64({"groups": [], "base": "x"})]),
        ("sites-get: хранилище недоступно", nothing, ["sites-get"]),
        ("sites-refresh: один список — содержимое изменилось", mend("store"), lambda w: ["sites-refresh", next(g["id"] for g in w.groups if g.get("url", "").endswith("friend.txt"))]),
        ("sites-refresh: тот же список ещё раз — ничего не изменилось", nothing, lambda w: ["sites-refresh", next(g["id"] for g in w.groups if g.get("url", "").endswith("friend.txt"))]),
        ("sites-refresh: такого списка нет", nothing, ["sites-refresh", "zzzzzz"]),
        ("sites-refresh: обычная группа — не список", nothing, ["sites-refresh", "a00001"]),
        ("sites-refresh ночью: список резко уменьшился — не верим", lambda w: w.files.update({"https://lists.example.org/friend.txt": (0, "200", shrunk, "")}), ["sites-refresh"]),
        ("sites-refresh вручную: уменьшившийся список принят", nothing, lambda w: ["sites-refresh", next(g["id"] for g in w.groups if g.get("url", "").endswith("friend.txt"))]),
        ("sites-refresh ночью: один не скачался, другой обновился", lambda w: w.files.update({"https://lists.example.org/friend.txt": (0, "404", "", ""), "https://lists.example.org/new.txt": (0, "200", "one.example\nthree.example\n", "")}), ["sites-refresh"]),
        ("sites-refresh ночью: применить не удалось — у списков записана причина", both(lambda w: w.files.update({"https://lists.example.org/new.txt": (0, "200", "one.example\nfour.example\n", "")}), fail("health")), ["sites-refresh"]),
        ("sites-refresh: хранилище недоступно", both(mend("health"), fail("store")), ["sites-refresh"]),
        ("mode-set: тот же режим", mend("store"), ["mode-set", "list"]),
        ("mode-set: всё зарубежное", nothing, ["mode-set", "geo"]),
        ("sites-get в режиме «всё зарубежное»", nothing, ["sites-get"]),
        ("check в режиме «всё зарубежное»", nothing, ["check", "anything.example.org"]),
        ("mode-set: обратно на список, сторож не применил — возвращён прежний", fail("apply:list"), ["mode-set", "list"]),
        ("mode-set: и прежний не вернулся", fail("apply:geo"), ["mode-set", "list"]),
        ("mode-set: DNS не настроился", both(mend("apply:list", "apply:geo"), lambda w: open(w.path["route_mode"], "w").write("geo\n"), fail("dns_config")), ["mode-set", "list"]),
        ("mode-set: DNS молчит после переключения", both(mend("dns_config"), fail("answers")), ["mode-set", "list"]),
        ("mode-set: хранилище не приняло режим", both(mend("answers"), fail("mode-store")), ["mode-set", "list"]),
        ("mode-set: на список — применено, имена узнаны заранее", mend("mode-store"), ["mode-set", "list"]),
        ("mode-set: файл и хранилище расходятся", set_("stored_mode", "geo"), ["mode-set", "list"]),
        ("mode-set: хранилище недоступно", fail("store"), ["mode-set", "geo"]),
        ("check: имя из списка", mend("store"), ["check", "www.chatgpt.com"]),
        ("check: шаблон — DNS точно, VPN расширенно", nothing, ["check", "x.fresh.example"]),
        ("check: под расширенный шаблон, но не под точный", nothing, ["check", "fresh.example"]),
        ("check: напрямую", nothing, ["check", "yandex.ru"]),
        ("check: заблокировано своим правилом", set_("verdict", {"reason": "FilteredBlackList", "rules": [{"text": "||casino.example^", "filter_list_id": 0}]}), ["check", "casino.example"]),
        ("check: заблокировано списком", set_("verdict", {"reason": "FilteredBlackList", "rules": [{"text": "||ads.tracker.example^", "filter_list_id": 1}]}), ["check", "ads.tracker.example"]),
        ("check: заблокировано подключённым списком", set_("verdict", {"reason": "FilteredBlackList", "rule": "||x.example^", "rules": [{"filter_list_id": 7}]}), ["check", "x.example"]),
        ("check: опасный сайт", set_("verdict", {"reason": "FilteredSafeBrowsing", "rules": []}), ["check", "malware.example"]),
        ("check: блокировщик не отвечает", fail("blocker"), ["check", "www.chatgpt.com"]),
        ("check: хранилище недоступно", both(mend("blocker"), fail("store")), ["check", "www.chatgpt.com"]),
        ("probe: напрямую и через выходы", mend("store"), ["probe", "example.com", "/status?x=1"]),
        ("probe: без пути", nothing, ["probe", "example.com"]),
        ("probe: сайт отказывает", set_("curl", (0, "403 0.1")), ["probe", "example.com"]),
        ("probe: сервер ошибается", set_("curl", (0, "502 0.4")), ["probe", "example.com"]),
        ("probe: не отвечает", set_("curl", (28, "000 9.0")), ["probe", "example.com"]),
        ("probe: ошибка сертификата", set_("curl", (60, "000 0.3")), ["probe", "example.com"]),
        ("probe: внутренний адрес", nothing, ["probe", "inner.example.com"]),
        ("probe: имени нет", nothing, ["probe", "nxdomain.example.com"]),
    ]
    world = World("new")
    path = NewPath(world)
    socket.getaddrinfo = world.resolve
    try:
        for number, (what, prepare, arguments) in enumerate(steps):
            prepare(world)
            world.events.clear()
            world.lines.clear()
            reply = path.ask(arguments(world) if callable(arguments) else arguments)
            seen = plain({"reply": reply, "world": world.seen()})
            key = f"step {number:02d}: {what}"
            same = kept(key, seen)
            ok(same, f"{what}: тот же ответ, те же файлы и хранилище, те же обращения в том же порядке, та же запись в журнале"
               + ("" if reply.get("ok") else f" («{reply.get('error', '')[:70]}»)"), None if same else first_difference(GOLDEN.get(key), seen))
    finally:
        socket.getaddrinfo = REAL_GETADDRINFO
    ok(world.seen()["run_dir"] == ["exit1.state", "exit3.state"], "скачанные файлы списков за собой убраны", world.seen()["run_dir"])


def t_socket_names():
    print("\n— что сокет не пропускает")
    world = World("names")
    path = NewPath(world)
    for arguments, why in ((["mode-set", "all"], "неизвестный режим"), (["sites-refresh", "НЕ-ID"], "негодный id списка"), (["check", "bad host"], "пробел в имени"),
                           (["probe", "example.com", "no-slash"], "путь не с косой черты"), (["sites-set", "не base64!"], "не base64"), (["sites-get", "лишнее"], "лишний аргумент")):
        reply = path.ask(arguments)
        ok(reply == {"ok": False, "error": "bad request"} and world.events == [], f"{why}: «bad request», ничего не тронуто", reply)


def t_store():
    """With a real, throwaway Postgres (PG_DSN, PG_PASSWORD): the sites, the mode and the block groups as the package keeps them."""
    print("\n— хранилище: сайты и режим в настоящей базе")
    import psycopg
    from awgscale.agent.sites.store import StoredBlockGroups, StoredMode, StoredSites
    from awgscale.agent.store.documents import Kind
    from awgscale.agent.store.store import Store
    dsn = os.environ["PG_DSN"]
    password_file = Path(TMP) / "password"
    password_file.write_text(os.environ["PG_PASSWORD"] + "\n")
    store = Store(dsn, password_file)
    run = LOOP.run_until_complete

    def sql(query, *arguments):
        with psycopg.connect(dsn, password=os.environ["PG_PASSWORD"], autocommit=True) as connection:
            connection.execute(query, arguments)

    def down(work):
        try:
            run(work)
        except errors.StoreDown:
            return True
        return False

    sql("DROP SCHEMA public CASCADE")
    sql("CREATE SCHEMA public")
    ok(down(StoredSites(store).read()) and down(StoredMode(store).read()) and down(StoredMode(store).save(RouteMode.FOREIGN)) and down(StoredBlockGroups(store).read()),
       "база без настроек: сайты, режим и группы блокировки не читаются и не пишутся — «недоступно», а не «пусто»")

    async def make():
        async with store.transaction() as tx:
            await tx.create_schema()
    run(make())
    sql("INSERT INTO meta (key, value) VALUES ('ready', '1')")
    typed = [SiteGroup.of(record) for record in SEED_GROUPS]
    run(StoredSites(store).save(typed))
    read_back = [group.wire() for group in run(StoredSites(store).read())]
    ok(read_back == SEED_GROUPS, "группы, записанные пакетом, читаются такими же", read_back)
    changed = json.loads(json.dumps(SEED_GROUPS))
    changed[3].update(items=["only.example"], updated=None, error="не скачался: ответ 404", skipped=0)
    changed.insert(0, {"id": "a00009", "name": "Новая", "on": False, "items": []})
    run(StoredSites(store).save([SiteGroup.of(record) for record in changed]))
    ok([group.wire() for group in run(StoredSites(store).read())] == changed, "изменённые группы читаются такими же (и время, и «никогда»)")
    ok(run(StoredMode(store).read()) is RouteMode.LIST, "режим не выбирали — «по списку»")
    run(StoredMode(store).save(RouteMode.FOREIGN))
    ok(run(StoredMode(store).read()) is RouteMode.FOREIGN, "режим «всё зарубежное» записан и прочитан")
    run(StoredMode(store).save(RouteMode.LIST))
    ok(run(StoredMode(store).read()) is RouteMode.LIST, "режим «по списку» записан и прочитан")
    sql("UPDATE meta SET value = 'мусор' WHERE key = 'route_mode'")
    ok(run(StoredMode(store).read()) is RouteMode.LIST, "непонятное значение режима — «по списку»")
    block = [{"id": "b00001", "name": "Свои", "on": True, "kind": "block", "items": ["casino.example"]}]

    run(OwnBlockGroups(store).save(block))        # the block groups are a part of the rules now (rules/store.py)
    ok(list(run(StoredBlockGroups(store).read())) == block, "группы блокировки для check читаются такими, как они записаны")


def t_public_host():
    print("\n— адрес из ссылки на список: обычный ли он (одна проверка с блокировкой, IPv4 и IPv6), и куда качать")
    real = socket.getaddrinfo
    socket.getaddrinfo = made_up_getaddrinfo
    try:
        for host, public, ipv4 in (("v4.example", True, ["93.184.215.14"]), ("v6.example", True, []), ("both.example", True, ["8.8.8.8", "93.184.215.14"]),
                                   ("inner6.example", False, []), ("inner4.example", False, []), ("loop.example", False, []), ("nxdomain.example", False, [])):
            ok(lists.is_public(host) is public and lists.public_addresses(host) == ipv4, f"{host}: {'обычный' if public else 'не обычный'} адрес, для скачивания — {ipv4 or 'ничего'}",
               (lists.is_public(host), lists.public_addresses(host)))
        ok(lists.global_addresses("both.example") == ["2606:2800:21f:cb07:6820:80da:af6b:8b2c", "8.8.8.8", "93.184.215.14"], "все адреса имени, обоих семейств, по порядку",
           lists.global_addresses("both.example"))
    finally:
        socket.getaddrinfo = real


def t_rules():
    print("\n— правила одним списком: настоящие сайты, их маршрутизация и проверки")
    world = World("rules")
    path = NewPath(world)
    exit_file = Path(world.path["domains_exit"])
    direct_file = Path(world.path["domains_direct_rules"])
    route = lambda *changes, order=None: path.ask(["rule-route", b64({"changes": [{"rule": r, "route": w} for r, w in changes], **({"order": order} if order else {})})])
    ids = lambda: [rule["id"] for rule in path.ask(["rules-get"])["rules"]]
    path.ask(sites_set(lambda groups: None)(world))       # the seed as a save of the page stores it (*.perplexity.ai is kept as perplexity.ai)
    before = json.loads(json.dumps(world.groups))
    reply = route(("a00003", "exit"))
    work = next(group for group in world.groups if group["name"] == "Работа")
    ok(reply.get("ok") and work["on"] is True and "notion.so" in exit_file.read_text() and [g for g in world.groups if g["name"] != "Работа"] == [g for g in before if g["name"] != "Работа"],
       "«Работа» за границу: включена, её имена в маршрутизации, остальные группы (Telegram, список по ссылке) — как были", reply)
    ok(any("Правила: «Работа» — за границу" in line for line in world.lines), "в журнале — какое правило куда", world.lines)
    reply = route(("a00001", "block"))
    ok(reply.get("ok") and [g["id"] for g in world.block] == ["a00001", "b00001"] and not any(g["name"] == "ИИ" for g in world.groups) and "chatgpt.com" not in exit_file.read_text(),
       "«ИИ» в блок: то же правило с тем же id, на своём месте в списке (выше «Свои»), из маршрутизации ушли", [reply, world.block])
    reply = route(("a00001", "exit"), ("a00002", "off"))
    telegram = next(group for group in world.groups if group["name"] == "Telegram")
    ok(reply.get("ok") and "chatgpt.com" in exit_file.read_text() and telegram["on"] is False and telegram.get("auto") == "telegram" and Path(world.path["nets_auto_off"]).exists(),
       "«ИИ» обратно за границу и Telegram выключен одной правкой; автосети Telegram выключены, группа цела", reply)
    route(("a00002", "exit"))

    print("\n— напрямую")
    reply = route(("a00003", "direct"))
    ipset = Path(world.path["domains_ipset"]).read_text()
    ok(reply.get("ok") and "notion.so" in direct_file.read_text() and "notion.so" not in exit_file.read_text()
       and [line for line in ipset.splitlines() if line.endswith("/force_direct")] == ["notion.so,cdn.example.net/force_direct"],
       "«Работа» напрямую: её имена в наборе force_direct (шаблон расширен), из маршрутизации за границу ушли", [reply, ipset[-120:]])
    ok(path.ask(["check", "www.notion.so"]).get("vpn") == {"via": "direct", "group": "Работа", "rule": "notion.so"}, "проверка сайта называет группу «напрямую»")
    reply = path.ask(sites_set(lambda groups: next(g for g in groups if g["name"] == "Работа").pop("direct", None))(world))
    ok(reply.get("ok") and next(g for g in world.groups if g["name"] == "Работа").get("direct") is True, "страница, которая не знает о «напрямую», сохраняет — пометка остаётся", reply)
    reply = path.ask(sites_set(lambda groups: next(g for g in groups if g["name"] == "Работа")["items"].append("10.30.0.0/16"))(world))
    ok(not reply.get("ok") and "без сетей" in reply.get("error", ""), "сеть в группу «напрямую» не добавить", reply)
    world.events.clear()
    reply = path.ask(["mode-set", "geo"])
    posted = [event for event in world.events if event[0] == "blocker" and event[1] == "dns_config"]
    upstream = json.loads(posted[-1][2])["upstream_dns"] if posted else []
    ok(reply.get("ok") and any(line.startswith("[/") and "notion.so" in line and "8.8.8.8" in line for line in upstream), "во «всём зарубежном» имена «напрямую» спрашиваются напрямую", upstream)
    path.ask(["mode-set", "list"])

    print("\n— порядок: срабатывает верхнее правило")
    path.ask(sites_set(lambda groups: groups.append({"name": "Поддомен", "on": True, "items": ["eu.claude.ai"]}))(world))
    sub = next(group for group in world.groups if group["name"] == "Поддомен")
    route((sub["id"], "direct"))
    shown = path.ask(["rules-get"])
    ok("eu.claude.ai" not in direct_file.read_text() and shown["covered"].get(sub["id"]) == {"eu.claude.ai": "ИИ"},
       "eu.claude.ai «напрямую» стоит ниже «ИИ» (claude.ai за границу): перекрыто, сказано кем", shown.get("covered"))
    world.events.clear()
    reply = route(order=[sub["id"], *[i for i in ids() if i != sub["id"]]])
    posted = [event for event in world.events if event[0] == "blocker" and event[1] == "dns_config"]
    upstream = json.loads(posted[-1][2])["upstream_dns"] if posted else []
    ok(reply.get("ok") and reply.get("ordered") and "eu.claude.ai" in direct_file.read_text() and "claude.ai" in exit_file.read_text()
       and any("eu.claude.ai" in line and "8.8.8.8" in line for line in upstream) and path.ask(["rules-get"])["covered"].get(sub["id"]) is None,
       "поставлено наверх: поддомен напрямую (и в наборе, и в DNS), домен — за границу", [reply, upstream[-2:]])
    checked = path.ask(["check", "eu.claude.ai"])
    ok(checked.get("vpn", {}).get("group") == "Поддомен" and path.ask(["check", "claude.ai"]).get("vpn", {}).get("via") == "exit", "проверка сайта — по верхнему правилу", checked)
    reply = path.ask(sites_set(lambda groups: groups.append({"name": "Дубль", "on": True, "items": ["chatgpt.com"]}))(world))
    ok(reply.get("ok") and reply.get("covered", {}).get(next(g["id"] for g in world.groups if g["name"] == "Дубль")) == {"chatgpt.com": "ИИ"},
       "один сайт в двух группах можно: нижняя запись помечена перекрытой правилом выше", reply.get("covered"))
    flipped = list(reversed(ids()))
    reply = route(order=flipped)
    ok(reply.get("ok") and ids() == flipped and path.ask(["rules-get"])["covered"].get(next(g["id"] for g in world.groups if g["name"] == "ИИ"), {}).get("chatgpt.com") == "Дубль",
       "порядок перевёрнут: теперь «Дубль» выше «ИИ» и перекрывает его chatgpt.com", reply)

    print("\n— «за границу» и «напрямую» для отдельных групп: имена, наборы, перезапуск резолвера")
    scoped_file = Path(world.path["domains_scoped"])
    ipset_file = Path(world.path["domains_ipset"])
    shared_file = Path(world.path["domains_shared"])
    path.ask(sites_set(lambda groups: groups.append({"name": "Детям", "on": True, "items": ["kids.example", "m.youtube-like.example"]}))(world))
    kids = next(group for group in world.groups if group["name"] == "Детям")
    path.ask(sites_set(lambda groups: groups.append({"name": "Всем ютуб", "on": True, "items": ["youtube-like.example"]}))(world))
    route((kids["id"], "off"))
    order = [kids["id"], *[i for i in ids() if i != kids["id"]]]
    route(order=order)
    exit_before = exit_file.read_text()
    world.events.clear()
    reply = path.ask(["rule-route", b64({"changes": [{"rule": kids["id"], "route": "direct", "groups": ["g00001"]}]})])
    pairs = scoped_file.read_text().splitlines() if scoped_file.exists() else []
    ipset = ipset_file.read_text()
    ok(reply.get("ok") and pairs == [f"rs_{kids['id']} kids.example", f"rs_{kids['id']} m.youtube-like.example"] and exit_file.read_text() == exit_before
       and not any(g["id"] == kids["id"] for g in world.groups) and "kids.example" not in shared_file.read_text() and "kids.example" not in direct_file.read_text(),
       "«Детям» напрямую для «Дети»: его имена — в domains-scoped.txt, не в списках для всех и не в «Сайтах»", [reply, pairs])
    ok(f"m.youtube-like.example/force_exit,rs_{kids['id']}" in ipset and f"kids.example/rs_{kids['id']}" in ipset,
       "в файле резолвера имя под доменом для всех несёт оба набора; своё имя — только набор правила", ipset)
    ok(("keeper", "stop") in world.events and ("watch", "domains", "list", True) in world.events, "имена изменились — резолвер перезапущен, наборы сброшены сторожем", world.events[:6])
    prewarmed = [event[1] for event in world.events if event[0] == "dig"]
    ok("kids.example" in prewarmed, "имена правила для групп разогреты сразу", prewarmed[:10])
    world.events.clear()
    reply = path.ask(["rule-route", b64({"changes": [{"rule": kids["id"], "route": "exit"}]})])
    ok(reply.get("ok") and not any(event[0] == "keeper" for event in world.events), "«напрямую» ⇄ «за границу» для той же группы: имена те же — резолвер не трогается", world.events[:4])
    world.fail.add("domains")
    world.events.clear()
    reply = path.ask(["rule-route", b64({"changes": [{"rule": kids["id"], "route": "off"}]})])
    after_failure = ipset_file.read_text()
    world.fail.discard("domains")
    ok(not reply.get("ok") and "rs_" not in after_failure and "force_exit" in after_failure and ("keeper", "start") in world.events,
       "сторож не сбросил наборы — отказ, а резолвер запущен с файлом только тех наборов, что есть всегда", [reply.get("error"), after_failure[-200:]])
    reply = path.ask(["rule-route", b64({"changes": [{"rule": kids["id"], "route": "direct"}]})])
    ok(reply.get("ok") and f"kids.example/rs_{kids['id']}" in ipset_file.read_text() and scoped_file.read_text().strip(),
       "сторож снова на месте: следующая правка возвращает наборы правила для групп (их имена были забыты вместе с файлом)", [reply, ipset_file.read_text()[-200:]])
    routing_files = routing.RoutingFiles(**{key: Path(world.path[key]) for key in ("domains_exit", "domains_direct", "domains_direct_rules", "nets_auto", "nets_auto_off", "domains_ipset", "domains_scoped", "domains_shared", "resolver_yaml", "route_mode")})

    class Down:
        async def health(self, timeout):
            return Outcome(True)

        async def domains(self, timeout):
            return Outcome(False, "сторож отказал")

    class Up:
        async def stop(self, timeout):
            return Outcome(True)

        async def start(self, timeout):
            return Outcome(True)

    scoped_file.write_text("rs_ab12cd only.example\n")
    bare = routing.Routing(routing_files, Down(), Up(), None)
    try:
        LOOP.run_until_complete(bare.relearn())
    except routing.NotApplied:
        pass
    ok("rs_ab12cd" not in ipset_file.read_text() and "force_exit" in ipset_file.read_text() and scoped_file.read_text() == "",
       "сброс наборов не удался — резолвер получает файл без наборов правил для групп, их имена забыты (следующая правка вернёт)", ipset_file.read_text()[-160:])
    scoped_file.write_text("rs_ab12cd only.example\n")
    groups_now = [SiteGroup.of(g) for g in world.groups]
    Path(world.path["domains_exit"]).write_text("".join(entry + "\n" for entry in site_groups.flat_entries(groups_now)))
    Path(world.path["domains_direct_rules"]).write_text("".join(entry + "\n" for entry in site_groups.direct_entries(groups_now)))
    from awgscale.agent.rules.scoped import ScopedName
    ok(bare.changed(groups_now, [ScopedName("rs_ab12cd", "only.example")]) is False and bare.changed(groups_now, [ScopedName("rs_ab12cd", "other.example")]) is True
       and bare.changed(groups_now, []) is True, "changed(): прежние проверки и ещё имена правил для групп", None)
    ok(routing.ipset_text("a.example\n*.b.example\n", "c.example\n") == routing.ipset_text("a.example\n*.b.example\n", "c.example\n", ()) == "a.example,b.example/force_exit\nc.example/force_direct\n",
       "без правил для групп файл резолвера тот же до байта")

    world.fail.add("store")
    reply = route(("a00003", "off"))
    ok(not reply.get("ok") and reply.get("error"), "хранилище недоступно — отказ словами", reply)


def main():
    t_pure()
    t_public_host()
    t_commands()
    t_rules()
    t_socket_names()
    if os.environ.get("PG_DSN"):
        t_store()
    golden_written()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
