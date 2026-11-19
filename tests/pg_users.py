#!/usr/bin/env python3
"""The users service against a real, throwaway Postgres, a stand-in for the
`awg` tool that records what it is asked, and a tunnel file in a temp folder.
Run by tests/run.sh inside the service's own image. Nothing live is touched."""
import asyncio
import base64
import datetime
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import threading
import time
import zlib
import urllib.error
import urllib.request
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="pg-users-")
PEERS, CALLS, FAIL_FLAG = (os.path.join(TMP, n) for n in ("kernel-peers", "awg-calls", "awg-fail"))
STUB = os.path.join(TMP, "awg")
with open(STUB, "w") as f:
    f.write(f'''#!/usr/bin/env python3
import os, sys
a = sys.argv[1:]
stdin = sys.stdin.read() if a[:1] == ["set"] and "/dev/stdin" in a else ""
open({CALLS!r}, "a").write(" ".join(a) + ("  <stdin %d bytes>" % len(stdin) if stdin else "") + "\\n")
if os.path.exists({FAIL_FLAG!r}):
    sys.exit("stub: failing on request")
peers = open({PEERS!r}).read().split() if os.path.exists({PEERS!r}) else []
def keep(now):                       # written beside and moved into place: nobody ever reads half a list
    open({PEERS!r} + ".new", "w").write("\\n".join(now))
    os.replace({PEERS!r} + ".new", {PEERS!r})
if a[0] == "show" and a[2] == "peers":
    print("\\n".join(peers))
elif a[0] == "set" and a[2] == "peer" and a[4:] == ["remove"]:
    keep(p for p in peers if p != a[3])
elif a[0] == "set" and a[2] == "peer":
    assert len(stdin.strip()) == 44 and a[6] == "allowed-ips" and a[7].endswith("/32")
    keep(dict.fromkeys(peers + [a[3]]))
else:
    sys.exit("stub: unknown " + " ".join(a))
''')
os.chmod(STUB, os.stat(STUB).st_mode | stat.S_IEXEC)

LINK_EVENTS = os.path.join(TMP, "link-events")
IP_STUB = os.path.join(TMP, "ip")
with open(IP_STUB, "w") as f:
    f.write(f'''#!/usr/bin/env python3
import sys, time
assert sys.argv[1:] == ["-o", "monitor", "link"], sys.argv
open({LINK_EVENTS!r}, "a").close()
with open({LINK_EVENTS!r}) as events:
    events.seek(0, 2)
    while True:
        line = events.readline()
        if line:
            sys.stdout.write(line)
            sys.stdout.flush()
        else:
            time.sleep(0.05)
''')
os.chmod(IP_STUB, os.stat(IP_STUB).st_mode | stat.S_IEXEC)

os.environ.update({"USERS_AWG": STUB, "USERS_IP": IP_STUB, "USERS_RETRY_SECONDS": "1", "USERS_CONF": os.path.join(TMP, "awg0.conf"), "USERS_DSN": os.environ["PG_DSN"], "USERS_ENDPOINT": "vpn.example",
                   "USERS_DB_PASSWORD_FILE": os.path.join(TMP, "db-password"), "USERS_TOKEN_FILE": os.path.join(TMP, "token")})
with open(os.environ["USERS_DB_PASSWORD_FILE"], "w") as f:
    f.write(os.environ["PG_PASSWORD"] + "\n")
with open(os.environ["USERS_TOKEN_FILE"], "w") as f:
    f.write("s3cret-token\n")
import psycopg  # noqa: E402
import uvicorn  # noqa: E402
from pydantic import SecretStr, ValidationError  # noqa: E402

from awgscale.service.errors import Down, Refused  # noqa: E402
from awgscale.core import patterns  # noqa: E402
from awgscale.users import conf as tunnel_conf, device, keys  # noqa: E402
from awgscale.users.app import app  # noqa: E402
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402

from awgscale.users.factory import build_service  # noqa: E402
from awgscale.users.migrate import Importer  # noqa: E402
from awgscale.users.models import AddRequest, ExpiryRequest, Peer, RemoveRequest, User  # noqa: E402
from awgscale.users.settings import get_settings  # noqa: E402
from awgscale.users.store import UserStore  # noqa: E402


class Service:
    """The service as these checks ask it: the same calls its HTTP routes make,
    run to the end on one event loop. Answers come back as plain data; moments
    as Unix seconds, so the checks can do arithmetic on them."""

    Refused = Refused
    Down = Down
    KEY_RE = patterns.KEY_RE
    x25519_public = staticmethod(keys.public_of)
    new_private = staticmethod(keys.new_private)
    new_psk = staticmethod(keys.new_preshared)
    uri_json = staticmethod(device.uri_json)
    uri_qr_text = staticmethod(device.qr_frame)

    def __init__(self):
        self.settings = get_settings()
        self.loop = asyncio.new_event_loop()
        self.scheduler = AsyncIOScheduler(event_loop=self.loop, timezone=datetime.UTC)
        self.service = build_service(self.settings, self.scheduler)
        self.run(self.start_scheduler())
        self.devices = device.DeviceConfigs(self.settings)
        self.file = tunnel_conf.TunnelFile(self.settings.conf_path)
        self.CONF = str(self.settings.conf_path)

    def run(self, work):
        return self.loop.run_until_complete(work)

    async def start_scheduler(self):
        self.scheduler.start()

    def wait(self, seconds):
        """Lets the loop run: scheduled jobs and watchers do their work meanwhile."""
        self.run(asyncio.sleep(seconds))

    # the address of the database is changed by the checks to see what happens without it
    DSN = property(lambda self: self.settings.dsn, lambda self, value: setattr(self.settings, "dsn", value))

    @staticmethod
    def user(u):
        """A user written as plain data (the way the checks make them up) → the model."""
        if isinstance(u, Peer):
            return u
        return User(name=u["name"], ip=u["ip"], public_key=u["public_key"], private_key=SecretStr(u["private_key"]), preshared_key=SecretStr(u["preshared_key"]),
                    expires_at=None, created_at=datetime.datetime.fromtimestamp(0, datetime.UTC))

    @staticmethod
    def seconds(moment):
        return None if moment is None else int(moment.timestamp())

    def plain(self, model):
        data = model.model_dump(mode="json", exclude_none=True) | {"expires_at": self.seconds(getattr(model, "expires_at", None))}
        return data | ({"created_at": self.seconds(model.created_at)} if hasattr(model, "created_at") else {})

    def op_list(self):
        return [self.plain(view) for view in self.run(self.service.list())]

    def op_add(self, name, expires=None):
        return self.plain(self.run(self.service.add(AddRequest(name=name, expires=expires))))

    def op_remove(self, names):
        return [removal.wire() for removal in self.run(self.service.remove(RemoveRequest(names=names).names))]

    def op_expiry(self, names, expires):
        return self.seconds(self.run(self.service.set_expiry(ExpiryRequest(names=names, expires=expires))))

    def op_config(self, name):
        return self.run(self.service.config(AddRequest(name=name).name)).model_dump()

    def background_pass(self):
        """What the service does by itself when it starts: plans the terms (those already over end at once) and completes the kernel's peers."""
        self.run(self.service.plan_terms())
        self.wait(0.5)
        return self.run(self.service.restore_lost_peers())

    def restore_lost_peers(self):
        return self.run(self.service.restore_lost_peers())

    def watch_kernel_while(self, scenario):
        """Runs the watcher of the kernel for as long as `scenario` (a coroutine function) takes."""
        async def both():
            watcher = asyncio.ensure_future(self.service.watch_kernel())
            try:
                return await scenario()
            finally:
                watcher.cancel()
        return self.run(both())

    def all_users(self):
        return self.run(UserStore(self.settings).all())

    def write_conf(self, users, removed=()):
        return self.file.write([self.user(u) for u in users], removed)

    def parse_conf(self, text):
        return tunnel_conf.parse(text)

    def render_conf(self, block, users):
        return tunnel_conf.render(tunnel_conf.InterfaceBlock(block), [self.user(u) for u in users])

    def client_conf(self, u, block, server_public):
        return self.devices.conf(self.user(u), tunnel_conf.InterfaceBlock(block), server_public)

    def client_uri(self, u, block, server_public, conf_text):
        return self.devices.uri(self.user(u), tunnel_conf.InterfaceBlock(block), server_public, conf_text)

    def cmd_import(self, src, dry=False):
        return self.run(Importer(self.settings).run(pathlib.Path(src), dry))

    def serve(self):
        """The real application on a free port, in a thread of its own (its event loop and its service are its own). → (server, its address)"""
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, access_log=False, log_config=None))
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started:
            time.sleep(0.05)
        return server, f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"


U = Service()

PASS = FAIL = 0
SERVER_PRIVATE = U.new_private()
IFACE = ("[Interface]\nPrivateKey = " + SERVER_PRIVATE + "\nAddress = 10.9.8.1/24\nMTU = 1280\nListenPort = 51111\nPostUp = true; echo up\nPostDown = true\n"
         "Jc = 5\nJmin = 45\nJmax = 101\nS1 = 49\nS2 = 91\nS3 = 40\nS4 = 19\nH1 = 1\nH2 = 2\nH3 = 3\nH4 = 4\nI1 = <r 2><b 0x85>\n"
         "HeaderProtectionKey = " + U.new_psk() + "\nContentPaddingAddition = 32-128\n")


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:500])


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except (U.Refused, ValidationError) as e:        # turned down by the service, or already by the model of the request
        return "refused", str(e)
    except U.Down as e:
        return "down", str(e)
    return None, None


def calls():
    return open(CALLS).read().splitlines() if os.path.exists(CALLS) else []


def conf():
    return open(U.CONF).read()


def conf_names():
    return [p.name for p in U.parse_conf(conf())[1]]


def kernel():
    return set(open(PEERS).read().split()) if os.path.exists(PEERS) else set()


def sql(q, *a):
    with psycopg.connect(os.environ["PG_DSN"], password=os.environ["PG_PASSWORD"], autocommit=True) as c:
        cur = c.execute(q, a)
        return cur.fetchall() if cur.description else None


def wipe():
    sql("DROP SCHEMA public CASCADE")
    sql("CREATE SCHEMA public")
    for p in (PEERS, CALLS, FAIL_FLAG):
        if os.path.exists(p):
            os.unlink(p)


def installer_files(names):
    """A folder as the installer left it, and the tunnel file with the same peers."""
    src = tempfile.mkdtemp(prefix="installer-", dir=TMP)
    os.mkdir(os.path.join(src, "expiry"))
    server_public = U.x25519_public(SERVER_PRIVATE)
    open(os.path.join(src, "server_public.key"), "w").write(server_public + "\n")
    users = []
    for i, n in enumerate(names):
        priv = U.new_private()
        u = {"name": n, "ip": f"10.9.8.{i + 3}", "private_key": priv, "public_key": U.x25519_public(priv), "preshared_key": U.new_psk()}
        c = U.client_conf(u, IFACE, server_public)
        open(os.path.join(src, n + ".conf"), "w").write(c)
        open(os.path.join(src, n + ".vpnuri"), "w").write(U.client_uri(u, IFACE, server_public, c) + "\n")
        users.append(u)
    open(U.CONF, "w").write(U.render_conf(IFACE, users) + "\n")          # the installer leaves a blank line at the end
    open(PEERS, "w").write("\n".join(u["public_key"] for u in users))
    return src, users


def run_import(src, dry=False):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = U.cmd_import(src, dry)
    return code, buf.getvalue()


def t_keys():
    print("\n— ключи")
    priv = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    ok(base64.b64decode(U.x25519_public(base64.b64encode(priv).decode())).hex() == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a", "открытый ключ из закрытого — как в RFC 7748")
    a, b = U.new_private(), U.new_private()
    ok(a != b and U.KEY_RE.match(a) and U.KEY_RE.match(U.new_psk()) and U.KEY_RE.match(U.x25519_public(a)), "новые ключи разные и нужного вида")


def t_import():
    print("\n— перенос из файлов установщика")
    wipe()
    ok(raises(U.op_list)[0] == "down" and raises(U.op_add, "ann")[0] == "down", "до переноса сервис не отвечает «пользователей нет» и никого не создаёт — отказ")
    src, users = installer_files(["ann", "bob", "zoe_2"])
    open(os.path.join(src, "expiry", "bob"), "w").write("1999999999\n")
    before = conf()
    code, text = run_import(src, dry=True)
    ok(code == 0 and "NO" not in text and "check only" in text and raises(U.op_list)[0] == "down", "проверка без записи: все сверки сошлись, в базе ничего нет", text)
    code, text = run_import(src)
    got = U.op_list()
    ok(code == 0 and "stored: 3 users" in text and [u["name"] for u in got] == ["ann", "bob", "zoe_2"] and [u["ip"] for u in got] == ["10.9.8.3", "10.9.8.4", "10.9.8.5"], "перенесены все, в порядке файла", text)
    ok(got[1]["expires_at"] == 1999999999 and got[0]["expires_at"] is None, "срок перенесён, у остальных его нет", got)
    ok(conf() == before and calls() == ["show awg0 peers", "show awg0 peers"], "перенос не трогает ни файл туннеля, ни ядро (только смотрит список)", calls())
    ok(not any(u["private_key"] in text or u["preshared_key"] in text for u in users), "в выводе переноса нет ни одного ключа")
    code, text = run_import(src)
    ok(code == 0 and "already" in text and len(U.op_list()) == 3, "повторный перенос ничего не меняет", text)
    for what, spoil in (("чужой закрытый ключ в конфиге", lambda: open(os.path.join(src, "bob.conf"), "w").write(open(os.path.join(src, "bob.conf")).read().replace(users[1]["private_key"], U.new_private()))),
                        ("в ядре лишний узел", lambda: open(PEERS, "a").write("\n" + U.x25519_public(U.new_private())))):
        wipe()
        src, users = installer_files(["ann", "bob"])
        spoil()
        code, text = run_import(src)
        ok(code == 1 and "NO" in text and "nothing was stored" in text and raises(U.op_list)[0] == "down", f"расхождение ({what}) — не перенесено ничего", text)


def t_upgrade():
    print("\n— таблица прежнего вида")
    wipe()
    src, users = installer_files(["ann", "bob"])
    open(os.path.join(src, "expiry", "bob"), "w").write("1999999999\n")
    run_import(src)
    before = U.op_list()
    for column in ("expires_at", "created_at"):
        sql(f"ALTER TABLE users ALTER COLUMN {column} TYPE bigint USING extract(epoch from {column})::bigint")
    fresh = Service()                                   # a process that has just started
    got = fresh.op_list()
    types = sql("SELECT data_type FROM information_schema.columns WHERE table_name = 'users' AND column_name IN ('expires_at', 'created_at')")
    ok(got == before and got[1]["expires_at"] == 1999999999 and types == [("timestamp with time zone",)] * 2, "время числом (как хранил прежний сервис) переведено в метки времени, значения те же", (got, types))
    ok(fresh.op_list() == before and fresh.op_add("carl", "1d")["status"] == "created", "после перевода сервис работает как обычно")


def t_add_remove():
    print("\n— создание и удаление")
    wipe()
    src, users = installer_files(["ann", "bob"])
    run_import(src)
    open(CALLS, "w").close()
    r = U.op_add("carl", "7d")
    row = sql("SELECT ip, public_key, private_key, preshared_key, expires_at FROM users WHERE name = 'carl'")[0]
    ok(r["status"] == "created" and r["ip"] == "10.9.8.2" == row[0] and abs(r["expires_at"] - time.time() - 7 * 86400) < 5, "новый получает наименьший свободный адрес и срок от сейчас", r)
    ok(U.x25519_public(row[2]) == row[1] and row[1] in kernel(), "его открытый ключ выведен из закрытого и добавлен в ядро")
    line = calls()[-1]
    ok(line.startswith(f"set awg0 peer {row[1]} preshared-key /dev/stdin allowed-ips 10.9.8.2/32") and row[3] not in open(CALLS).read() and row[2] not in open(CALLS).read(),
       "общий ключ уходит в awg через stdin: в командной строке нет ни его, ни закрытого ключа", line)
    ok(conf_names() == ["ann", "bob", "carl"] and conf().startswith(IFACE) and oct(os.stat(U.CONF).st_mode & 0o777) == "0o600", "файл туннеля пересобран: блок [Interface] слово в слово, новый узел в конце, права 600")
    ok(not any(c.startswith(("syncconf", "setconf")) or " down" in c for c in calls()), "туннель не перезапускается и не синхронизируется из файла", calls())
    for bad, why in (("carl", "занятое имя"), ("-x", "имя с дефиса"), ("a b", "пробел"), ("", "пустое"), ("x" * 33, "длинное"), (None, "не строка")):
        ok(raises(U.op_add, bad)[0] == "refused", f"отказ: {why}")
    ok(raises(U.op_add, "dan", "7 days")[0] == "refused" and raises(U.op_add, "dan", "0d")[0] == "refused" and "dan" not in conf_names(), "срок не того вида — отказ, никто не создан")
    U.op_add("dan")
    ok(sql("SELECT ip, expires_at FROM users WHERE name = 'dan'")[0] == ("10.9.8.5", None), "следующий свободный адрес — после занятых; адрес самого сервера (.1) не выдаётся")

    before, kbefore = conf(), kernel()
    open(FAIL_FLAG, "w").close()
    kind, _ = raises(U.op_add, "eve")
    ok(kind == "down" and sql("SELECT count(*) FROM users WHERE name = 'eve'")[0][0] == 0 and conf() == before, "ядро не приняло нового — запись из базы убрана, файл не тронут", kind)
    res = U.op_remove(["ann"])
    ok(res == [{"name": "ann", "status": "failed", "error": res[0].get("error")}] and len(U.op_list()) == 4 and conf() == before, "ядро не отдало узел — пользователь остался и в базе, и в файле", res)
    os.unlink(FAIL_FLAG)

    ok(raises(U.op_remove, ["ann", "-bad"])[0] == "refused" and "ann" in conf_names(), "в списке на удаление негодное имя — отказ, не удалён никто")
    res = U.op_remove(["ann", "nobody"])
    ok([r["status"] for r in res] == ["removed", "not_found"], "удаление: кто удалён, кого нет", res)
    ok(users[0]["public_key"] not in kernel() and conf_names() == ["bob", "carl", "dan"] and len(kernel()) == len(kbefore) - 1, "удалённый убран из ядра и из файла, остальные на месте")
    r = U.op_add("ann2")
    ok(r["ip"] == "10.9.8.3", "освободившийся адрес выдаётся следующему", r)


def t_guards():
    print("\n— «не смог прочитать» не значит «никого нет»")
    before, kbefore = conf(), kernel()
    kind, text = raises(U.write_conf, [])
    ok(kind == "down" and conf() == before, "запись файла туннеля без пользователей отклонена: файл цел", text)
    users = U.all_users()
    ok(raises(U.write_conf, users[:-2], removed=[users[-1].public_key])[0] == "down" and conf() == before, "файл не теряет больше узлов, чем удалено этой операцией")
    swapped = users[:-1] + [users[-1].model_copy(update={"name": "other", "public_key": U.x25519_public(U.new_private())})]
    ok(raises(U.write_conf, swapped)[0] == "down" and conf() == before, "столько же узлов, но одного из нынешних среди них нет (база из старой копии) — файл не переписывается")
    ok(U.write_conf(users) is False and conf() == before, "тот же состав — файл не трогается")
    good = U.DSN
    U.DSN = "host=127.0.0.1 port=1 dbname=vpn user=vpn connect_timeout=2"
    n = len(calls())
    ok(raises(U.op_list)[0] == "down" and raises(U.op_add, "zed")[0] == "down" and raises(U.restore_lost_peers)[0] == "down", "база недоступна: список, создание и сверка участников ядра — отказ")
    res = U.op_remove(["bob"])
    ok(res[0]["status"] == "failed" and conf() == before and kernel() == kbefore and len(calls()) == n, "…удаление тоже; ядро и файл не тронуты ни одной командой", res)
    U.DSN = good
    ok(len(U.op_list()) == len(users), "база вернулась — все на месте")


def t_terms():
    print("\n— сроки и фоновый проход")
    now = int(time.time())
    until = U.op_expiry(["bob", "carl"], "12h")
    ok(abs(until - now - 12 * 3600) < 5 and {u["name"]: u["expires_at"] for u in U.op_list()}["carl"] == until, "новый срок считается от сейчас")
    ok(U.op_expiry(["carl"], None) is None and {u["name"]: u["expires_at"] for u in U.op_list()}["carl"] is None, "срок снимается")
    ok(raises(U.op_expiry, ["carl", "nobody"], "1d")[0] == "refused" and {u["name"]: u["expires_at"] for u in U.op_list()}["carl"] is None, "есть неизвестное имя — срок не меняется ни у кого")
    sql("UPDATE users SET expires_at = to_timestamp(%s) WHERE name = 'bob'", now - 1)
    stranger = U.x25519_public(U.new_private())
    lost = sql("SELECT public_key FROM users WHERE name = 'dan'")[0][0]
    now_in_kernel = (kernel() - {lost}) | {stranger}
    open(PEERS, "w").write("\n".join(now_in_kernel))
    U.background_pass()
    ok("bob" not in [u["name"] for u in U.op_list()] and "bob" not in conf_names(), "срок вышел — пользователь удалён из базы, ядра и файла")
    ok(lost in kernel(), "узел, пропавший из ядра, возвращён")
    ok(stranger in kernel(), "незнакомый узел в ядре не тронут")
    n = len(calls())
    U.background_pass()
    ok(calls()[n:] == ["show awg0 peers"], "когда всё на месте, проход только смотрит", calls()[n:])


def t_terms_by_schedule():
    print("\n— срок по расписанию, без опроса")
    U.op_add("soon")
    U.op_add("later", "2h")
    n = len(calls())
    U.wait(1)
    ok(len(calls()) == n, "пока сроки далеко, сервис ничего не опрашивает и не трогает ядро")
    sql("UPDATE users SET expires_at = now() + interval '2 seconds' WHERE name = 'soon'")
    U.run(U.service.plan_terms())                   # the way a starting service plans what the database holds
    U.wait(1)
    still = "soon" in conf_names()
    U.wait(2)
    ok(still and "soon" not in conf_names() and "soon" not in [u["name"] for u in U.op_list()], "пользователь удаляется ровно когда срок вышел, не раньше")
    U.op_add("moved", "1h")
    sql("UPDATE users SET expires_at = now() + interval '1 second' WHERE name = 'moved'")
    U.op_expiry(["moved"], None)
    sql("UPDATE users SET expires_at = now() - interval '1 second' WHERE name = 'later'")
    U.op_expiry(["later"], "3h")
    U.wait(2)
    ok("moved" in conf_names() and "later" in conf_names(), "срок сняли или продлили — прежнее задание не срабатывает")
    U.op_add("retry")
    sql("UPDATE users SET expires_at = now() + interval '1 second' WHERE name = 'retry'")
    U.run(U.service.plan_terms())
    open(FAIL_FLAG, "w").close()
    U.wait(1.5)
    held = "retry" in conf_names()
    os.unlink(FAIL_FLAG)
    U.wait(2)
    ok(held and "retry" not in conf_names(), "ядро не ответило в момент окончания срока — попытка повторяется, пользователь удалён позже")
    U.op_remove(["later", "moved"])
    ok(not [job.id for job in U.scheduler.get_jobs()], "у удалённых заданий не остаётся", [job.id for job in U.scheduler.get_jobs()])


def t_kernel_events():
    print("\n— участники ядра: по событию интерфейса")
    lost = sql("SELECT public_key FROM users WHERE name = 'dan'")[0][0]

    def lose():
        open(PEERS + ".lost", "w").write("\n".join(kernel() - {lost}))
        os.replace(PEERS + ".lost", PEERS)

    def event(line):
        open(LINK_EVENTS, "a").write(line + "\n")

    async def scenario():
        seen = {}
        lose()
        await asyncio.sleep(0.6)
        seen["at start"] = lost in kernel()          # the watcher has just started: it compares once
        lose()
        n = len(calls())
        await asyncio.sleep(1.5)
        seen["no polling"] = lost not in kernel() and len(calls()) == n
        for line in ("Deleted 7: awg0: <POINTOPOINT,NOARP> mtu 1280 qdisc noop state DOWN", "8: awg0: <POINTOPOINT,NOARP> mtu 1280 qdisc noop state DOWN",
                     "9: awgk1: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280 state UNKNOWN", "3: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500"):
            event(line)
        await asyncio.sleep(1.8)
        seen["other events"] = lost not in kernel()
        event("8: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280 qdisc noqueue state UNKNOWN group default qlen 1000")
        await asyncio.sleep(0.5)
        seen["settles first"] = lost not in kernel()
        await asyncio.sleep(1.3)
        seen["came up"] = lost in kernel()
        return seen
    seen = U.watch_kernel_while(scenario)
    ok(seen["at start"], "при старте сервис сверяет участников ядра с пользователями")
    ok(seen["no polling"], "дальше — никакого опроса: пропавший участник сам не возвращается, к ядру ни одного обращения")
    ok(seen["other events"], "интерфейс удалён, ещё не поднят, или это другой интерфейс — ничего не делается")
    ok(seen["settles first"] and seen["came up"], "интерфейс туннеля поднялся — участники возвращены (после паузы: сначала тот, кто поднял, загружает файл)", seen)
    stranger_count = len(kernel())
    good_ip = U.settings.ip
    U.settings.ip = os.path.join(TMP, "no-such-ip")

    async def without_monitor():
        lose()
        await asyncio.sleep(0.5)
        return lost in kernel()
    ok(U.watch_kernel_while(without_monitor) and len(kernel()) == stranger_count, "события слушать нечем — сервис жив и сверяет при каждой новой попытке")
    U.settings.ip = good_ip


def t_device():
    print("\n— что получает устройство")
    p = U.op_config("carl")
    row = sql("SELECT ip, private_key, preshared_key FROM users WHERE name = 'carl'")[0]
    c = p["conf"]
    ok(f"PrivateKey = {row[1]}\nAddress = {row[0]}/32\nDNS = 172.16.17.1\nMTU = 1280\nJc = 5\n" in c and f"PresharedKey = {row[2]}\nEndpoint = vpn.example:51111\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 33\n" in c
       and f"PublicKey = {U.x25519_public(SERVER_PRIVATE)}\n" in c and "PostUp" not in c and SERVER_PRIVATE not in c, "конфиг: свои ключ и адрес, настройки туннеля, сервер; ничего серверного лишнего")
    j = U.uri_json(p["uri"])
    inner = j["containers"][0]["awg"]["last_config"]
    ok(p["uri"].startswith("vpn://") and "=" not in p["uri"] and inner["config"] == c and inner["client_ip"] == row[0] and inner["port"] == 51111 and j["hostName"] == "vpn.example" and inner["I2"] == "",
       "строка vpn:// раскрывается в тот же конфиг и адрес сервера")
    ok(all(base64.b64decode(p[k])[:8] == b"\x89PNG\r\n\x1a\n" for k in ("conf_png", "uri_png")), "обе QR-картинки — PNG")
    # the QR for the AmneziaVPN app: read back the way the app's scanner reads it (importController.cpp, parseQrCodeChunk)
    frame = base64.urlsafe_b64decode(U.uri_qr_text(p["uri"]) + "==")
    n = int.from_bytes(frame[4:8], "big")
    body = base64.urlsafe_b64decode(p["uri"][6:] + "==")
    ok(int.from_bytes(frame[:2], "big") == 1984 and frame[2] == 1 and frame[3] == 0 and n == len(frame) - 8 and frame[8:] == body
       and json.loads(zlib.decompress(frame[12:]))["containers"][0]["container"] == "amnezia-awg" and "vpn" not in U.uri_qr_text(p["uri"])[:6],
       "QR для AmneziaVPN — в формате её сканера: метка 1984, один код из одного, внутри те же данные, что в строке vpn://")
    ok(raises(U.op_config, "nobody")[0] == "refused" and raises(U.op_config, "../x")[0] == "refused", "неизвестное или негодное имя — отказ")


def t_http():
    print("\n— HTTP")
    srv, base = U.serve()

    def ask(method, path, body=None, token="s3cret-token"):
        req = urllib.request.Request(base + path, method=method, data=None if body is None else body if isinstance(body, bytes) else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    for t in (None, "wrong", ""):
        ok(ask("GET", "/v1/users", token=t)[0] == 401, f"без верного токена — 401 ({t!r})")
    code, r = ask("GET", "/v1/users")
    ok(code == 200 and r["ok"] and all(set(u) == {"name", "ip", "expires_at", "created_at"} for u in r["users"]), "список: имя, адрес, срок, когда создан — и ни одного ключа", r["users"][:1])
    code, r = ask("POST", "/v1/users", {"name": "http_1", "expires": "1w"})
    ok(code == 200 and r["ok"] and r["results"][0]["status"] == "created" and "http_1" in conf_names(), "создание", r)
    ok(ask("POST", "/v1/users", {"name": "http_1"}) == (400, {"ok": False, "error": "пользователь с таким именем уже есть"}), "занятое имя — 400 со словами сервиса")
    ok(ask("POST", "/v1/users", {})[0] == 422 and ask("POST", "/v1/users", b"{not json")[0] == 422, "пустой запрос и не JSON — 422, как отвечает FastAPI")
    code, r = ask("GET", "/v1/users/http_1/config")
    ok(code == 200 and r["conf"].startswith("[Interface]") and r["uri"].startswith("vpn://"), "конфиг устройства")
    code, r = ask("POST", "/v1/users/expiry", {"names": ["http_1"], "expires": None})
    ok(code == 200 and r["expires_at"] is None, "срок", r)
    code, r = ask("DELETE", "/v1/users", {"names": ["http_1", "nobody"]})
    ok(code == 200 and r["ok"] is False and [x["status"] for x in r["results"]] == ["removed", "not_found"] and "http_1" not in conf_names(), "удаление: общий ok=false, если удалён не каждый", r)
    code, r = ask("GET", "/v1/health")
    ok(code == 200 and r["users"] == len(conf_names()) == r["peers"] - 1, "health: пользователей в базе и узлов в ядре (один незнакомый с прошлой проверки)", r)
    ok(ask("GET", "/v1/nothing")[0] == 404 and ask("PUT", "/v1/users")[0] == 405 and ask("DELETE", "/v1/users", {"names": []})[0] == 422, "неизвестный путь — 404, чужой метод — 405, пустое удаление — 422")
    good = U.DSN
    U.DSN = "host=127.0.0.1 port=1 dbname=vpn user=vpn connect_timeout=2"
    code, r = ask("GET", "/v1/users")
    ok(code == 503 and r["ok"] is False, "база недоступна — 503, а не пустой список", r)
    U.DSN = good
    code, r = ask("POST", "/v1/users", {"name": "ok_name", "expires": "7 days"})
    ok(code == 422 and "срок: например 12h, 7d, 4w" in json.dumps(r, ensure_ascii=False), "негодный срок — 422, в ответе сказано, каким он должен быть", r)
    ok(ask("POST", "/v1/users", {"name": "ok_name\n"})[0] == 422 and ask("GET", "/v1/users/-bad/config")[0] == 422 and "ok_name" not in conf_names(), "негодное имя (перевод строки в конце, дефис в начале) — 422")
    code, r = ask("POST", "/v1/users/expiry", {"names": ["nobody"], "expires": "1d"})
    ok(code == 400 and r["error"].startswith("нет такого пользователя: nobody"), "срок неизвестному — 400", r)
    code, r = ask("POST", "/v1/users", {"name": "http_2", "expires": "2d"})
    made = r["results"][0]
    listed = [u for u in ask("GET", "/v1/users")[1]["users"] if u["name"] == "http_2"][0]
    until = datetime.datetime.fromisoformat(made["expires_at"])
    ok(until.tzinfo is not None and abs(until.timestamp() - time.time() - 2 * 86400) < 5 and listed["expires_at"] == made["expires_at"] and datetime.datetime.fromisoformat(listed["created_at"]).tzinfo is not None,
       "время в ответах — дата со временем и поясом (ISO 8601), а не число", made)
    ok(sql("SELECT data_type FROM information_schema.columns WHERE table_name = 'users' AND column_name IN ('expires_at', 'created_at')") == [("timestamp with time zone",)] * 2, "в базе время хранится как метка времени")
    ask("DELETE", "/v1/users", {"names": ["http_2"]})
    ok(ask("GET", "/v1/nothing", token="wrong") == (401, {"ok": False, "error": "unauthorized"}), "токен спрашивается раньше, чем разбирается путь")
    ok(all(ask("GET", path)[0] == 404 for path in ("/docs", "/redoc", "/openapi.json")), "описания API наружу нет")
    token_file = os.environ["USERS_TOKEN_FILE"]
    os.rename(token_file, token_file + ".away")
    ok(ask("GET", "/v1/users") == (503, {"ok": False, "error": "no token"}), "файла с токеном нет — 503, никого не пускаем")
    os.rename(token_file + ".away", token_file)
    srv.should_exit = True


def main():
    t_keys()
    t_import()
    t_upgrade()
    t_add_remove()
    t_guards()
    t_terms()
    t_terms_by_schedule()
    t_kernel_events()
    t_device()
    t_http()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
