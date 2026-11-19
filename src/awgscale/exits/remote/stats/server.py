#!/usr/bin/env python3
"""How an exit server is doing: load, memory, disk — the same figures the entry
shows about itself, asked by the entry's agent through the tunnel.

    GET /v1/system → {"hostname", "load1", "cpus", "mem_total", "mem_used", "uptime", "disk_pct"}

Listens on the tunnel's own address only (STATS_BIND), so it is reachable from
the entry and from nowhere else; the exit's firewall says the same. The address
exists only while the tunnel is up: until then it keeps trying to bind.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND = os.environ.get("STATS_BIND") or ""
PORT = int(os.environ.get("STATS_PORT") or 9101)
DISK = os.environ.get("STATS_DISK") or "/host-root"       # any path that lives on the host's root filesystem
NAME = os.environ.get("STATS_NAME") or os.uname().nodename


def system():
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) * 1024
    with open("/proc/loadavg") as f:
        load = float(f.read().split()[0])
    with open("/proc/uptime") as f:
        uptime = int(float(f.read().split()[0]))
    try:
        st = os.statvfs(DISK)
        disk = round(100 * (1 - st.f_bavail / st.f_blocks))
    except (OSError, ZeroDivisionError):
        disk = None
    return {"hostname": NAME, "load1": load, "cpus": os.cpu_count() or 1, "mem_total": mem.get("MemTotal", 0),
            "mem_used": mem.get("MemTotal", 0) - mem.get("MemAvailable", 0), "uptime": uptime, "disk_pct": disk}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/system":
            code, body = 200, json.dumps(system()).encode()
        else:
            code, body = 404, b"{}"
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    if not BIND:
        print("STATS_BIND is not set: refusing to listen on every address", flush=True)
        return 1
    said = False
    while True:
        try:
            srv = ThreadingHTTPServer((BIND, PORT), Handler)
            break
        except OSError as e:
            if not said:
                print(f"waiting for {BIND}:{PORT}: {e}", flush=True)
                said = True
            time.sleep(5)
    print(f"listening on {BIND}:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
