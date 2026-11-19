#!/usr/bin/env python3
"""What the page receives from /api/state, and what Prometheus reads from
/metrics, byte for byte the same as recorded (tests/panel_state_golden.json).

The panel server's application runs in this process (FastAPI's TestClient)
with a made-up agent that answers a scripted run of looks: none yet, failures
of every kind before and after good looks, good looks that grow, a look that
carries fields nobody knows yet, numbers of the "wrong" kind, keys the panel
adds itself. The clock is frozen at each step, so the bytes depend on nothing
else. The recording was made by the server as it was before its state became
models; it is never re-recorded to make a change pass — it was recorded anew
once, on purpose, when the state stopped listing the users (they are counted
in "users"; a page of them is /api/clients, checked at the end).

    uv run python tests/test_panel_state.py
    RECORD=1 uv run python tests/test_panel_state.py      # write the recording anew
One line per check, the last line is "N of M checks passed".
"""
import asyncio
import copy
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "panel_state_golden.json")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fastapi.testclient import TestClient  # noqa: E402

from awgscale.core.clock import utc_now  # noqa: E402
from awgscale.panel import metrics, state  # noqa: E402
from awgscale.panel.app import app  # noqa: E402
from awgscale.panel.deps import require_session  # noqa: E402
from awgscale.panel.factory import get_state, get_web_files  # noqa: E402
from awgscale.panel.looks import PanelState  # noqa: E402
from awgscale.panel.settings import PanelSettings  # noqa: E402

PASS = FAIL = 0
T0 = 1794690000                      # the agent's clock at the first good look; whole seconds, as it sends them


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:900])


# ── the agent's answers ──────────────────────────────────────────────────────

def entry():
    return {"hostname": "entry", "load1": 0.31, "cpus": 2, "mem_total": 2014359552, "mem_used": 905224192, "uptime": 170042, "disk_pct": 41,
            "wan_if": "eth0", "wan_rx": 100000000000, "wan_tx": 90000000000, "listen_port": 58728, "client_if_up": True}


def exit_(index, name, iface_rx, iface_tx, clients, state_="up", **more):
    """An exit as `state` has it: how many users are on it, never who."""
    shown = {"index": index, "name": name, "iface": f"awgk{index}", "state": state_, "handshake": T0 - 30, "rx": 10 ** 9, "tx": 10 ** 10,
             "probe_ms": 47.25, "changed": 1794600000.123456, "weight": 2, "endpoint": f"198.51.100.{index}:47211",
             "iface_rx": iface_rx, "iface_tx": iface_tx, "users": len(clients), "online": len(clients) // 2}
    shown.update(more)
    return shown


def client(name, octet, handshake, rx, tx, live=True, exit_index=1, **more):
    """A user as the agent knows them: what `traffic` gives of them, and what `state` counts."""
    shown = {"name": name, "suspended": False, "ip": f"172.16.17.{octet}", "expires_at": None, "endpoint": f"203.0.113.{octet}:51820" if live else None,
             "handshake": handshake, "rx": rx, "tx": tx, "live": live, "exit": exit_index, "pin": None}
    shown.update(more)
    return shown


def dns():
    return {"ok": True, "queries": 48211, "blocked": 7990, "safe": 3, "avg_ms": 11.2,
            "top_blocked": [{"domain": "ads.example", "count": 900}], "top_clients": [{"ip": "172.16.17.3", "count": 9000}],
            "top_sites": [{"name": "Яндекс", "count": 5000}], "top_sites_total": 20000, "top_exit": [{"name": "YouTube", "count": 4000}], "top_exit_total": 9000,
            "top_direct": [{"name": "Яндекс", "count": 5000}], "top_direct_total": 11000, "top_sites_hours": 24}


def counted(now, clients):
    """The users as `state` has them: counted, never listed."""
    online = sum(1 for each in clients if each["handshake"] and now - each["handshake"] < 180)
    return {"users": len(clients), "online": online, "paused": 0, "live": sum(1 for each in clients if each["live"]),
            "never": sum(1 for each in clients if not each["handshake"]), "expiring": sum(1 for each in clients if each["expires_at"]),
            "rx": sum(each["rx"] for each in clients), "tx": sum(each["tx"] for each in clients),
            "down_bps": 1600.0 * len(clients), "up_bps": 400.0 * len(clients), "groups": [{"id": "g00001", "users": len(clients), "online": online}],
            "ungrouped": {"users": 0, "online": 0}, "exits": [{"index": 1, "users": len(clients), "online": online}]}


TRAFFIC = {}                          # id of a `state` answer → its users, for `traffic` asked at that look


def good(now, clients, exits, **more):
    """A good answer of `state`, in the shape and order the agent sends it."""
    answer = {"ok": True, "time": now, "entry": entry(), "exits": exits,
              "watch": {"started": 1794680000.5, "applied": 1794680001.25, "last_tick": now - 3.5, "tick_seconds": 0.31, "ticks": 2000, "tick_failed": 2, "ok": True},
              "ru_networks": 8664,
              "routing": {"mode": "list", "udp_via_exit": True, "exit_nets": 11, "exit_domains": 13, "exit_patterns": 1, "direct_domains": 0, "exit_ips": 1336, "direct_ips": 0},
              "users": counted(now, clients), "dns": dns(), "ugroups_v": "0123456789ab", "sites_v": "abcdef012345", "spaces_v": "fedcba987654", "store_ok": True}
    answer.update(more)
    TRAFFIC[id(answer)] = clients
    return answer


FIRST = good(T0, [client("ann", 2, T0 - 20, 1000, 50000, expires_at=T0 + 86400), client("bob", 3, 0, 0, 0, live=False, exit_index=None),
                  client("carl", 4, T0 - 400, 500, 700, exit_index=3, pin=3)],
             [exit_(1, "exit1-nl", 10 ** 9, 2 * 10 ** 9, ["172.16.17.2"], sys={"load1": 0.05, "cpus": 1, "mem_total": 2 * 10 ** 9, "mem_used": 5 * 10 ** 8, "uptime": 300000, "disk_pct": 26}),
              exit_(3, "exit2-nl", 5 * 10 ** 8, 6 * 10 ** 8, ["172.16.17.4"], state_="down", probe_ms=None, changed=None)])
# five seconds on: the counters grew, the tunnel was restarted under carl (handshake 0: Prometheus knows when he was last seen),
# a new user came, an exit went to "unknown", and fields nobody knows yet appear at every level
SECOND = good(T0 + 5, [client("ann", 2, T0 - 2, 3000, 950000, expires_at=T0 + 86400, future_flag="x"), client("bob", 3, 0, 0, 0, live=False, exit_index=None),
                       client("carl", 4, 0, 900, 1700, exit_index=3, pin=3), client("dina", 5, T0 + 4, 10, 20, exit_index=1)],
              [exit_(1, "exit1-nl", 10 ** 9 + 4000000, 2 * 10 ** 9 + 125000, ["172.16.17.2", "172.16.17.5"], future={"a": [1, 2.5, None]},
                     sys={"load1": 1, "cpus": 1, "mem_total": 2 * 10 ** 9, "mem_used": 5 * 10 ** 8, "uptime": 300005, "disk_pct": 26, "temp": 51.5}),
               exit_(3, "exit2-nl", 5 * 10 ** 8, 6 * 10 ** 8 - 10, ["172.16.17.4"], state_="unknown", probe_ms=47)],
              future_top={"nested": {"deep": [1, "два", 3.0]}})
SECOND["entry"]["future_entry"] = True
SECOND["dns"]["future_dns"] = 1
SECOND["routing"]["future_routing"] = None
SECOND["watch"]["future_watch"] = "x"
# the counters grew again; a float where an int is usual, an int where a float is usual, a handshake of null
THIRD = good(T0 + 10, [client("ann", 2, T0 + 9, 3500.5, 1950000, expires_at=T0 + 86400), client("bob", 3, None, 0, 0, live=False, exit_index=None),
                       client("carl", 4, T0 + 8, 1900, 2700, exit_index=3, pin=3), client("dina", 5, T0 + 4, 10, 20, exit_index=1)],
             [exit_(1, "exit1-nl", 10 ** 9 + 8000000, 2 * 10 ** 9 + 250000, ["172.16.17.2", "172.16.17.5"])], ugroups_v=None, sites_v=None, spaces_v=None, store_ok=False)
THIRD["entry"]["load1"] = 1
THIRD["entry"]["disk_pct"] = None
THIRD["watch"] = {"ok": False}
# an answer already carrying what the panel adds (the stand-in server's own state answers look like this)
CARRYING = good(T0 + 20, [client("ann", 2, T0 + 19, 4000, 2950000, online=False, down_bps=1.5, up_bps=2.5)],
                [exit_(1, "exit1-nl", 10 ** 9 + 9000000, 2 * 10 ** 9 + 350000, ["172.16.17.2"], down_bps=0.0)],
                totals={"online": 7}, history=[{"t": 1, "down": 2, "up": 3}], ui="from-agent", server_time=5, fetched_at=7, stale=False)
# nobody at all
EMPTY = good(T0 + 25, [], [], dns={"ok": False, "error": "блокировщик не отвечает", "top_sites": [], "top_sites_total": 0, "top_exit": [], "top_exit_total": 0,
                                   "top_direct": [], "top_direct_total": 0, "top_sites_hours": 24})


class Socket(Exception):
    pass


# step name → (the clock, the agent's answer or what it raises; None: no look this step)
STEPS = [
    ("ещё ни одного опроса", 1794689990.25, None),
    ("первый опрос: агент отказал", 1794689991.5, {"ok": False, "error": "агент молчит"}),
    ("второй опрос: сокет недоступен", 1794689992.75, Socket("[Errno 111] Connection refused")),
    ("отказ без текста ошибки", 1794689993.125, {"ok": False}),
    ("отказ с полями сверх того", 1794689994.5, {"ok": False, "error": "занят", "time": 0, "retry": 3}),
    ("первый хороший опрос", T0 + 0.5, FIRST),
    ("второй хороший: скорости, восстановленная связь, новые поля", T0 + 5.25, SECOND),
    ("опрос не удался: прежние данные устарели", T0 + 6.5, {"ok": False, "error": "агент молчит"}),
    ("и снова: сокет", T0 + 7.75, Socket("timed out")),
    ("опрос без ошибки: снова устарело", T0 + 8.0, {"ok": False}),
    ("третий хороший: числа не того вида, хранилище недоступно", T0 + 10.125, THIRD),
    ("ответ ok без пользователей", T0 + 12.5, {"ok": True, "time": T0 + 12, "exits": []}),
    ("ответ ok без времени", T0 + 13.5, {"ok": True, "users": counted(T0, []), "exits": []}),
    ("ответ ok без выходов", T0 + 14.5, {"ok": True, "time": T0 + 14, "users": counted(T0 + 14, [client("ann", 2, T0, 1, 1)])}),
    ("ответ уже несёт то, что добавляет панель", T0 + 20.5, CARRYING),
    ("никого нет", T0 + 25.5, EMPTY),
    ("никого нет, и снова", T0 + 30.5, copy.deepcopy(EMPTY) | {"time": T0 + 30}),
    ("через минуту без опросов: метрики несвежие", T0 + 95.5, None),
]


# ── the server around a made-up agent ────────────────────────────────────────

class Agent:
    def __init__(self):
        self.answer = None

    async def call(self, *args, timeout=0.0, secret=None):
        if isinstance(self.answer, Exception):
            raise self.answer
        if args[0] == "traffic":
            clients = TRAFFIC.get(id(self.answer), [])
            return {"ok": True, "time": self.answer.get("time", 0), "users": [[each["name"], each["rx"], each["tx"]] for each in clients if each["live"]]}
        return copy.deepcopy(self.answer)


class Seen:
    """Prometheus: when each user last moved traffic."""

    async def last_seen(self, now, ask=None):
        return {"carl": datetime.fromtimestamp(T0 - 300, UTC), "bob": datetime.fromtimestamp(T0 + 10 ** 6, UTC)}


class Files:
    def build_id(self):
        return "b1d2"


CLOCK = [datetime.fromtimestamp(0, UTC)]


def clock():
    return CLOCK[0]


def freeze(moment):
    CLOCK[0] = datetime.fromtimestamp(moment, UTC)


def run():
    """Every step → {"step", "state": the body of /api/state, "metrics": the body of /metrics}."""
    agent = Agent()
    board = state.StateBoard(PanelSettings(), agent, Seen(), clock)
    app.dependency_overrides[utc_now] = clock
    metrics.metrics_app.dependency_overrides[utc_now] = clock
    app.dependency_overrides[require_session] = lambda: None
    app.dependency_overrides[get_state] = lambda: board
    app.dependency_overrides[get_web_files] = lambda: Files()
    metrics.metrics_app.dependency_overrides[get_state] = lambda: board
    page = TestClient(app)
    prometheus = TestClient(metrics.metrics_app)
    loop = asyncio.new_event_loop()
    seen = []
    for name, moment, answer in STEPS:
        freeze(moment)
        if answer is not None:
            agent.answer = answer
            loop.run_until_complete(board.look())
        body = page.get("/api/state")
        text = prometheus.get("/metrics")
        seen.append({"step": name, "status": [body.status_code, text.status_code], "state": body.content.decode(), "metrics": text.content.decode()})
    return seen


def load_stub():
    spec = importlib.util.spec_from_file_location("panel_stub", os.path.join(HERE, "stub.py"))
    stub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stub)
    return stub


def main():
    seen = run()
    if os.environ.get("RECORD"):
        with open(GOLDEN, "w") as file:
            json.dump(seen, file, ensure_ascii=False, indent=1)
            file.write("\n")
        print(f"recorded {len(seen)} steps in {GOLDEN}")
    with open(GOLDEN) as file:
        recorded = json.load(file)
    print("— /api/state и /metrics — байт в байт как записано")
    ok([step["step"] for step in seen] == [step["step"] for step in recorded], "те же шаги, что в записи")
    for now, then in zip(seen, recorded):
        ok(now["status"] == then["status"] == [200, 200], f"{now['step']}: 200 и 200", now["status"])
        ok(now["state"] == then["state"], f"{now['step']}: /api/state — те же байты", (now["state"], then["state"]))
        ok(now["metrics"] == then["metrics"], f"{now['step']}: /metrics — те же байты", (now["metrics"], then["metrics"]))
    print("\n— ответы, которые панель передаёт как есть")
    stub = load_stub()
    # not here: THIRD (its handshake of null comes back as 0, "never") and CARRYING (the panel's own fields come back
    # as the panel's kinds: a chart's int as a float) — what the page receives of those is in the recording above
    relayed_as_sent = [(name, answer) for name, _, answer in STEPS if isinstance(answer, dict) and answer is not THIRD and answer is not CARRYING]
    for name, answer in (("состояние заглушки страницы (tests/stub.py)", stub.state()), *relayed_as_sent):
        sent = json.dumps(answer, ensure_ascii=False)
        relayed = json.dumps(PanelState.model_validate(json.loads(sent)).wire(), ensure_ascii=False)
        ok(relayed == sent, f"{name}: принят и отдан дальше байт в байт", (relayed, sent))
    t_same_look()
    t_clients()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


def t_same_look():
    print("\n— тот же взгляд агента прочитан дважды (агент смотрит так же часто, как его спрашивают)")
    agent = Agent()
    board = state.StateBoard(PanelSettings(), agent, Seen(), clock)
    loop = asyncio.new_event_loop()
    agent.answer = FIRST
    freeze(T0 + 0.5)
    loop.run_until_complete(board.look())
    agent.answer = SECOND
    freeze(T0 + 5.25)
    loop.run_until_complete(board.look())
    before = board.snapshot()
    freeze(T0 + 6.25)
    loop.run_until_complete(board.look())
    again = board.snapshot()
    ok([exit_.down_bps for exit_ in again.exits] == [exit_.down_bps for exit_ in before.exits] and again.exits[0].down_bps > 0
       and again.totals.via_exits_down_bps == before.totals.via_exits_down_bps and len(again.history) == len(before.history) == 2,
       "скорости выходов те же, точка на график не добавлена", [[exit_.down_bps for exit_ in again.exits], len(again.history)])


def t_clients():
    print("\n— /api/clients: страница пользователей от агента")
    asked = []

    class PageAgent(Agent):
        async def call(self, *args, timeout=0.0, secret=None):
            asked.append(args)
            if args[0] == "clients":
                return {"ok": True, "time": T0, "total": 3, "offset": 0, "clients": [client("ann", 2, T0 - 20, 1, 2, online=True), client("carl", 4, 0, 1, 2), client("bob", 3, 0, 0, 0)]}
            return await super().call(*args, timeout=timeout, secret=secret)

    board = state.StateBoard(PanelSettings(), PageAgent(), Seen(), clock)
    app.dependency_overrides[get_state] = lambda: board
    freeze(T0 + 1)
    page = TestClient(app)
    answer = page.get("/api/clients", params={"q": "a", "group": "none", "status": "online", "sort": "traffic", "desc": "true", "offset": 50, "limit": 100, "names": ["ann", "bob"]})
    body = answer.json()
    query = json.loads(__import__("base64").b64decode(asked[-1][1]))
    ok(answer.status_code == 200 and query == {"q": "a", "group": "none", "status": "online", "sort": "traffic", "desc": True, "offset": 50, "limit": 100, "names": ["ann", "bob"]},
       "запрос страницы дошёл до агента как есть", query)
    people = {each["name"]: each for each in body["clients"]}
    ok(body["total"] == 3 and people["ann"]["handshake"] == T0 - 20 and people["carl"]["handshake"] == T0 - 300 and people["bob"]["handshake"] == T0,
       "страница передана; кого туннель забыл после перезапуска — время последнего трафика из Prometheus (из будущего обрезано)", body)
    count = len(asked)
    for params in ({"limit": 101}, {"status": "sleeping"}, {"group": "-x"}, {"names": "-x"}, {"sort": "random"}, {"offset": -1}, {"unknown": 1}):
        ok(page.get("/api/clients", params=params).status_code == 422, f"запрос не того вида — 422, до агента не доходит: {params}")
    ok(len(asked) == count, "агента не спрашивали ни разу")
    ok(page.get("/api/clients").status_code == 200 and json.loads(__import__("base64").b64decode(asked[-1][1])) == {}, "без параметров — первая страница по умолчанию")


if __name__ == "__main__":
    sys.exit(main())
