#!/usr/bin/env python3
"""The agent's daemon (awgscale.agent.daemon, .dispatcher, .commands): what it lets
through to a command and what it refuses itself, how it stops while a command
runs, and the real thing — `python -m awgscale.agent` on its own socket, asked to
stop in the middle of a command. And two edges of the invitations no other suite
reaches: the ceiling of forty, and what making a user for a guest takes.

    uv run python tests/agent_daemon.py

Nothing live is touched."""
import asyncio
import base64
import datetime
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from awgscale.agent import commands
from awgscale.agent.__main__ import nightly_trigger
from awgscale.agent.commands import InviteAdd
from awgscale.agent.daemon import Daemon
from awgscale.agent.dispatcher import Dispatcher
from awgscale.agent.errors import Refused
from awgscale.agent.invites.models import MAX_INVITES
from awgscale.agent.invites.newcomers import GuestUsers
from awgscale.agent.invites.service import InvitesService
from awgscale.agent.protocol import ASKING_DEVICE, Caller
from awgscale.agent.settings import AgentSettings
from awgscale.agent.users.models import Made, StoredGroups

PASS = FAIL = 0
TMP = tempfile.mkdtemp(prefix="agent-daemon-")
GONE = "Ссылка недействительна или уже использована"


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + json.dumps(seen, ensure_ascii=False, default=str)[:600])


def b64(value):
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode()


class DaemonUnderTest:
    """The daemon and its dispatcher, every command handed to whatever `agent` is put here (an object with serve_command), in a thread."""

    def __init__(self):
        self.agent = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.fresh()

    def fresh(self):
        """A dispatcher that has not been asked to stop, every command going to `agent`."""
        self.dispatcher = self.wait(self.make())
        self.daemon = Daemon(self.dispatcher, None, 1)

    async def make(self):
        dispatcher = Dispatcher()
        for command in commands.Command:
            dispatcher.register(command, self.handle)
        return dispatcher

    async def handle(self, request, caller, secret):
        if secret is None:
            return await asyncio.to_thread(self.agent.serve_command, request.to_args(), caller.value)
        return await asyncio.to_thread(self.agent.serve_command, request.to_args(), caller.value, secret.get_secret_value())

    def wait(self, work):
        return asyncio.run_coroutine_threadsafe(work, self.loop).result(30)

    @staticmethod
    def valid(args):
        return commands.parse(args) is not None

    def ask(self, request):
        """One line of the protocol → the answer, as a client of the socket gets it."""
        return json.loads(json.dumps(self.wait(self.daemon.answer(json.dumps(request).encode() + b"\n")), ensure_ascii=False))

    def serve(self, args, by=""):
        return self.ask({"args": args, "by": by})

    @property
    def running(self):
        return self.dispatcher.running

    def begin_stop(self, wait_seconds=10):
        """Asks to stop, without waiting for the commands that run."""
        asyncio.run_coroutine_threadsafe(self.dispatcher.stop(wait_seconds), self.loop)
        while not self.dispatcher.stopping:
            pass


D = DaemonUnderTest()


def t_daemon():
    print("\n— демон: передача команд, остановка, допустимые команды")
    trigger = nightly_trigger(AgentSettings())

    def at(hour, minute=0, day=13):
        return datetime.datetime(2026, 11, day, hour, minute, tzinfo=datetime.UTC)
    ok(trigger.get_next_fire_time(None, at(2)) == at(2, 20) and trigger.get_next_fire_time(at(2, 20), at(2, 20)) == at(2, 20, 14) and trigger.get_next_fire_time(None, at(23)) == at(2, 20, 14),
       "ночное обновление списков: до 02:20 UTC — сегодня, ровно в 02:20 и после — завтра")
    gate = threading.Event()
    seen = []

    class FakeAgent:
        @staticmethod
        def serve_command(args, by):
            seen.append((args, by))
            if args == ["journal"]:
                gate.wait(10)
            return {"ok": True, "args": args}
    D.agent = FakeAgent
    D.fresh()
    ok(D.serve(["state"], "panel") == {"ok": True, "args": ["state"]} and seen == [(["state"], "panel")] and D.running == 0, "команда передана агенту вместе с тем, кто спросил; счётчик вернулся к нулю")
    worker = threading.Thread(target=D.serve, args=(["journal"],))
    worker.start()
    for _ in range(100):
        if D.running == 1:
            break
        time.sleep(0.01)
    D.begin_stop()
    reply = D.serve(["state"], "panel")
    ok(reply.get("ok") is False and "перезапускается" in reply.get("error", "") and len(seen) == 2, "после просьбы остановиться новые команды не начинаются", reply)
    ok(D.running == 1, "идущая команда не оборвана — её ждут")
    gate.set()
    worker.join(5)
    ok(D.running == 0, "она закончилась — ждать больше нечего")
    D.fresh()
    ok(D.valid(["user-exit", "auto", "alice"]) and D.valid(["user-exit", "3", "alice", "bob"]) and not D.valid(["user-exit", "0", "alice"]) and not D.valid(["user-exit", "3"]) and not D.valid(["user-exit", "3", "-x"])
       and D.valid(["weights-set", "eyJ3IjoxfQ=="]) and not D.valid(["weights-set", "x y"]) and not D.valid(["cascade-set", "eyJ3IjoxfQ=="]) and not D.valid(["cascade-get"]),
       "демон пропускает от панели ручной выбор выхода и доли — и по-прежнему не пропускает настройки каскада целиком")
    ok(D.valid(["exits-get"]) and D.valid(["exit-add", "eyJhIjoxfQ=="]) and D.valid(["exit-attach", "eyJhIjoxfQ=="]) and D.valid(["exit-rename", "2", "exit-de"]) and D.valid(["exit-remove", "2"]) and D.valid(["exit-remove", "2", "force"])
       and D.valid(["exit-update", "2", "reboot"]) and not D.valid(["exit-update", "2", "force"]) and not D.valid(["exit-remove", "0"]) and not D.valid(["exit-rename", "2", "a b"]) and not D.valid(["exit-add", "x y"]),
       "демон пропускает команды управления выходами и только в их точном виде")
    ok(D.valid(["exit-country", "2", "nl"]) and D.valid(["exit-country", "2", "auto"]) and not D.valid(["exit-country", "2", "NL"]) and not D.valid(["exit-country", "2", "nld"])
       and not D.valid(["exit-country", "2", "Auto"]) and not D.valid(["exit-country", "0", "nl"]) and not D.valid(["exit-country", "2"]) and not D.valid(["exit-country", "2", "nl", "x"]),
       "демон пропускает страну выхода: номер и код из двух строчных латинских букв или auto — и только так")
    ok(D.valid(["mode-set", "geo"]) and not D.valid(["mode-set", "all"]) and not D.valid(["store-init"]) and not D.valid(["add", "-x"]), "демон пропускает только известные команды со строго оформленными аргументами")


def t_invite_commands():
    print("\n— демон: пять invite-команд")
    secret = "aB3_-" * 4 + "Zz"
    pay = b64({"group": "abcdef", "uses": 10, "days": 30})
    valid = D.valid
    ok(len(secret) == 22, "секрет для проверок — 22 знака")
    for args, why in ((["invite-list"], "invite-list"), (["invite-add", pay], "invite-add <b64>"), (["invite-add", "A" * 400], "invite-add, 400 знаков"),
                      (["invite-add", "QQ=="], "invite-add с двумя = в конце"), (["invite-remove", "abc123"], "invite-remove <номер>"),
                      (["invite-info", secret], "invite-info <секрет>"), (["invite-info", "-" * 22], "invite-info, секрет из одних дефисов"),
                      (["invite-info", "_" * 22], "invite-info, секрет из одних подчёркиваний"), (["invite-redeem", secret, "eve"], "invite-redeem <секрет> <имя>"),
                      (["invite-redeem", secret, "x" * 32], "invite-redeem, имя из 32 знаков"), (["invite-redeem", secret, "_a-b_9"], "invite-redeem, имя с _ и -")):
        ok(valid(args) is True, f"демон пропускает: {why}", args)
    ok(len(pay) <= 400 and len(b64({"group": None, "uses": 1, "days": 1})) <= 400, "то, что шлёт панель для invite-add, в предел 400 знаков укладывается", len(pay))

    bad = (
        (["invite-list", "x"], "invite-list с аргументом"), (["invite-list\n"], "invite-list с переводом строки"), (["invite-list", ""], "invite-list с пустым аргументом"),
        (["Invite-list"], "другой регистр команды"), (["invite-purge"], "неизвестная invite-команда"), (["invite"], "просто invite"),
        (["invite-add"], "invite-add без данных"), (["invite-add", pay, pay], "invite-add с лишним аргументом"), (["invite-add", ""], "invite-add с пустыми данными"),
        (["invite-add", "A" * 401], "invite-add, 401 знак"), (["invite-add", pay + "\n"], "invite-add с переводом строки в конце"),
        (["invite-add", "ab-_"], "invite-add с urlsafe-знаками"), (["invite-add", "a b"], "invite-add с пробелом"), (["invite-add", "QQ==="], "invite-add с тремя ="),
        (["invite-add", "--help"], "invite-add с ключом вместо данных"), (["invite-add", 5], "invite-add с числом"), (["invite-add", None], "invite-add с null"),
        (["invite-remove"], "invite-remove без номера"), (["invite-remove", "abc123", "abc124"], "invite-remove с двумя номерами"),
        (["invite-remove", "abc12"], "invite-remove, 5 знаков"), (["invite-remove", "abc1234"], "invite-remove, 7 знаков"),
        (["invite-remove", "ABC123"], "invite-remove, заглавные"), (["invite-remove", "abc123\n"], "invite-remove с переводом строки"),
        (["invite-remove", "abc-12"], "invite-remove с дефисом"), (["invite-remove", ""], "invite-remove с пустым номером"), (["invite-remove", secret], "invite-remove с секретом вместо номера"),
        (["invite-info"], "invite-info без секрета"), (["invite-info", secret, "x"], "invite-info с лишним аргументом"), (["invite-info", secret[:21]], "invite-info, 21 знак"),
        (["invite-info", secret + "a"], "invite-info, 23 знака"), (["invite-info", secret + "\n"], "invite-info с переводом строки в конце"),
        (["invite-info", secret[:21] + "\n"], "invite-info: 21 знак и перевод строки (всего 22)"), (["invite-info", "\n" + secret[:21]], "invite-info с переводом строки в начале"),
        (["invite-info", secret[:21] + "+"], "invite-info со знаком +"), (["invite-info", secret[:21] + "/"], "invite-info с косой чертой"),
        (["invite-info", secret[:21] + "="], "invite-info со знаком ="), (["invite-info", secret[:21] + "."], "invite-info с точкой"), (["invite-info", secret[:21] + " "], "invite-info с пробелом"),
        (["invite-info", secret[:21] + "é"], "invite-info с не-ASCII буквой"), (["invite-info", secret[:21] + "\x00"], "invite-info с нулевым байтом"),
        (["invite-info", ""], "invite-info с пустым секретом"), (["invite-info", 5], "invite-info с числом"), (["invite-info", [secret]], "invite-info со списком"),
        (["invite-redeem"], "invite-redeem без аргументов"), (["invite-redeem", secret], "invite-redeem без имени"), (["invite-redeem", secret, "eve", "7d"], "invite-redeem с лишним аргументом (срок)"),
        (["invite-redeem", secret, "eve", "bob"], "invite-redeem с двумя именами"), (["invite-redeem", secret, "eve\n"], "invite-redeem: имя с переводом строки"),
        (["invite-redeem", secret + "\n", "eve"], "invite-redeem: секрет с переводом строки"), (["invite-redeem", secret[:21], "eve"], "invite-redeem: секрет из 21 знака"),
        (["invite-redeem", secret + "a", "eve"], "invite-redeem: секрет из 23 знаков"), (["invite-redeem", secret, "-rf"], "invite-redeem: имя с дефиса"),
        (["invite-redeem", secret, "--json"], "invite-redeem: ключ вместо имени"), (["invite-redeem", secret, ""], "invite-redeem: пустое имя"),
        (["invite-redeem", secret, "x" * 33], "invite-redeem: имя из 33 знаков"), (["invite-redeem", secret, "a b"], "invite-redeem: имя с пробелом"),
        (["invite-redeem", secret, "имя"], "invite-redeem: кириллица"), (["invite-redeem", secret, "a/b"], "invite-redeem: косая черта"), (["invite-redeem", secret, "a.b"], "invite-redeem: точка"),
        (["invite-redeem", "eve", secret], "invite-redeem: имя и секрет переставлены"), (["invite-redeem", secret, 5], "invite-redeem: имя числом"), (["invite-redeem", None, "eve"], "invite-redeem: секрет null"),
    )
    for args, why in bad:
        ok(valid(args) is False, f"демон отказывает: {why}", args)
    for args, why in ((None, "null"), ("invite-list", "строка вместо списка"), (("invite-list",), "кортеж вместо списка"), ([], "пустой список"), ({"0": "invite-list"}, "объект"),
                      ([["invite-list"]], "список в списке")):
        ok(valid(args) is False, f"демон отказывает: {why}", args)
    ok(valid(["state"]) is True and valid(["add", "eve"]) is True and valid(["qr", "eve"]) is True and valid(["add", "eve\n"]) is False, "прежние команды демон проверяет как раньше")

    # what it hands back (the agent itself is a fake here)
    ran = []
    answer = {"ok": False, "error": GONE, "shown": True}

    class FakeAgent:
        @staticmethod
        def serve_command(args, by=""):
            ran.append((list(args), by))
            return answer

    D.agent = FakeAgent
    got = D.ask({"args": ["invite-redeem", secret, "eve"], "by": "panel"})
    ok(ran == [(["invite-redeem", secret, "eve"], "panel")], "демон передаёт команду агенту: те же аргументы, автор panel", ran)
    ok(got == answer, "…и отдаёт его ответ как есть, с пометкой shown", got)
    del ran[:]
    for args in (["invite-info", secret], ["invite-list"], ["invite-add", pay], ["invite-remove", "abc123"], ["add", "eve"], ["state"]):
        D.ask({"args": args, "by": "bot"})
    ok([each[0][0] for each in ran] == ["invite-info", "invite-list", "invite-add", "invite-remove", "add", "state"] and all(each[1] == "bot" for each in ran),
       "каждая команда доходит до агента один раз, автор bot", ran)
    del ran[:]
    got = D.ask({"args": ["invite-redeem", secret, "eve\n"], "by": "panel"})
    ok(got == {"ok": False, "error": "bad request"} and ran == [], "запрос не того вида до агента не доходит: «bad request», без пометки shown", [got, ran])
    del ran[:]
    D.ask({"args": ["invite-redeem", secret, "eve"], "by": "invite"})
    ok(ran and ran[0][1] == "", "автором можно назваться только panel, bot или app: прочее демон заменяет пустым", ran)
    devices = []

    class DeviceAgent:
        @staticmethod
        def serve_command(args, by=""):
            devices.append((by, ASKING_DEVICE.get()))
            return {"ok": True}
    D.agent = DeviceAgent
    D.ask({"args": ["state"], "by": "app", "device": "phone"})
    D.ask({"args": ["state"], "by": "app", "device": "bad name\n"})
    D.ask({"args": ["state"], "by": "panel", "device": "phone"})
    D.ask({"args": ["state"], "by": "app"})
    ok(devices == [("app", "phone"), ("app", None), ("panel", None), ("app", None)],
       "изменение из приложения: устройство названо только при авторе app и только именем пользователя; каждый запрос — своё", devices)


def t_real_daemon():
    print("\n— демон на своём сокете: SIGTERM посреди команды")
    # The command is `state`; the watch service it asks first is a stand-in that can take its time.
    directory = tempfile.mkdtemp(dir=TMP)
    delay = {"seconds": 0.0}

    class SlowWatch(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            time.sleep(delay["seconds"])
            body = b'{"ok": false, "error": "busy"}'
            self.send_response(503)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    watch_stand_in = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowWatch)
    threading.Thread(target=watch_stand_in.serve_forever, daemon=True).start()
    with open(os.path.join(directory, "token"), "w") as handle:
        handle.write("t\n")
    sock = os.path.join(directory, "a.sock")
    nowhere = "http://127.0.0.1:1"
    process = subprocess.Popen([sys.executable, "-m", "awgscale.agent"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               env=dict(os.environ, VPN_AGENT_SOCKET=sock, VPN_WATCH_URL=f"http://127.0.0.1:{watch_stand_in.server_address[1]}", VPN_WATCH_TOKEN_FILE=os.path.join(directory, "token"),
                                        VPN_STORE_DSN="host=127.0.0.1 port=1 dbname=vpn user=vpn connect_timeout=1", VPN_STORE_PASSWORD_FILE=os.path.join(directory, "no-password"),
                                        VPN_CASCADE_DIR=directory, VPN_RUN_DIR=directory, VPN_USERS_CACHE_FILE=os.path.join(directory, "clients.json"), VPN_TUNNEL_FILE=os.path.join(directory, "awg0.conf"),
                                        VPN_USERS_URL=nowhere, VPN_USERS_TOKEN_FILE=os.path.join(directory, "token"), VPN_ADGUARD_URL=nowhere + "/control",
                                        VPN_ADGUARD_CREDENTIALS_FILE=os.path.join(directory, "no-credentials"), VPN_ADGUARD_QUERYLOG_FILE=os.path.join(directory, "querylog.json"),
                                        VPN_JOURNAL_SOCKET=os.path.join(directory, "no-journal"), VPN_CLIENT_IF="awg-none"))

    def ask(args, out):
        try:
            client = socket.socket(socket.AF_UNIX)
            client.settimeout(10)
            client.connect(sock)
            client.sendall(json.dumps({"args": args, "by": "bot"}).encode() + b"\n")
            client.shutdown(socket.SHUT_WR)
            out.append(json.loads(client.makefile().readline()))
        except Exception as e:
            out.append({"error": repr(e)})
    for _ in range(100):
        if os.path.exists(sock):
            break
        time.sleep(0.05)
    quick = []
    slow = []

    def answered(out):
        return len(out) == 1 and out[0].get("ok") is True and out[0].get("watch") == {"ok": False} and out[0].get("store_ok") is False and isinstance(out[0].get("time"), int)
    ask(["state"], quick)
    ok(answered(quick), "демон на своём сокете отвечает", quick)
    delay["seconds"] = 1.5
    ask(["dns-remove", "tv"], [])          # a command that changes something (refused here): the next `state` waits for a look of its own
    asking = threading.Thread(target=ask, args=(["state"], slow))
    asking.start()
    time.sleep(0.4)
    started = time.time()
    process.send_signal(signal.SIGTERM)
    try:
        code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        code = "killed"
    asking.join(5)
    watch_stand_in.shutdown()
    ok(code == 0 and answered(slow) and 0.5 < time.time() - started < 6, "SIGTERM посреди команды: она доведена до конца и получила ответ, потом демон вышел сам",
       [code, slow, round(time.time() - started, 2), process.stdout.read()[-300:]])


class Invitations:
    def __init__(self):
        self.saved = []

    async def read(self):
        return list(self.saved)

    async def save(self, invitations):
        self.saved = list(invitations)


class NoGroups:
    async def read(self):
        return StoredGroups.of([])


class BrokenNotes:
    async def made(self, text, caller):
        raise OSError("journald away")


def t_ceiling():
    print("\n— приглашения: не больше сорока сразу")
    invitations = Invitations()
    service = InvitesService(invitations, NoGroups(), None, None, None, None, None, None, BrokenNotes(), os.path.join(TMP, "users.lock"), os.path.join(TMP, "invite.lock"))
    loop = asyncio.new_event_loop()
    made = [loop.run_until_complete(service.add(InviteAdd(payload=b64({"group": None, "uses": 1, "days": 1})), Caller.PANEL)) for _ in range(MAX_INVITES)]
    ok(MAX_INVITES == 40 and len(invitations.saved) == 40, "сорок приглашений создаются — и журнал, который не принял строку, этому не мешает", len(invitations.saved))
    ok(len({each.token for each in made}) == 40 and len({each.id for each in made}) == 40 and all(commands.INVITE_RE.match(each.token) for each in made),
       "сорок секретов и сорок номеров — все разные, все нужного вида")
    ok(all(D.valid(["invite-info", each.token]) and D.valid(["invite-redeem", each.token, "eve"]) and D.valid(["invite-remove", each.id]) for each in made),
       "любой выданный агентом секрет и номер проходит проверку демона")
    try:
        loop.run_until_complete(service.add(InviteAdd(payload=b64({"group": None, "uses": 1, "days": 1})), Caller.PANEL))
        refused = None
    except Refused as e:
        refused = e.error
    ok(refused == "не больше 40 приглашений одновременно" and len(invitations.saved) == 40, "сорок первое — отказ, хранилище не тронуто", refused)
    loop.close()


def t_guests():
    print("\n— приглашения: что нужно, чтобы сделать пользователя для гостя")
    asked = []

    class Recorder:
        def __init__(self, name, answer=None):
            self.name = name
            self.answer = answer

        def __getattr__(self, method):
            async def call(*arguments):
                asked.append((self.name, method, arguments))
                return self.answer
            return call
    made = Made(ok=True, answer={"name": "eve"})
    guests = GuestUsers(Recorder("store"), Recorder("forgetting"), Recorder("seen"), Recorder("users", made), Recorder("pinning"), Recorder("profiles"))
    loop = asyncio.new_event_loop()
    loop.run_until_complete(guests.ready())
    loop.run_until_complete(guests.forget("eve"))
    added = loop.run_until_complete(guests.add("eve"))
    records = [{"id": "aaaaaa", "name": "Семья", "members": ["eve", 5]}]
    loop.run_until_complete(guests.pin(records, ["eve"]))
    loop.run_until_complete(guests.filter(records, ["eve"]))
    ok([(name, method) for name, method, _ in asked] == [("store", "ping"), ("forgetting", "forget"), ("seen", "back"), ("users", "add"), ("pinning", "compile"), ("profiles", "sync")],
       "готовность — вопрос хранилищу; забыть — память о пользователе и заметки; сделать — сервис пользователей; затем закрепление и фильтрация", asked)
    ok(added is True and asked[1][2] == (["eve"],) and asked[3][2] == ("eve", None), "сделан пользователь без срока; забывается ровно он", asked)
    group = asked[4][2][0][0]
    ok(group.id == "aaaaaa" and group.members == ("eve",) and asked[5][2][0][0] == group, "группы приходят записями хранилища и уходят дальше типизированными, без мусора в членах", asked[4])
    silent = GuestUsers(None, None, None, Recorder("users", None), None, None)
    try:
        loop.run_until_complete(silent.add("eve"))
        raised = None
    except ConnectionError as e:
        raised = str(e)
    ok(raised == "сервис пользователей не отвечает", "сервис пользователей промолчал — это не «нет», а обрыв связи", raised)
    loop.close()


def main():
    t_daemon()
    t_invite_commands()
    t_real_daemon()
    t_ceiling()
    t_guests()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
