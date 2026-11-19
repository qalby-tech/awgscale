#!/usr/bin/env python3
"""The resolver's keeper (src/awgscale/resolver), run for real with a stand-in
for AdGuard Home: a process that listens on a port, and that writes a file on
its way out the way AdGuard writes its config.

    uv run python tests/keeper.py"""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = FAIL = 0


def ok(cond, name, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", name)
    else:
        FAIL += 1
        print("FAIL", name, "" if detail is None else detail)


def free_ports(n):
    """n different ports: all picked before any is given back."""
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


d = tempfile.mkdtemp()
dns, api = free_ports(2)
child = os.path.join(d, "child.py")
with open(child, "w") as f:
    f.write(f'''import os, signal, socket, sys, time
d = {d!r}
open(d + "/starts", "a").write("x")
def out(*_):
    time.sleep(0.4)                       # it takes a moment to write its config
    open(d + "/saved", "a").write("x")
    sys.exit(0)
signal.signal(signal.SIGTERM, out)
if os.path.exists(d + "/no-address"):     # the tunnel is not up yet
    sys.exit(1)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", {dns})); s.listen(8)
while True:
    c, _ = s.accept(); c.close()
''')
with open(os.path.join(d, "token"), "w") as f:
    f.write("s3cret\n")


def count(name):
    try:
        return len(open(os.path.join(d, name)).read())
    except OSError:
        return 0


def call(method, path, token="s3cret", timeout=30):
    req = urllib.request.Request(f"http://127.0.0.1:{api}{path}", method=method, data=b"{}" if method == "POST" else None,
                                 headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def listens():
    try:
        socket.create_connection(("127.0.0.1", dns), timeout=1).close()
        return True
    except OSError:
        return False


def wait(cond, seconds=10):
    until = time.time() + seconds
    while time.time() < until:
        if cond():
            return True
        time.sleep(0.05)
    return False


env = dict(os.environ, RESOLVER_PORT=str(api), RESOLVER_TOKEN=os.path.join(d, "token"), RESOLVER_DNS=f"127.0.0.1:{dns}",
           RESOLVER_COMMAND=json.dumps([sys.executable, child]), RESOLVER_HOLD="3", RESOLVER_RETRY="0.3", RESOLVER_LISTEN_WAIT="5", PYTHONUNBUFFERED="1")
keeper = subprocess.Popen([sys.executable, "-m", "awgscale.resolver"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    ok(wait(listens), "смотритель сам запускает блокировщик")
    code, h = call("GET", "/v1/health")
    ok(code == 200 and h["running"] and h["wanted"] and h["starts"] == 1 and h["exits"] == 0, "health: работает, запущен один раз", h)
    ok(call("GET", "/v1/health", token="wrong")[0] == 401 and call("POST", "/v1/stop", token="")[0] == 401 and listens(), "без токена — отказ, блокировщик не тронут")
    ok(call("POST", "/v1/restart")[0] == 404, "неизвестный путь — 404")
    started = __import__("datetime").datetime.fromisoformat(h["started"])
    ok(started.tzinfo is not None and abs(started.timestamp() - time.time()) < 30 and h["listening"] is True and h["last_exit"] is None, "health: когда запущен — дата со временем и поясом; слушает; выходов не было", h)
    ok(call("GET", "/v1/health", token="wrong")[1] == {"ok": False, "error": "unauthorized"} and call("GET", "/docs")[0] == 404 and call("GET", "/v1/stop")[0] == 405, "отказ — в общем виде сервисов; описания API наружу нет; чужой метод — 405")

    code, r = call("POST", "/v1/stop")
    ok(code == 200 and r == {"ok": True} and count("saved") == 1 and not listens(), "stop отвечает, только когда процесс вышел и дописал свой файл", [code, r, count("saved")])
    ok(call("POST", "/v1/stop")[1] == {"ok": True} and count("saved") == 1, "повторный stop — уже остановлен, не ошибка")
    time.sleep(1)
    ok(not listens() and count("starts") == 1 and call("GET", "/v1/health")[1]["running"] is False, "остановленный по просьбе сам не поднимается (пока не вышел срок)")
    code, r = call("POST", "/v1/start")
    ok(code == 200 and r == {"ok": True} and listens() and count("starts") == 2, "start отвечает, когда блокировщик уже слушает", [code, r])
    ok(call("POST", "/v1/start")[1] == {"ok": True} and count("starts") == 2, "повторный start — второй процесс не запускается")

    call("POST", "/v1/stop")
    ok(wait(listens, 8) and count("starts") == 3, "остановили и не попросили запустить — через срок запускается сам")

    pid = int(subprocess.run(["pgrep", "-P", str(keeper.pid)], capture_output=True, text=True).stdout.split()[0])
    os.kill(pid, signal.SIGKILL)
    ok(wait(lambda: count("starts") == 4 and listens(), 8), "блокировщик умер сам — запущен снова")
    h = call("GET", "/v1/health")[1]
    ok(h["exits"] == 1 and h["last_exit"] == -9 and h["starts"] == 4, "health считает падения и помнит код выхода", h)

    open(os.path.join(d, "no-address"), "w").close()
    call("POST", "/v1/stop")
    code, r = call("POST", "/v1/start")
    ok(code == 500 and r == {"ok": False, "error": "блокировщик не запустился"}, "не смог запуститься (нет адреса туннеля) — start так и отвечает: 500 и причина словами", [code, r])
    n = count("starts")
    ok(wait(lambda: count("starts") >= n + 3, 8), "и смотритель продолжает пробовать")
    os.remove(os.path.join(d, "no-address"))
    ok(wait(listens, 8), "адрес появился — блокировщик поднялся без просьбы")

    saved = count("saved")
    keeper.send_signal(signal.SIGTERM)
    ok(keeper.wait(15) == 0 and count("saved") == saved + 1 and not listens(), "SIGTERM смотрителю: блокировщик завершён чисто, затем выход с кодом 0")
finally:
    if keeper.poll() is None:
        keeper.kill()
    if FAIL:
        print(keeper.stdout.read())
print()
print(f"{PASS} of {PASS + FAIL} checks passed")
sys.exit(1 if FAIL else 0)
