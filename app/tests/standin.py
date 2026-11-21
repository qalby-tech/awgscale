#!/usr/bin/env python3
"""A stand-in for the app API (panel/routes/app.py) on 127.0.0.1:8088 (or the address given),
for the pictures of the screens and the tunnel test. Everything here is made up; the answers have the panel's shapes."""
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

EXITS = [{"index": 1, "name": "nl-ams-1", "up": True, "share": 67}, {"index": 2, "name": "de-fra-1", "up": True, "share": 33},
         {"index": 3, "name": "fi-hel-1", "up": False, "share": 0}]
STATE = {"pinned": None}


def me():
    pinned = STATE["pinned"]
    name = next((exit["name"] for exit in EXITS if exit["index"] == pinned), None)
    return {"ok": True, "name": "test_laptop", "address": "172.16.17.23", "owner": True, "expires_at": None,
            "exit": {"pinned": pinned, "group": None, "name": name},
            "group": {"name": "Дом", "filter": "pro", "space": False, "members": []}}


class Handler(BaseHTTPRequestHandler):
    def answer(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        print("get", self.path, self.client_address[0], flush=True)
        if self.path == "/app/api/me":
            return self.answer(200, me())
        if self.path == "/app/api/exits":
            return self.answer(200, {"ok": True, "exits": EXITS})
        self.answer(404, {"detail": "Not Found"})

    def do_POST(self):
        if self.path != "/app/api/exit" or self.headers.get("X-Awgscale") != "1":
            return self.answer(400, {"detail": "bad request"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        STATE["pinned"] = None if body["exit"] == "auto" else body["exit"]
        print("choose", body["exit"], flush=True)
        self.answer(200, {"ok": True})

    def log_message(self, *args):
        pass


def dns(address):
    """The network's DNS, as little as the service's own check needs: every question answered
    with the same id and no records (the service only asks whether answers come back)."""
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind((address, 53))
    while True:
        query, sender = server.recvfrom(512)
        if len(query) >= 12:
            server.sendto(query[:2] + b"\x81\x80" + query[4:6] + b"\x00\x00\x00\x00\x00\x00" + query[12:], sender)


ADDRESS = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
threading.Thread(target=dns, args=(ADDRESS,), daemon=True).start()
HTTPServer((ADDRESS, 8088), Handler).serve_forever()
