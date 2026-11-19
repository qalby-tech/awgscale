#!/usr/bin/env python3
"""The Telegram bot (src/awgscale/bot) against a stand-in Telegram Bot API (a
local HTTP server that records every call and hands out updates) and a stand-in
agent (a unix socket that answers from a table). Nothing reaches the real
Telegram or any server.

    uv run python tests/bot.py

The texts, the counters and the alarms are also compared with what the bot
has always written for the same answers of the agent (tests/golden/bot.json):
word for word, number for number.
One line per check, the last line is "N of M checks passed".
"""
import asyncio
import base64
import datetime
import importlib.util
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="bot-")
TOKEN = "123456:TEST-token-that-must-never-be-logged"
ADMIN = 777
STRANGER = 4242
NOW = 1_800_000_000                     # the server's clock in the made-up states (a Sunday, 05:20 Moscow time)

PASS = FAIL = 0


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:700])


# ── the stand-in Telegram ────────────────────────────────────────────────────
class FakeTelegram:
    def __init__(self):
        self.calls = []                 # {"method", "token", fields…, "files": {field: (name, bytes)}}
        self.updates = []               # handed out by getUpdates, once each
        self.fail = set()               # methods answered with a refusal
        self.cut = set()                # methods whose connection is dropped without an answer
        self.lock = threading.Lock()

    def sent(self, method=None):
        with self.lock:
            return [c for c in self.calls if method is None or c["method"] == method]

    def texts(self):
        return [c.get("text", c.get("caption")) for c in self.sent() if c["method"] in ("sendMessage", "sendPhoto", "sendDocument", "editMessageText")]

    def clear(self):
        with self.lock:
            self.calls.clear()


TG = FakeTelegram()


class TelegramHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        token, _, method = self.path[len("/bot"):].rpartition("/")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        ctype = self.headers.get("Content-Type", "")
        call = {"method": method, "token": token, "files": {}}
        if ctype.startswith("application/json"):
            call.update(json.loads(body or b"{}"))
        elif ctype.startswith("multipart/form-data"):
            for part in BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body).iter_parts():
                name = part.get_param("name", header="content-disposition")
                if part.get_filename():
                    call["files"][name] = (part.get_filename(), part.get_payload(decode=True))
                else:
                    call[name] = part.get_payload(decode=True).decode()
        with TG.lock:
            TG.calls.append(call)
        if method in TG.cut:
            self.connection.close()
            return
        if method in TG.fail:
            return self.answer(400, {"ok": False, "error_code": 400, "description": "Bad Request: the stand-in says no"})
        if method == "getMe":
            return self.answer(200, {"ok": True, "result": {"id": 1, "is_bot": True, "first_name": "Cascade", "username": "cascade_bot"}})
        if method == "getUpdates":
            deadline = time.time() + min(0.3, float(call.get("timeout", 0)))
            while True:
                with TG.lock:
                    ready = [u for u in TG.updates if u["update_id"] >= call.get("offset", 0)]
                    TG.updates[:] = [u for u in TG.updates if u not in ready]
                if ready or time.time() >= deadline:
                    return self.answer(200, {"ok": True, "result": ready})
                time.sleep(0.02)
        self.answer(200, {"ok": True, "result": True})

    def answer(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):        # a bot that was stopped mid-request is not news
        pass


TG_SERVER = QuietServer(("127.0.0.1", 0), TelegramHandler)
threading.Thread(target=TG_SERVER.serve_forever, daemon=True).start()


# ── the stand-in agent ───────────────────────────────────────────────────────
class FakeAgent:
    def __init__(self):
        self.asked = []                 # every request as it came: {"args": [...], "by": "bot"}
        self.answers = {}               # first argument → the answer, or a function of the arguments
        self.garbage = False            # answers with something that is not JSON

    def commands(self):
        return [r["args"] for r in self.asked]


AG = FakeAgent()
AGENT_SOCKET = os.path.join(TMP, "agent.sock")


class AgentHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.read().decode().strip() or "{}")
        AG.asked.append(request)
        if AG.garbage:
            self.wfile.write(b"this is not json\n")
            return
        answer = AG.answers.get(request["args"][0], {"ok": False, "error": "неизвестная команда"})
        if request["args"][0] in WORLD_COMMANDS:
            answer = WORLD.answer(request["args"], AG.answers.get("state"))
        if callable(answer):
            answer = answer(request["args"])
        self.wfile.write(b"a line of noise the agent's tools may print first\n" + json.dumps(answer, ensure_ascii=False).encode() + b"\n")


class AgentServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


threading.Thread(target=AgentServer(AGENT_SOCKET, AgentHandler).serve_forever, daemon=True).start()

# ── the settings the bot is given ────────────────────────────────────────────
TOKEN_FILE = os.path.join(TMP, "token")
with open(TOKEN_FILE, "w") as f:
    f.write(TOKEN + "\n")
DATA = os.path.join(TMP, "data")
os.mkdir(DATA)
ENV = {"BOT_TOKEN_FILE": TOKEN_FILE, "BOT_ADMIN_ID": str(ADMIN), "BOT_DOMAIN": "vpn.example", "BOT_DATA_DIR": DATA, "PANEL_AGENT_SOCKET": AGENT_SOCKET,
       "REPORT_HOUR": "9", "REPORT_UTC_OFFSET": "3", "BOT_API_URL": f"http://127.0.0.1:{TG_SERVER.server_address[1]}", "BOT_POLL_SECONDS": "1", "BOT_RETRY_SECONDS": "0.2"}
os.environ.update(ENV)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import httpx  # noqa: E402

from awgscale.bot import format as new_format, texts  # noqa: E402
from awgscale.bot.agent import Agent  # noqa: E402
from awgscale.bot.commands import MENU, Commands, invite_request  # noqa: E402
from awgscale.bot.models import Bucket, CallbackQuery, Message, State, Stats  # noqa: E402
from awgscale.bot.settings import BotSettings, get_settings  # noqa: E402
from awgscale.bot.stats import Sampler, StatsFile  # noqa: E402
from awgscale.bot.telegram import Admin, Telegram  # noqa: E402
from awgscale.bot.watch import Alarm, Watch  # noqa: E402
from datetime import datetime as Moment, timedelta as Span, timezone as Zone  # noqa: E402

LOOP = asyncio.new_event_loop()
ZONE = datetime.timezone(datetime.timedelta(hours=3))
GOLDEN = json.load(open(os.path.join(HERE, "golden", "bot.json")))


def run(work):
    return LOOP.run_until_complete(work)


class Bot:
    """The parts of the bot as python -m awgscale.bot puts them together — made anew for every "start of the process"."""

    def __init__(self, **changed):
        self.settings = BotSettings(**changed)
        self.telegram = Telegram(self.settings, httpx.AsyncClient())
        self.admin = Admin(self.settings, self.telegram)
        self.agent = Agent(self.settings)
        self.sampler = Sampler(self.settings, self.agent, self.admin, StatsFile(self.settings.stats_file))
        self.watch = Watch(self.settings, self.agent, self.admin)
        self.commands = Commands(self.settings, self.telegram, self.admin, self.agent, self.sampler)
        self.commands.named("cascade_bot")

    def say(self, text, sender=ADMIN, chat=ADMIN, caption=None):
        """A message written to the bot → what it sent in answer (the calls it made to Telegram)."""
        TG.clear()
        message = {"message_id": 5, "date": NOW, "chat": {"id": chat, "type": "private"}, "from": {"id": sender, "is_bot": False, "first_name": "A"}}
        if text is not None:
            message["text"] = text
        if caption is not None:
            message.update(caption=caption, photo=[{"file_id": "x", "file_unique_id": "y", "width": 1, "height": 1}])
        run(self.commands.on_message(Message.model_validate(message)))
        return TG.sent()

    def press(self, data, sender=ADMIN):
        TG.clear()
        callback = {"id": "cb1", "from": {"id": sender, "is_bot": False, "first_name": "A"}, "data": data, "chat_instance": "x",
                    "message": {"message_id": 9, "date": NOW, "chat": {"id": ADMIN, "type": "private"}}}
        run(self.commands.on_callback(CallbackQuery.model_validate(callback)))
        return TG.sent()


# ── the agent's world: what it is asked about the users ──────────────────────
# The made-up states below are written as the agent once answered: every user listed. The stand-in agent
# answers `state` with them counted, and `clients` and `day` from them, through the agent's own code
# (awgscale.agent.state): the bot never sees the list, and its texts are those recorded for the same users.
from awgscale.agent.payloads import decode  # noqa: E402
from awgscale.agent.state.clients import ClientsIndex, ClientsQuery, LookInput, make_rows  # noqa: E402
from awgscale.agent.state.ledger import Ledger  # noqa: E402
from awgscale.agent.tunnel import Peer  # noqa: E402
from awgscale.agent.users.models import KnownUser  # noqa: E402
from awgscale.bot.models import ClientsPage, DayTraffic  # noqa: E402
from awgscale.bot.texts import USERS_SHOWN, Attention  # noqa: E402

WORLD_COMMANDS = ("state", "clients", "day")


def address_of(position):
    """The made-up users share an address: each is given its own, in the order they are listed."""
    return f"10.20.{position // 250}.{position % 250 + 2}"


def rows_of(s):
    now = moment(s["time"])
    users = []
    peers = {}
    on_exit = {}
    for position, each in enumerate(s["clients"]):
        ip = address_of(position)
        users.append(KnownUser(name=each["name"], ip=ip, expires_at=moment(each.get("expires_at"))))
        peers[ip] = Peer(address=ip, endpoint=each.get("endpoint"), handshake=moment(each.get("handshake")), received=each.get("rx", 0), sent=each.get("tx", 0))
        if each.get("exit") is not None:
            on_exit[ip] = each["exit"]
    look = LookInput(now=now, users=users, peers=peers, on_exit=on_exit, paused=frozenset(), pins={}, group_of={}, group_ids=(), online_for=Span(seconds=180))
    return make_rows(look, {}, 0.0)


def counted(s):
    """A state as the agent answers it now: the users counted, each exit with how many are on it."""
    if not isinstance(s, dict) or "clients" not in s:
        return s
    shown = {key: value for key, value in s.items() if key != "clients"}
    rows = rows_of(s)
    shown["users"] = {"users": len(rows), "online": sum(1 for row in rows if row.online)}
    shown["exits"] = [{**{key: value for key, value in exit_.items() if key != "clients"}, "users": len(exit_.get("clients", []))} for exit_ in s.get("exits", [])]
    return shown


class World:
    """The agent's side: the last look at a state, and the day's traffic folded in at every look."""

    def __init__(self):
        self.ledger = Ledger(type(TMP_PATH)(TMP) / "day.json")
        self.before = {}
        self.index = None
        self.time = None

    def show(self, s):
        """The users of this state are who `clients` answers about; nothing is folded."""
        self.index = ClientsIndex(rows_of(s), moment(s["time"]))
        self.time = moment(s["time"])

    def look(self, s):
        rows = rows_of(s)
        self.ledger.fold(rows, self.before)
        self.before = {row.name: row for row in rows}
        self.show(s)

    def answer(self, args, s):
        if not isinstance(s, dict) or "clients" not in s:
            return s if isinstance(s, dict) else {"ok": False, "error": "неизвестная команда"}
        if args[0] == "state":
            self.look(s)
            return counted(s)
        if args[0] == "clients":
            return self.index.page(ClientsQuery.model_validate(decode(args[1]))).wire()
        return self.ledger.answer(moment(int(args[1])), self.time).wire()


TMP_PATH = __import__("pathlib").Path(TMP)
WORLD = World()


def page_of(s, **query):
    """What `clients` answers about the users of this state."""
    WORLD.show(s)
    return ClientsPage.model_validate(WORLD.index.page(ClientsQuery(**query)).wire())


# ── made-up answers of the agent ─────────────────────────────────────────────
def client(name, seen=20, rx=1000, tx=5000, exit=1, expires=None):
    return {"name": name, "ip": "172.16.17.9", "suspended": False, "endpoint": None, "handshake": NOW - seen if seen is not None else 0,
            "rx": rx, "tx": tx, "exit": exit, "pin": None, "online": seen is not None and seen < 180, "expires_at": NOW + expires if expires else None}


def exit_(index, name, state="up", clients=()):
    return {"index": index, "name": name, "state": state, "handshake": NOW - 30 if state == "up" else NOW - 900, "clients": list(clients), "weight": 1}


def STATE(**changed):
    s = {"ok": True, "time": NOW, "ui": "t",
         "entry": {"hostname": "entry", "load1": 0.3, "cpus": 2, "mem_total": 2 * 10 ** 9, "mem_used": 9 * 10 ** 8, "uptime": 170000, "disk_pct": 41, "client_if_up": True, "listen_port": 5},
         "exits": [exit_(1, "exit1-nl", clients=["172.16.17.2", "172.16.17.3"]), exit_(3, "exit2-nl", clients=["172.16.17.4"])],
         "clients": [client("anna_phone", 20, 4 * 10 ** 9, 6 * 10 ** 9), client("tv <b>&", 400, 9 * 10 ** 8, 10 ** 11, 3, expires=2 * 86400),
                     client("never", None, 0, 0, None), client("old_one", 9 * 86400, 5, 7, 7), client("soon", 100, 10, 20, 1, expires=3600)],
         "dns": {"ok": True, "queries": 48211, "blocked": 7990, "top_blocked": [{"domain": "ads.example", "count": 900}, {"domain": "t.example", "count": 4}],
                 "top_exit": [{"name": "YouTube", "count": 4000}, {"name": "Zero", "count": 0}], "top_direct": [{"name": "Яндекс", "count": 5000}]},
         "routing": {"mode": "list", "exit_domains": 13, "exit_nets": 11}}
    for key, value in changed.items():
        if isinstance(value, dict) and isinstance(s.get(key), dict):
            s[key] = {**s[key], **value}
        else:
            s[key] = value
    return s


state = STATE

STATES = {
    "as usual": state(),
    "an exit is down": state(exits=[exit_(1, "exit1-nl", "down"), exit_(3, "exit2-nl", clients=["a", "b", "c"])]),
    "an exit has no verdict yet": state(exits=[exit_(1, "exit1-nl", "unknown"), exit_(3, "exit2-nl")]),
    "nobody at all": state(clients=[], exits=[]),
    "the blocker is silent": state(dns={"ok": False}),
    "the blocker saw nothing": state(dns={"ok": True, "queries": 0, "blocked": 0, "top_blocked": [], "top_exit": [], "top_direct": []}),
    "everything foreign goes abroad": state(routing={"mode": "geo"}),
    "the tunnel is down, no disk figure, whole numbers": state(entry={"client_if_up": False, "disk_pct": None, "load1": 5, "cpus": 4}),
    "no dns, no routing in the answer": {k: v for k, v in state().items() if k not in ("dns", "routing")},
    "twenty who never connected": state(clients=[client(f"u{i}", None, 0, 0, None) for i in range(20)]),
    "a routing mode the bot has not heard of": state(routing={"mode": "something-new"}),
    # every key the agent puts into its answer (cmd_state, system_info, dns_stats, the watch service's exits)
    "as the real agent builds it": {
        "ok": True, "time": NOW,
        "entry": {"hostname": "entry", "load1": 0.07, "cpus": 2, "mem_total": 2063597568, "mem_used": 911466496, "uptime": 68345, "disk_pct": 41, "wan_if": "eth0",
                  "wan_rx": 10 ** 11, "wan_tx": 10 ** 11, "listen_port": None, "client_if_up": True},
        "exits": [{"index": 1, "name": "exit1-nl", "iface": "awgk1", "weight": 2, "endpoint": "198.51.100.38:47211", "state": "up", "handshake": NOW - 30, "rx": 10 ** 9, "tx": 10 ** 10,
                   "iface_rx": 1, "iface_tx": 2, "clients": ["172.16.17.2"], "down_bps": 12.5, "up_bps": 0.0, "sys": {"load1": 0.05, "cpus": 1, "mem_total": 1, "mem_used": 1, "uptime": 3, "disk_pct": 26}},
                  {"index": 3, "name": "exit2-nl", "iface": "awgk2", "weight": 1, "state": "down", "handshake": 0, "clients": []}],
        "watch": {"ok": True, "passes": 5}, "ru_networks": 8664,
        "routing": {"mode": "list", "udp_via_exit": True, "exit_nets": 11, "exit_domains": 13, "exit_patterns": 1, "direct_domains": 0, "exit_ips": 1336, "direct_ips": 0},
        "clients": [{"name": "anna_phone", "suspended": False, "ip": "172.16.17.2", "expires_at": None, "endpoint": "203.0.113.2:50002", "handshake": NOW - 20, "rx": 41, "tx": 57,
                     "live": True, "exit": 1, "pin": None},
                    {"name": "guest", "suspended": True, "ip": "172.16.17.11", "expires_at": NOW + 5000, "endpoint": None, "handshake": 0, "rx": 0, "tx": 0, "live": False, "exit": None, "pin": 3}],
        "dns": {"ok": True, "queries": 48211, "blocked": 7990, "safe": 3, "avg_ms": 11.2, "top_blocked": [{"domain": "ads.example", "count": 900}], "top_clients": [{"ip": "172.16.17.3", "count": 9000}],
                "top_sites": [{"name": "Яндекс", "count": 5000}], "top_sites_total": 20000, "top_exit": [{"name": "YouTube", "count": 4000}], "top_exit_total": 9000, "top_direct": [], "top_direct_total": 0},
        "ugroups_v": "a", "sites_v": None, "spaces_v": "c", "store_ok": True},
}


def new_state(s):
    return State.model_validate(counted(s))


def moment(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.UTC) if ts else None


# ── the checks ───────────────────────────────────────────────────────────────
def t_texts():
    print("\n— тексты: слово в слово те, что бот писал всегда")
    same = [x for x in (0, 1, 99, 1023, 1024, 1536, 10 ** 5, 10 ** 8, 5 * 10 ** 9, 3 * 10 ** 12, 9 * 10 ** 15) if GOLDEN["sizes"][str(x)] != new_format.size_text(x)]
    ok(not same, "размеры", same)
    same = [x for x in (0, 59, 60, 3599, 3600, 86399, 200000) if GOLDEN["durations"][str(x)] != new_format.duration_text(x)]
    ok(not same, "длительности", same)
    same = [x for x in (0, 1, 59, 60, 3599, 3600, 86399, 86400, 10 ** 6) if GOLDEN["ago"][str(x)] != new_format.ago(moment(NOW - x), moment(NOW))]
    ok(not same and new_format.ago(None, moment(NOW)) == "никогда", "«сколько назад» и «никогда»", same)
    ok(GOLDEN["escaped"] == new_format.escaped("<a href='x'>&</a>"), "экранирование для HTML")
    for name, s in STATES.items():
        ok(GOLDEN["status"][name] == texts.status_text(new_state(s)), f"/status — {name}", texts.status_text(new_state(s)))
        ok(GOLDEN["users"][name] == texts.users_text(new_state(s), page_of(s, limit=USERS_SHOWN), ZONE), f"/users — {name}", texts.users_text(new_state(s), page_of(s, limit=USERS_SHOWN), ZONE))
    many = state(clients=[client(f"u{i:03d}", 20 * i) for i in range(45)])
    shown = texts.users_text(new_state(many), page_of(many, limit=USERS_SHOWN), ZONE)
    listed = [line.split("<code>", 1)[1].split("</code>", 1)[0] for line in shown.split("\n")[1:-1]]
    ok(shown.count("\n") == USERS_SHOWN + 1 and shown.endswith("\n… и ещё 5. Поиск: /users часть имени") and listed == [f"u{i:03d}" for i in range(USERS_SHOWN)],
       "/users — больше сорока: первые сорок по порядку адресов и сколько ещё", shown[-80:])
    ok(texts.users_text(new_state(many), page_of(many, q="u04", limit=USERS_SHOWN), ZONE, "u04").count("<code>u04") == 5
       and texts.users_text(new_state(many), page_of(many, q="zz", limit=USERS_SHOWN), ZONE, "zz") == "Никого не нашлось.", "/users часть имени — только найденные")
    spec = importlib.util.spec_from_file_location("panel_stub", os.path.join(HERE, "stub.py"))
    stub = importlib.util.module_from_spec(spec)
    argv = sys.argv
    sys.argv = argv[:1]
    with mock.patch("time.time", return_value=float(NOW)):      # the stand-in's made-up moments, at this suite's clock
        spec.loader.exec_module(stub)
        made = stub.state()
    sys.argv = argv
    page = ClientsPage.model_validate(stub.clients_page({"limit": str(USERS_SHOWN)}, ())[1])
    seen = [texts.status_text(State.model_validate(made)), texts.users_text(State.model_validate(made), page, ZONE)]
    ok(seen == [GOLDEN["stub"]["status"], GOLDEN["stub"]["users"]], "состояние и страница пользователей, которые отдаёт подставной сервер панели (tests/stub.py), читаются и дают те же тексты", seen)
    for text in ("", "3", "2 7", "2 7 Семья", "Семья и друзья", "0", "11", "1 31", "1 0", "5 Семья", "2 7 9 детей", "x 2", None):
        request = invite_request(text or "")
        ok(GOLDEN["invite"][repr(text)] == (None if request is None else list(request)), f"разбор /invite {text!r}", request)


def attention_of(s):
    return Attention(stale=page_of(s, status="stale", within=7 * 86400, limit=texts.STALE_SHOWN), soon=page_of(s, status="expiring", within=3 * 86400, limit=100))


def t_report():
    print("\n— сводка и счётчики: числа те, что бот считал всегда (трафик пользователей считает агент)")
    bot = Bot()
    bot.sampler.stats.bucket.since = moment(NOW - 7200)
    world = World()
    steps = [
        state(),
        state(clients=[client("anna_phone", 20, 4 * 10 ** 9 + 500, 6 * 10 ** 9 + 9000), client("tv <b>&", 30, 9 * 10 ** 8 + 1, 10 ** 11 + 2, 3), client("new_one", 10, 77, 88)]),
        state(clients=[client("anna_phone", 500, 100, 200), client("new_one", 10, 177, 1088)], exits=[exit_(1, "exit1-nl", "down"), exit_(3, "exit2-nl", clients=["a"])]),
        state(clients=[client("anna_phone", 5, 300, 900), client("new_one", 10, 177, 1088)], exits=[exit_(1, "exit1-nl", "down"), exit_(3, "exit2-nl", "unknown")]),
        state(clients=[client("anna_phone", 5, 300, 900)], exits=[exit_(1, "exit1-nl"), exit_(3, "exit2-nl")], time=NOW + 240),
    ]
    since = moment(NOW - 7200)
    world.show(steps[0])
    world.ledger.answer(since, moment(NOW))                  # the bot asks for its day at every sample: counted from the first
    for i, s in enumerate(steps):
        bot.sampler.fold(new_state(s))
        world.look(s)
        mine = json.loads(bot.sampler.stats.model_dump_json())
        expected = GOLDEN["steps"][i]
        users = expected["bucket"]["users"]
        day = world.ledger.answer(since, moment(s["time"]))
        top = sorted(users.items(), key=lambda item: item[1]["rx"] + item[1]["tx"], reverse=True)[:5]
        ok(mine["bucket"] == {key: value for key, value in expected["bucket"].items() if key != "users"} and mine["prev_exit_state"] == expected["prev_exit_state"]
           and (day.down, day.up, day.active) == (sum(u["tx"] for u in users.values()), sum(u["rx"] for u in users.values()), sum(1 for u in users.values() if u["seen"]))
           and all(name in users and (rx, tx) == (users[name]["rx"], users[name]["tx"]) for name, rx, tx in day.top)        # the order among equals is the agent's own
           and [rx + tx for name, rx, tx in day.top] == [u["rx"] + u["tx"] for name, u in top],
           f"шаг {i + 1}: счётчики совпали (трафик у агента, обнуление счётчика, новый и пропавший пользователь, простой выхода, сбои, пик)", [mine["bucket"], day])
    last = steps[-1]
    day = DayTraffic.model_validate(world.ledger.answer(since, moment(last["time"])).wire())
    for final in (False, True):
        mine, _ = texts.report_text(new_state(last), bot.sampler.stats.bucket, None, ZONE, final, day, attention_of(last))
        ok(mine == GOLDEN["report"]["final" if final else "so far"], f"сводка {'за сутки' if final else 'с начала периода'} — та же", mine)
    bot.sampler.stats.history = Stats.model_validate(GOLDEN["stats_file"]).history      # one day before: 2026-11-14
    mine, totals = texts.report_text(new_state(last), bot.sampler.stats.bucket, bot.sampler.stats.history[-1], ZONE, True, day, attention_of(last))
    ok(mine == GOLDEN["report"]["against the day before"] and "к прошлым суткам" in mine and totals.model_dump() == GOLDEN["report"]["totals"], "…и сравнение с прошлыми сутками, и итоги дня для истории", mine)
    for name, s in STATES.items():
        mine, _ = texts.report_text(new_state(s), bot.sampler.stats.bucket, None, ZONE, False, day, attention_of(s))
        ok(mine == GOLDEN["report"]["states"][name], f"сводка — {name}", mine)
    # the file of the counters as the bot has always kept it is read; the users' traffic in it is the agent's to count now
    kept = os.path.join(TMP, "stats-kept.json")
    with open(kept, "w") as f:
        json.dump(GOLDEN["stats_file"], f)
    taken = StatsFile(type(bot.settings.stats_file)(kept)).load()
    without_users = {key: value for key, value in GOLDEN["stats_file"].items() if key != "last"} | {"bucket": {key: value for key, value in GOLDEN["stats_file"]["bucket"].items() if key != "users"}}
    ok(json.loads(taken.model_dump_json()) == without_users, "файл счётчиков в прежнем виде читается (трафик пользователей считает агент)")
    StatsFile(bot.settings.stats_file).save(bot.sampler.stats)
    back = json.load(open(bot.settings.stats_file))
    ok(back == without_users and isinstance(back["bucket"]["since"], (int, float)), "…а записанный файл — прежнего вида (время числом), без счётчиков каждого пользователя", back["bucket"]["since"])
    os.unlink(bot.settings.stats_file)


def t_commands():
    print("\n— команды")
    bot = Bot()
    AG.answers["state"] = state()
    AG.asked.clear()
    sent = bot.say("/status")
    ok(len(sent) == 1 and sent[0]["method"] == "sendMessage" and sent[0]["chat_id"] == ADMIN and sent[0]["text"] == GOLDEN["status"]["as usual"]
       and sent[0]["parse_mode"] == "HTML" and sent[0]["link_preview_options"] == {"is_disabled": True} and sent[0]["token"] == TOKEN,
       "/status: состояние, в чат админа, HTML, без предпросмотра ссылок", sent)
    ok(AG.asked == [{"args": ["state"], "by": "bot"}], "агента спрашивают одной командой и подписываются «bot»", AG.asked)
    ok(bot.say("/users")[0]["text"] == GOLDEN["users"]["as usual"], "/users")
    ok(bot.say("/report")[0]["text"].startswith("📊 <b>Сводка с "), "/report: сводка за текущие сутки, не итоговая")
    for text in ("/help", "/start"):
        sent = bot.say(text)
        ok(len(sent) == 1 and sent[0]["text"] == GOLDEN["help"] and "в 9:00 по Москве" in sent[0]["text"], f"{text}: список команд, слово в слово")
    ok(bot.say("/status@cascade_bot")[0]["text"].startswith("<b>VPN-каскад</b>") and bot.say("/status@Cascade_Bot")[0]["text"].startswith("<b>VPN-каскад</b>"),
       "команда с именем этого бота — его команда")
    for text in ("/nothing", "/Status", "/status@other_bot", "/statusx", "/"):
        sent = bot.say(text)
        ok(len(sent) == 1 and sent[0]["text"] == "Не знаю такой команды. /help", f"{text} — «не знаю такой команды»", sent)
    AG.asked.clear()
    ok(bot.say("привет") == [] and bot.say(None) == [] and bot.say(" /status") == [] and AG.asked == [], "не команда (текст, картинка без текста) — молчание, агента не трогаем")
    ok(bot.say(None, caption="/status")[0]["text"].startswith("<b>VPN-каскад</b>") and bot.say(None, caption="/nothing") == [] and bot.say(None, caption="просто фото") == [],
       "команда в подписи под картинкой выполняется (как всегда); незнакомая подпись — молчание")

    print("\n— только админ, только личный чат")
    AG.asked.clear()
    for what, kw in (("чужой в личном чате", {"sender": STRANGER, "chat": STRANGER}), ("админ в общем чате", {"chat": -100123}), ("чужой в чате админа", {"sender": STRANGER})):
        ok(all(bot.say(text, **kw) == [] for text in ("/status", "/add evil", "/del anna_phone", "/nothing", "/help")), f"{what}: ни слова в ответ")
    ok(bot.press("del:anna_phone", sender=STRANGER) == [] and bot.press("cancel", sender=STRANGER) == [] and AG.asked == [],
       "чужое нажатие кнопки — ни ответа, ни удаления; агента не спрашивали ни разу", AG.commands())

    print("\n— сервер не отвечает")
    AG.answers["state"] = {"ok": False, "error": "сервер <занят>"}
    for text in ("/status", "/users", "/report"):
        ok(bot.say(text)[0]["text"] == "Нет связи с сервером: сервер &lt;занят&gt;", f"{text}: отказ агента — его словами")
    AG.answers["state"] = {"ok": False}
    ok(bot.say("/status")[0]["text"] == "Нет связи с сервером: ", "отказ без объяснения")
    gone = Bot(agent_socket=os.path.join(TMP, "no-such.sock"))
    ok(gone.say("/status")[0]["text"] == "Нет связи с сервером: агент на сервере недоступен (FileNotFoundError)", "сокета агента нет", gone.say("/status"))
    AG.garbage = True
    ok(bot.say("/status")[0]["text"] == "Нет связи с сервером: агент на сервере недоступен (JSONDecodeError)", "агент ответил не JSON", bot.say("/status"))
    AG.garbage = False
    unreadable = {k: v for k, v in state().items() if k != "entry"}
    unreadable["clients"][0]["name"] = "SECRET-NAME"
    AG.answers["state"] = unreadable
    try:
        bot.say("/status")
        raised = None
    except Exception as e:
        raised = e
    ok(type(raised).__name__ == "ValidationError" and TG.sent() == [] and "SECRET-NAME" not in str(raised) and "entry" in str(raised),
       "ответ агента не того вида: ничего не отвечаем (ошибка уйдёт в журнал процесса), и в тексте ошибки нет присланных данных", str(raised)[:200])
    AG.answers["state"] = state()

    print("\n— /add, /key")
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n-picture-").decode()
    AG.answers["qr"] = {"ok": True, "name": "carl", "conf": "[Interface]\nPrivateKey = x\n", "uri": "vpn://AAA<&", "uri_png": png, "conf_png": png}
    AG.answers["add"] = {"ok": True}
    AG.asked.clear()
    sent = bot.say("/add carl 7d")
    ok(AG.commands() == [["add", "carl", "7d"], ["qr", "carl"]], "создание со сроком, затем код", AG.commands())
    ok([c["method"] for c in sent] == ["sendMessage", "sendPhoto", "sendMessage", "sendDocument"] and sent[0]["text"] == "Создан пользователь <code>carl</code>, срок 7d",
       "ответ: создан → QR → строка → файл", [c["method"] for c in sent])
    ok(sent[1]["files"]["photo"] == ("carl.png", b"\x89PNG\r\n\x1a\n-picture-") and sent[1]["caption"] == "carl: QR для приложения AmneziaVPN" and sent[1]["chat_id"] == str(ADMIN)
       and sent[1]["parse_mode"] == "HTML", "QR — картинкой carl.png с подписью", {k: v for k, v in sent[1].items() if k != "files"})
    ok(sent[2]["text"] == "Строка для компьютера (AmneziaVPN → «Вставить ключ»):\n<code>vpn://AAA&lt;&amp;</code>", "строка vpn:// — отдельным сообщением", sent[2].get("text"))
    ok(sent[3]["files"]["document"] == ("carl.conf", b"[Interface]\nPrivateKey = x\n") and sent[3]["caption"] == "Файл для AmneziaWG и WG Tunnel", "конфиг — файлом carl.conf")
    AG.asked.clear()
    ok(bot.say("/add dan")[0]["text"] == "Создан пользователь <code>dan</code>" and AG.commands()[0] == ["add", "dan"], "создание без срока")
    AG.asked.clear()
    for text in ("/add", "/add a b c d" if False else "/add имя", "/add " + "x" * 33, "/add bad!name 7d"):
        ok(bot.say(text)[0]["text"] == "Формат: /add имя [срок]\nИмя: латиница, цифры, _ и -, до 32 символов. Срок: 12h, 7d, 4w.", f"{text[:20]} — подсказка формата")
    for text in ("/add eve 7", "/add eve 0d", "/add eve 7days", "/add eve 1000d"):
        ok(bot.say(text)[0]["text"] == "Срок указывается так: 12h, 7d, 30d, 4w", f"{text} — подсказка про срок")
    ok(AG.asked == [], "негодные имя или срок до агента не доходят")
    AG.answers["add"] = {"ok": False, "error": "пользователь с таким именем уже есть"}
    sent = bot.say("/add carl")
    ok(len(sent) == 1 and sent[0]["text"] == "Не получилось создать: пользователь с таким именем уже есть", "отказ агента — его словами, код не присылается", sent)
    AG.answers["add"] = {"ok": False}
    ok(bot.say("/add carl")[0]["text"] == "Не получилось создать: ошибка", "отказ без объяснения")
    AG.asked.clear()
    sent = bot.say("/key  carl ")
    ok([c["method"] for c in sent] == ["sendPhoto", "sendMessage", "sendDocument"] and AG.commands() == [["qr", "carl"]], "/key: QR, строка и файл ещё раз")
    AG.answers["qr"] = {"ok": True, "conf": "[Interface]\n", "uri": "", "uri_png": ""}
    ok([c["method"] for c in bot.say("/key carl")] == ["sendDocument"], "строки vpn:// и её QR нет — только файл")
    AG.answers["qr"] = {"ok": True, "uri": "vpn://PRIVATE-KEY-INSIDE", "uri_png": png}
    try:
        bot.say("/key carl")
        raised = None
    except Exception as e:
        raised = e
    ok(type(raised).__name__ == "ValidationError" and "PRIVATE-KEY-INSIDE" not in str(raised) and "conf" in str(raised), "код без конфига — ошибка, и в её тексте нет ни строки vpn://, ни картинки", str(raised)[:200])
    from awgscale.bot.models import DeviceKey
    ok("PRIVATE" not in repr(DeviceKey(conf="PRIVATE-conf", uri="vpn://PRIVATE", uri_png="PRIVATE")), "код устройства не виден в repr")
    AG.answers["qr"] = {"ok": False, "error": "нет такого пользователя"}
    ok(bot.say("/key carl")[0]["text"] == "Не получилось: нет такого пользователя", "/key неизвестному — отказ агента")
    ok(bot.say("/key")[0]["text"] == "Формат: /key имя" and bot.say("/key a b")[0]["text"] == "Формат: /key имя", "/key без имени или с двумя — подсказка")

    print("\n— /del")
    AG.asked.clear()
    sent = bot.say("/del carl")
    keyboard = sent[0]["reply_markup"]["inline_keyboard"]
    ok(sent[0]["text"] == "Удалить пользователя <code>carl</code>? Его ключ перестанет работать сразу." and keyboard == [[{"text": "Удалить", "callback_data": "del:carl"}, {"text": "Отмена", "callback_data": "cancel"}]]
       and AG.asked == [], "/del: вопрос с двумя кнопками, ничего ещё не удалено", sent)
    ok(bot.say("/del")[0]["text"] == "Формат: /del имя", "/del без имени — подсказка")
    sent = bot.press("cancel")
    ok([c["method"] for c in sent] == ["answerCallbackQuery", "editMessageText"] and sent[0]["callback_query_id"] == "cb1" and sent[1]["text"] == "Отменено."
       and (sent[1]["chat_id"], sent[1]["message_id"]) == (ADMIN, 9) and AG.asked == [], "«Отмена»: нажатие принято, сообщение заменено, никто не удалён", sent)
    AG.answers["remove"] = {"ok": True}
    sent = bot.press("del:carl")
    ok(AG.commands() == [["remove", "carl"]] and sent[1]["text"] == "Пользователь <code>carl</code> удалён." and sent[1]["parse_mode"] == "HTML", "«Удалить»: агент удаляет, сообщение заменено", sent)
    AG.answers["remove"] = {"ok": False, "error": "ядро не отдало узел"}
    ok(bot.press("del:carl")[1]["text"] == "Не получилось удалить: ядро не отдало узел", "удаление не удалось — словами агента")
    AG.asked.clear()
    sent = bot.press("del:bad name; rm")
    ok([c["method"] for c in sent] == ["answerCallbackQuery"] and AG.asked == [], "в кнопке негодное имя — нажатие принято, больше ничего")
    ok(bot.press("something-else") == [], "незнакомая кнопка — молчание")

    print("\n— /invite")
    AG.answers["ugroups-get"] = {"ok": True, "v": "1", "groups": [{"id": "g00001", "name": "Семья", "members": []}, {"id": "g00002", "name": "Офис & Ко", "members": []}]}
    AG.answers["invite-add"] = lambda args: {"ok": True, "token": "S" * 22, "id": "i1"}
    AG.asked.clear()
    sent = bot.say("/invite")
    link = "https://vpn.example/i/" + "S" * 22
    request = json.loads(base64.b64decode(AG.commands()[0][1]))
    ok(AG.commands()[0][0] == "invite-add" and request == {"group": None, "uses": 1, "days": 7} and len(AG.asked) == 1, "без параметров: одно устройство, семь дней, без группы", request)
    ok(sent[0]["text"] == f"Приглашение: устройств 1, действует 7 дн.\n<code>{link}</code>", "ответ со ссылкой", sent[0]["text"])
    button = sent[0]["reply_markup"]["inline_keyboard"][0][0]
    ok(button["text"] == "Переслать" and button["url"].startswith("https://t.me/share/url?url=https%3A%2F%2Fvpn.example%2Fi%2F") and "callback_data" not in button, "кнопка «Переслать» открывает пересылку в Telegram", button)
    AG.asked.clear()
    sent = bot.say("/invite 2 10 семья")
    request = json.loads(base64.b64decode(AG.commands()[1][1]))
    ok(AG.commands()[0] == ["ugroups-get"] and request == {"group": "g00001", "uses": 2, "days": 10} and sent[0]["text"].startswith("Приглашение в группу «семья»: устройств 2, действует 10 дн."),
       "с группой: она находится по имени без учёта регистра", (request, sent[0]["text"]))
    AG.asked.clear()
    sent = bot.say("/invite 3 Друзья")
    ok(sent[0]["text"] == "Группы «Друзья» нет. Есть: Семья, Офис &amp; Ко" and AG.commands() == [["ugroups-get"]], "группы нет — перечислены те, что есть; приглашение не создано", sent[0]["text"])
    AG.answers["ugroups-get"] = {"ok": False, "error": "x"}
    ok(bot.say("/invite Семья")[0]["text"] == "Группы «Семья» нет. Есть: групп нет", "группы прочитать не удалось — «групп нет»")
    for text in ("/invite 0", "/invite 11", "/invite 1 31", "/invite 2 0 Семья"):
        ok(bot.say(text)[0]["text"] == "Формат: /invite [устройств 1–10] [дней 1–30] [группа]\nНапример: /invite 2 7 Семья", f"{text} — подсказка формата")
    AG.answers["invite-add"] = {"ok": False, "error": "слишком много приглашений"}
    ok(bot.say("/invite")[0]["text"] == "Не получилось: слишком много приглашений", "отказ агента")
    ok(Bot(domain="").say("/invite")[0]["text"] == "Не настроен адрес сервера" and Bot(domain="").say("/invite 99")[0]["text"] == "Не настроен адрес сервера", "адрес сервера не задан — так и сказано")


def t_alarm():
    print("\n— тревога: срабатывает и отбивается так, как всегда")
    bot = Bot()
    for fire_after, clear_after in ((1, 1), (2, 1), (2, 2), (10, 6)):
        seq = [True] * 12 + [False] * 7 + [True, False, True, True, False, False, True] * 3 + [False] * 8 + [True] * 11
        TG.clear()
        theirs = GOLDEN["alarm"][f"{fire_after} {clear_after}"]
        mine = Alarm(bot.admin, "плохо:", "снова хорошо.", fire_after, clear_after)
        for i, bad in enumerate(seq):
            run(mine.check(bad, f"шаг {i}." if i % 2 else ""))
        ok(TG.texts() == theirs["said"] and len(theirs["said"]) >= 2 and mine.active == theirs["active"], f"после {fire_after} плохих подряд — тревога, после {clear_after} хороших — отбой: те же сообщения в том же порядке ({len(theirs['said'])})", TG.texts()[:3])

    print("\n— тревога и отбой, которые не дошли, говорятся на следующей проверке")
    alarm = Alarm(bot.admin, "плохо.", "снова хорошо.")
    for kind, broken in (("cut", TG.cut), ("fail", TG.fail)):
        TG.clear()
        broken.add("sendMessage")
        run(alarm.check(True))
        broken.discard("sendMessage")
        ok(not alarm.active, f"тревога не дошла ({kind}) — не считается сказанной")
        run(alarm.check(True))
        run(alarm.check(True))
        ok(TG.texts() == ["🔴 плохо.", "🔴 плохо."] and alarm.active, f"…сказана на следующей проверке, и один раз ({kind})", TG.texts())
        TG.clear()
        broken.add("sendMessage")
        run(alarm.check(False))
        broken.discard("sendMessage")
        ok(alarm.active, f"отбой не дошёл ({kind}) — тревога ещё не снята")
        run(alarm.check(False))
        run(alarm.check(False))
        ok(TG.texts() == ["🟢 снова хорошо.", "🟢 снова хорошо."] and not alarm.active, f"…отбой сказан на следующей проверке, и один раз ({kind})", TG.texts())

    print("\n— тревога, которая так и не дошла, говорится вместе с отбоем: с какого и до какого часа")
    moscow = Zone(Span(hours=3))
    moments = iter([Moment(2026, 11, 17, 7, 18, 30, tzinfo=Zone.utc) + Span(minutes=15 * step) for step in range(10)])
    alarm = Alarm(bot.admin, "Выход <b>exit1-nl</b> недоступен.", "Выход <b>exit1-nl</b> снова работает.", zone=moscow, clock=lambda: next(moments))
    TG.clear()
    TG.cut.add("sendMessage")
    for _ in range(5):
        run(alarm.check(True))                           # 10:18 … 11:18 по Москве: связи с Telegram нет
    TG.cut.discard("sendMessage")
    TG.clear()
    run(alarm.check(False))                              # 11:33: выход вернулся, связь тоже
    ok(TG.texts() == ["🟢 Выход <b>exit1-nl</b> снова работает.\nНе дошло вовремя: 🔴 Выход <b>exit1-nl</b> недоступен. — с 10:18 до 11:33."] and not alarm.active,
       "одно сообщение: отбой и тревога, которая не дошла, с началом и концом по местному времени", TG.texts())
    run(alarm.check(False))
    ok(TG.texts()[1:] == [], "…и только один раз")
    TG.clear()
    late = Alarm(bot.admin, "плохо.", "снова хорошо.", zone=moscow, clock=iter([Moment(2026, 11, 17, 20, 0, tzinfo=Zone.utc), Moment(2026, 11, 18, 6, 0, tzinfo=Zone.utc), Moment(2026, 11, 18, 6, 1, tzinfo=Zone.utc)]).__next__)
    TG.cut.add("sendMessage")
    run(late.check(True))
    TG.cut.discard("sendMessage")
    TG.cut.add("sendMessage")
    run(late.check(False))                               # отбой тоже не дошёл — скажется на следующей проверке
    TG.cut.discard("sendMessage")
    TG.clear()
    run(late.check(False))
    ok(TG.texts() == ["🟢 снова хорошо.\nНе дошло вовремя: 🔴 плохо. — с 17.11 23:00 до 18.11 09:01."], "через полночь — с датами; отбой, который не дошёл, повторён", TG.texts())


def t_watch():
    print("\n— оповещения")
    everyone = [client("anna_phone", 20), client("tv", 400, exit=3)]

    def state(**changed):                           # as usual, but nobody's key is about to end: that has checks of its own below
        return STATE(**{"clients": everyone, **changed})

    def look(bot, s):
        AG.answers["state"] = s
        AG.answers["journal"] = {"ok": False}
        TG.clear()
        run(bot.watch.look())
        return TG.texts()

    bot = Bot()
    ok(look(bot, state()) == [], "всё в порядке — тишина")
    ok(look(bot, state(exits=[exit_(1, "exit1-nl", "down"), exit_(3, "exit2-nl")])) == ["🔴 Выход <b>exit1-nl</b> недоступен — его пользователи переведены на другой."], "выход упал — сказано сразу")
    ok(look(bot, state(exits=[exit_(1, "exit1-nl", "down"), exit_(3, "exit2-nl")])) == [], "…и один раз")
    ok(look(bot, state(exits=[exit_(1, "exit1-nl", "unknown"), exit_(3, "exit2-nl")])) == [], "по выходу ещё нет вердикта (unknown) — ни тревоги, ни отбоя")
    ok(look(bot, state()) == ["🟢 Выход <b>exit1-nl</b> снова работает."], "выход вернулся — отбой")
    fresh = Bot()
    ok(look(fresh, state(exits=[exit_(1, "exit1-nl", "unknown"), exit_(3, "exit2-nl", "unknown")])) == [], "после загрузки вердиктов нет ни по одному выходу — тишина, «все выходы недоступны» не говорится")
    ok(look(fresh, state(exits=[exit_(1, "exit1-nl", "down"), exit_(3, "exit2-nl", "unknown")])) == ["🔴 Выход <b>exit1-nl</b> недоступен — его пользователи переведены на другой."],
       "один упал, по другому вердикта нет — тревога по одному, но не «все недоступны»")
    said = look(fresh, state(exits=[exit_(1, "exit1-nl", "down"), exit_(3, "exit2-nl", "down")]))
    ok(said == ["🔴 Выход <b>exit2-nl</b> недоступен — его пользователи переведены на другой.", "🔴 <b>Все выходы недоступны.</b> Зарубежный трафик заблокирован, российский работает."], "оба упали — «все выходы недоступны»", said)
    said = look(fresh, state(exits=[exit_(1, "exit1-nl"), exit_(3, "exit2-nl", "down")]))
    ok(said == ["🟢 Выход <b>exit1-nl</b> снова работает.", "🟢 Хотя бы один выход снова работает."], "один вернулся — два отбоя", said)
    ok(look(Bot(), state(exits=[])) == [], "выходов нет вовсе — это не «все недоступны»")

    bot = Bot()
    down = state(entry={"client_if_up": False})
    ok(look(bot, down) == ["🔴 Туннель для пользователей на входном сервере упал — никто не может подключиться."] and look(bot, down) == [] and look(bot, state()) == ["🟢 Туннель для пользователей снова поднят."],
       "туннель пользователей: тревога сразу, отбой сразу")
    silent = state(dns={"ok": False})
    ok(look(bot, silent) == [] and look(bot, silent) == ["🔴 Блокировщик рекламы (DNS) не отвечает — у пользователей могут не открываться сайты."], "блокировщик: тревога со второго раза")
    ok(look(bot, state()) == [] and look(bot, state()) == ["🟢 Блокировщик рекламы снова отвечает."], "…отбой тоже со второго")
    full = state(entry={"disk_pct": 85})
    ok(look(bot, full) == [] and look(bot, full) == ["🔴 Диск входного сервера почти заполнен: 85%."] and look(bot, state(entry={"disk_pct": 84})) == [] and look(bot, state()) == ["🟢 На диске входного сервера снова достаточно места."],
       "диск от 85%: тревога и отбой со второго раза")
    ok(look(bot, state(entry={"disk_pct": None})) == [], "нет данных о диске — о диске ни слова")
    busy = state(entry={"load1": 4.5})
    said = [look(bot, busy) for _ in range(10)]
    ok(said[:9] == [[]] * 9 and said[9] == ["🔴 Высокая нагрузка на входном сервере уже 5 минут: 4.5 на 2 ядра."], "нагрузка выше двух на ядро: тревога после десяти раз подряд", said[9])
    said = [look(bot, state()) for _ in range(6)]
    ok(said[:5] == [[]] * 5 and said[5] == ["🟢 Нагрузка на входном сервере пришла в норму."] and look(bot, state(entry={"load1": 4.0})) == [], "…отбой после шести; ровно два на ядро — ещё не высокая")
    tight = state(entry={"mem_used": 18 * 10 ** 8})
    said = [look(bot, tight) for _ in range(10)]
    ok(said[9] == ["🔴 Память входного сервера почти заполнена: 90%."] and [look(bot, state()) for _ in range(6)][5] == ["🟢 Память входного сервера освободилась."], "память от 90%: десять раз до тревоги, шесть до отбоя", said[9])

    AG.asked.clear()
    lost = {"ok": False, "error": "агент <молчит>"}
    ok(look(bot, lost) == [] and look(bot, lost) == ["🔴 Бот не получает данные от входного сервера. агент &lt;молчит&gt;"], "нет данных от сервера: тревога со второго раза, с причиной")
    ok(look(bot, lost) == [] and look(bot, state()) == ["🟢 Данные от входного сервера снова поступают."], "…отбой сразу; пока данных нет, остальные тревоги не трогаются")
    ok(AG.commands()[:2] == [["journal"], ["state"]], "за один взгляд — журнал и состояние", AG.commands()[:4])
    broken = {k: v for k, v in state().items() if k != "entry"}
    ok(look(bot, broken) == [] and look(bot, broken) == [] and look(bot, broken) == [] and look(bot, state()) == [],
       "состояние не того вида: взгляд пропущен (запись в журнале процесса), ни тревог, ни отбоев")

    bot = Bot()
    soon = state(clients=[client("carl", 20, 1, 1, 1, expires=3600), client("far", 20, 1, 1, 1, expires=2 * 86400), client("past", 20, 1, 1, 1, expires=-5)])
    ok(look(bot, soon) == ["⏳ Ключ пользователя <code>carl</code> истекает 14.01 в 12:20."] if False else look(bot, soon)[0].startswith("⏳ Ключ пользователя <code>carl</code> истекает ") and len(look(bot, soon)) == 0,
       "ключ истекает в течение суток — предупреждение один раз; о дальних и уже истёкших — ничего")
    ok(look(Bot(), soon) == [f"⏳ Ключ пользователя <code>carl</code> истекает {GOLDEN['expiry_local']}."], "время в предупреждении — местное (Москва), как всегда", look(Bot(), soon))
    look(bot, state(clients=[]))
    ok(len(look(bot, soon)) == 1, "пользователь пропал и появился снова — предупреждение снова")

    print("\n— журнал изменений")

    def journal(bot, items, deliver=True):
        AG.answers["state"] = state()
        AG.answers["journal"] = {"ok": True, "items": items}
        TG.clear()
        TG.fail = set() if deliver else {"sendMessage"}
        run(bot.watch.look())
        TG.fail = set()
        return TG.texts() if deliver else TG.sent("sendMessage")

    def entry(t, text, who="panel", kind="change", bad=False):
        return {"t": t, "text": text, "bad": bad, "kind": kind, "who": who}

    bot = Bot()
    before = [entry(NOW - 50, "Создан пользователь old"), entry(NOW - 60, "Выход exit2-nl недоступен", "system", "exit", True)]
    ok(journal(bot, before) == [], "что было в журнале до запуска бота — не пересказывается")
    ok(journal(bot, before) == [], "ничего нового — тишина")
    news = [entry(NOW - 5, "Удалён <carl>"), entry(NOW - 6, "Срок вышел: bob", "system", "term"), entry(NOW - 7, "Отказ: список устарел", "system", "refused", True),
            entry(NOW - 8, "Приглашение использовано", "invite", "invite"), entry(NOW - 9, "Бот создал dan", "bot", "change"), entry(NOW - 10, "Выход exit2-nl снова работает", "system", "exit"),
            entry(NOW - 11, "Ночное обновление списков", "system", "lists")]
    said = journal(bot, news + before)
    ok(said == ["📝 Приглашение использовано\n🔴 Отказ: список устарел\nСрок вышел: bob\nУдалён &lt;carl&gt;"],
       "новое: сделанное в панели, срок, отказ, приглашение — старое сверху; своё, выходы и рутина сервера — нет; плохое помечено", said)
    ok(journal(bot, news + before) == [], "пересказанное не повторяется")
    many = [entry(NOW + i, f"Изменение {i}") for i in range(13, 0, -1)]
    said = journal(bot, many + news + before)
    ok(said[0].count("\n") == 10 and said[0].startswith("📝 Изменение 1\n") and said[0].endswith("\n… и ещё 3"), "больше десяти — первые десять и «и ещё N»", said)
    late = [entry(NOW + 100, "Доставить позже")]
    tried = journal(bot, late + many, deliver=False)
    ok(len(tried) == 1, "Telegram не принял сообщение…")
    ok(journal(bot, late + many) == ["📝 Доставить позже"], "…значит, не пересказано: придёт в следующий раз")
    ok(journal(bot, []) == [] and journal(bot, late + many + news + before) == [], "журнал ненадолго опустел (перезапуск хранилища) и вернулся — это не новости")
    long = [entry(NOW + 200, "я" * 400)]
    ok(len(journal(bot, long)[0]) == len("📝 ") + 300, "длинная запись обрезается до 300 знаков")
    AG.answers["journal"] = {"ok": True, "items": ["not an entry", {"t": {"odd": 1}, "text": "x", "who": "panel"}, dict(entry(NOW + 300, "Запись рядом с мусором"), bad=None)]}
    TG.clear()
    run(bot.watch.look())
    ok(TG.texts() == ["📝 Запись рядом с мусором"], "мусор среди записей пропускается")


def t_sampler():
    print("\n— счётчики за сутки и ежедневная сводка")
    global WORLD
    WORLD = World()                                 # an agent that has counted no day yet
    hour = 3600
    day = 86400
    morning = NOW - (NOW + 3 * hour) % day + 9 * hour        # 09:00 local (UTC+3) of the made-up day

    def sample(bot, at, **changed):
        AG.answers["state"] = state(time=at, **changed)
        TG.clear()
        run(bot.sampler.sample())
        return TG.texts()

    bot = Bot()
    ok(sample(bot, morning + 60) == [] and bot.sampler.stats.last_report_day == datetime.datetime.fromtimestamp(morning, ZONE).strftime("%Y-%m-%d") and bot.sampler.stats.bucket.samples == 1,
       "первый запуск после часа сводки: пустую сводку не шлём, день просто начат")
    saved = json.load(open(bot.settings.stats_file))
    ok(saved["bucket"]["samples"] == 1 and "last" not in saved and "users" not in saved["bucket"], "после каждого замера счётчики записаны в файл — без счётчиков каждого пользователя")
    for i in range(3):
        sample(bot, morning + 120 + 60 * i, clients=[client("anna_phone", 20, 4 * 10 ** 9 + 1000 * (i + 1), 6 * 10 ** 9 + 5000 * (i + 1))])
    counted_day = WORLD.ledger.answer(moment(int(bot.sampler.stats.bucket.since.timestamp())), moment(morning + 240))
    ok(counted_day.top == [("anna_phone", 3000, 15000)] and counted_day.active >= 1 and ["day", str(int(bot.sampler.stats.bucket.since.timestamp()))] in AG.commands(),
       "трафик копится от замера к замеру — у агента, за сутки, которые назвал бот", counted_day)
    ok(sample(bot, morning + day - 60, clients=[client("anna_phone", 20, 4 * 10 ** 9 + 3000, 6 * 10 ** 9 + 15000)]) == [], "до часа сводки следующего дня — тишина")

    restarted = Bot()                                            # the process starts again: the counters are read from the file
    ok(json.loads(restarted.sampler.stats.model_dump_json()) == json.loads(bot.sampler.stats.model_dump_json()), "после перезапуска счётчики читаются из файла такими же")
    said = sample(restarted, morning + day + 30, clients=[client("anna_phone", 20, 4 * 10 ** 9 + 4000, 6 * 10 ** 9 + 20000)])
    ok(len(said) == 1 and said[0].startswith("📊 <b>Сводка за сутки</b>") and "↓ 19.5 КБ  ↑ 3.9 КБ" in said[0], "в час сводки следующего дня она уходит — со счётчиками, накопленными до перезапуска и после", said)
    stats = restarted.sampler.stats
    ok(len(stats.history) == 1 and stats.history[0].down == 20000 and stats.history[0].up == 4000 and stats.bucket.samples == 0 and stats.bucket.since == moment(morning + day + 30),
       "итоги дня ушли в историю, счётчики начаты заново", stats.history)
    ok(sample(restarted, morning + day + 90) == [], "вторая сводка за тот же день не шлётся")
    AG.answers["state"] = {"ok": False, "error": "x"}
    before = restarted.sampler.stats.bucket.samples
    run(restarted.sampler.sample())
    ok(restarted.sampler.stats.bucket.samples == before, "сервер не ответил — замер пропущен, счётчики целы")
    open(restarted.settings.stats_file, "w").write("{broken")
    ok(Bot().sampler.stats.bucket.samples == 0 and Bot().sampler.stats.history == [], "файл счётчиков испорчен — бот начинает с чистых, а не падает")
    os.unlink(restarted.settings.stats_file)


def t_process():
    print("\n— процесс целиком: python -m awgscale.bot")
    AG.answers.update({"state": state(), "journal": {"ok": True, "items": []}})
    AG.asked.clear()
    TG.clear()
    TG.updates.append({"update_id": 41, "message": {"message_id": 1, "date": NOW, "chat": {"id": ADMIN, "type": "private"}, "from": {"id": ADMIN, "is_bot": False, "first_name": "A"}, "text": "/status"}})
    env = dict(os.environ, **ENV, BOT_WATCH_SECONDS="0.5", BOT_SAMPLE_SECONDS="0.7", PYTHONPATH=os.path.join(HERE, "..", "src"))
    log_path = os.path.join(TMP, "process.log")

    def start():
        return subprocess.Popen([sys.executable, "-m", "awgscale.bot"], env=env, stdout=open(log_path, "a"), stderr=subprocess.STDOUT)

    def wait_for(cond, seconds=15):
        deadline = time.time() + seconds
        while time.time() < deadline and not cond():
            time.sleep(0.05)
        return cond()

    p = start()
    ok(wait_for(lambda: any(t == GOLDEN["status"]["as usual"] for t in TG.texts())), "процесс запустился и ответил на команду, пришедшую через getUpdates")
    methods = [c["method"] for c in TG.sent()]
    ok(methods[:2] == ["getMe", "setMyCommands"] and "Бот VPN-каскада запущен. /help — список команд." in TG.texts(), "при старте: знакомство, список команд, сообщение админу о запуске", methods[:5])
    menu = TG.sent("setMyCommands")[0]["commands"]
    ok(menu == [m.model_dump() for m in MENU] and [m["command"] for m in menu] == ["status", "users", "add", "key", "del", "invite", "report"], "меню Telegram: семь команд с описаниями", menu)
    polls = TG.sent("getUpdates")
    ok(polls[0]["allowed_updates"] == ["message", "callback_query"] and "offset" not in polls[0] and wait_for(lambda: any(c.get("offset") == 42 for c in TG.sent("getUpdates"))),
       "обновления: только сообщения и кнопки; после обработанного — запрос со следующего номера", polls[0])
    ok(wait_for(lambda: AG.commands().count(["journal"]) >= 2 and AG.commands().count(["state"]) >= 4), "задания по расписанию идут: взгляд на каскад и замер счётчиков", AG.commands()[:8])
    ok(wait_for(lambda: os.path.exists(os.path.join(DATA, "stats.json"))), "счётчики пишутся в файл")
    TG.fail = {"getUpdates"}
    time.sleep(1)
    TG.fail = set()
    TG.cut = {"getUpdates"}
    time.sleep(0.6)
    TG.cut = set()
    TG.clear()
    TG.updates.append({"update_id": 49, "message": {"message_id": "not a number", "chat": "nonsense"}})
    TG.updates.append({"update_id": 50, "callback_query": {"id": "cbX", "from": {"id": ADMIN, "is_bot": False, "first_name": "A"}, "data": "cancel", "chat_instance": "x",
                                                            "message": {"message_id": 3, "date": NOW, "chat": {"id": ADMIN, "type": "private"}}}})
    ok(wait_for(lambda: "Отменено." in TG.texts()) and p.poll() is None, "Telegram отказывал и рвал соединение, потом прислал обновление, которое не разобрать, — бот жив, спрашивает снова и обрабатывает следующее (кнопку)")
    ok(wait_for(lambda: any(c.get("offset") == 51 for c in TG.sent("getUpdates"))), "…и неразобранное обновление пройдено: Telegram не пришлёт его снова")
    TG.updates.append({"update_id": 51, "message": {"message_id": 2, "date": NOW, "chat": {"id": STRANGER, "type": "private"}, "from": {"id": STRANGER, "is_bot": False, "first_name": "S"}, "text": "/status"}})
    TG.updates.append({"update_id": 52, "edited_message": {"message_id": 2, "date": NOW, "chat": {"id": ADMIN, "type": "private"}, "text": "/users"}})
    wait_for(lambda: any(c.get("offset") == 53 for c in TG.sent("getUpdates")))
    ok(not [c for c in TG.sent("sendMessage") if c["chat_id"] == STRANGER] and not [t for t in TG.texts() if t and t.startswith("<b>Пользователи</b>")], "чужому и на незнакомый вид обновления — ни слова")
    p.send_signal(signal.SIGTERM)
    ok(p.wait(timeout=10) == 0, "SIGTERM — чистое завершение, код 0", p.returncode)
    log = open(log_path).read()
    ok("started as @cascade_bot" in log and TOKEN not in log and TOKEN.split(":")[1] not in log and str(ADMIN) not in log, "в журнале процесса — имя бота, и нигде нет ни токена, ни id админа (хотя Telegram отказывал и запросы рвались)", log[-400:])
    ok("cannot ask Telegram for updates: getUpdates: Bad Request: the stand-in says no" in log, "отказ Telegram записан его словами")

    TG.clear()
    p = start()                                                  # the process starts again
    ok(wait_for(lambda: "Бот VPN-каскада запущен. /help — список команд." in TG.texts()) and "offset" not in TG.sent("getUpdates")[0],
       "перезапуск: снова сообщение о запуске; что уже обработано, помнит Telegram (первый запрос без номера)")
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)
    TG.fail = {"getMe"}
    p = start()
    ok(p.wait(timeout=15) != 0, "Telegram не признал бота при старте — процесс завершается с ошибкой (Docker запустит снова)", p.returncode)
    TG.fail = set()
    p = subprocess.run([sys.executable, "-m", "awgscale.bot"], env=dict(env, BOT_TOKEN_FILE=os.path.join(TMP, "none")), capture_output=True, text=True, timeout=30)
    ok(p.returncode == 1 and "the token cannot be read" in p.stderr, "файла с токеном нет — процесс не стартует", p.stderr[-200:])
    p = subprocess.run([sys.executable, "-m", "awgscale.bot"], env={k: v for k, v in env.items() if k != "BOT_ADMIN_ID"}, capture_output=True, text=True, timeout=30)
    ok(p.returncode != 0 and "admin_id" in p.stderr.lower() + p.stdout.lower(), "id админа не задан — процесс не стартует")


def t_secrets():
    print("\n— секреты и настройки")
    settings = get_settings()
    ok(TOKEN not in repr(settings) and TOKEN not in repr(settings.token()) and settings.token().get_secret_value() == TOKEN, "токен читается из файла и не виден в repr")
    ok(str(ADMIN) not in repr(settings) and settings.admin_id == ADMIN, "id админа тоже не виден в repr настроек")
    ok(settings.admin_id == ADMIN and settings.report_hour == 9 and settings.report_utc_offset == 3 and str(settings.agent_socket) == AGENT_SOCKET and settings.domain == "vpn.example"
       and str(settings.data_dir) == DATA, "имена переменных — те, что уже заданы контейнеру (BOT_*, PANEL_AGENT_SOCKET, REPORT_HOUR, REPORT_UTC_OFFSET)")
    ok(all(c["token"] == TOKEN for c in TG.sent()), "каждый запрос к Telegram несёт токен из файла")


def main():
    t_texts()
    t_report()
    t_commands()
    t_alarm()
    t_watch()
    t_sampler()
    t_secrets()
    t_process()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
