#!/usr/bin/env python3
"""The panel server (src/awgscale/panel): what it lets through to the agent,
what it refuses itself, who it lets in, and what it hands to strangers.

    uv run python tests/panel_server.py

The real server runs as a process (python -m awgscale.panel) with a made-up
build of the page; the agent is a stand-in on a unix socket that remembers what
it was asked and answers what the checks tell it to. A long list of requests
is answered the way the panel has always answered them (tests/golden/panel_server.json):
status, the headers that matter, the body and what reached the agent. Nothing
here touches a real server.

One line per check, the last line is "N of M checks passed".
"""
import asyncio
import base64
import datetime
import hashlib
import http.client
import http.server
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="panel-server-")
GOLDEN = json.load(open(os.path.join(HERE, "golden", "panel_server.json")))
PASS = FAIL = 0


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:700])


def moment_at(seconds):
    """Unix seconds as the moment they are."""
    return datetime.datetime.fromtimestamp(seconds, datetime.UTC)


# ── the stand-in agent ───────────────────────────────────────────────────────
ASKED = []                       # every request that reached the agent: {"args", "by", "secret"?}
REPLIES = {}                     # command → the answer (a dict, or a function of the arguments)
NOW = int(time.time())


def clients_now():
    return [{"name": "ann", "ip": "172.16.17.2", "handshake": int(time.time()) - 10, "rx": 100, "tx": 9000, "live": True, "exit": 1, "online": True, "down_bps": 800.0, "up_bps": 8.0},
            {"name": "bob", "ip": "172.16.17.3", "handshake": 0, "rx": 0, "tx": 0, "live": True, "exit": 1, "online": False, "down_bps": 0.0, "up_bps": 0.0}]


def state_answer():
    return {"ok": True, "time": int(time.time()), "entry": {"load1": 0.3, "mem_total": 2000, "mem_used": 900, "disk_pct": 41, "client_if_up": True},
            "exits": [{"index": 1, "name": "exit1-nl", "state": "up", "iface_rx": 1000, "iface_tx": 2000, "users": 2, "online": 1}],
            "users": {"users": 2, "online": 1, "down_bps": 800.0, "up_bps": 8.0}}


def clients_answer(args):
    return {"ok": True, "time": int(time.time()), "total": 2, "offset": 0, "clients": clients_now()}


def traffic_answer(args):
    return {"ok": True, "time": int(time.time()), "users": [[client["name"], client["rx"], client["tx"]] for client in clients_now() if client["live"]]}


QUIET = ("state", "clients", "traffic")          # what the panel asks by itself: not counted among the requests a test makes


class Agent(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline())
        if request["args"][0] not in QUIET:
            ASKED.append(request)
        answer = REPLIES.get(request["args"][0], {"clients": clients_answer, "traffic": traffic_answer}.get(request["args"][0]))
        if callable(answer):
            answer = answer(request["args"])
        if answer is None:
            answer = state_answer() if request["args"][0] == "state" else {"ok": True, "did": request["args"][0], "with": request["args"][1:]}
        self.wfile.write(json.dumps(answer, ensure_ascii=False).encode() + b"\n")


class AgentServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


SOCKET = os.path.join(TMP, "agent.sock")
threading.Thread(target=AgentServer(SOCKET, Agent).serve_forever, daemon=True).start()


# ── the two servers ──────────────────────────────────────────────────────────
WEB = os.path.join(TMP, "web")
os.makedirs(os.path.join(WEB, "_next", "static", "chunks"))
open(os.path.join(WEB, "index.html"), "w").write('<meta name="ui" content="__UI_ID__"><script src="/_next/static/chunks/a1.js"></script>')
open(os.path.join(WEB, "_next", "static", "chunks", "a1.js"), "w").write("console.log(1)")
open(os.path.join(WEB, "_next", "index.txt"), "w").write("flight")
open(os.path.join(TMP, "secret.txt"), "w").write("not for the web")
PASSWORD_FILE = os.path.join(TMP, ".password")
FONTS = os.path.join(REPO, "panel", "fonts")
FONT = sorted(f for f in os.listdir(FONTS) if f.endswith(".woff2"))[0]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(command, **more):
    """Starts a server on a free port → (the process, its port). A port another process took in between is simply tried again."""
    for _ in range(5):
        port = free_port()
        if "PANEL_METRICS_PORT" in more:
            more["PANEL_METRICS_PORT"] = str(free_port())
        process = start_on(command, port, **more)
        if process is not None:
            return process, port, int(more.get("PANEL_METRICS_PORT", 0))
    raise SystemExit(f"{command} did not start:\n" + open(os.path.join(TMP, "server.log")).read()[-2000:])


def start_on(command, port, **more):
    env = dict(os.environ, PANEL_WEB_DIR=WEB, PANEL_PORT=str(port), PANEL_BIND="127.0.0.1", PANEL_AGENT_SOCKET=SOCKET, PANEL_PASSWORD_FILE=PASSWORD_FILE,
               PANEL_FONTS_DIR=FONTS, PANEL_INVITE_FILE=os.path.join(REPO, "panel", "invite.html"), PANEL_BEHIND_TLS_PROXY="1", PYTHONDONTWRITEBYTECODE="1", **more)
    env.pop("PANEL_METRICS_PORT", None) if "PANEL_METRICS_PORT" not in more else None
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=open(os.path.join(TMP, "server.log"), "a"), cwd=REPO)
    for _ in range(300):
        if process.poll() is not None:
            return None                                  # it could not have the port
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            time.sleep(0.3)
            return process if process.poll() is None else None
        except OSError:
            time.sleep(0.1)
    process.terminate()
    return None


NEW, NEW_PORT, METRICS_PORT = start([sys.executable, "-m", "awgscale.panel"], PANEL_METRICS_PORT="0", PANEL_POLL_SECONDS="1")
PASSWORD = open(PASSWORD_FILE).read().strip()             # made by the new server on its first start
SHORT, SHORT_PORT, _ = start([sys.executable, "-m", "awgscale.panel"], PANEL_SESSION_HOURS="0.0005")       # a login that lasts under two seconds


def ask(port, method, path, body=None, cookie=None, headers=None, raw=None):
    """→ (status, headers with names in one spelling, body: parsed when it is JSON)."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    send = dict(headers or {})
    if cookie:
        send["Cookie"] = cookie
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    if data is not None:
        send.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=data, headers=send)
    response = connection.getresponse()
    content = response.read()
    head = {name.title(): value for name, value in response.getheaders()}
    connection.close()
    try:
        return response.status, head, json.loads(content)
    except ValueError:
        return response.status, head, content


def login(port, who="10.0.0.1"):
    status, head, answer = ask(port, "POST", "/api/login", {"password": PASSWORD}, headers={"X-Real-Ip": who})
    if "Set-Cookie" not in head:
        raise SystemExit(f"login refused on port {port}: {status} {answer}")
    return head["Set-Cookie"].split(";")[0]


CHANGE = {"X-Panel": "1"}
COOKIE = login(NEW_PORT)


def new(method, path, body=None, cookie=COOKIE, headers=CHANGE, raw=None):
    status, _, answer = ask(NEW_PORT, method, path, body, cookie, headers, raw)
    return status, answer


def packed(argument):
    return json.loads(base64.b64decode(argument))


# ── checks ───────────────────────────────────────────────────────────────────
def t_session():
    print("\n— вход")
    status, head, answer = ask(NEW_PORT, "POST", "/api/login", {"password": PASSWORD}, headers={"X-Real-Ip": "10.0.0.2"})
    cookie = head.get("Set-Cookie", "")
    ok(status == 200 and answer == {"ok": True} and cookie.startswith("sid=") and "HttpOnly; SameSite=Strict; Path=/; Max-Age=43200; Secure" in cookie and len(cookie.split(";")[0]) > 40,
       "верный пароль — сессия на 12 часов в cookie: HttpOnly, SameSite=Strict, Secure (за TLS-прокси)", cookie)
    ok(head.get("Cache-Control") == "no-store" and head.get("Content-Type") == "application/json; charset=utf-8", "ответы API не кэшируются", head)
    ok(oct(os.stat(PASSWORD_FILE).st_mode & 0o777) == "0o600" and len(PASSWORD) >= 20, "пароль создан при первом запуске, файл читает только владелец")
    for bad in ({"password": "nope"}, {}, {"password": None}, {"password": 5}):
        status, _, answer = ask(NEW_PORT, "POST", "/api/login", bad, headers={"X-Real-Ip": "10.0.0.3"})
        ok(status == 403 and answer == {"ok": False, "error": "неверный пароль"}, f"неверный пароль ({bad}) — 403", (status, answer))
    status, _, answer = ask(NEW_PORT, "POST", "/api/login", raw=b"{not json", headers={"X-Real-Ip": "10.0.0.3"})
    ok(status == 403, "тело не JSON — тоже просто неверный пароль", (status, answer))
    for _ in range(3):
        ask(NEW_PORT, "POST", "/api/login", {"password": "x"}, headers={"X-Real-Ip": "10.0.0.3"})
    status, _, answer = ask(NEW_PORT, "POST", "/api/login", {"password": PASSWORD}, headers={"X-Real-Ip": "10.0.0.3"})
    ok(status == 429 and answer == {"ok": False, "error": "слишком много попыток, подождите 5 минут"}, "восемь неверных за пять минут — адрес ждёт, даже с верным паролем", (status, answer))
    status, _, _ = ask(NEW_PORT, "POST", "/api/login", {"password": PASSWORD}, headers={"X-Real-Ip": "10.0.0.4"})
    ok(status == 200, "другой адрес (X-Real-Ip от Traefik) входит как обычно")
    ok(new("GET", "/api/state", cookie=None)[0] == 401 and new("GET", "/api/state", cookie="sid=nope") == (401, {"ok": False, "error": "нужен вход"}), "без сессии и с чужой — 401 «нужен вход»")
    mine = login(NEW_PORT, "10.0.0.5")
    ok(new("GET", "/api/sites", cookie=mine)[0] == 200, "с сессией — пускает")
    status, head, answer = ask(NEW_PORT, "POST", "/api/logout", cookie=mine)
    ok(status == 200 and answer == {"ok": True} and head.get("Set-Cookie") == "sid=; Path=/; Max-Age=0" and new("GET", "/api/sites", cookie=mine)[0] == 401, "выход: cookie снята, сессия закрыта")
    short = login(SHORT_PORT, "10.0.0.6")
    before = ask(SHORT_PORT, "GET", "/api/sites", cookie=short)[0]
    time.sleep(2.2)
    ok(before == 200 and ask(SHORT_PORT, "GET", "/api/sites", cookie=short)[0] == 401, "срок сессии вышел — снова 401")
    ok(new("POST", "/api/dns", {"name": "tv"}, headers={}) == (400, {"ok": False, "error": "bad request"}), "изменение без заголовка X-Panel — 400 (чужой сайт его не поставит)")
    ok(new("POST", "/api/dns", {"name": "tv"}, cookie=None) == (401, {"ok": False, "error": "нужен вход"}), "изменение без сессии — 401, раньше чем что-либо ещё")


def t_units():
    print("\n— сессии и бюджет приглашений (на подставных часах)")
    from awgscale.panel.budget import Cost, InviteBudget
    from awgscale.panel.sessions import LoginAttempts, Sessions
    clock = [datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)]

    def tick(seconds):
        clock[0] += datetime.timedelta(seconds=seconds)
    sessions = Sessions(datetime.timedelta(hours=12), lambda: clock[0])
    sid = sessions.open()
    tick(12 * 3600 - 1)
    alive = sessions.is_open(sid)
    tick(2)
    ok(alive and not sessions.is_open(sid) and not sessions.is_open("") and sessions.open() != sid, "сессия живёт ровно свой срок; пустой идентификатор — не сессия")
    attempts = LoginAttempts(lambda: clock[0])
    for _ in range(7):
        attempts.failed("a")
    seven = attempts.must_wait("a")
    attempts.failed("a")
    eight = attempts.must_wait("a")
    tick(301)
    ok(not seven and eight and not attempts.must_wait("a") and not attempts.must_wait("b"), "семь ошибок — ещё можно, восемь — ждать; через пять минут снова можно; счёт по адресам")
    budget = InviteBudget(lambda: clock[0])
    spent = [budget.allows("1.2.3.4", Cost.PAGE) for _ in range(31)]
    ok(spent == [True] * 30 + [False], "бюджет адреса — 30 очков за 10 минут")
    tick(601)
    ok(budget.allows("1.2.3.4", Cost.PAGE), "через 10 минут бюджет снова есть")
    ok([budget.allows("2001:db8:1:2::1", Cost.REDEEM) for _ in range(10)] == [True] * 10 and not budget.allows("2001:db8:1:2:ffff::9", Cost.PAGE)
       and budget.allows("2001:db8:1:3::1", Cost.PAGE), "IPv6: бюджет один на всю /64, у соседней /64 — свой")
    ok(all(budget.allows("garbage", Cost.PAGE) for _ in range(30)) and not budget.allows("other garbage", Cost.PAGE), "непонятные адреса делят один бюджет на всех")
    ok(int(Cost.PAGE) + int(Cost.WRONG_SECRET) == 5 and int(Cost.REDEEM) == 3 and int(Cost.REDEEM_FAILED) == 2, "цены прежние: страница 1, неверный секрет 5, попытка создать 3 (+2 при неудаче)")


def t_reads():
    print("\n— чтение: запросы уходят агенту как были")
    for path, command in (("/api/sites", "sites-get"), ("/api/block", "block-get"), ("/api/spaces", "spaces-get"), ("/api/ugroups", "ugroups-get"),
                          ("/api/invites", "invite-list"), ("/api/journal", "journal"), ("/api/dns", "dns-list"), ("/api/exits", "exits-get")):
        ASKED.clear()
        status, answer = new("GET", path)
        ok(status == 200 and answer["did"] == command and ASKED == [{"args": [command], "by": "panel"}], f"GET {path} → {command}, от имени panel", (status, answer, ASKED))
    REPLIES["sites-get"] = {"ok": False, "error": "агент занят"}
    ok(new("GET", "/api/sites") == (200, {"ok": False, "error": "агент занят"}), "отказ агента на чтение списка отдаётся странице как есть, с кодом 200")
    del REPLIES["sites-get"]
    ASKED.clear()
    ok(new("GET", "/api/clients/ann/qr") == (200, {"ok": True, "did": "qr", "with": ["ann"]}) and new("GET", "/api/diag?name=ann")[1]["with"] == ["ann"], "код подключения и проверка пользователя")
    ok(new("GET", "/api/clients/-x/qr") == (400, {"ok": False, "error": "некорректное имя"}) and new("GET", "/api/diag?name=a%20b")[0] == 400 and new("GET", "/api/diag")[0] == 400
       and new("GET", "/api/clients/a/b/qr")[0] == 400 and len(ASKED) == 2, "негодное имя не доходит до агента — 400")
    ASKED.clear()
    ok(new("GET", "/api/check?host=Example.COM.")[1]["with"] == ["example.com"] and new("GET", "/api/probe?host=example.com&path=/watch%3Fv%3D1")[1]["with"] == ["example.com", "/watch?v=1"]
       and new("GET", "/api/probe?host=example.com")[1]["with"] == ["example.com", "/"], "проверка адреса: имя в нижнем регистре без точки в конце; путь страницы по умолчанию «/»")
    ok(new("GET", "/api/check?host=-bad-") == (400, {"ok": False, "error": "некорректное имя"}) and new("GET", "/api/probe?host=example.com&path=nope") == (400, {"ok": False, "error": "некорректный адрес"})
       and new("GET", "/api/check")[0] == 400, "негодное имя или путь — 400 со словами")
    REPLIES["check"] = {"ok": False, "error": "не знаю"}
    ok(new("GET", "/api/check?host=example.com") == (400, {"ok": False, "error": "не знаю"}), "отказ агента на проверку — 400")
    del REPLIES["check"]
    for path in ("/api/sites", "/api/block", "/api/spaces", "/api/ugroups", "/api/invites", "/api/journal", "/api/dns", "/api/traffic", "/api/state", "/api/diag?name=a", "/api/check?host=a.b", "/api/clients/a/qr"):
        if new("GET", path, cookie=None) != (401, {"ok": False, "error": "нужен вход"}):
            ok(False, f"GET {path} без сессии должен быть 401")
            break
    else:
        ok(True, "всё чтение, кроме списка выходов, спрашивает сессию")


def t_changes():
    print("\n— изменения: форма проверяется здесь, смысл — агентом")

    def sent(method, path, body):
        ASKED.clear()
        status, answer = new(method, path, body)
        return status, answer, (ASKED[-1] if ASKED else None)

    status, _, call = sent("POST", "/api/clients", {"name": "carl", "expires": ""})
    ok(status == 200 and call["args"] == ["add", "carl"], "новый пользователь без срока", call)
    ok(sent("POST", "/api/clients", {"name": "carl", "expires": "7d"})[2]["args"] == ["add", "carl", "7d"], "…и со сроком")
    ok(new("POST", "/api/clients", {"name": "-x"}) == (400, {"ok": False, "error": "имя: латиница, цифры, _ и -, до 32 символов"}) and new("POST", "/api/clients", {}) [0] == 400
       and new("POST", "/api/clients", {"name": "ok\n"})[0] == 400 and new("POST", "/api/clients", {"name": "x" * 33})[0] == 400, "негодное имя — 400 со словами для админа")
    ok(new("POST", "/api/clients", {"name": "carl", "expires": "7 days"}) == (400, {"ok": False, "error": "срок: например 12h, 7d, 4w"}), "негодный срок — 400 со своими словами")
    REPLIES["add"] = {"ok": False, "error": "уже есть"}
    ok(new("POST", "/api/clients", {"name": "carl"}) == (502, {"ok": False, "error": "уже есть"}), "отказ агента создать — 502, как и было")
    del REPLIES["add"]
    ok(sent("DELETE", "/api/clients/carl", None)[2]["args"] == ["remove", "carl"] and new("DELETE", "/api/clients/-x")[0] == 400 and new("DELETE", "/api/clients/a/b")[0] == 400, "удаление одного")
    ok(sent("POST", "/api/clients/remove", {"names": ["a", "b"]})[2]["args"] == ["remove", "a", "b"], "удаление нескольких")
    ok(sent("POST", "/api/clients/expire", {"names": ["a"], "expires": "4w"})[2]["args"] == ["expire", "4w", "a"] and sent("POST", "/api/clients/expire", {"names": ["a"], "expires": ""})[2]["args"] == ["expire", "none", "a"]
       and sent("POST", "/api/clients/expire", {"names": ["a"]})[2]["args"] == ["expire", "none", "a"], "срок нескольким; пустой — «без срока»")
    ok(new("POST", "/api/clients/expire", {"names": ["a"], "expires": "soon"}) == (400, {"ok": False, "error": "срок: например 12h, 7d, 4w"})
       and new("POST", "/api/clients/expire", {"names": [], "expires": "1d"}) == (400, {"ok": False, "error": "некорректные данные"})
       and new("POST", "/api/clients/remove", {"names": ["a"] * 61})[0] == 400 and new("POST", "/api/clients/remove", {"names": ["a", "-b"]})[0] == 400 and new("POST", "/api/clients/remove", {"names": "a"})[0] == 400,
       "пустой список, больше 60 имён, негодное имя — 400")
    ok(sent("POST", "/api/clients/suspend", {"names": ["a"], "on": True})[2]["args"] == ["suspend", "on", "a"] and sent("POST", "/api/clients/suspend", {"names": ["a"], "on": False})[2]["args"] == ["suspend", "off", "a"]
       and new("POST", "/api/clients/suspend", {"names": ["a"], "on": 1})[0] == 400 and new("POST", "/api/clients/suspend", {"names": ["a"]})[0] == 400, "пауза и возобновление; «on» — только true или false")
    ok(sent("POST", "/api/clients/exit", {"names": ["a"], "exit": 3})[2]["args"] == ["user-exit", "3", "a"] and sent("POST", "/api/clients/exit", {"names": ["a"], "exit": None})[2]["args"] == ["user-exit", "auto", "a"]
       and new("POST", "/api/clients/exit", {"names": ["a"], "exit": 51})[0] == 400 and new("POST", "/api/clients/exit", {"names": ["a"], "exit": "3"})[0] == 400 and new("POST", "/api/clients/exit", {"names": ["a"], "exit": True})[0] == 400,
       "выход пользователя: номер 1…50 или авто")
    for status in (502,):
        REPLIES["remove"] = {"ok": False, "error": "нет", "removed": ["a"]}
        ok(new("POST", "/api/clients/remove", {"names": ["a", "b"]}) == (status, {"ok": False, "error": "нет", "removed": ["a"]}), "отказ агента удалить — 502, с тем, что он успел")
        del REPLIES["remove"]

    groups = [{"id": "g00001", "name": "Дом", "space": 1, "exit": None, "filter": "kids", "members": ["a", "b"], "junk": 1}, {"id": None, "name": "Новая", "members": []}]
    status, _, call = sent("POST", "/api/ugroups", {"base": "0123456789ab", "groups": groups})
    ok(status == 200 and call["args"][0] == "ugroups-set" and packed(call["args"][1]) == {"base": "0123456789ab", "groups": [
        {"id": "g00001", "name": "Дом", "space": True, "exit": None, "filter": "kids", "members": ["a", "b"]}, {"id": None, "name": "Новая", "space": False, "members": []}]},
       "группы: только известные поля; выход и фильтр — если страница их прислала; версия списка — в base", call and packed(call["args"][1]))
    ok(packed(sent("POST", "/api/ugroups", {"base": "zzz", "groups": []})[2]["args"][1]) == {"base": "", "groups": []}, "негодная версия списка — как без версии")
    levels = [{"id": "g00001", "name": "Дом", "filter": "kids", "kids_lists": ["social", "nsfw"], "members": []}, {"id": "g00002", "name": "Офис", "filter": "ultimate", "members": []}]
    ok(packed(sent("POST", "/api/ugroups", {"base": "0123456789ab", "groups": levels})[2]["args"][1])["groups"] == [
        {"id": "g00001", "name": "Дом", "space": False, "filter": "kids", "kids_lists": ["social", "nsfw"], "members": []}, {"id": "g00002", "name": "Офис", "space": False, "filter": "ultimate", "members": []}],
       "уровни фильтрации и списки детского режима уходят агенту; списков нет — их нет и в запросе")
    ok(all(new("POST", "/api/ugroups", {"groups": [{"name": "x", "filter": "kids", "kids_lists": bad}]}) == (400, {"ok": False, "error": "некорректные данные"})
           for bad in (["porn"], "nsfw", ["nsfw"] * 5)), "списки детского режима не той формы — 400")
    bad_groups = ([{"name": "x", "members": ["-bad"]}], [{"name": 5}], [{"name": "x", "filter": "strict"}], [{"name": "x", "exit": 100}], [{"name": "x", "exit": True}], [{"name": "x", "id": "x" * 13}],
                  [{"name": "x" * 201}], [{"name": "x"}] * 31, "nope", [{"name": "x", "members": ["a"] * 254}])
    ok(all(new("POST", "/api/ugroups", {"groups": bad}) == (400, {"ok": False, "error": "некорректные данные"}) for bad in bad_groups) and new("POST", "/api/ugroups", {})[0] == 400, "группы не той формы — 400")
    spaces = [{"id": "s00001", "name": "Игры", "on": True, "members": ["a"]}]
    ok(packed(sent("POST", "/api/spaces", {"base": "0123456789ab", "spaces": spaces})[2]["args"][1]) == {"base": "0123456789ab", "spaces": spaces}, "пространства уходят агенту как присланы")
    ok(new("POST", "/api/spaces", {"spaces": [{"name": "x", "members": ["a"] * 65}]}) == (400, {"ok": False, "error": "некорректный список"}) and new("POST", "/api/spaces", {"spaces": [{"members": []}]})[0] == 400
       and new("POST", "/api/spaces", {"spaces": [{"name": "x"}] * 31})[0] == 400 and new("POST", "/api/spaces", {})[0] == 400, "пространства не той формы — 400")

    site_groups = [{"id": "a00001", "name": "YouTube", "on": True, "items": ["youtube.com"], "auto": "x"}, {"id": None, "name": "По ссылке", "on": True, "url": "https://example.org/l.txt"}]
    call = sent("POST", "/api/sites", {"base": "0123456789ab", "groups": site_groups})[2]
    ok(call["args"][0] == "sites-set" and packed(call["args"][1]) == {"base": "0123456789ab", "groups": site_groups}, "сайты: группы уходят как присланы, со всеми своими полями", packed(call["args"][1]))
    bad_sites = ([{"name": "x", "items": ["a"] * 501}], [{"name": "x", "items": ["a" * 261]}], [{"name": "x", "items": [5]}], [{"name": "x", "url": "u" * 601}], [{"name": "x"}] * 61, [{"items": []}], [5], "x")
    ok(all(new("POST", "/api/sites", {"groups": bad}) == (400, {"ok": False, "error": "некорректный список"}) for bad in bad_sites) and new("POST", "/api/sites", {})[0] == 400, "сайты не той формы — 400")
    big = {"groups": [{"name": "x", "items": ["a" * 250] * 400}]}
    ok(new("POST", "/api/sites", big)[0] == 400, "тело больше предела (80 КБ) — как пустое: 400")
    ok(sent("POST", "/api/mode", {"mode": "geo"})[2]["args"] == ["mode-set", "geo"] and sent("POST", "/api/mode", {"mode": "list"})[2]["args"] == ["mode-set", "list"]
       and new("POST", "/api/mode", {"mode": "all"}) == (400, {"ok": False, "error": "некорректный режим"}) and new("POST", "/api/mode", {})[0] == 400, "режим: «по списку» или «всё зарубежное»")
    ok(sent("POST", "/api/sites/refresh", {"id": "a00005"})[2]["args"] == ["sites-refresh", "a00005"] and sent("POST", "/api/sites/refresh", {})[2]["args"] == ["sites-refresh"]
       and new("POST", "/api/sites/refresh", {"id": "A-1"}) == (400, {"ok": False, "error": "некорректные данные"}), "обновление списка по ссылке: один или все")
    routed = sent("POST", "/api/rules/route", {"changes": [{"rule": "a00005", "route": "block"}, {"rule": "b00001", "route": "off"}]})[2]["args"]
    ok(routed[0] == "rule-route" and len(routed) == 2 and json.loads(base64.b64decode(routed[1])) == {"changes": [{"rule": "a00005", "route": "block"}, {"rule": "b00001", "route": "off"}]}
       and new("POST", "/api/rules/route", {"changes": [{"rule": "a00005", "route": "sideways"}]}) == (400, {"ok": False, "error": "некорректные данные"})
       and new("POST", "/api/rules/route", {"changes": [{"rule": "A-1", "route": "exit"}]})[0] == 400 and new("POST", "/api/rules/route", {"changes": []})[0] == 400
       and new("POST", "/api/rules/route", {})[0] == 400
       and json.loads(base64.b64decode(sent("POST", "/api/rules/route", {"order": ["a00002", "a00001"], "base": "0123456789ab"})[2]["args"][1])) == {"changes": [], "order": ["a00002", "a00001"], "base": "0123456789ab"}
       and new("POST", "/api/rules/route", {"order": ["bad!"]})[0] == 400
       and json.loads(base64.b64decode(sent("POST", "/api/rules/route", {"changes": [{"rule": "a00005", "groups": ["g00001"]}]})[2]["args"][1])) == {"changes": [{"rule": "a00005", "groups": ["g00001"]}]}
       and new("POST", "/api/rules/route", {"changes": [{"rule": "a00005"}]})[0] == 400,
       "куда ведут правила: всё отложенное одним запросом — id и exit, direct, block или off")
    call = sent("POST", "/api/block", {"groups": [{"id": "b00001", "name": "Свои", "on": True, "kind": "block", "items": ["ads.example"]}], "lists": {"1": True, "2": False}, "safebrowsing": 1})[2]
    ok(call["args"][0] == "block-set" and packed(call["args"][1]) == {"groups": [{"id": "b00001", "name": "Свои", "on": True, "kind": "block", "items": ["ads.example"]}], "lists": {"1": True, "2": False}, "safebrowsing": True},
       "блокировка: группы как присланы, списки по номерам, «опасные сайты» — да или нет", packed(call["args"][1]))
    ok(packed(sent("POST", "/api/block", {"groups": []})[2]["args"][1]) == {"groups": [], "lists": {}, "safebrowsing": False}, "без списков и флага — пустые")
    ok(all(new("POST", "/api/block", bad) == (400, {"ok": False, "error": "некорректный список"}) for bad in ({"groups": [], "lists": {"x": True}}, {"groups": [], "lists": {"1": 1}}, {"groups": [], "lists": []},
                                                                                                              {"lists": {}}, {"groups": [], "lists": {str(i): True for i in range(51)}})), "блокировка не той формы — 400")
    lists = (({"op": "add", "name": "Мой", "url": "https://example.org/a.txt"}, {"op": "add", "name": "Мой", "url": "https://example.org/a.txt"}),
             ({"op": "remove", "id": 7, "name": "лишнее"}, {"op": "remove", "id": 7}), ({"op": "rename", "id": 7, "name": "Новое"}, {"op": "rename", "id": 7, "name": "Новое"}),
             ({"op": "refresh", "id": 7}, {"op": "refresh"}))
    ok(all(packed(sent("POST", "/api/block/lists", body)[2]["args"][1]) == want for body, want in lists), "списки блокировки по ссылке: агенту — вид изменения и только его поля")
    ok(all(new("POST", "/api/block/lists", bad) == (400, {"ok": False, "error": "некорректные данные"}) for bad in (
        {"op": "add", "name": "", "url": "u"}, {"op": "add", "name": "x"}, {"op": "add", "name": "x", "url": "u" * 601}, {"op": "remove", "id": "7"}, {"op": "remove", "id": 0},
        {"op": "remove", "id": True}, {"op": "rename", "id": 7}, {"op": "drop"}, {})), "списки по ссылке не той формы — 400")

    ok(sent("POST", "/api/dns", {"name": "tv"})[2]["args"] == ["dns-add", "tv"] and sent("DELETE", "/api/dns/tv", None)[2]["args"] == ["dns-remove", "tv"]
       and new("POST", "/api/dns", {"name": "t v"}) == (400, {"ok": False, "error": "имя: латиница, цифры, _ и -, до 32 символов"}) and new("DELETE", "/api/dns/-x") == (400, {"ok": False, "error": "некорректное имя"}), "устройства DNS")

    status, _, call = sent("POST", "/api/exits/add", {"address": "203.0.113.10", "name": "exit3-de", "weight": 25, "password": "s3cret pass"})
    ok(status == 200 and call["args"][0] == "exit-add" and packed(call["args"][1]) == {"address": "203.0.113.10", "name": "exit3-de", "weight": 25, "ssh_port": 22} and call["secret"] == "s3cret pass"
       and "s3cret" not in " ".join(call["args"]) and "s3cret" not in base64.b64decode(call["args"][1]).decode(), "новый выход: пароль уходит агенту рядом с командой, а не в ней", call)
    call = sent("POST", "/api/exits/add", {"address": "203.0.113.10", "name": "exit3-de", "password": ""})[2]
    ok("secret" not in call and packed(call["args"][1])["weight"] == 1, "без пароля — без секрета; доля по умолчанию 1")
    bad_exits = ({"address": "example.com", "name": "x"}, {"address": "203.0.113.10", "name": "плохое"}, {"address": "203.0.113.10", "name": "x", "weight": 100}, {"address": "203.0.113.10", "name": "x", "weight": "5"},
                 {"address": "203.0.113.10", "name": "x", "ssh_port": 0}, {"address": "203.0.113.10", "name": "x", "password": "a\nb"}, {"address": "203.0.113.10", "name": "x", "password": "p" * 201}, {})
    ok(all(new("POST", "/api/exits/add", bad) == (400, {"ok": False, "error": "адрес сервера (IPv4), имя латиницей и, если нужен, пароль root"}) for bad in bad_exits), "новый выход не той формы — 400 с подсказкой")
    ok(sent("POST", "/api/exits/rename", {"index": 3, "name": "exit3.de"})[2]["args"] == ["exit-rename", "3", "exit3.de"]
       and new("POST", "/api/exits/rename", {"index": 3, "name": "a b"}) == (400, {"ok": False, "error": "имя выхода: латиница, цифры, точка, дефис, до 32 знаков"}) and new("POST", "/api/exits/rename", {"index": 51, "name": "x"})[0] == 400, "переименование выхода")
    ok(sent("POST", "/api/exits/country", {"index": 3, "country": "de"})[2]["args"] == ["exit-country", "3", "de"] and sent("POST", "/api/exits/country", {"index": 1, "country": "auto"})[2]["args"] == ["exit-country", "1", "auto"]
       and all(new("POST", "/api/exits/country", bad) == (400, {"ok": False, "error": "страна выхода: код ISO из двух строчных латинских букв или auto"})
               for bad in ({"index": 3, "country": "DE"}, {"index": 3, "country": "deu"}, {"index": 3, "country": ""}, {"index": 3, "country": None}, {"index": 0, "country": "de"}, {"index": "3", "country": "de"}, {"index": 3})),
       "страна выхода: агенту — номер и код (или auto); не та форма — 400 с подсказкой")
    REPLIES["exit-country"] = {"ok": False, "error": "нет такой страны: нужен код ISO 3166 из двух латинских букв, например nl или de"}
    ok(new("POST", "/api/exits/country", {"index": 3, "country": "zz"}) == (400, {"ok": False, "error": "нет такой страны: нужен код ISO 3166 из двух латинских букв, например nl или de"}), "отказ агента — 400 его словами")
    del REPLIES["exit-country"]
    ok(sent("POST", "/api/exits/remove", {"index": 3})[2]["args"] == ["exit-remove", "3"] and sent("POST", "/api/exits/remove", {"index": 3, "force": True})[2]["args"] == ["exit-remove", "3", "force"]
       and sent("POST", "/api/exits/remove", {"index": 3, "force": 1})[2]["args"] == ["exit-remove", "3"] and new("POST", "/api/exits/remove", {"index": "3"}) == (400, {"ok": False, "error": "некорректные данные"}), "удаление выхода; force — только true")
    ok(sent("POST", "/api/exits/update", {"index": 1})[2]["args"] == ["exit-update", "1"] and sent("POST", "/api/exits/update", {"index": 1, "reboot": True})[2]["args"] == ["exit-update", "1", "reboot"], "обновление выхода, с перезагрузкой и без")
    REPLIES["exit-remove"] = {"ok": False, "error": "на нём 5 пользователей", "confirm": True}
    ok(new("POST", "/api/exits/remove", {"index": 3}) == (409, {"ok": False, "error": "на нём 5 пользователей", "confirm": True}), "агент просит подтвердить — 409")
    REPLIES["exit-remove"] = {"ok": False, "error": "нет такого"}
    ok(new("POST", "/api/exits/remove", {"index": 3})[0] == 400, "прочий отказ агента — 400")
    del REPLIES["exit-remove"]
    call = sent("POST", "/api/exits/weights", {"weights": {"1": 75, "3": 25}, "base": {"1": 2, "3": 1}})[2]
    ok(call["args"][0] == "weights-set" and packed(call["args"][1]) == {"weights": {"1": 75, "3": 25}, "base": {"1": 2, "3": 1}} and packed(sent("POST", "/api/exits/weights", {"weights": {"1": 100}})[2]["args"][1]) == {"weights": {"1": 100}}, "доли выходов в процентах")
    ok(all(new("POST", "/api/exits/weights", bad) == (400, {"ok": False, "error": "доля: целое число процентов от 1 до 99"}) for bad in ({"weights": {"1": 0}}, {"weights": {"1": 101}}, {"weights": {"x": 5}}, {"weights": {"123": 5}},
                                                                                                                                 {"weights": {}}, {"weights": {"1": 5.5}}, {"weights": {"1": 5}, "base": {"1": "2"}}, {})), "доли не той формы — 400")
    REPLIES["weights-set"] = {"ok": False, "error": "доли изменились", "conflict": True}
    ok(new("POST", "/api/exits/weights", {"weights": {"1": 100}})[0] == 409, "доли изменены в другом окне — 409")
    del REPLIES["weights-set"]

    call = sent("POST", "/api/invites", {"group": "g00001", "uses": 3, "days": 7})[2]
    ok(call["args"][0] == "invite-add" and packed(call["args"][1]) == {"group": "g00001", "uses": 3, "days": 7} and packed(sent("POST", "/api/invites", {"uses": 1, "days": 1})[2]["args"][1])["group"] is None, "новое приглашение")
    ok(all(new("POST", "/api/invites", bad) == (400, {"ok": False, "error": "некорректные данные"}) for bad in ({"uses": 0, "days": 1}, {"uses": 11, "days": 1}, {"uses": 1, "days": 31}, {"uses": True, "days": 1},
                                                                                                             {"uses": 1, "days": "3"}, {"group": "G1", "uses": 1, "days": 1}, {})), "приглашение не той формы — 400")
    link = "https://vpn.example.org/i/" + "Zx3kQ0aB1cD2eF3gH4iJ5k"
    ASKED.clear()
    status, answer = new("POST", "/api/invites/qr", {"link": link})
    ok(status == 200 and answer["ok"] and base64.b64decode(answer["png"]).startswith(b"\x89PNG") and not ASKED, "QR-код ссылки-приглашения рисует сама панель, агент не спрошен")
    ok(all(new("POST", "/api/invites/qr", bad) == (400, {"ok": False, "error": "некорректные данные"}) for bad in ({"link": "ftp://vpn.example.org/i/" + "A" * 22}, {"link": link + "/info"}, {"link": "https://vpn.example.org/i/short"},
                                                                                                            {"link": "javascript:alert(1)"}, {"link": link + "\n"}, {"link": 5}, {})), "QR-код только ссылки-приглашения — 400")
    ok(new("POST", "/api/invites/qr", {"link": "http://192.0.2.1:8080/i/" + "A" * 22})[0] == 200, "QR-код и ссылки панели без TLS (как её показывает страница)")
    ok(new("POST", "/api/invites/qr", {"link": link}, cookie=None)[0] == 401, "QR-код ссылки только со входом")
    ok(sent("DELETE", "/api/invites/abc123", None)[2]["args"] == ["invite-remove", "abc123"] and new("DELETE", "/api/invites/ABC") == (400, {"ok": False, "error": "некорректные данные"}), "отзыв приглашения")
    for method, path in (("POST", "/api/clients"), ("POST", "/api/ugroups"), ("POST", "/api/sites"), ("POST", "/api/block"), ("POST", "/api/exits/add"), ("POST", "/api/exits/weights"), ("POST", "/api/invites"),
                         ("DELETE", "/api/clients/a"), ("DELETE", "/api/dns/a"), ("DELETE", "/api/invites/abc123"), ("POST", "/api/mode"), ("POST", "/api/spaces")):
        ASKED.clear()
        if new(method, path, {}, cookie=None)[0] != 401 or new(method, path, {}, headers={})[0] != 400 or ASKED:
            ok(False, f"{method} {path} без сессии или без X-Panel не должен доходить до агента")
            break
    else:
        ok(True, "ни одно изменение не проходит без сессии и без заголовка страницы")


TOKEN = "A" * 22


def declares(length, path, headers):
    """Sends only the head of a POST that says its body is `length` bytes long — and not a byte of it. → the status line and the body, or None when the server waits for the body."""
    with socket.create_connection(("127.0.0.1", NEW_PORT), timeout=3) as connection:
        head = [f"POST {path} HTTP/1.1", "Host: x", "Content-Type: application/json", f"Content-Length: {length}", *[f"{name}: {value}" for name, value in headers.items()]]
        connection.sendall(("\r\n".join(head) + "\r\n\r\n").encode())
        answer = b""
        try:
            while b"}" not in answer:                     # the answers here are one small JSON object
                piece = connection.recv(4096)
                if not piece:
                    break
                answer += piece
        except TimeoutError:
            return None
    text = answer.decode()
    return text.split("\r\n")[0], text.split("\r\n\r\n", 1)[-1]


class FakePrometheus(http.server.BaseHTTPRequestHandler):
    seen = []
    status = 200
    reply = {}

    def do_GET(self):
        FakePrometheus.seen.append(self.path)
        body = json.dumps(FakePrometheus.reply).encode()
        self.send_response(FakePrometheus.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def t_invites():
    print("\n— приглашения: то, что видят посторонние")

    def visitor(method, path, body=None, who="198.51.100.1", headers=None):
        return ask(NEW_PORT, method, path, body, headers={"X-Real-Ip": who, **(headers or {})})
    status, head, page = visitor("GET", f"/i/{TOKEN}")
    ok(status == 200 and page == open(os.path.join(REPO, "panel", "invite.html"), "rb").read() and head["Content-Type"] == "text/html; charset=utf-8", "страница приглашения — без входа, одна для любого секрета")
    ok(head["Content-Security-Policy"].startswith("default-src 'none'") and head["X-Frame-Options"] == "DENY" and head["Cache-Control"] == "no-store" and head["Referrer-Policy"] == "no-referrer"
       and head["X-Robots-Tag"] == "noindex, nofollow" and head["X-Content-Type-Options"] == "nosniff" and "Strict-Transport-Security" in head, "со своей политикой: ничего не кэшируется, не индексируется, не утекает", head)
    ok(visitor("GET", f"/i/{TOKEN}/")[0] == 200, "косая черта в конце допустима")
    for path in ("/i/short", f"/i/{TOKEN}x", f"/i/{TOKEN}/other", f"/i/{TOKEN}/info/x", "/i/", f"/i/{'!' * 22}"):
        status, head, answer = visitor("GET", path, who="198.51.100.2")
        ok(status == 404 and answer == {"ok": False, "error": "not found"} and head["X-Robots-Tag"] == "noindex, nofollow", f"адрес не того вида ({path}) — 404")
    REPLIES["invite-info"] = {"ok": True, "group": "Дом", "left": 2, "total": 3, "expires_at": 1999999999, "secret_thing": "x", "id": "abc123"}
    ASKED.clear()
    status, head, answer = visitor("GET", f"/i/{TOKEN}/info")
    ok(status == 200 and answer == {"ok": True, "group": "Дом", "left": 2, "total": 3, "expires_at": 1999999999} and ASKED == [{"args": ["invite-info", TOKEN], "by": "panel"}] and head["Cache-Control"] == "no-store",
       "сведения о приглашении: только пять полей, остальное от агента не показывается", answer)
    REPLIES["invite-info"] = {"ok": False, "error": "внутренняя причина"}
    ok(visitor("GET", f"/i/{TOKEN}/info")[0::2] == (404, {"ok": False, "error": "Ссылка недействительна или уже использована"}), "неверный секрет — одна фраза, причина агента не показывается")
    wrong = [visitor("GET", f"/i/{TOKEN}/info", who="198.51.100.3")[0] for _ in range(8)]
    ok(wrong == [404] * 6 + [429] * 2, "неверный секрет стоит 5 очков: после шести попыток адрес ждёт", wrong)
    pages = [visitor("GET", f"/i/{TOKEN}", who="198.51.100.4")[0] for _ in range(32)]
    ok(pages == [200] * 30 + [429] * 2, "страница стоит 1 очко: тридцать показов за 10 минут", pages)
    ok(visitor("GET", f"/i/{TOKEN}", who="198.51.100.4")[2] == {"ok": False, "error": "слишком много запросов"} and visitor("GET", f"/i/{TOKEN}", who="198.51.100.5")[0] == 200, "бюджет у каждого адреса свой")

    REPLIES["invite-redeem"] = {"ok": True, "name": "phone", "uri": "vpn://x", "uri_png": "p", "conf": "c", "conf_png": "q", "left": 1, "group": "Дом", "private": "never"}
    ASKED.clear()
    status, head, answer = visitor("POST", f"/i/{TOKEN}", {"name": "phone"}, who="198.51.100.6", headers={"X-Invite": "1"})
    ok(status == 200 and answer == {"ok": True, "name": "phone", "uri": "vpn://x", "uri_png": "p", "conf": "c", "conf_png": "q", "left": 1, "group": "Дом"} and ASKED == [{"args": ["invite-redeem", TOKEN, "phone"], "by": "panel"}]
       and head["Cache-Control"] == "no-store", "использование приглашения: человек получает свой конфиг и ничего лишнего", answer)
    ok(visitor("POST", f"/i/{TOKEN}", {"name": "phone"}, who="198.51.100.7")[0::2] == (429, {"ok": False, "error": "слишком много запросов"}), "без заголовка страницы (X-Invite) — отказ")
    ok(visitor("POST", f"/i/{TOKEN}/info", {"name": "phone"}, who="198.51.100.7", headers={"X-Invite": "1"})[0] == 404, "POST на /info — нет такого")
    ok(visitor("POST", f"/i/{TOKEN}", {"name": "мой телефон"}, who="198.51.100.7", headers={"X-Invite": "1"})[0::2] == (400, {"ok": False, "error": "Имя: латинские буквы, цифры, _ и -, до 32 символов"})
       and visitor("POST", f"/i/{TOKEN}", {"name": "x" * 400}, who="198.51.100.7", headers={"X-Invite": "1"})[0] == 400, "негодное имя устройства и слишком длинное тело — 400 со словами для человека")
    REPLIES["invite-redeem"] = {"ok": False, "error": "Traceback: внутренности", "shown": False}
    ok(visitor("POST", f"/i/{TOKEN}", {"name": "phone"}, who="198.51.100.8", headers={"X-Invite": "1"})[0::2] == (400, {"ok": False, "error": "Не получилось создать подключение — попробуйте ещё раз чуть позже"}),
       "чужие слова агента не показываются — одна фраза")
    REPLIES["invite-redeem"] = {"ok": False, "error": "Такое имя уже занято", "shown": True}
    ok(visitor("POST", f"/i/{TOKEN}", {"name": "phone"}, who="198.51.100.8", headers={"X-Invite": "1"})[2] == {"ok": False, "error": "Такое имя уже занято"}, "слова, написанные агентом для человека, показываются")
    tries = [visitor("POST", f"/i/{TOKEN}", {"name": "phone"}, who="198.51.100.9", headers={"X-Invite": "1"})[0] for _ in range(8)]
    ok(tries == [400] * 6 + [429] * 2, "неудачная попытка стоит 5 очков (3 + 2): после шести адрес ждёт", tries)
    del REPLIES["invite-redeem"], REPLIES["invite-info"]
    ASKED.clear()
    answer = declares(50_000_000, f"/i/{TOKEN}", {"X-Invite": "1", "X-Real-Ip": "198.51.100.20"})
    ok(answer is not None and " 400 " in answer[0] and "Имя: латинские буквы" in answer[1] and not ASKED, "посторонний заявляет тело в 50 МБ — отказ сразу, сервер не ждёт и не читает ни байта", answer)
    answer = declares(100_000, "/api/sites", {"X-Panel": "1", "Cookie": COOKIE})
    ok(answer is not None and " 400 " in answer[0] and "некорректный список" in answer[1] and not ASKED, "и админское тело сверх предела не читается", answer)


def t_app():
    print("\n— приложение: только из туннеля, по адресу устройства")

    def device(method, path, body=None, who="172.16.17.14", headers=None):
        return ask(NEW_PORT, method, path, body, headers={"X-Real-Ip": who, **(headers or {})})
    REPLIES["app-me"] = {"ok": True, "name": "phone", "owner": False}
    ASKED.clear()
    status, head, answer = device("GET", "/app/api/me")
    ok(status == 200 and answer == {"ok": True, "name": "phone", "owner": False} and ASKED == [{"args": ["app-me", "172.16.17.14"], "by": "panel"}] and head["Cache-Control"] == "no-store",
       "кто я: агент спрошен об адресе, с которого пришёл запрос", [status, answer, ASKED])
    ASKED.clear()
    outside = [device("GET", "/app/api/me", who=who)[0::2] for who in ("198.51.100.30", "172.16.18.14", "", "not-an-address", "::1")]
    ok(all(seen == (403, {"ok": False, "error": "только через VPN"}) for seen in outside) and not ASKED, "не из туннеля — 403, агент не спрошен", outside)
    REPLIES["app-me"] = {"ok": False, "error": "это устройство не найдено в сети"}
    ok(device("GET", "/app/api/me")[0::2] == (404, {"ok": False, "error": "это устройство не найдено в сети"}), "адреса нет среди пользователей — 404")
    del REPLIES["app-me"]
    ASKED.clear()
    exits = {"ok": True, "exits": [{"index": 1, "name": "exit1-nl", "address": "198.51.100.38", "country": "nl", "up": True, "share": 67, "users": 17, "online": 4},
                                   {"index": 3, "name": "exit3", "address": "203.0.113.20", "country": None, "up": False, "share": 33, "users": 0, "online": 0}]}
    REPLIES["app-exits"] = exits
    status, _, answer = device("GET", "/app/api/exits")
    del REPLIES["app-exits"]
    ok(status == 200 and ASKED == [{"args": ["app-exits"], "by": "panel"}] and answer == exits, "выходы: ответ агента — как есть, с адресом, страной и числом людей", [status, answer])
    ASKED.clear()
    status, _, answer = device("POST", "/app/api/exit", {"exit": 3}, headers={"X-Awgscale": "1"})
    ok(status == 200 and ASKED == [{"args": ["app-exit", "172.16.17.14", "3"], "by": "panel"}], "выбор выхода — для этого устройства", [status, ASKED])
    ASKED.clear()
    device("POST", "/app/api/exit", {"exit": "auto"}, headers={"X-Awgscale": "1"})
    ok(ASKED == [{"args": ["app-exit", "172.16.17.14", "auto"], "by": "panel"}], "auto — как есть", ASKED)
    ASKED.clear()
    refused = [device("POST", "/app/api/exit", body, headers=headers)[0::2] for body, headers in (
        ({"exit": 3}, {}), ({"exit": 3}, {"X-Awgscale": "0"}), ({"exit": 0}, {"X-Awgscale": "1"}), ({"exit": "3"}, {"X-Awgscale": "1"}), ({"exit": "any"}, {"X-Awgscale": "1"}), ({}, {"X-Awgscale": "1"}))]
    ok([status for status, _ in refused] == [400] * 6 and not ASKED, "без заголовка приложения или с негодным выходом — 400, агент не спрошен", refused)
    ok(device("POST", "/app/api/exit", {"exit": 3}, who="198.51.100.31", headers={"X-Awgscale": "1"})[0] == 403 and not ASKED, "выбор выхода не из туннеля — 403")
    ASKED.clear()
    status, _, answer = device("POST", "/app/api/filter", {"filter": "proplus"}, headers={"X-Awgscale": "1"})
    ok(status == 200 and ASKED == [{"args": ["app-filter", "172.16.17.14", "proplus"], "by": "panel"}], "своя защита — для этого устройства", [status, ASKED])
    ASKED.clear()
    refused = [device("POST", "/app/api/filter", body, headers=headers)[0] for body, headers in (
        ({"filter": "pro"}, {}), ({"filter": "max"}, {"X-Awgscale": "1"}), ({}, {"X-Awgscale": "1"}))]
    ok(refused == [400] * 3 and not ASKED, "своя защита без заголовка или с чужим уровнем — 400", refused)
    ASKED.clear()
    status, _, answer = device("POST", "/app/api/route", {"route": "all"}, headers={"X-Awgscale": "1"})
    ok(status == 200 and ASKED == [{"args": ["app-route", "172.16.17.14", "all"], "by": "panel"}], "свой маршрут — для этого устройства", [status, ASKED])
    ASKED.clear()
    refused = [device("POST", "/app/api/route", body, headers=headers)[0] for body, headers in (
        ({"route": "all"}, {}), ({"route": "geo"}, {"X-Awgscale": "1"}), ({}, {"X-Awgscale": "1"}))]
    ok(refused == [400] * 3 and not ASKED, "свой маршрут без заголовка или с чужим значением — 400", refused)
    ok(device("GET", "/app/api/other")[0] == 404 and device("GET", "/api/state")[0] == 401, "других путей у приложения нет, а панель без входа закрыта")
    REPLIES["invite-redeem"] = {"ok": True, "name": "phone", "uri": "vpn://x", "uri_png": "p", "conf": "c", "conf_png": "q", "left": 0, "group": None, "owner": True}
    status, _, answer = ask(NEW_PORT, "POST", f"/i/{TOKEN}", {"name": "phone"}, headers={"X-Invite": "1", "X-Real-Ip": "198.51.100.32"})
    ok(status == 200 and answer.get("owner") is True, "код владельца: в ответе сказано, что устройство — владелец", answer)
    del REPLIES["invite-redeem"]


def t_static():
    print("\n— страница и её файлы")
    status, head, body = ask(NEW_PORT, "GET", "/")
    stamped = body.split(b'content="')[1].split(b'"')[0].decode()
    ok(status == 200 and len(stamped) == 12 and b"__UI_ID__" not in body and "script-src 'self'" in head["Content-Security-Policy"] and head["Cache-Control"] == "no-store" and head["X-Frame-Options"] == "DENY"
       and head["Strict-Transport-Security"] == "max-age=15552000" and head["Referrer-Policy"] == "no-referrer", "страница: с меткой сборки и заголовками защиты, без входа")
    state = new("GET", "/api/state")[1]
    ok(state["ui"] == stamped and state["ok"] is True and isinstance(state["server_time"], float), "состояние несёт ту же метку сборки, что и страница")
    time.sleep(0.01)
    open(os.path.join(WEB, "index.html"), "a").write("<!-- a new build -->")
    ok(new("GET", "/api/state")[1]["ui"] != stamped and ask(NEW_PORT, "GET", "/")[2].count(new("GET", "/api/state")[1]["ui"].encode()) == 1, "сборка изменилась на диске — метка другая без перезапуска")
    status, head, body = ask(NEW_PORT, "GET", "/_next/static/chunks/a1.js")
    ok(status == 200 and body == b"console.log(1)" and head["Content-Type"] == "text/javascript; charset=utf-8" and head["Cache-Control"] == "public, max-age=31536000, immutable", "скрипт сборки — навсегда в кэш")
    ok(ask(NEW_PORT, "GET", "/_next/index.txt")[1]["Cache-Control"] == "no-store", "файл вне static/ не кэшируется")
    for path in ("/_next/../secret.txt", "/_next/%2e%2e/secret.txt", "/_next/.hidden", "/_next/static", "/_next/", "/fonts/../server.py", "/fonts/x.ttf", "/fonts/none.woff2", "/index.html", "/secret.txt", "/api/none", "/docs", "/openapi.json"):
        status, _, body = ask(NEW_PORT, "GET", path)
        ok(status == 404 and body == {"ok": False, "error": "not found"}, f"вне сборки ничего нет: {path}", (status, body))
    status, head, body = ask(NEW_PORT, "GET", "/fonts/" + FONT)
    ok(status == 200 and head["Content-Type"] == "font/woff2" and head["Cache-Control"] == "public, max-age=2592000" and body == open(os.path.join(FONTS, FONT), "rb").read(), "шрифт страницы — на месяц в кэш")
    for method in ("PUT", "PATCH", "OPTIONS", "HEAD"):
        status, _, body = ask(NEW_PORT, method, "/api/sites", cookie=COOKIE)
        ok(status == 404 and (method == "HEAD" or body == {"ok": False, "error": "not found"}), f"{method} — такого здесь нет: 404", (status, body))
    ok(ask(NEW_PORT, "GET", "/")[1].get("Server") == "panel", "о себе сервер говорит только «panel»", ask(NEW_PORT, "GET", "/")[1].get("Server"))


def t_state_and_metrics():
    print("\n— состояние каскада и счётчики для Prometheus")
    time.sleep(2.5)
    state = new("GET", "/api/state")[1]
    ok("clients" not in state and state["users"]["users"] == 2 and state["totals"] == {"down_bps": 800.0, "up_bps": 8.0, "via_exits_down_bps": 0.0, "online": 1, "clients": 2},
       "в состоянии пользователи сосчитаны, а не перечислены; итоги — из счёта агента", state["totals"])
    ok(len(state["history"]) >= 2 and set(state["history"][0]) == {"t", "down", "up"} and "up_bps" in state["exits"][0], "скорости выходов и точки графика копятся между опросами", state["history"][:2])
    status, page = new("GET", "/api/clients?q=an&status=online&sort=traffic&desc=true&offset=0&limit=2")
    ok(status == 200 and [client["name"] for client in page["clients"]] == ["ann", "bob"] and page["total"] == 2 and page["clients"][0]["online"] is True, "страница пользователей — как ответил агент", page)
    ok(new("GET", "/api/clients?limit=101")[0] == 422 and new("GET", "/api/clients?status=sleeping")[0] == 422 and new("GET", "/api/clients?group=-x")[0] == 422
       and new("GET", "/api/clients?names=-x")[0] == 422 and new("GET", "/api/clients?unknown=1")[0] == 422, "запрос не того вида — 422, до агента не доходит")
    REPLIES["state"] = {"ok": False, "error": "агент молчит"}
    time.sleep(2.2)
    stale = new("GET", "/api/state")[1]
    ok(stale.get("stale") is True and stale.get("poll_error") == "агент молчит" and stale["time"] == state["time"] or stale["time"] >= state["time"], "опрос не удался — прежние данные с пометкой «устарело» и причиной", {k: stale.get(k) for k in ("stale", "poll_error", "ok")})
    status, head, text = ask(METRICS_PORT, "GET", "/metrics")
    ok(status == 200 and head["Content-Type"] == "text/plain; version=0.0.4; charset=utf-8" and b"vpn_panel_state_fresh 0\n" in text and b"vpn_user_bytes_total{" not in text, "устаревшее состояние: на порту метрик только признак несвежести", text[:200])
    del REPLIES["state"]
    time.sleep(2.2)
    text = ask(METRICS_PORT, "GET", "/metrics")[2].decode()
    ok('vpn_user_bytes_total{user="ann",dir="down"} 9000\n' in text and "vpn_panel_state_fresh 1\n" in text and "vpn_users_online 1\n" in text and 'vpn_exit_up{exit="exit1-nl"} 1\n' in text, "свежее состояние: счётчики пользователей и выходов", text[:300])
    ok(ask(METRICS_PORT, "GET", "/")[0] == 404 and ask(METRICS_PORT, "GET", "/api/state")[0] == 404 and ask(NEW_PORT, "GET", "/metrics")[0] == 404, "на порту метрик нет ничего, кроме /metrics, а на порту страницы нет метрик")

    from awgscale.panel.looks import PanelState, TrafficLook
    from awgscale.panel.metrics import metrics_text as metrics_of

    def as_now(state):
        """The state as the agent gives it now: the users counted, the exits with how many users, the counters by `traffic`."""
        clients = state.get("clients", [])
        now_state = {key: value for key, value in state.items() if key != "clients"}
        if "exits" in state:
            now_state["exits"] = [{**{key: value for key, value in exit_.items() if key != "clients"}, **({"users": len(exit_["clients"])} if "clients" in exit_ else {})} for exit_ in state["exits"]]
        if "clients" in state:
            now_state["users"] = {"users": len(clients), "online": sum(1 for client in clients if client.get("online"))}
        traffic = TrafficLook(ok=True, users=[(client["name"], client["rx"], client["tx"]) for client in clients if client.get("live")])
        return PanelState.model_validate(now_state), traffic

    def metrics_text(state, at):
        now_state, traffic = as_now(state)
        return metrics_of(now_state, moment_at(at), traffic)
    now = time.time()
    base = {"ok": True, "fetched_at": now, "clients": [{"name": "alice", "live": True, "rx": 100, "tx": 9000, "online": True}, {"name": "bob", "live": False, "rx": 1, "tx": 1, "online": True},
                                                       {"name": 'q"uo\\te', "live": True, "rx": 1, "tx": 2}, {"name": "float", "live": True, "rx": 1.5, "tx": 2}],
            "exits": [{"name": "exit1-nl", "state": "up", "probe_ms": 47.5, "clients": ["a", "b", "c"], "sys": {"load1": 0.05, "mem_total": 1000, "mem_used": 250, "disk_pct": 26}},
                      {"name": "exit2-nl", "state": "down", "probe_ms": None, "clients": []}, {"name": "exit3", "state": "?"}],
            "entry": {"load1": 0.3, "mem_total": 2000, "mem_used": 900, "disk_pct": 41}, "watch": {"ok": True, "last_tick": int(now) - 4, "tick_seconds": 0.31, "tick_failed": 2}}
    m = metrics_text(base, now)
    ok("# TYPE vpn_users gauge\nvpn_users 4\n" in m, "сколько пользователей всего — одной строкой")
    ok('vpn_user_bytes_total{user="alice",dir="down"} 9000\n' in m and 'vpn_user_bytes_total{user="alice",dir="up"} 100\n' in m and 'user="bob"' not in m and 'user="float"' not in m and 'user="q\\"uo\\\\te"' in m,
       "скачанное пользователем — то, что сервер ему отправил; о ком туннель не сообщил — нет строки; имя экранировано")
    ok("vpn_users_online 2\n" in m and 'vpn_exit_up{exit="exit1-nl"} 1\n' in m and 'vpn_exit_up{exit="exit2-nl"} 0\n' in m and "exit3" not in m and 'vpn_exit_probe_seconds{exit="exit1-nl"} 0.0475\n' in m
       and 'vpn_exit_probe_seconds{exit="exit2-nl"}' not in m and 'vpn_exit_clients{exit="exit1-nl"} 3\n' in m and 'vpn_exit_clients{exit="exit2-nl"} 0\n' in m, "выходы: состояние, время ответа, число клиентов")
    ok('vpn_server_load1{server="entry"} 0.3000\n' in m and 'vpn_server_memory_used_ratio{server="entry"} 0.4500\n' in m and 'vpn_server_disk_used_ratio{server="exit1-nl"} 0.2600\n' in m and 'server="exit2-nl"' not in m, "серверы: нагрузка, память, диск — у тех, кто о себе сообщил")
    ok("vpn_watch_up 1\n" in m and f"vpn_watch_last_tick_timestamp_seconds {int(now) - 4}\n" in m and "vpn_watch_tick_seconds 0.31\n" in m and "vpn_watch_ticks_failed_total 2\n" in m and m.endswith("\n"), "сторож: жив, последний проход, длительность, неудачи")
    for why, state, at in (("устарело", base, now + 60), ("помечено stale", {**base, "stale": True}, now), ("не ok", {**base, "ok": False}, now), ("пусто", {}, now)):
        m = metrics_text(state, at)
        ok("vpn_user_bytes_total{" not in m and "vpn_exit_up{" not in m and m == "# TYPE vpn_panel_state_fresh gauge\nvpn_panel_state_fresh 0\n", f"состояние {why}: только признак несвежести")
    fixed = 1_800_000_000.25
    m = metrics_text({**base, "fetched_at": fixed, "watch": {**base["watch"], "last_tick": int(fixed) - 4}}, fixed)
    ok(m == GOLDEN["metrics"], "текст метрик — тот же, что всегда, символ в символ", m)


def t_history():
    print("\n— история трафика (Prometheus)")
    from awgscale.panel.prometheus import Prometheus, traffic_report
    from awgscale.panel.settings import PanelSettings
    from awgscale.panel.state import StateBoard
    loop = asyncio.new_event_loop()

    def utc(*a):
        return int(datetime.datetime(*a, tzinfo=datetime.UTC).timestamp())
    now = utc(2026, 11, 12, 20, 30, 0)
    asked = []

    def series(query, start, end, step):
        asked.append((query, start, end, step))
        if step == 3600:
            return [{"metric": {"user": "alice", "dir": "down"}, "values": [[end, "1000.4"], [end - 3600, "500"], [end - 23 * 3600, "7"], [end - 24 * 3600, "999"]]},
                    {"metric": {"user": "alice", "dir": "up"}, "values": [[end, "10"]]}, {"metric": {"user": "idle", "dir": "down"}, "values": [[end, "0"], [end - 3600, "0.3"]]},
                    {"metric": {"dir": "down"}, "values": [[end, "5"]]}, {"metric": {"user": "x", "dir": "sideways"}, "values": [[end, "5"]]}]
        return [{"metric": {"user": "alice", "dir": "down"}, "values": [[end, "3000"], [end - 29 * 86400, "40"]]}, {"metric": {"user": "bob", "dir": "up"}, "values": [[end - 86400, "NaN"], [end - 2 * 86400, "77.6"]]}]

    async def ask_range(query, start, end, step):
        return series(query, start, end, step)
    report = loop.run_until_complete(traffic_report(now, ask_range)).model_dump()
    ok(len(report["hours"]) == 24 and report["hours"][-1] == "2026-11-12T23" and report["hours"][0] == "2026-11-12T00" and len(report["days"]) == 30 and report["days"][-1] == "2026-11-12" and report["days"][0] == "2026-10-14",
       "24 часа и 30 дней по Москве, старые первыми")
    (hours_query, hours_start, hours_end, _), (days_query, days_start, days_end, _) = asked
    ok(hours_end == utc(2026, 11, 12, 21, 0, 0) and hours_start == hours_end - 23 * 3600 and "[1h]" in hours_query and days_end == utc(2026, 11, 12, 21, 0, 0) and days_start == days_end - 29 * 86400 and "[1d]" in days_query,
       "часы спрашиваются на концах часов, дни — на московскую полночь")
    alice, bob = report["users"]["alice"], report["users"]["bob"]
    ok(alice["hours"][23] == [1000, 10] and alice["hours"][22] == [500, 0] and alice["hours"][0] == [7, 0] and alice["totals"] == {"day": [1507, 10], "month": [3040, 0]} and bob["days"][27] == [0, 78] and bob["totals"]["month"] == [0, 78]
       and set(report["users"]) == {"alice", "bob"}, "значения по своим часам и дням; NaN — ноль; без байта и без имени — не попадает", report["users"].keys())
    ok(report == GOLDEN["traffic"], "отчёт — тот же, что всегда")
    asked.clear()
    midnight = utc(2026, 11, 12, 21, 0, 0)

    async def nothing(*a):
        return []
    edge = loop.run_until_complete(traffic_report(midnight, nothing)).model_dump()
    ok(edge["days"][-1] == "2026-11-13" and edge["hours"][-1] == "2026-11-13T00" and edge["users"] == {}, "ровно в полночь начинается новый день; нет данных — пустой список")

    settings = PanelSettings(prometheus_url="http://127.0.0.1:1/")
    history = Prometheus(settings)
    first = loop.run_until_complete(history.traffic(ask_range))
    calls = len(asked)
    second = loop.run_until_complete(history.traffic(ask_range))
    ok(first["ok"] and first is second and calls == 2 and len(asked) == 2 and set(first) == {"ok", "time", "days", "hours", "users"}, "ответ держится полминуты: два запроса на первый вызов, ни одного на второй; форма прежняя")
    history.forget_traffic()

    async def broken(*a):
        raise ValueError("bad query")
    ok(loop.run_until_complete(history.traffic(broken)) == {"ok": False, "error": "История сейчас недоступна"} and loop.run_until_complete(history.traffic(ask_range))["ok"], "Prometheus не отвечает — ok=false, и ошибка не запоминается")
    ok(loop.run_until_complete(history.traffic()) is not None and loop.run_until_complete(Prometheus(PanelSettings(prometheus_url="")).traffic()) == {"ok": False, "error": "История не подключена"}, "адрес Prometheus не задан — ok=false без запросов")
    ok(new("GET", "/api/traffic") == (200, {"ok": False, "error": "История не подключена"}), "…и так же по HTTP")

    # through HTTP, the way the running server asks
    fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakePrometheus)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    real = Prometheus(PanelSettings(prometheus_url=f"http://127.0.0.1:{fake.server_address[1]}/"))
    FakePrometheus.reply = {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {"user": "alice", "dir": "down"}, "values": [[int(time.time()) // 3600 * 3600 + 3600, "2048"]]}]}}

    async def three_pages():
        return await asyncio.gather(real.traffic(), real.traffic(), real.traffic())
    answers = loop.run_until_complete(three_pages())
    ok(answers[0]["ok"] and answers[0] is answers[1] is answers[2] and len(FakePrometheus.seen) == 2 and all(path.startswith("/api/v1/query_range?") and "step=" in path for path in FakePrometheus.seen)
       and answers[0]["users"]["alice"]["totals"]["day"] == [2048, 0], "настоящий запрос по HTTP: три страницы разом — два запроса к Prometheus (часы и дни), ответ один на всех", FakePrometheus.seen)
    real.forget_traffic()
    FakePrometheus.status = 500
    ok(loop.run_until_complete(real.traffic()) == {"ok": False, "error": "История сейчас недоступна"}, "Prometheus отвечает 500 — ok=false")
    FakePrometheus.status = 200
    FakePrometheus.reply = {"status": "error", "error": "bad query"}
    ok(loop.run_until_complete(real.traffic())["ok"] is False, "ответ со status=error — тоже ok=false")
    FakePrometheus.reply = {"status": "success", "data": {"result": [{"metric": {"user": "alice"}, "value": [0, "1794690000.4"]}]}}
    FakePrometheus.seen.clear()
    ok(loop.run_until_complete(real.last_seen(moment_at(time.time()))) == {"alice": moment_at(1794690000)} and FakePrometheus.seen[0].startswith("/api/v1/query?") and "time=" in FakePrometheus.seen[0], "последняя связь — настоящим запросом", FakePrometheus.seen)
    fake.shutdown()

    rows = [{"metric": {"user": "alice"}, "value": [0, "1794690000.4"]}, {"metric": {}, "value": [0, "5"]}]

    async def seen(query, moment):
        return rows

    async def never(query, moment):
        raise ZeroDivisionError
    ok(loop.run_until_complete(history.last_seen(moment_at(1000), seen)) == {"alice": moment_at(1794690000)} and loop.run_until_complete(history.last_seen(moment_at(1060), never)) == {"alice": moment_at(1794690000)}
       and loop.run_until_complete(history.last_seen(moment_at(2000), never)) == {"alice": moment_at(1794690000)}, "последняя связь: по трафику; не чаще раза в две минуты; Prometheus молчит — прежний ответ")

    class FakeAgent:
        async def call(self, *args, timeout=0, secret=None):
            return {"ok": True, "time": 5000, "total": 4, "offset": 0,
                    "clients": [{"name": "alice", "ip": "1", "handshake": 0, "rx": 0, "tx": 0, "online": False}, {"name": "bob", "ip": "2", "handshake": 0, "rx": 0, "tx": 0, "online": False},
                                {"name": "carol", "ip": "3", "handshake": 4990, "rx": 0, "tx": 0, "online": True}, {"name": "dave", "ip": "4", "handshake": 0, "rx": 0, "tx": 0, "online": False}]}

    class FakeHistory:
        async def last_seen(self, now):
            return {"alice": moment_at(300), "bob": moment_at(10 ** 10)}
    board = StateBoard(settings, FakeAgent(), FakeHistory())
    people = {client["name"]: client for client in loop.run_until_complete(board.clients("e30=")).wire()["clients"]}
    ok(people["alice"]["handshake"] == 300 and people["alice"]["online"] is False and people["bob"]["handshake"] == 5000 and people["carol"]["handshake"] == 4990 and people["carol"]["online"] is True and people["dave"]["handshake"] == 0,
       "туннель перезапущен: время последней связи — по трафику, из будущего обрезается, «онлайн» не считается")


def hashed(value):
    """A body that is not JSON, as its fingerprint; the rest as JSON has it."""
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (list, tuple)):
        return [hashed(item) for item in value]
    if isinstance(value, dict):
        return {key: hashed(item) for key, item in value.items()}
    return value


def t_same_as_always():
    print("\n— те же запросы — те же ответы, что всегда")
    compared = ("Content-Type", "Cache-Control", "Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy", "Strict-Transport-Security", "X-Robots-Tag")
    REPLIES["invite-info"] = {"ok": True, "group": "Дом", "left": 2, "total": 3, "expires_at": 1999999999, "more": 1}
    REPLIES["invite-redeem"] = {"ok": True, "name": "phone", "uri": "vpn://x", "left": 1, "private": "never"}
    requests = [
        ("GET", "/", None, False), ("GET", "/_next/static/chunks/a1.js", None, False), ("GET", "/_next/index.txt", None, False), ("GET", "/_next/../secret.txt", None, False), ("GET", "/fonts/" + FONT, None, False),
        ("GET", "/fonts/x.ttf", None, False), ("GET", "/nothing", None, False), ("GET", "/api/sites", None, False), ("GET", "/api/exits", None, False), ("PUT", "/api/sites", None, True),
        ("GET", "/api/sites", None, True), ("GET", "/api/block", None, True), ("GET", "/api/spaces", None, True), ("GET", "/api/ugroups", None, True), ("GET", "/api/invites", None, True), ("GET", "/api/journal", None, True),
        ("GET", "/api/dns", None, True), ("GET", "/api/traffic", None, True), ("GET", "/api/diag?name=ann", None, True), ("GET", "/api/diag?name=-x", None, True), ("GET", "/api/clients/ann/qr", None, True),
        ("GET", "/api/clients/a.b/qr", None, True), ("GET", "/api/check?host=Example.com.", None, True), ("GET", "/api/check?host=..", None, True), ("GET", "/api/probe?host=example.com&path=/a%20b", None, True),
        ("GET", "/api/probe?host=example.com&path=x", None, True),
        ("POST", "/api/clients", {"name": "carl", "expires": "7d"}, True), ("POST", "/api/clients", {"name": "carl", "expires": ""}, True), ("POST", "/api/clients", {"name": "-x"}, True), ("POST", "/api/clients", {"name": "x", "expires": "y"}, True),
        ("POST", "/api/clients/remove", {"names": ["a", "b"]}, True), ("POST", "/api/clients/remove", {"names": []}, True), ("POST", "/api/clients/expire", {"names": ["a"], "expires": ""}, True),
        ("POST", "/api/clients/expire", {"names": ["a"], "expires": "2w"}, True), ("POST", "/api/clients/expire", {"names": ["a"], "expires": "x"}, True), ("POST", "/api/clients/suspend", {"names": ["a"], "on": True}, True),
        ("POST", "/api/clients/suspend", {"names": ["a"], "on": "yes"}, True), ("POST", "/api/clients/exit", {"names": ["a"], "exit": 3}, True), ("POST", "/api/clients/exit", {"names": ["a"], "exit": None}, True),
        ("POST", "/api/clients/exit", {"names": ["a"], "exit": 0}, True), ("DELETE", "/api/clients/carl", None, True), ("DELETE", "/api/clients/-x", None, True),
        ("POST", "/api/ugroups", {"base": "0123456789ab", "groups": [{"id": "g00001", "name": "Дом", "space": True, "exit": 3, "filter": "kids", "members": ["a"]}, {"id": None, "name": "Н", "members": []}]}, True),
        ("POST", "/api/ugroups", {"groups": [{"name": "x", "members": ["-a"]}]}, True), ("POST", "/api/ugroups", {}, True),
        ("POST", "/api/spaces", {"base": "0123456789ab", "spaces": [{"id": "s00001", "name": "Игры", "on": True, "members": ["a"]}]}, True), ("POST", "/api/spaces", {"spaces": [{"name": 5}]}, True),
        ("POST", "/api/sites", {"base": "0123456789ab", "groups": [{"id": "a00001", "name": "YouTube", "on": True, "items": ["youtube.com"]}, {"id": None, "name": "L", "on": True, "url": "https://example.org/l"}]}, True),
        ("POST", "/api/sites", {"groups": [{"name": "x", "items": [1]}]}, True), ("POST", "/api/mode", {"mode": "geo"}, True), ("POST", "/api/mode", {"mode": "x"}, True),
        ("POST", "/api/sites/refresh", {"id": "a00005"}, True), ("POST", "/api/sites/refresh", {}, True), ("POST", "/api/sites/refresh", {"id": "bad!"}, True),
        ("POST", "/api/block", {"groups": [{"id": "b00001", "name": "Свои", "on": True, "items": ["a.example"]}], "lists": {"1": True}, "safebrowsing": True}, True), ("POST", "/api/block", {"groups": [], "lists": {"x": True}}, True),
        ("POST", "/api/block/lists", {"op": "add", "name": "L", "url": "https://example.org/a"}, True), ("POST", "/api/block/lists", {"op": "remove", "id": 7}, True), ("POST", "/api/block/lists", {"op": "rename", "id": 7, "name": "N"}, True),
        ("POST", "/api/block/lists", {"op": "refresh"}, True), ("POST", "/api/block/lists", {"op": "rename", "id": 7}, True),
        ("POST", "/api/dns", {"name": "tv"}, True), ("POST", "/api/dns", {"name": "t v"}, True), ("DELETE", "/api/dns/tv", None, True), ("DELETE", "/api/dns/t%20v", None, True),
        ("POST", "/api/exits/add", {"address": "203.0.113.10", "name": "exit3-de", "weight": 25, "password": "pw"}, True), ("POST", "/api/exits/add", {"address": "x", "name": "y"}, True),
        ("POST", "/api/exits/rename", {"index": 3, "name": "e3"}, True), ("POST", "/api/exits/rename", {"index": 3, "name": "e 3"}, True), ("POST", "/api/exits/remove", {"index": 3, "force": True}, True),
        ("POST", "/api/exits/remove", {"index": 0}, True), ("POST", "/api/exits/update", {"index": 1, "reboot": True}, True), ("POST", "/api/exits/weights", {"weights": {"1": 75, "3": 25}, "base": {"1": 2, "3": 1}}, True),
        ("POST", "/api/exits/weights", {"weights": {"1": 0}}, True), ("POST", "/api/invites", {"group": "g00001", "uses": 2, "days": 3}, True), ("POST", "/api/invites", {"uses": 99, "days": 3}, True),
        ("DELETE", "/api/invites/abc123", None, True), ("DELETE", "/api/invites/NOPE", None, True), ("POST", "/api/nothing", {}, True), ("DELETE", "/api/nothing", None, True),
        ("GET", f"/i/{TOKEN}", None, False), ("GET", f"/i/{TOKEN}/info", None, False), ("GET", "/i/short", None, False), ("POST", f"/i/{TOKEN}/info", {"name": "p"}, False),
    ]
    different = []
    for (method, path, body, logged_in), expected in zip(requests, GOLDEN["requests"], strict=True):
        ASKED.clear()
        status, head, answer = ask(NEW_PORT, method, path, body, COOKIE if logged_in else None, {**CHANGE, "X-Real-Ip": "192.0.2.51"})
        seen = hashed([status, {name: head.get(name) for name in compared}, answer, list(ASKED)])
        if seen != expected:
            different.append((method, path, [part for part in zip(seen, expected) if part[0] != part[1]][:1]))
    ok(not different, f"{len(requests)} запросов: статус, заголовки, тело и то, что дошло до агента, — те же", different[:3])
    ASKED.clear()
    status, head, answer = ask(NEW_PORT, "POST", f"/i/{TOKEN}", {"name": "phone"}, headers={"X-Invite": "1", "X-Real-Ip": "192.0.2.60"})
    seen = hashed([status, {name: head.get(name) for name in compared}, answer, list(ASKED)])
    ok(seen == GOLDEN["redeem"] and status == 200, "использование приглашения — то же", seen)
    status, head, answer = ask(NEW_PORT, "POST", "/api/login", {"password": PASSWORD}, headers={"X-Real-Ip": "192.0.2.70"})
    cookie = head["Set-Cookie"]
    seen = [status, answer, cookie.split(";")[1:], len(cookie.split(";")[0])]
    ok(seen == GOLDEN["login"], "вход: тот же ответ и те же свойства cookie", seen)
    ok(ask(NEW_PORT, "POST", "/api/login", {"password": "wrong"}, headers={"X-Real-Ip": "192.0.2.71"})[0::2] == (403, {"ok": False, "error": "неверный пароль"}), "неверный пароль — как всегда")
    state = ask(NEW_PORT, "GET", "/api/state", cookie=COOKIE)[2]
    fields = {"answer": sorted(state), "exits": sorted(state["exits"][0]), "totals": sorted(state["totals"]), "history": sorted(state["history"][0])}
    ok(fields == GOLDEN["state_fields"], "состояние: те же поля у ответа, выходов, итогов и точек графика; пользователи сосчитаны (users), а не перечислены", fields)
    del REPLIES["invite-info"], REPLIES["invite-redeem"]


def main():
    try:
        t_session()
        t_units()
        t_reads()
        t_changes()
        t_invites()
        t_app()
        t_static()
        t_state_and_metrics()
        t_history()
        t_same_as_always()
    finally:
        for process in (NEW, SHORT):
            process.terminate()
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
