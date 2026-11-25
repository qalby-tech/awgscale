#!/usr/bin/env python3
"""The watch service (src/awgscale/watch) against stand-ins for ip / ipset /
iptables / awg / ping / conntrack (tests/kstub.py): what it asks the kernel to
change. For `apply` on generated worlds command by command and byte for byte,
for a tick as the same changes and the same health files after — both
compared with what the engine asked when it was checked
(tests/golden/watch-engine.json). Then the rules the service must never break
are checked on their own, and the real service is started
(`python -m awgscale.watch`).

The engine (Cascade) is asked directly, the way the boot gate and the
service's own jobs ask it.

    uv run python tests/watch_service.py
    GOLDEN_WRITE=1 uv run python tests/watch_service.py    a change on purpose: the golden file written anew"""
import dataclasses
import json
import os
import pathlib
import random
import re
import datetime
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PASS = FAIL = 0
TOOLS = ("ip", "ipset", "iptables", "iptables-restore", "awg", "ping", "conntrack")
KEY = lambda n: f"{n:02d}" + "k" * 41 + "="
# every piece of code handed to Box.py starts with the engine at hand, as `server`
ENGINE = "from awgscale.service.errors import Refused\nfrom awgscale.watch.cascade import Cascade\nserver = Cascade()\n"
GOLDEN_FILE = os.path.join(HERE, "golden", "watch-engine.json")
GOLDEN_WRITE = os.environ.get("GOLDEN_WRITE") == "1"
# the engine's status, flat: moments as Unix seconds, the flag as a number
STATUS_FLAT = '''import dataclasses, json
s = dataclasses.asdict(server.status())
for e in s["exits"]:
    e["handshake"] = int(e["handshake"].timestamp()) if e["handshake"] else 0
    e["clients"] = list(e["clients"])
print(json.dumps(dict(s, ok=True, time=int(s["time"].timestamp()), udp_via_exit=1 if s["udp_via_exit"] else 0), default=str))
'''


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok   " + text)
    else:
        FAIL += 1
        print("FAIL " + text + ("" if seen is None else "\n     " + json.dumps(seen, ensure_ascii=False, default=str)[:1500]))


class Box:
    """One temp world: the cascade's directory, /run/cascade, the stubs."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="watch-service-")
        self.dir, self.run, self.bin, self.stub = (os.path.join(self.root, n) for n in ("cascade", "run", "bin", "stub"))
        for d in (self.dir, self.run, self.bin, self.stub):
            os.makedirs(d)
        for t in TOOLS:
            os.symlink(os.path.join(HERE, "kstub.py"), os.path.join(self.bin, t))
        self.env = dict(os.environ, PATH=self.bin + ":" + os.environ["PATH"], KSTUB=self.stub, CASCADE_DIR=self.dir, CASCADE_RUN=self.run,
                        PYTHONPATH=os.path.join(ROOT, "src"), PYTHONDONTWRITEBYTECODE="1")

    def put(self, name, text, where=None):
        with open(os.path.join(where or self.dir, name), "w") as f:
            f.write(text)

    def get(self, name, where=None):
        try:
            with open(os.path.join(where or self.dir, name)) as f:
                return f.read()
        except OSError:
            return None

    def world(self, files, runfiles, scenario):
        for d in (self.dir, self.run):
            for n in os.listdir(d):
                os.remove(os.path.join(d, n))
        for n, t in files.items():
            self.put(n, t)
        for n, t in runfiles.items():
            self.put(n, t, self.run)
        self.put("scenario.json", json.dumps(scenario), self.stub)
        self.put("log", "", self.stub)
        if os.path.exists(os.path.join(self.stub, "counts.json")):
            os.remove(os.path.join(self.stub, "counts.json"))

    def calls(self):
        return [json.loads(l) for l in (self.get("log", self.stub) or "").splitlines()]

    def py(self, code):
        r = subprocess.run([sys.executable, "-c", ENGINE + code], env=self.env, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def runstate(self):
        return {n: self.get(n, self.run) for n in sorted(os.listdir(self.run)) if n.startswith("exit")}

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def changes(calls):
    """Only what changes the kernel, in order; reads and probes are left out."""
    res = []
    for c in calls:
        t, a = c["tool"], c["argv"]
        if t in ("awg", "ping"):
            continue
        if t == "ip" and a[:2] == ["link", "show"]:
            continue
        if t == "ipset" and a[:1] in (["list"], ["test"], ["save"]):
            continue
        if t == "iptables" and ("-C" in a or "-nL" in a or "-S" in a):
            continue
        line = t + " " + " ".join(a) + ("\n" + c["stdin"] if c["stdin"] else "")
        res.append(re.sub(r"sp_tmp_\d+", "sp_tmp", line))
    return res


CONF = '''# test settings
CLIENT_IF="awg0"
CLIENT_SUBNET="172.16.17.0/24"
UDP_VIA_EXIT=%(udp)s

# a comment with (brackets) above the list
EXITS=(
%(exits)s
)
DNS_VIA_EXIT=(1.1.1.1 1.0.0.1 9.9.9.9)
VIA_EXIT_SUBNETS=(%(subnets)s)
PROBE_TARGETS=(1.1.1.1 9.9.9.9)
EXTRA_RU_NETS="%(extra)s"
'''
RU = "".join(f"5.{i}.0.0/16\n" for i in range(40))
EXITS3 = [(1, "awgk1", "10.77.1.2", "exit1-nl", 2), (3, "awgk2", "10.77.2.2", "exit2-nl", 1), (4, "awgk4", "10.77.4.2", "", None)]


def conf(exits, udp=1, subnets="172.30.99.0/24", extra=""):
    lines = "\n".join('    "%d:%s:%s:%s%s"' % (i, ifc, tip, name, "" if w is None else f":{w}") for i, ifc, tip, name, w in exits)
    return CONF % {"udp": udp, "exits": lines, "subnets": subnets, "extra": extra}


def peers(n, start=2):
    return [[KEY(i), [f"172.16.17.{i}/32"]] for i in range(start, start + n)]


class Golden:
    """What the engine asked the stand-ins when it was checked, by part; GOLDEN_WRITE=1 writes it anew."""

    def __init__(self):
        try:
            with open(GOLDEN_FILE) as f:
                self.known = json.load(f)
        except OSError:
            self.known = {}
        self.seen = {}

    def same(self, part, seen):
        """→ None when `seen` is what was recorded, else the first case that is not"""
        self.seen[part] = seen
        if GOLDEN_WRITE:
            return None
        known = self.known.get(part)
        if known is None:
            return ["nothing recorded for " + part + " (GOLDEN_WRITE=1 records it)"]
        if len(known) != len(seen):
            return ["cases", len(known), len(seen)]
        return next(([n, k, s] for n, (k, s) in enumerate(zip(known, seen)) if k != s), None)

    def write(self):
        if GOLDEN_WRITE:
            os.makedirs(os.path.dirname(GOLDEN_FILE), exist_ok=True)
            with open(GOLDEN_FILE, "w") as f:
                json.dump(self.seen, f, ensure_ascii=False, indent=1, sort_keys=True)
                f.write("\n")
            print("written: " + GOLDEN_FILE)


def t_apply(box, golden):
    print("\n— apply: те же команды ядру, в том же порядке, байт в байт")
    rnd = random.Random(7)
    cases = []
    for mode in ("list", "geo", None, "мусор"):
        for udp in (0, 1):
            for spaces in (False, True):
                exits = EXITS3 if rnd.random() < 0.5 else EXITS3[:2]
                files = {"cascade.conf": conf(exits, udp, "172.30.99.0/24" if rnd.random() < 0.7 else "", "203.0.113.0/24 198.51.100.7" if rnd.random() < 0.5 else ""),
                         "ru.zone": RU, "domains-exit.txt": "chatgpt.com\n# comment\n149.154.160.0/20\n*.example.org\n91.108.4.1\n",
                         "nets-auto.txt": "# Telegram\n91.108.56.0/22\n185.76.151.0/24\n"}
                if mode:
                    files["route.mode"] = mode + "\n"
                if rnd.random() < 0.4:
                    files["nets-auto.off"] = ""
                if rnd.random() < 0.6:
                    files["force_exit.save"] = "add force_exit 1.2.3.4\nadd force_exit 5.6.7.8\n"
                if spaces:
                    files["spaces.sets"] = (f"sp_ab12 172.16.17.2,{KEY(2)} 172.16.17.3,{KEY(3)} 172.16.17.9,{KEY(99)}\n"
                                            f"sp_bad! 172.16.17.2,{KEY(2)}\nsp_cd34 172.16.17.4,{KEY(4)} bad,pair 172.16.17.5,\n")
                sc = {"links": [e[1] for e in exits if rnd.random() < 0.8], "peers": peers(5), "sets": {"ru": ["x"] * 40, "sp_old": [], "sp_ab12": [], "cx_1": []},
                      "peerchain": spaces, "forward": ["-P FORWARD DROP", "-A FORWARD -i awg0 -o awg0 -j DROP"] + (["-A FORWARD -i awg0 -o awg0 -j CASCADE-PEERS"] if rnd.random() < 0.5 else []),
                      "present": ["-t mangle -C FORWARD -j CASCADE-MSS"] if rnd.random() < 0.5 else []}
                box.world(files, {}, sc)
                rc_p, _, err_p = box.py("server.apply()")
                cases.append({"case": f"mode={mode} udp={udp} spaces={spaces}", "rc": rc_p, "changes": changes(box.calls()), "sig": box.get("peers.sig", box.run),
                              "error": "" if rc_p == 0 else err_p[-300:]})
    bad = golden.same("apply", cases)
    ok(bad is None and all(c["rc"] == 0 for c in cases), f"{len(cases)} сочетаний (режим, UDP, пространства, сети, выходы): команды те же до байта, отметка о пространствах та же", bad)

    # what the marking must contain, whoever wrote it
    files = {"cascade.conf": conf(EXITS3[:2]), "ru.zone": RU, "domains-exit.txt": "chatgpt.com\n"}
    box.world(files, {}, {"links": ["awgk1", "awgk2"], "sets": {"ru": ["x"]}})
    box.py("server.apply()")
    calls = box.calls()
    restores = [c["stdin"] for c in calls if c["tool"] == "iptables-restore" and c["stdin"].startswith("*mangle")]
    mangle = restores[0]
    scoped_chain = restores[-1]
    ok("match-set ru dst -j RETURN" not in mangle and "-A CASCADE -g CASCADE-MARK" not in mangle and "! --dport 443 -m set ! --match-set ru dst -g CASCADE-MARK" in mangle,
       "нет файла режима — правила «по списку»: помечается только список и UDP для звонков")
    ok(all(f"-A CASCADE -d {n} -j DROP" in mangle for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")) and mangle.index("-j DROP") < mangle.index("force_exit"),
       "частные адреса отбрасываются раньше любой пометки")
    ch = [" ".join([c["tool"]] + c["argv"]) for c in calls]
    ok(all(f"ip route replace blackhole default metric 1000 table {t}" in ch for t in (201, 203, 299)), "под каждой таблицей выхода и под запасной лежит blackhole", [c for c in ch if "blackhole" in c])
    ok(not [c for c in ch if c in ("ipset flush force_exit", "ipset flush force_direct", "ipset destroy force_exit", "ipset destroy force_direct")],
       "apply не очищает адреса, выученные для списка")
    ok(not [c for c in ch if c.startswith("conntrack")] and not [c for c in ch if c.startswith("ping")], "apply не проверяет выходы и не рвёт соединения")

    # the boot gate runs on the host's bare Python: its code must need nothing but the standard library (-S: no site-packages)
    box.world(files, {}, {"links": ["awgk1", "awgk2"], "sets": {"ru": ["x"]}})
    gate = [sys.executable, "-S", "-m", "awgscale.watch.gate"]
    r = subprocess.run(gate + ["rules"], env=box.env, capture_output=True, text=True, timeout=60)
    ok(r.returncode == 0 and r.stdout == mangle + scoped_chain and scoped_chain.startswith("*mangle\n:CASCADE-SCOPED") and not box.calls(),
       "шлюз загрузки на голом Python (без пакетов): `rules` печатает те же правила (и цепочку правил для групп) и ничего не трогает", [r.returncode, r.stderr[-300:]])
    r = subprocess.run(gate + ["apply"], env=box.env, capture_output=True, text=True, timeout=60)
    ok(r.returncode == 0 and changes(box.calls()) == changes(calls), "шлюз загрузки: `apply` даёт ядру те же команды, что и сервис", [r.returncode, r.stderr[-300:]])
    box.put("cascade.conf", "CLIENT_IF=\"awg0\"\nCLIENT_SUBNET=\"172.16.17.0/24\"\nEXITS=()\n")
    r = subprocess.run(gate + ["apply"], env=box.env, capture_output=True, text=True, timeout=60)
    ok(r.returncode == 1 and "rules not applied" in r.stderr, "шлюз загрузки: настройки без выходов — код 1 (туннель не поднимется)", [r.returncode, r.stderr[-300:]])

    box.world(dict(files, **{"ru.zone": ""}), {}, {"links": ["awgk1"], "sets": {}})
    rc, out, err = box.py("server.fetch_home_zone = lambda conf: False\ntry:\n    server.apply()\nexcept Refused as e:\n    print('REFUSED', e)\n")
    ok("REFUSED" in out and not [c for c in changes(box.calls()) if c.startswith(("iptables", "ip rule", "ip route"))],
       "пустой список российских сетей: отказ до любых правил (иначе за границу ушло бы всё)", [out, err[-300:], changes(box.calls())])

    box.world(dict(files, **{"spaces.sets": f"sp_ab12 172.16.17.2,{KEY(2)}\n"}), {}, {"links": ["awgk1", "awgk2"], "sets": {"ru": ["x"]}, "peers": peers(2), "restore_fails": "*filter"})
    rc, out, err = box.py("print(server.apply())")
    ok(rc == 0 and out.strip() == "list" and box.get("peers.sig", box.run) is None, "пространства не загрузились — apply всё равно удался (правила безопасности на месте), отметка не поставлена", [rc, out, err[-300:]])


def tick_world(rnd):
    exits = EXITS3 if rnd.random() < 0.6 else EXITS3[:2]
    run, alive = {}, []
    for i, ifc, tip, _, _ in exits:
        kind = rnd.choice(("up", "up", "down", "missing"))
        if kind != "missing":
            run[f"exit{i}.state"] = kind + "\n"
            run[f"exit{i}.ok"] = f"{rnd.choice((0, 1, 2, 3, 4, 9))}\n"
            run[f"exit{i}.fail"] = f"{rnd.choice((0, 1, 2, 5))}\n"
        if rnd.random() < 0.65:
            alive.append(tip)
    n = rnd.randint(0, 14)
    ps = peers(n, rnd.choice((2, 2, 7)))
    ips = [p[1][0][:-3] for p in ps]
    sets = {f"cx_{i}": [ip for ip in ips + ["172.16.17.200"] if rnd.random() < 0.3] for i, *_ in exits}
    pins = ""
    for ip in ips:
        if rnd.random() < 0.3:
            pins += f"{ip} {rnd.choice((1, 3, 4, 7, 'x'))}\n"
    pins += "garbage line\n1.2.3 1\n" if rnd.random() < 0.3 else ""
    files = {"cascade.conf": conf(exits), "ru.zone": RU, "pins.map": pins}
    if rnd.random() < 0.3:
        files.pop("pins.map")
    sc = {"links": [e[1] for e in exits if rnd.random() < 0.85], "alive": alive, "peers": ps, "sets": sets, "forward": []}
    return files, run, sc


def t_tick(box, golden):
    print("\n— tick и pin: те же изменения и те же файлы здоровья")
    rnd = random.Random(11)
    for cmd, code in (("tick", "server.tick()"), ("pin", "server.tick(probe=False)")):
        cases = []
        bad = None
        for n in range(120):
            files, run, sc = tick_world(rnd)
            box.world(files, run, sc)
            rc_p, _, err_p = box.py(code)
            p = changes(box.calls())
            run_p = box.runstate()
            pings_p = len([c for c in box.calls() if c["tool"] == "ping"])
            # the routes of the probe come in order; who is added to or taken from a set is a set
            cases.append({"rc": rc_p, "routes": [c for c in p if not c.startswith("ipset")], "sets": sorted(c for c in p if c.startswith("ipset")),
                          "health": run_p, "pings": pings_p, "error": "" if rc_p == 0 else err_p[-300:]})
            if bad is None and cmd == "pin" and (pings_p or [c for c in p if c.startswith(("conntrack", "ip route"))] or run_p != {k: v for k, v in run.items()}):
                bad = [n, "pin трогает здоровье", p]
        bad = bad or golden.same(cmd, cases)
        ok(bad is None and all(c["rc"] == 0 for c in cases), f"{cmd}: 120 случайных состояний (выходы, счётчики, закрепления, состав наборов) — те же изменения" + (" ; pin не проверяет выходы и не меняет их состояние" if cmd == "pin" else ""), bad)


def t_rules(box, golden):
    print("\n— правила сторожа сами по себе")
    sys.path.insert(0, os.path.join(ROOT, "src"))
    os.environ.update(CASCADE_DIR=box.dir, CASCADE_RUN=box.run)
    from awgscale.watch import rules as W
    from awgscale.watch.conf import CascadeConf, conf_read
    from awgscale.watch.places import Places
    box.world({"cascade.conf": conf(EXITS3)}, {}, {})
    s = CascadeConf.load(Places.from_environment())
    ok([(e.index, e.iface, e.tip, e.name, e.weight) for e in s.exits] == [(1, "awgk1", "10.77.1.2", "exit1-nl", 2), (3, "awgk2", "10.77.2.2", "exit2-nl", 1), (4, "awgk4", "10.77.4.2", "", 1)]
       and list(s.dns_via_exit) == ["1.1.1.1", "1.0.0.1", "9.9.9.9"] and list(s.via_exit_subnets) == ["172.30.99.0/24"] and s.udp_via_exit and s.mode == "list",
       "cascade.conf прочитан как текст: выходы с весами, списки, скаляры; комментарий со скобками не мешает", s)
    real = conf_read(pathlib.Path(ROOT, "tests", "cascade.conf"))
    r = subprocess.run(["bash", "-c", f'source {os.path.join(ROOT, "tests", "cascade.conf")}; printf "%s\\n" "$CLIENT_IF" "$CLIENT_SUBNET" "$UDP_VIA_EXIT" "${{EXITS[*]}}" "${{DNS_VIA_EXIT[*]}}" "${{VIA_EXIT_SUBNETS[*]}}" "${{PROBE_TARGETS[*]}}" "$EXTRA_RU_NETS"'],
                       capture_output=True, text=True)
    mine = [real["CLIENT_IF"], real["CLIENT_SUBNET"], real["UDP_VIA_EXIT"], " ".join(real["EXITS"]), " ".join(real["DNS_VIA_EXIT"]), " ".join(real["VIA_EXIT_SUBNETS"]), " ".join(real["PROBE_TARGETS"]), real["EXTRA_RU_NETS"]]
    ok(r.stdout.splitlines() == mine, "настоящий cascade.conf: сервис читает то же, что bash, когда файл подключают (source)", [r.stdout.splitlines(), mine])

    ips = [f"172.16.17.{i}" for i in range(2, 8)]
    w = W.pins_wanted(s, ips, {1, 3, 4}, "")
    ok([w[i] for i in ips] == [1, 1, 3, 4, 1, 1], "веса 2:1:1 — клиенты по кругу: выход 1 дважды, потом 3, потом 4", w)
    w = W.pins_wanted(s, ips, {3, 4}, "")
    ok(all(v in (3, 4) for v in w.values()), "выход 1 лежит — все его клиенты на здоровых", w)
    w = W.pins_wanted(s, ips, set(), "172.16.17.2 3\n")
    ok(w["172.16.17.2"] == 3 and [w[i] for i in ips[1:]] == [1, 3, 4, 1, 1], "ни одного здорового выхода: каждый остаётся на своём (blackhole), никто не уходит напрямую", w)
    w = W.pins_wanted(s, ips, {1, 3, 4}, "172.16.17.2 4\n172.16.17.3 9\n172.16.17.4 x\nмусор\n999 1\n")
    ok(w["172.16.17.2"] == 4 and w["172.16.17.3"] == 1 and w["172.16.17.4"] == 3, "pins.map: считается только настроенный выход, остальное в файле игнорируется", w)
    pins = "172.16.17.2 4\n172.16.17.3 all\n172.16.17.4 1\n172.16.17.4 all\n172.16.17.99 all\nмусор all\n"
    w = W.pins_wanted(s, ips, {1, 3, 4}, pins)
    ok(w["172.16.17.2"] == 4 and w["172.16.17.3"] == 1 and w["172.16.17.4"] == 1, "строки «адрес all» не мешают закреплению: выход как был", w)
    whole = W.whole_wanted(ips, pins)
    ok(whole == {"172.16.17.3", "172.16.17.4"}, "весь трафик через выход — только у подключённых адресов со строкой «all»", whole)
    mangle = W.mangle_rules(s)
    order = [line for line in mangle.splitlines() if line.startswith("-A CASCADE ")]
    at = next(i for i, line in enumerate(order) if W.WHOLE_SET in line)
    ok("--ctstate NEW -g CASCADE-MARK" in order[at] and any("force_direct" in line for line in order[:at]) and any("-j DROP" in line for line in order[:at])
       and not any("match-set ru" in line for line in order[:at]),
       "весь трафик: после частных адресов и «напрямую» админа, раньше российского списка; только новые соединения", order)
    now_on = {ip: 3 for ip in ips}
    w = W.pins_wanted(s, ips, set(), "", now_on)
    ok(all(v == 3 for v in w.values()), "ни одного здорового выхода: кто уже на выходе, остаётся на нём — не переводится на другой мёртвый", w)
    w = W.pins_wanted(s, ips, set(), "", {"172.16.17.2": 4})
    ok(w["172.16.17.2"] == 4 and [w[i] for i in ips[1:]] == [1, 3, 4, 1, 1], "ни одного здорового: новый клиент — на свой выход, остальные остаются где были", w)
    w = W.pins_wanted(s, ips, {1}, "", now_on)
    ok(all(v == 1 for v in w.values()), "выход поднялся — все уходят на здоровый, где бы ни были", w)
    w = W.pins_wanted(s, ips, {1, 3}, "172.16.17.2 4\n")
    ok(w["172.16.17.2"] in (1, 3), "закреплённый выход лежит — клиент временно на здоровом", w)
    off = dataclasses.replace(s, exits=tuple(dataclasses.replace(e, weight=0) if e.index == 1 else e for e in s.exits))
    w = W.pins_wanted(off, ips, {1, 3, 4}, "172.16.17.2 1\n")
    ok(all(v in (3, 4) for v in w.values()), "выход 1 выключен (вес 0): никого на него, даже закреплённого, хоть он и жив", w)
    w = W.pins_wanted(off, ips, {1}, "")
    ok(all(v == 1 for v in w.values()), "включённые выходы лежат, жив только выключенный — он последний запасной: лучше он, чем никакой", w)
    w = W.pins_wanted(off, ips, {1, 3}, "")
    ok(all(v == 3 for v in w.values()), "пока жив хоть один включённый, выключенный не берёт никого", w)
    none_on = dataclasses.replace(s, exits=tuple(dataclasses.replace(e, weight=0) for e in s.exits))
    w = W.pins_wanted(none_on, ips, {1, 3, 4}, "")
    ok([w[i] for i in ips] == [1, 3, 4, 1, 3, 4], "все выключены (настройки этого не дают) — каждый выход по разу, никто не без выхода", w)

    # a real up→down flushes that exit's connections; nothing else does
    def tick_calls(run, alive):
        box.world({"cascade.conf": conf(EXITS3[:2]), "pins.map": ""}, run, {"links": ["awgk1", "awgk2"], "alive": alive, "peers": peers(3), "sets": {}})
        box.py("server.tick()")
        return [" ".join([c["tool"]] + c["argv"]) for c in box.calls()], box.runstate()
    up = {"exit1.state": "up\n", "exit1.ok": "9\n", "exit1.fail": "0\n", "exit3.state": "up\n", "exit3.ok": "9\n", "exit3.fail": "0\n"}
    ch, st = tick_calls(up, ["10.77.2.2"])
    ok(st["exit1.state"] == "up\n" and st["exit1.fail"] == "1\n" and not [c for c in ch if c.startswith("conntrack")], "один пропущенный ответ: выход ещё работает, соединения не трогаются", st)
    ch, st = tick_calls(dict(up, **{"exit1.fail": "1\n"}), ["10.77.2.2"])
    ok(st["exit1.state"] == "down\n" and ch.count("conntrack -D --mark 0x101") == 1 and not [c for c in ch if c == "conntrack -D --mark 0x103"],
       "второй подряд: выход DOWN, сброшены соединения именно его, один раз", [st, [c for c in ch if "conntrack" in c]])
    ok("ip route replace default dev awgk2 metric 10 table 299" in ch, "запасная таблица переведена на здоровый выход")
    down = dict(up, **{"exit1.state": "down\n", "exit1.ok": "2\n"})
    ch, st = tick_calls(down, ["10.77.1.2", "10.77.2.2"])
    ok(st["exit1.state"] == "down\n" and st["exit1.ok"] == "3\n", "ожил, но ещё не 4 ответа подряд — остаётся DOWN (клиентов не дёргаем туда-сюда)", st)
    ch, st = tick_calls(dict(down, **{"exit1.ok": "3\n"}), ["10.77.1.2", "10.77.2.2"])
    ok(st["exit1.state"] == "up\n" and not [c for c in ch if c.startswith("conntrack")], "четвёртый ответ: UP, соединения не сбрасываются", st)
    ch, st = tick_calls(dict(up, **{"exit1.fail": "1\n", "exit3.fail": "1\n"}), [])
    ok(st["exit1.state"] == st["exit3.state"] == "down\n" and "ip route del default metric 10 table 299" in ch and not [c for c in ch if c.startswith("ipset del")],
       "все выходы легли: запасной маршрут убран (остаётся blackhole), клиенты из наборов не вынимаются", [st, [c for c in ch if c.startswith(("ipset del", "ip route del"))]])

    box.world({"cascade.conf": conf(EXITS3[:2]), "pins.map": "172.16.17.3 all\n172.16.17.4 all\n172.16.17.50 all\n"}, up,
              {"links": ["awgk1", "awgk2"], "alive": ["10.77.1.2", "10.77.2.2"], "peers": peers(3), "sets": {"all_exit": ["172.16.17.2", "172.16.17.4"]}})
    rc, _, err = box.py("server.tick(probe=False)")
    ch = [" ".join([c["tool"]] + c["argv"]) for c in box.calls()]
    whole = [c for c in ch if "all_exit" in c and not c.startswith("ipset create")]
    ok(whole == ["ipset list all_exit", "ipset del all_exit 172.16.17.2", "ipset add all_exit 172.16.17.3 -exist"],
       "весь трафик: передумавший вынут из набора, выбравший добавлен, уже стоящий и не подключённый не трогаются", whole)
    box.world({"cascade.conf": conf(EXITS3[:2]), "domains-exit.txt": "a.example\n149.154.160.0/20\n", "force_exit.save": "add force_exit 1.2.3.4\n"}, {}, {"sets": {"force_exit_net": ["x"]}})
    rc, out, _ = box.py("print(server.domains_reset())")
    ch = changes(box.calls())
    ok(rc == 0 and "ipset flush force_exit" in ch and "ipset flush force_direct" in ch and box.get("force_exit.save") is None
       and any(c.startswith("ipset restore\nadd force_exit_net_tmp 149.154.160.0/20 -exist") for c in ch),
       "domains: сети списка загружены заново, выученные адреса забыты вместе с их сохранённой копией", ch)
    box.world({"cascade.conf": conf(EXITS3[:2]), "route.mode": "geo\n"}, {"exit1.state": "up\n", "exit3.state": "down\n"},
              {"sets": {"force_exit_net": ["a", "b"], "ru": ["x"] * 7, "force_exit": ["1.1.1.1"], "force_direct": [], "cx_1": ["172.16.17.2", "172.16.17.3"], "cx_3": []}})
    rc, out, err = box.py(STATUS_FLAT)
    p = json.loads(out) if rc == 0 else {"error": err[-300:]}
    p.pop("time", None)
    p.pop("watch", None)
    single = p.pop("single_server", None)      # newer than the recording: a cascade is never a single server
    country = p.pop("home_country", None)      # newer too: a server that names none is Russian
    for e in p.get("exits", []):
        e.pop("probe_ms")
        e.pop("changed")
    bad = golden.same("status", [p])
    ok(bad is None and single is False and country == "ru", "status: те же поля и значения (выходы, режим, наборы, клиенты); каскад — не один сервер, страна — Россия", [bad, single, country])


def t_scoped(box):
    print("\n— правила для отдельных групп «за границу» и «напрямую»")
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from awgscale.watch import scoped as S
    rules = S.parse_rules("rs_ab12cd exit rg_g00001 rg_c0ffee\nrs_bad direct rg_g00001\nrs_ffffff direct rg_g00002\nмусор\nrs_eeeeee exit\n")
    ok([(r.rule_set, r.way.value, r.groups) for r in rules] == [("rs_ab12cd", "exit", ("rg_g00001", "rg_c0ffee")), ("rs_ffffff", "direct", ("rg_g00002",))],
       "scoped.rules: только верные строки, по порядку; без групп или с кривым именем — мимо", rules)
    members = S.parse_members("rg_g00001 172.16.17.9 172.16.17.12\nrg_c0ffee\nrg_x 1.2.3.4\nrg_g00002 172.16.17.3 bad\n")
    ok(members == {"rg_g00001": ["172.16.17.9", "172.16.17.12"], "rg_c0ffee": []}, "scoped.members: группы с адресами, пустая группа — пустой набор", members)
    ok(S.ipset_sets("a.com,b.com/force_exit\nm.a.com/force_exit,rs_ab12cd\nc.org/rs_ffffff\nd.ru/force_direct,rs_BAD\n") == {"rs_ab12cd", "rs_ffffff"},
       "domains.ipset: названы наборы правил для групп (только верные имена)")
    payload = S.scoped_payload(rules)
    ok(payload == ("*mangle\n:CASCADE-SCOPED - [0:0]\n-F CASCADE-SCOPED\n"
                   "-A CASCADE-SCOPED -m set --match-set rg_g00001 src -m set --match-set rs_ab12cd dst -g CASCADE-MARK\n"
                   "-A CASCADE-SCOPED -m set --match-set rg_c0ffee src -m set --match-set rs_ab12cd dst -g CASCADE-MARK\n"
                   "-A CASCADE-SCOPED -m set --match-set rg_g00002 src -m set --match-set rs_ffffff dst -g CASCADE-DIRECT\nCOMMIT\n"),
       "цепочка: по правилу и группе, по порядку; за границу — как все помеченные (выход человека), напрямую — своя метка", payload)

    files = {"cascade.conf": conf(EXITS3[:2]), "ru.zone": RU, "domains-exit.txt": "a.com\n",
             "domains.ipset": "a.com/force_exit\nm.a.com/force_exit,rs_ab12cd\nc.org/rs_ffffff\n",
             "scoped.rules": "rs_ab12cd exit rg_g00001\nrs_ffffff direct rg_g00002 rg_g00001\n",
             "scoped.members": "rg_g00001 172.16.17.2 172.16.17.99\nrg_g00002 172.16.17.3\n",
             "scoped.save": "add rs_ab12cd 1.2.3.4\nadd rs_gone00 5.6.7.8\nadd force_exit 9.9.9.9\n"}
    box.world(files, {}, {"links": ["awgk1", "awgk2"], "peers": peers(3), "sets": {"ru": ["x"], "rs_ab12cd": [], "rs_ffffff": [], "rs_old000": [], "rg_old000": [], "sp_ab12": []}})
    rc, out, err = box.py("print(server.apply())")
    ch = changes(box.calls())
    creates = [c for c in ch if c.startswith("ipset create r")]
    ok(rc == 0 and all(f"ipset create {n} hash:ip -exist" in creates for n in ("rs_ab12cd", "rs_ffffff", "rg_g00001", "rg_g00002")), "apply: наборы правил и групп созданы (их называет файл резолвера)", [rc, err[-300:], creates])
    at_create = ch.index("ipset create rs_ab12cd hash:ip -exist")
    at_restore_main = next(i for i, c in enumerate(ch) if c.startswith("iptables-restore --noflush\n*mangle\n:CASCADE "))
    ok(at_create < at_restore_main and "iptables -w 5 -t mangle -N CASCADE-SCOPED" in ch[:at_restore_main], "наборы и цепочка — раньше правил для всех, которые на неё переходят")
    restored = [c for c in ch if c.startswith("ipset restore -exist\nadd rs_")]
    ok(restored == ["ipset restore -exist\nadd rs_ab12cd 1.2.3.4\n"], "выученные адреса правил — только в наборы, что есть сейчас", restored)
    swap = next((c for c in ch if c.startswith("ipset restore\ncreate rg_g00001")), "")
    ok("add rg_tmp_" in swap and "172.16.17.2 -exist" in swap and "172.16.17.99" not in swap and "swap rg_tmp_" in swap, "люди группы — только подключённые сейчас, набор подменён целиком", swap)
    scoped_restore = [c for c in ch if c.startswith("iptables-restore --noflush\n*mangle\n:CASCADE-SCOPED")]
    ok(len(scoped_restore) == 1 and "rg_g00001 src -m set --match-set rs_ffffff dst -g CASCADE-DIRECT" in scoped_restore[0] and ch.index(scoped_restore[0]) > at_restore_main,
       "цепочка правил для групп загружена отдельно, после правил для всех", scoped_restore)
    destroyed = sorted(c for c in ch if c.startswith("ipset destroy r") and "tmp" not in c)
    ok(destroyed == ["ipset destroy rg_old000", "ipset destroy rs_old000"], "наборы ушедших правил и групп удалены, когда на них ничто не ссылается", destroyed)

    box.world(dict(files, **{"scoped.rules": "rs_ab12cd exit rg_g00001\n"}), {}, {"links": ["awgk1", "awgk2"], "peers": peers(3), "sets": {"ru": ["x"]}, "restore_fails": ":CASCADE-SCOPED"})
    rc, out, err = box.py("print(server.apply())")
    ok(rc == 0 and out.strip() == "list", "цепочка не загрузилась — apply всё равно удался, люди групп идут по правилам для всех", [rc, out, err[-300:]])
    rc, out, err = box.py("try:\n    server.scoped_apply()\nexcept Refused as e:\n    print('REFUSED', e)\n")
    ok("REFUSED правила для групп не загрузились" in out, "а загрузка по просьбе агента — отказ словами", [out, err[-300:]])
    box.world(dict(files, **{"scoped.rules": "мусор\n\x00\n"}), {}, {"links": ["awgk1", "awgk2"], "peers": peers(3), "sets": {"ru": ["x"]}})
    rc, out, err = box.py("print(server.apply())")
    scoped_restore = [c for c in changes(box.calls()) if c.startswith("iptables-restore --noflush\n*mangle\n:CASCADE-SCOPED")]
    ok(rc == 0 and scoped_restore == ["iptables-restore --noflush\n*mangle\n:CASCADE-SCOPED - [0:0]\n-F CASCADE-SCOPED\nCOMMIT\n"], "мусор в файле — пустая цепочка, apply удался", [rc, scoped_restore])

    box.world(files, {}, {"links": ["awgk1", "awgk2"], "peers": peers(4), "sets": {"rg_g00001": ["172.16.17.4", "172.16.17.2"], "rg_g00002": []}})
    rc, out, err = box.py("server.tick(probe=False)")
    moved = [c for c in changes(box.calls()) if c.startswith(("ipset add rg_", "ipset del rg_"))]
    ok(rc == 0 and sorted(moved) == ["ipset add rg_g00002 172.16.17.3 -exist", "ipset del rg_g00001 172.16.17.4"] and not [c for c in changes(box.calls()) if c.startswith("iptables-restore")],
       "человек перешёл в другую группу: только разница наборов, цепочка не трогается", moved)

    box.world(files, {}, {"links": ["awgk1", "awgk2"], "peers": peers(4), "sets": {"rg_g00001": ["172.16.17.4"], "rg_g00002": []},
                          "rc_seq": {"ipset create cx_1 hash:ip -exist": [1]}})
    rc, out, err = box.py("try:\n    server.tick(probe=False)\nexcept Refused as e:\n    print('REFUSED', e)\n")
    moved = [c for c in changes(box.calls()) if c.startswith(("ipset add rg_", "ipset del rg_"))]
    ok("REFUSED" in out and sorted(moved) == ["ipset add rg_g00001 172.16.17.2 -exist", "ipset add rg_g00002 172.16.17.3 -exist", "ipset del rg_g00001 172.16.17.4"],
       "шаг прохода до групп не удался: люди групп всё равно приведены в порядок", [out, moved])
    box.world(files, {}, {"links": ["awgk1", "awgk2"], "peers": peers(4), "sets": {"rg_g00002": []},
                          "rc_seq": {"ipset create rg_g00001 hash:ip -exist": [1]}})
    rc, out, err = box.py("server.tick(probe=False)")
    moved = [c for c in changes(box.calls()) if c.startswith(("ipset add rg_", "ipset del rg_"))]
    ok(rc == 0 and moved == ["ipset add rg_g00002 172.16.17.3 -exist"], "одна группа не далась — другие всё равно приведены в порядок", [rc, moved, err[-200:]])

    contention = {"iptables -w 5 -t mangle -N CASCADE-SCOPED": [1], "iptables -w 5 -t mangle -nL CASCADE-SCOPED": [1, 0]}
    box.world(files, {}, {"links": ["awgk1", "awgk2"], "peers": peers(3), "sets": {"ru": ["x"]}, "rc_seq": contention})
    rc, out, err = box.py("print(server.apply())")
    ok(rc == 0 and out.strip() == "list", "цепочка уже есть, а первый взгляд на неё не удался (замок xtables): -N отказал, второй взгляд её видит — apply удался", [rc, err[-300:]])
    box.world(files, {}, {"links": ["awgk1", "awgk2"], "peers": peers(3), "sets": {"ru": ["x"]}, "rc_seq": {"iptables -w 5 -t mangle -N CASCADE-SCOPED": [1]}})
    rc, out, err = box.py("print(server.apply())")
    ok(rc != 0 and "CASCADE-SCOPED: цепочка не создана" in err, "цепочки нет и создать её нельзя — apply отказывает (правила для всех на неё переходят)", [rc, err[-300:]])

    box.world(dict(files, **{"domains.ipset": "a.com/force_exit\n", "domains-scoped.txt": "rs_ab12cd m.a.com\nrs_c0ffee c.org\nмусор\n"}), {}, {"sets": {"force_exit_net": ["x"]}})
    rc, out, err = box.py("print(server.domains_reset())")
    ch = changes(box.calls())
    ok(rc == 0 and ch[:4] == ["ipset create rs_ab12cd hash:ip -exist", "ipset flush rs_ab12cd", "ipset create rs_c0ffee hash:ip -exist", "ipset flush rs_c0ffee"],
       "domains: наборы, которые файл резолвера назовёт (domains-scoped.txt, пока он их не называет), созданы и очищены первыми", ch[:4])
    box.world(dict(files, **{"domains.ipset": "a.com/force_exit\n", "domains-scoped.txt": "rs_ab12cd m.a.com\nrs_c0ffee c.org\n"}), {},
              {"sets": {"force_exit_net": ["x"]}, "rc_seq": {"ipset create rs_c0ffee hash:ip -exist": [1]}})
    rc, out, err = box.py("print(server.domains_reset())")
    ch = changes(box.calls())
    ok(rc == 0 and "missing=['rs_c0ffee']" in out and "ipset flush rs_ab12cd" in ch and "ipset flush force_exit" in ch and "ipset flush force_direct" in ch,
       "domains: набор правила для групп не создаётся — шаг для всех всё равно сделан, а этот набор назван в ответе (агент не даст его резолверу)", [rc, out, err[-300:]])
    from awgscale.watch.models import FEATURES, DomainsReset
    ok("scoped-names" in FEATURES and "scoped" in FEATURES and DomainsReset.model_validate({"nets": 3}).missing == [],
       "сторож говорит «scoped-names» (и по-прежнему «scoped»); ответ сторожа прежней версии без «missing» читается как «все наборы есть»", FEATURES)
    box.world(dict(files, **{"domains.ipset": "a.com/force_exit\n", "domains-scoped.txt": "rs_c0ffee c.org\n"}), {},
              {"links": ["awgk1", "awgk2"], "peers": peers(3), "sets": {"ru": ["x"], "rs_c0ffee": [], "rs_old000": []}})
    rc, out, err = box.py("print(server.apply())")
    destroyed = sorted(c for c in changes(box.calls()) if c.startswith("ipset destroy rs_"))
    ok(rc == 0 and destroyed == ["ipset destroy rs_old000"], "набор, который назовёт файл резолвера, не удаляется как ушедший", destroyed)

    box.world(dict(files, **{"domains.ipset": "m.a.com/force_exit,rs_ab12cd\n"}), {}, {"sets": {"force_exit_net": ["x"]}})
    rc, out, err = box.py("print(server.domains_reset())")
    ch = changes(box.calls())
    ok(rc == 0 and ch[:2] == ["ipset create rs_ab12cd hash:ip -exist", "ipset flush rs_ab12cd"] and box.get("scoped.save") is None,
       "domains: наборы правил для групп созданы и очищены первыми (резолвер запустят и после сбоя), их копия забыта", ch[:3])

    box.world(files, {}, {"sets": {"rs_ab12cd": ["1.1.1.1", "2.2.2.2"], "rs_ffffff": []}})
    rc, out, err = box.py("server.save_learned()")
    ok(rc == 0 and box.get("scoped.save") == "add rs_ab12cd 1.1.1.1\nadd rs_ab12cd 2.2.2.2\n", "выученные адреса правил для групп сохранены рядом с force_exit.save", box.get("scoped.save"))

    box.world(dict(files, **{"cascade.conf": "CLIENT_IF=\"awg0\"\nCLIENT_SUBNET=\"172.16.17.0/24\"\nSINGLE_SERVER=1\nEXITS=()\n", "scoped.rules": ""}), {}, {"sets": {}})
    rc, out, err = box.py("print(server.apply())")
    ch = changes(box.calls())
    ok(rc == 0 and "ipset create rs_ab12cd hash:ip -exist" in ch and not [c for c in ch if "CASCADE-SCOPED" in c or "CASCADE-DIRECT" in c],
       "один сервер: наборы, что называет файл резолвера, есть; цепочек правил для групп нет", [rc, err[-300:], [c for c in ch if "SCOPED" in c]])


def free_port():
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


def asker(port):
    def ask(method, path, token="t0ken"):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"{}" if method == "POST" else None,
                                     headers={"Authorization": "Bearer " + token} if token is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
        except OSError as e:
            return 0, {"error": str(e)}
    return ask


def started(ask):
    """Waits until the service has put the rules in and made its first pass. → the health answer"""
    h = (0, {})
    for _ in range(100):
        h = ask("GET", "/v1/health")
        if h[0] == 200 and h[1].get("applied") and h[1].get("last_tick"):
            break
        time.sleep(0.2)
    return h


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=12)
    except subprocess.TimeoutExpired:
        proc.kill()


def t_http(box):
    print("\n— сервис целиком: запуск, токен, пути")
    port = free_port()
    box.world({"cascade.conf": conf(EXITS3[:2]), "ru.zone": RU, "domains-exit.txt": "chatgpt.com\n", "pins.map": ""}, {},
              {"links": ["awgk1", "awgk2"], "alive": ["10.77.1.2"], "peers": peers(3), "sets": {"ru": ["x"], "cx_1": [], "cx_3": []}})
    box.put("token", "t0ken\n", box.root)
    env = dict(box.env, WATCH_PORT=str(port), WATCH_TOKEN_FILE=os.path.join(box.root, "token"))
    proc = subprocess.Popen([sys.executable, "-m", "awgscale.watch"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    ask = asker(port)
    try:
        h = started(ask)
        ok(h[0] == 200 and h[1].get("applied") and h[1].get("last_tick"), "запустился: правила применены, первый проход сделан", h)
        calls = box.calls()
        first_restore = next(i for i, c in enumerate(calls) if c["tool"] == "iptables-restore")
        first_ping = next(i for i, c in enumerate(calls) if c["tool"] == "ping")
        ok(first_restore < first_ping, "сначала правила, потом проверка выходов")
        ok([ask("GET", "/v1/status", t)[0] for t in (None, "", "wrong", "t0ken2")] == [401] * 4, "без верного токена — 401")
        code, st = ask("GET", "/v1/status")
        ok(code == 200 and [(e["index"], e["state"]) for e in st["exits"]] == [(1, "up"), (3, "unknown")] and st["route_mode"] == "list" and st["exits"][0]["probe_ms"] is not None and st["exits"][1]["probe_ms"] is None,
           "status: выход 1 up с временем ответа; выход 3 молчит — после одной пробы приговора ещё нет (unknown)", st)
        box.put("log", "", box.stub)
        code, r = ask("POST", "/v1/pin")
        ok(code == 200 and r == {"ok": True} and not [c for c in box.calls() if c["tool"] == "ping"], "POST /v1/pin: проход без проверки выходов", [code, r])
        ok(ask("POST", "/v1/apply")[1].get("ok") is True and ask("POST", "/v1/spaces")[0] == 200 and ask("POST", "/v1/domains")[0] == 200, "apply, spaces, domains отвечают 200")
        ok(ask("GET", "/v1/nope")[0] == 404 and ask("POST", "/v1/status")[0] == 405, "неизвестный путь — 404, чужой метод — 405")
        ok(all(ask("GET", path)[0] == 404 for path in ("/docs", "/redoc", "/openapi.json")), "описания API наружу нет")
        moments = [h[1]["started"], h[1]["applied"], h[1]["last_tick"], st["time"], st["exits"][0]["handshake"], st["exits"][0]["changed"], st["watch"]["started"]]
        ok(all(datetime.datetime.fromisoformat(m).tzinfo is not None for m in moments) and st["udp_via_exit"] is True and st["exits"][1]["changed"] is None,
           "время в ответах — дата со временем и поясом (ISO 8601); флаг — да/нет", moments)
        box.put("cascade.conf", "CLIENT_IF=\"awg0\"\nCLIENT_SUBNET=\"172.16.17.0/24\"\nEXITS=()\n")
        code, r = ask("POST", "/v1/apply")
        ok(code == 400 and r == {"ok": False, "error": "в cascade.conf нет ни одного выхода"}, "настройки без выходов: отказ с причиной, сервис жив", [code, r])
        ok(ask("GET", "/v1/health")[0] == 200, "после отказа сервис отвечает")
    finally:
        stop(proc)
    ok(proc.returncode == 0, "по SIGTERM завершается сам, код 0", proc.returncode)
    os.remove(os.path.join(box.root, "token"))
    r = subprocess.run([sys.executable, "-m", "awgscale.watch"], env=env, capture_output=True, text=True, timeout=20)
    ok(r.returncode != 0 and not box.calls()[len(calls):] or r.returncode != 0, "без файла токена не запускается вовсе", r.returncode)


def t_schedule(box):
    print("\n— проходы по расписанию и по событию ядра")
    world = ({"cascade.conf": conf(EXITS3[:2]), "ru.zone": RU, "pins.map": ""}, {},
             {"links": ["awgk1", "awgk2"], "alive": ["10.77.1.2"], "peers": peers(3), "sets": {"ru": ["x"], "cx_1": [], "cx_3": []}})
    box.put("token", "t0ken\n", box.root)
    pings = lambda: len([c for c in box.calls() if c["tool"] == "ping"])
    passes = lambda: len([c for c in box.calls() if c["tool"] == "ping" and "10.77.1.2" in c["argv"]])       # one ping of the exit that answers = one pass

    # the probe is a job on an interval: nobody asks, and the passes come
    box.world(*world)
    port = free_port()
    env = dict(box.env, WATCH_PORT=str(port), WATCH_TOKEN_FILE=os.path.join(box.root, "token"), WATCH_PROBE_SECONDS="1")
    proc = subprocess.Popen([sys.executable, "-m", "awgscale.watch"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ask = asker(port)
    try:
        first = started(ask)
        time.sleep(3.6)
        code, st = ask("GET", "/v1/status")
        ok(first[0] == 200 and 4 <= passes() <= 6 and passes() - st["watch"]["ticks"] in (0, 1) and st["watch"]["tick_failed"] == 0,
           "проба выходов идёт по расписанию: за 3,6 секунды при интервале в секунду — 4–5 проходов, без единого запроса", [passes(), st.get("watch")])
        ok(st["watch"]["last_tick"] > first[1]["last_tick"] and [(e["index"], e["state"]) for e in st["exits"]] == [(1, "up"), (3, "down")],
           "проходы продолжаются: молчащий выход после второй пробы подряд — down", st["exits"])
    finally:
        stop(proc)

    # a tunnel that has just come up is probed at once: the kernel says so (ip monitor link), the interval is not waited for
    events = os.path.join(box.root, "link-events")
    monitor = os.path.join(box.root, "ip-monitor")
    with open(monitor, "w") as f:
        f.write(f"""#!{sys.executable}
import sys, time
assert sys.argv[1:] == ["-o", "monitor", "link"], sys.argv
open({events!r}, "a").close()
with open({events!r}) as lines:
    lines.seek(0, 2)
    while True:
        line = lines.readline()
        if line:
            sys.stdout.write(line)
            sys.stdout.flush()
        else:
            time.sleep(0.05)
""")
    os.chmod(monitor, 0o755)

    def event(line):
        with open(events, "a") as f:
            f.write(line + "\n")
    box.world(*world)
    port = free_port()
    env = dict(box.env, WATCH_PORT=str(port), WATCH_TOKEN_FILE=os.path.join(box.root, "token"), WATCH_PROBE_SECONDS="120", WATCH_IP=monitor, WATCH_LINK_SETTLE_SECONDS="0.2")
    proc = subprocess.Popen([sys.executable, "-m", "awgscale.watch"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ask = asker(port)
    try:
        started(ask)
        time.sleep(1.5)
        ok(passes() == 1, "между проходами сервис ничего не опрашивает (интервал две минуты — один проход при старте)", passes())
        for line in ("Deleted 7: awgk2: <POINTOPOINT,NOARP> mtu 1280 qdisc noop state DOWN", "8: awgk2: <POINTOPOINT,NOARP> mtu 1280 qdisc noop state DOWN",
                     "3: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500", "9: awgk9: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280"):
            event(line)
        time.sleep(1.5)
        ok(passes() == 1, "интерфейс удалён, ещё не поднят, или это не туннель каскада — пробы нет", passes())
        event("8: awgk2: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280 qdisc noqueue state UNKNOWN group default qlen 1000")
        time.sleep(1.5)
        ok(passes() == 2, "связь с выходом поднялась — проба сразу, не дожидаясь расписания", passes())
        event("5: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280 qdisc noqueue state UNKNOWN")
        time.sleep(1.5)
        ok(passes() == 3 and ask("GET", "/v1/status")[1]["watch"]["ticks"] == 3, "поднялся туннель пользователей — тоже проход сразу", passes())
    finally:
        stop(proc)
    ok(proc.returncode == 0, "и с подпиской на события завершается по SIGTERM сам, код 0", proc.returncode)
    os.remove(os.path.join(box.root, "token"))


def main():
    box = Box()
    golden = Golden()
    try:
        t_apply(box, golden)
        t_tick(box, golden)
        t_rules(box, golden)
        t_scoped(box)
        golden.write()
        t_http(box)
        t_schedule(box)
    finally:
        box.close()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
