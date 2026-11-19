#!/usr/bin/env python3
"""The panel server hands out the built page (web/out on the server: panel/web):
its index, stamped with the id of the build, and the files under /_next/ — and
nothing else from the disk.



The real server (python -m awgscale.panel) runs as a process against a made-up
build in a temp directory; its agent socket does not exist, so nothing reaches a server.

    uv run python tests/test_panel_page.py
One line per check, the last line is "N of M checks passed".
"""
import http.client
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
n = failed = 0


def ok(cond, what, got=None):
    global n, failed
    n += 1
    failed += not cond
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or got is None else f": {got!r}"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, {name.title(): value for name, value in r.getheaders()}, r.read()        # header names in one spelling: their case means nothing


def main():
    with tempfile.TemporaryDirectory() as tmp:
        web = os.path.join(tmp, "web")
        os.makedirs(os.path.join(web, "_next", "static", "chunks"))
        with open(os.path.join(web, "index.html"), "w") as f:
            f.write('<meta name="ui" content="__UI_ID__"><script src="/_next/static/chunks/a1.js"></script>')
        with open(os.path.join(web, "_next", "static", "chunks", "a1.js"), "w") as f:
            f.write("console.log(1)")
        with open(os.path.join(web, "_next", "data.txt"), "w") as f:
            f.write("flight")
        with open(os.path.join(tmp, "secret.txt"), "w") as f:
            f.write("not for the web")
        port = free_port()
        env = dict(os.environ, PANEL_WEB_DIR=web, PANEL_PORT=str(port), PANEL_BIND="127.0.0.1", PANEL_AGENT_SOCKET=os.path.join(tmp, "no.sock"),
                   PANEL_PASSWORD_FILE=os.path.join(tmp, ".password"), PYTHONDONTWRITEBYTECODE="1")
        env.pop("PANEL_METRICS_PORT", None)
        p = subprocess.Popen([sys.executable, "-m", "awgscale.panel"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    get(port, "/")
                    break
                except OSError:
                    time.sleep(0.1)
            code, head, body = get(port, "/")
            ok(code == 200 and head.get("Content-Type", "").startswith("text/html"), "the page is served at /", (code, head.get("Content-Type")))
            ok(b"__UI_ID__" not in body and b'content="' in body and len(body.split(b'content="')[1].split(b'"')[0]) == 12, "the page carries the 12-character id of the build", body[:80])
            ok("script-src 'self'" in head.get("Content-Security-Policy", "") and head.get("Cache-Control") == "no-store", "with the content policy, never cached", head)
            code, head, body = get(port, "/_next/static/chunks/a1.js")
            ok(code == 200 and body == b"console.log(1)" and head.get("Content-Type", "").startswith("text/javascript"), "a script of the build is served", (code, head.get("Content-Type")))
            ok("immutable" in head.get("Cache-Control", "") and head.get("X-Content-Type-Options") == "nosniff", "named by its content, so cached for good", head.get("Cache-Control"))
            code, head, body = get(port, "/_next/data.txt")
            ok(code == 200 and head.get("Cache-Control") == "no-store", "a file outside static/ is not cached", (code, head.get("Cache-Control")))
            for path in ("/_next/../secret.txt", "/_next/%2e%2e/secret.txt", "/_next/static/../../secret.txt", "/_next//etc/passwd", "/_next/static/chunks/none.js", "/_next/", "/_next/static"):
                code, _, body = get(port, path)
                ok(code == 404 and b"not for the web" not in body and b"root:" not in body, "nothing outside the build: " + path, code)
            code, _, _ = get(port, "/api/sites")
            ok(code == 401, "the API still asks for a login", code)
        finally:
            p.terminate()
            p.wait(timeout=5)
    print(f"\n{n - failed} of {n} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
