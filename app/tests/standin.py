#!/usr/bin/env python3
"""A stand-in for the app API (panel/routes/app.py) on 127.0.0.1:8088 (or the address given),
for the pictures of the screens and the tunnel test. Everything here is made up; the answers have the panel's shapes."""
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# A few countries, one server down, one of a country nobody knows. The best of nl is 5, not 2: as few online,
# fewer people.
EXITS = [
    {"index": 1, "name": "exit1-nl", "address": "198.51.100.38", "country": "nl", "up": True, "share": 30, "users": 17, "online": 4},
    {"index": 2, "name": "exit2-nl", "address": "198.51.100.41", "country": "nl", "up": True, "share": 20, "users": 9, "online": 1},
    {"index": 3, "name": "exit3-fi", "address": "203.0.113.12", "country": "fi", "up": False, "share": 0, "users": 0, "online": 0},
    {"index": 4, "name": "exit4-de", "address": "203.0.113.20", "country": "de", "up": True, "share": 15, "users": 6, "online": 2},
    {"index": 5, "name": "exit5-nl", "address": "198.51.100.52", "country": "nl", "up": True, "share": 10, "users": 3, "online": 1},
    {"index": 6, "name": "exit6-de", "address": "203.0.113.21", "country": "de", "up": True, "share": 10, "users": 4, "online": 0},
    {"index": 7, "name": "exit7-us", "address": "203.0.113.30", "country": "us", "up": True, "share": 10, "users": 5, "online": 3},
    {"index": 8, "name": "exit8", "address": "203.0.113.40", "country": None, "up": True, "share": 5, "users": 1, "online": 0},
]
STATE = {"pinned": None, "own": None, "all": False}


def me():
    pinned = STATE["pinned"]
    name = next((exit["name"] for exit in EXITS if exit["index"] == pinned), None)
    return {"ok": True, "name": "test_laptop", "address": "172.16.17.23", "owner": True, "expires_at": None,
            "exit": {"pinned": pinned, "group": None, "name": name},
            "group": {"name": "Дом", "filter": "pro", "space": False, "members": []},
            "filter": {"applies": STATE["own"] or "pro", "own": STATE["own"], "locked": False},
            "route": {"all": STATE["all"], "available": True}}


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
        if self.headers.get("X-Awgscale") != "1" or self.path not in ("/app/api/exit", "/app/api/filter", "/app/api/route"):
            return self.answer(400, {"detail": "bad request"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path == "/app/api/route":
            STATE["all"] = body["route"] == "all"
            print("route", body["route"], flush=True)
            return self.answer(200, {"ok": True})
        if self.path == "/app/api/filter":
            STATE["own"] = None if body["filter"] == "auto" else body["filter"]
            print("filter", body["filter"], flush=True)
            return self.answer(200, {"ok": True})
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
