#!/usr/bin/env python3
"""The exits service (src/awgscale/exits) without a server to log into:
`ssh`, `ip`, `ping` and `awg` are stand-ins that write down what they were asked
and answer as a healthy (or, on request, a broken) new server would."""
import asyncio
import io
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="exits-test-")
BIN, DIR, SECRET, REMOTE = (os.path.join(T, d) for d in ("bin", "awg", "secret", "remote"))
for d in (BIN, DIR, SECRET, os.path.join(REMOTE, "stats")):
    os.makedirs(d)
PKG_REMOTE = os.path.join(ROOT, "src", "awgscale", "exits", "remote")
for f in os.listdir(PKG_REMOTE):
    if os.path.isfile(os.path.join(PKG_REMOTE, f)):
        shutil.copy(os.path.join(PKG_REMOTE, f), REMOTE)
for f in ("server.py", "Dockerfile"):
    shutil.copy(os.path.join(PKG_REMOTE, "stats", f), os.path.join(REMOTE, "stats"))
LOG, PLAN = os.path.join(T, "calls.jsonl"), os.path.join(T, "plan.json")
PW = "s3cret-Pa55 word"


def tool(name, body):
    p = os.path.join(BIN, name)
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)


tool("ssh", f'''#!/usr/bin/env python3
import json, os, sys
plan = json.load(open({PLAN!r})) if os.path.exists({PLAN!r}) else {{}}
argv, data = sys.argv[1:], (sys.stdin.read() if not sys.stdin.isatty() else "")
cmd = argv[-1]
what = "?"
if "authorized_keys" in cmd: what = "key"
elif cmd == "echo @@OK": what = "login"
elif "base64 -d" in cmd: what = "file:" + cmd.split(" > ")[1].split(".new")[0]
elif "systemctl reboot" in cmd: what = "reboot"
elif "lsmod" in cmd: what = "after-reboot"
else:
    for f in os.listdir({REMOTE!r}):
        p = os.path.join({REMOTE!r}, f)
        if os.path.isfile(p) and open(p).read() == data: what = f + (":" + cmd.split("-- ")[1] if "-- " in cmd and cmd.split("-- ")[1] else "")
rec = {{"what": what, "argv": argv, "pw_env": os.environ.get("EXITS_PW"), "cmd": cmd}}
open({LOG!r}, "a").write(json.dumps(rec) + "\\n")
n = sum(1 for l in open({LOG!r}) if json.loads(l)["what"] == what)
if what in plan.get("fail", {{}}): print(plan["fail"][what]); sys.exit(1)
if what == "key":
    if os.environ.get("EXITS_PW") != plan.get("password"): sys.exit(255)
    print("@@OK")
elif what == "login":
    if plan.get("no_key") or (plan.get("lock_out") and any(json.loads(l)["what"] == "firewall.sh:arm" for l in open({LOG!r}))): sys.exit(255)
    print("@@OK")
elif what == "link.sh":
    print("@@PUB " + "E" * 43 + "="); print("@@PSK " + "P" * 43 + "=")
    for i in range(13): print("@@PARAM K%d = %d" % (i, i))
    print("@@OK awgc")
elif what == "update.sh": print("@@REBOOT нужна"); print("@@OK обновлено пакетов: 7")
elif what == "after-reboot": print("1"); print("active")
elif what == "?": sys.exit(3)
else: print("@@OK fine")
''')
tool("ip", f'''#!/bin/sh
echo "ip $*" >> {T}/ip.log
# a link is "made by the links service" once its file is there; it stays until deleted
if [ "$1 $2" = "link show" ]; then
    [ -f {T}/no-links ] && exit 1
    [ -f {DIR}/$3.conf ] && touch {T}/up-$3
    [ -f {T}/up-$3 ]; exit $?
fi
[ "$1 $2" = "link del" ] && rm -f {T}/up-$3
exit 0
''')
tool("ping", "#!/bin/sh\nexit 0\n")
tool("awg", '#!/bin/sh\n[ "$1" = genkey ] && echo "' + "k" * 43 + '="\n[ "$1" = pubkey ] && { cat >/dev/null; echo "' + "U" * 43 + '="; }\nexit 0\n')
os.environ.update(PATH=BIN + ":" + os.environ["PATH"], EXITS_DIR=DIR, EXITS_SECRET=SECRET, EXITS_REMOTE=REMOTE, EXITS_PPA_KEY=os.path.join(REMOTE, "prepare.sh"),
                  EXITS_ENTRY_ADDR="198.51.100.7", EXITS_SSH=os.path.join(BIN, "ssh"), EXITS_AWG=os.path.join(BIN, "awg"), EXITS_STATE_FILE=os.path.join(T, "state.json"),
                  EXITS_WAIT="0", EXITS_CLIENT_CONF=os.path.join(DIR, "awg0.conf"), EXITS_TOKEN_FILE=os.path.join(T, "token"))
with open(os.environ["EXITS_TOKEN_FILE"], "w") as f:
    f.write("s3cret-token\n")
with open(os.path.join(DIR, "awg0.conf"), "w") as f:
    f.write("[Interface]\nPrivateKey = x\nI1 = <b 0xabcdef>\n")
import uvicorn                                              # noqa: E402  (the environment above must be set first)
from pydantic import ValidationError                        # noqa: E402

from awgscale.exits.app import app                          # noqa: E402
from awgscale.exits.factory import build_service            # noqa: E402
from awgscale.exits.models import AddRequest, AdoptRequest, RemoveRequest, UpdateRequest  # noqa: E402
from awgscale.exits.settings import get_settings            # noqa: E402
from awgscale.service.errors import Refused                 # noqa: E402

LOOP = asyncio.new_event_loop()
ATTACHED = []
STATE = {"up": True, "hold": None}


async def fake_agent(args, timeout=200):
    """The agent as the service asks it. With STATE["hold"] set, attaching waits until it is let go: a job that is still running."""
    if args[0] == "exit-attach":
        if STATE["hold"] is not None:
            await STATE["hold"].wait()
        ATTACHED.append(json.loads(__import__("base64").b64decode(args[1])))
        return {"ok": not STATE.get("attach_fails"), "error": "нет"}
    return {"ok": True, "exits": [{"index": d["index"], "state": "up" if STATE["up"] else "down"} for d in ATTACHED]}


SVC = build_service(get_settings(), fake_agent)


class S:
    """The names the checks below use, on the service: the same calls its HTTP routes make, run to the end on one event loop."""

    Refused = Refused

    @staticmethod
    def call(work):
        return LOOP.run_until_complete(work)

    @staticmethod
    def op_add(d):
        return S.call(SVC.add(AddRequest(**d))).model_dump(mode="json")

    @staticmethod
    def op_update(d):
        return S.call(SVC.update(UpdateRequest(**d))).model_dump(mode="json")

    @staticmethod
    def op_remove(d):
        return S.call(SVC.remove(RemoveRequest(**d))).model_dump(mode="json")

    @staticmethod
    def op_adopt(d):
        return S.call(SVC.adopt(AdoptRequest(**d))).model_dump(mode="json")

    @staticmethod
    def job_public():
        job = S.call(SVC.job())
        return job.wire() if job else None

    @staticmethod
    def nodes():
        return S.call(SVC.nodes()).model_dump(mode="json")["nodes"]


n = failed = 0
OUT = io.StringIO()                                 # everything the service writes to its log
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(OUT)], force=True)


def ok(cond, text, detail=None):
    global n, failed
    n += 1
    if cond:
        print("ok   " + text)
    else:
        failed += 1
        print("FAIL " + text + ("\n     " + str(detail)[:600] if detail is not None else ""))


def calls():
    try:
        return [json.loads(l) for l in open(LOG)]
    except OSError:
        return []


def whats():
    return [c["what"] for c in calls()]


def plan(**k):
    json.dump(k, open(PLAN, "w"))
    open(LOG, "w").close()
    open(os.path.join(T, "ip.log"), "w").close()


def run(fn, d):
    """One request and the job it starts, to its end. → (the answer or the refusal, the job)."""
    try:
        r = fn(d)
    except (S.Refused, ValidationError) as e:          # turned down by the service, or already by the model of the request
        return str(e), None
    S.call(SVC.job_finished())
    return r, S.job_public()


ADD = {"index": 2, "name": "exit3-de", "weight": 2, "address": "45.10.20.10", "password": PW}
everything = lambda: json.dumps([[c["argv"], c["cmd"]] for c in calls()]) + open(os.environ["EXITS_STATE_FILE"]).read() + OUT.getvalue() + json.dumps(S.job_public())

# 1. a new server, with its password
plan(password=PW)
r, job = run(S.op_add, ADD)
ok(r == {"ok": True, "started": True} and job["state"] == "done" and [s["state"] for s in job["steps"]] == ["done"] * 8, "новый сервер с паролем: все восемь шагов пройдены", job)
w = whats()
ok(w[0] == "key" and w[1] == "login" and w.index("prepare.sh") < w.index("link.sh") < w.index("firewall.sh:arm") < w.index("firewall.sh:keep") < w.index("stats.sh"),
   "порядок на сервере: ключ, вход по ключу, пакеты, туннель, файрвол (включить → подтвердить), мониторинг", w)
ok(w[w.index("firewall.sh:arm") + 1] == "login", "между включением файрвола и его подтверждением — новый вход по SSH", w)
ok(ATTACHED == [{"index": 2, "name": "exit3-de", "weight": 2}], "к каскаду выход подключён один раз, с именем и долей", ATTACHED)
key = calls()[0]
ok(key["pw_env"] == PW and PW not in json.dumps(key["argv"]) and all(c["pw_env"] is None for c in calls()[1:]), "пароль дошёл до одного запуска ssh — через окружение, не в аргументах; дальше его нет нигде")
ok(PW not in everything() and "password" not in json.dumps(S.job_public()), "пароля нет ни в записи операции, ни в базе, ни в журнале сервиса")
ok('from="198.51.100.7" ssh-ed25519 ' in key["cmd"], "ключ на выходе принимается только с адреса входа", key["cmd"][-200:])
conf = open(os.path.join(DIR, "awgk2.conf")).read()
ok("Table = off" in conf and "Endpoint = 45.10.20.10:47212" in conf and "Address = 10.77.2.2/32" in conf and "I1 = <b 0xabcdef>" in conf and "K12 = 12" in conf
   and "PublicKey = " + "E" * 43 in conf and stat.S_IMODE(os.stat(os.path.join(DIR, "awgk2.conf")).st_mode) == 0o600 and stat.S_IMODE(os.stat(os.path.join(DIR, "awgk2.key")).st_mode) == 0o600,
   "связь на входе: Table = off, адрес и порт выхода, параметры маскировки с выхода, файлы только для root")
link = next(c for c in calls() if c["what"] == "link.sh")
ok("ENTRY_PUB=" + "U" * 43 in link["cmd"] and "PORT=47212" in link["cmd"] and "NET=10.77.2" in link["cmd"], "выходу переданы ключ входа, порт и сеть его номера", link["cmd"][:200])
fw = next(c for c in calls() if c["what"] == "firewall.sh:arm")
ok("SSH_PORT=22" in fw["cmd"] and "UDP_PORT=47212" in fw["cmd"] and "--dport 9101" in fw["cmd"] and "-s 10.77.2.2" in fw["cmd"], "файрвол: SSH, порт туннеля и мониторинг только со стороны входа", fw["cmd"][:260])
node = S.nodes()["2"]
ok(node["address"] == "45.10.20.10" and node["managed"] is True and node["port"] == 47212, "сервер записан как управляемый", node)
ok("StrictHostKeyChecking=accept-new" in key["argv"] and "StrictHostKeyChecking=yes" in " ".join(next(c for c in calls() if c["what"] == "prepare.sh")["argv"]), "ключ сервера запоминается при первом входе, дальше — строго он")

# 2. the same request again: nothing is broken, the link is not remade
plan(password=PW)
before = open(os.path.join(DIR, "awgk2.conf")).read()
r, job = run(S.op_add, ADD)
ok(job["state"] == "done" and open(os.path.join(DIR, "awgk2.conf")).read() == before and "ip link del awgk2" not in open(os.path.join(T, "ip.log")).read(), "повтор того же запроса: связь не пересоздаётся", open(os.path.join(T, "ip.log")).read())

# 3. no password, the key is not there
ATTACHED.clear()
plan(no_key=True)
r, job = run(S.op_add, dict(ADD, index=4, address="45.10.20.11", name="exit4", password=None))
ok(job["state"] == "failed" and job["steps"][0]["state"] == "fail" and "пароль не указан" in job["error"] and whats() == ["login"] and ATTACHED == [] and not os.path.exists(os.path.join(DIR, "awgk4.conf")),
   "без пароля и без ключа: остановка на первом шаге, на сервере и на входе ничего не сделано", job)
plan(password="other")
r, job = run(S.op_add, dict(ADD, index=4, address="45.10.20.11", name="exit4"))
ok(job["state"] == "failed" and "вход по паролю не удался" in job["error"] and PW not in json.dumps(job), "неверный пароль: понятная ошибка, пароль в неё не попадает", job)

# 4. the server is not fit / a step fails: the exit is not attached; started again, it goes on
plan(password=PW, fail={"prepare.sh": "@@ERR ядро 5.15 слишком старое: нужно 6.7 или новее"})
r, job = run(S.op_add, dict(ADD, index=4, address="45.10.20.11", name="exit4"))
ok(job["state"] == "failed" and job["error"] == "Проверка и установка пакетов: ядро 5.15 слишком старое: нужно 6.7 или новее" and ATTACHED == [] and "link.sh" not in whats() and not os.path.exists(os.path.join(DIR, "awgk4.conf")),
   "сервер не подходит: причина показана словами сервера, туннель не создаётся, к каскаду не подключён", job)
plan(password=PW, lock_out=True)
r, job = run(S.op_add, dict(ADD, index=4, address="45.10.20.11", name="exit4"))
ok(job["state"] == "failed" and "не пускает по SSH" in job["error"] and "firewall.sh:keep" not in whats() and "stats.sh" not in whats() and ATTACHED == [],
   "файрвол отрезал SSH: правила не подтверждаются (сами снимутся), выход не подключён", [job["error"], whats()])
plan(password=PW)
r, job = run(S.op_add, dict(ADD, index=4, address="45.10.20.11", name="exit4"))
ok(job["state"] == "done" and ATTACHED == [{"index": 4, "name": "exit4", "weight": 2}], "тот же запрос после исправления проходит до конца", job)
open(os.path.join(T, "no-links"), "w").close()
plan(password=PW)
r, job = run(S.op_add, dict(ADD, index=5, address="45.10.20.12", name="exit5"))
ok(job["state"] == "failed" and "vpn-links" in job["error"] and len(ATTACHED) == 1, "интерфейс на входе не появился: остановка до файрвола и подключения", job)
os.unlink(os.path.join(T, "no-links"))
STATE["up"] = False
plan(password=PW)
r, job = run(S.op_add, dict(ADD, index=5, address="45.10.20.12", name="exit5"))
ok(job["state"] == "failed" and job["steps"][-1]["state"] == "fail" and job["steps"][-2]["state"] == "done" and "пока не считает его рабочим" in job["error"], "подключён, но сторож не видит его рабочим: об этом сказано", job)
STATE["up"] = True

# 5. refusals
bad = [run(S.op_add, dict(ADD, index=9, address=a))[0] for a in ("10.0.0.5", "198.51.100.7", "nope", "45.10.20.10")]
ok(all(isinstance(b, str) for b in bad) and "уже записан как выход 2" in bad[3], "частный адрес, адрес самого входа, не адрес, уже подключённый сервер — отказ", bad)
ok(isinstance(run(S.op_add, dict(ADD, index=2, address="45.10.20.99"))[0], str) and isinstance(run(S.op_add, dict(ADD, name="bad name"))[0], str) and isinstance(run(S.op_add, dict(ADD, weight=0))[0], str),
   "занятый номер, имя с пробелом, нулевая доля — отказ")
plan(password=PW)
STATE["hold"] = asyncio.Event()                     # a job that stays at «Подключение к каскаду» until it is let go
ok(S.op_add(ADD) == {"ok": True, "started": True} and S.job_public()["state"] == "running" and S.call(SVC.nodes()).busy is True, "операция идёт: так и сказано")
ok("ещё идёт" in run(S.op_add, ADD)[0] and "ещё идёт" in run(S.op_update, {"index": 2})[0] and "ещё идёт" in run(S.op_remove, {"index": 2})[0] and os.path.exists(os.path.join(DIR, "awgk2.conf")),
   "пока идёт одна операция, вторая не начинается")
STATE["hold"].set()
STATE["hold"] = None
S.call(SVC.job_finished())
ok(S.job_public()["state"] == "done" and S.call(SVC.nodes()).busy is False, "операция закончилась — сервис снова свободен")

# 6. an exit made by hand is never logged into
with open(os.path.join(DIR, "awgk3.conf"), "w") as f:
    f.write("x")
ok(isinstance(run(S.op_adopt, {"index": 7, "iface": "awgk3", "address": "45.10.20.50", "managed": True})[0], str), "выход на чужом интерфейсе (номер 7, awgk3) управляемым не записывается")
ok(S.op_adopt({"index": 3, "address": "45.10.20.50", "managed": False}) == {"ok": True} and S.nodes()["3"]["managed"] is False, "существующая связь записывается как настроенная вручную")
plan()
r, _ = run(S.op_update, {"index": 3})
ok(isinstance(r, str) and "вручную" in r and calls() == [], "обновление выхода, настроенного вручную: отказ, на сервер не заходим", r)

# 7. update
plan()
r, job = run(S.op_update, {"index": 2, "reboot": False, "name": "exit3-de"})
ok(job["state"] == "done" and whats()[0] == "login" and "update.sh" in whats() and "reboot" not in whats() and [s["state"] for s in job["steps"] if s["key"] == "reboot"] == ["skip"] and job.get("reboot_needed") is True
   and "link.sh" not in whats() and "key" not in whats(), "обновление без перезагрузки: пакеты, модуль, файрвол, мониторинг; туннель не пересоздаётся; сказано, что перезагрузка нужна", [job, whats()])
plan()
r, job = run(S.op_update, {"index": 2, "reboot": True})
ok(job["state"] == "done" and "reboot" in whats() and whats().index("reboot") < whats().index("after-reboot") < whats().index("firewall.sh:arm") and job.get("reboot_needed") is False,
   "обновление с перезагрузкой: сервер перезагружен, проверено, что туннель поднялся сам", [job, whats()])

# 8. remove
plan()
r, job = run(S.op_remove, {"index": 2, "name": "exit3-de"})
ok(r == {"ok": True, "cleaning": True} and not os.path.exists(os.path.join(DIR, "awgk2.conf")) and not os.path.exists(os.path.join(DIR, "awgk2.key")) and "2" not in S.nodes()
   and "ip link del awgk2" in open(os.path.join(T, "ip.log")).read() and "clean.sh" in whats() and job["state"] == "done", "удаление: файл связи и ключ убраны, интерфейс снят, запись удалена, сервер прибран", [r, job])
plan()
with open(os.path.join(DIR, "awgk6.conf"), "w") as f:
    f.write("x")
r, job = run(S.op_remove, {"index": 3, "iface": "awgk6"})
ok(not os.path.exists(os.path.join(DIR, "awgk6.conf")) and os.path.exists(os.path.join(DIR, "awgk3.conf")), "удаление называет интерфейс само (старый выход 3 на другом интерфейсе): убирается именно его файл")
S.op_adopt({"index": 3, "address": "45.10.20.50", "managed": False})
r, job = run(S.op_remove, {"index": 3})
ok(r == {"ok": True, "cleaning": False} and calls() == [] and "3" not in S.nodes() and not os.path.exists(os.path.join(DIR, "awgk3.conf")), "удаление выхода, настроенного вручную: только сторона входа, на сервер не заходим", r)

# 9. moments are dates with a zone; what an older record kept as Unix seconds is read as the moment it was
with open(os.path.join(DIR, "awgk3.conf"), "w") as f:
    f.write("x")
S.op_adopt({"index": 3, "address": "45.10.20.50", "managed": False})
state_file = os.environ["EXITS_STATE_FILE"]
kept = json.load(open(state_file))
ok(all(isinstance(kept["exitjob"][k], str) and kept["exitjob"][k].endswith("Z") for k in ("started", "finished")), "время в записи операции — дата со временем и поясом (ISO 8601)", kept["exitjob"])
kept["exitjob"].update(started=1700000000, finished=1700000100)
kept["exitnodes"]["3"].update(added=1700000000, updated=1700000200)
json.dump(kept, open(state_file, "w"))
fresh = build_service(get_settings(), fake_agent)       # a process that has just started: it knows only what was kept
job, node = S.call(fresh.job()).wire(), S.call(fresh.nodes()).nodes["3"].wire()
ok(job["started"] == "2023-11-14T22:13:20Z" and job["finished"] == "2023-11-14T22:15:00Z" and node["added"] == "2023-11-14T22:13:20Z" and node["updated"] == "2023-11-14T22:16:40Z",
   "записи прежнего вида (время числом) читаются как те же моменты", [job, node])
kept["exitjob"].update(state="running", finished=None)
json.dump(kept, open(state_file, "w"))
S.call(fresh.mark_interrupted())
job = json.load(open(state_file))["exitjob"]
ok(job["state"] == "failed" and "прервана перезапуском" in job["error"] and job["finished"], "операция, оборванная перезапуском сервиса, так и помечается", job)

# 10. over HTTP: the real application (its own service and event loop), asked the way the agent asks
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, access_log=False, log_config=None))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
BASE = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"


def ask(method, path, body=None, token="s3cret-token"):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else body if isinstance(body, bytes) else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


for t in (None, "wrong", ""):
    ok(ask("GET", "/v1/nodes", token=t) == (401, {"ok": False, "error": "unauthorized"}), f"без верного токена — 401 ({t!r})")
code, r = ask("GET", "/v1/nodes")
ok(code == 200 and r["ok"] and set(r) == {"ok", "nodes", "job", "busy", "key"} and r["nodes"]["3"]["managed"] is False and r["busy"] is False and r["key"].startswith('from="198.51.100.7" ssh-ed25519 '),
   "список: серверы, последняя операция, занятость, строка ключа", r)
code, r = ask("POST", "/v1/add", dict(ADD, index=9, address="10.0.0.5"))
ok((code, r) == (400, {"ok": False, "error": "адрес сервера: нужен внешний адрес другого сервера"}), "отказ сервиса — 400 со словами для админа", r)
ok(ask("POST", "/v1/update", {"index": 3}) == (400, {"ok": False, "error": "этот выход настроен вручную — сервис на него не заходит. Обновите его сами или подключите заново через панель"})
   and ask("POST", "/v1/update", {"index": 40})[1]["error"] == "такого выхода в записях нет" and ask("POST", "/v1/adopt", {"index": 8, "address": "45.10.20.60"})[1]["error"] == "файла этой связи на сервере нет",
   "обновление выхода, настроенного вручную, неизвестного выхода и запись несуществующей связи — 400")
code, r = ask("POST", "/v1/add", dict(ADD, name="bad name"))
ok(code == 422 and "имя выхода: латиница, цифры, точка, дефис, до 32 знаков" in json.dumps(r, ensure_ascii=False), "запрос не по форме — 422, как отвечает FastAPI, и сказано, каким должно быть поле", r)
texts = [json.dumps(ask("POST", "/v1/add", body)[1], ensure_ascii=False) for body in
         (dict(ADD, index=0), dict(ADD, weight=100), dict(ADD, address="nope"), dict(ADD, password="a\nb"))]
ok("номер выхода: от 1 до 50" in texts[0] and "доля от 1 до 99 процентов" in texts[1] and "нужен IPv4-адрес" in texts[2] and "пароль: одна строка до 200 знаков" in texts[3], "у каждого поля свои слова", texts)
no_index = {k: v for k, v in ADD.items() if k != "index"}
answers = [ask("POST", "/v1/add", body) for body in (no_index, dict(ADD, name="bad name"), dict(ADD, index="2"), b'{"password": "' + PW.encode() + b'", "index": ')]
ok(all(code == 422 for code, _ in answers) and PW not in json.dumps(answers, ensure_ascii=False), "в ответе на негодный запрос пароль не повторяется", answers)
ok(ask("POST", "/v1/add", {})[0] == 422 and ask("POST", "/v1/remove", {})[0] == 422 and ask("GET", "/v1/nothing")[0] == 404 and ask("PUT", "/v1/add")[0] == 405, "пустой запрос — 422, неизвестный путь — 404, чужой метод — 405")
ok(ask("GET", "/v1/nothing", token="wrong") == (401, {"ok": False, "error": "unauthorized"}), "токен спрашивается раньше, чем разбирается путь")
ok(all(ask("GET", path)[0] == 404 for path in ("/docs", "/redoc", "/openapi.json")), "описания API наружу нет")
plan()
code, r = ask("POST", "/v1/remove", {"index": 3})
ok((code, r) == (200, {"ok": True, "cleaning": False}) and "3" not in ask("GET", "/v1/nodes")[1]["nodes"] and calls() == [], "удаление через HTTP: тот же ответ, на сервер не заходим", r)
token_file = os.environ["EXITS_TOKEN_FILE"]
os.rename(token_file, token_file + ".away")
ok(ask("GET", "/v1/nodes") == (503, {"ok": False, "error": "no token"}), "файла с токеном нет — 503, никого не пускаем")
os.rename(token_file + ".away", token_file)
server.should_exit = True

ok(PW not in everything(), "за весь прогон пароль не появился ни в одном выводе и ни в одной записи")
shutil.rmtree(T, ignore_errors=True)
print(f"\n{n - failed} of {n} checks passed")
sys.exit(1 if failed else 0)
