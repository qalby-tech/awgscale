#!/usr/bin/env python3
"""A stand-in for the app API (panel/routes/app.py) on 127.0.0.1:8088 (or the address given),
for the pictures of the screens and the tunnel test. Everything here is made up; the answers have the panel's shapes.

The screens' test changes how the protection's choice is answered (POST /test/filter, {"delay": seconds,
"refuse": true|false, "locked": true|false}): late, refused as the agent refuses it, or the device in a
children's group (its level set by an adult, a change refused)."""
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
# STANDIN_PAYLOAD=older: the exits as a server older than the locations sends them, with no address, country,
# users or online
OLDER_FIELDS = ("address", "country", "users", "online")
if os.environ.get("STANDIN_PAYLOAD") == "older":
    EXITS = [{key: value for key, value in exit.items() if key not in OLDER_FIELDS} for exit in EXITS]
STATE = {"pinned": None, "own": None, "all": False}
# The owner's network (panel/owner.py): as an older server, no owner's part at all (404)
OWNER = os.environ.get("STANDIN_PAYLOAD") != "older"
RULES = [
    {"id": "yt0001", "name": "YouTube", "route": "exit", "way": "exit", "items": 10, "by_link": False, "movable": True, "groups": []},
    {"id": "ai0001", "name": "AI", "route": "exit", "way": "exit", "items": 6, "by_link": False, "movable": True, "groups": []},
    {"id": "sc0001", "name": "Social", "route": "block", "way": "block", "items": 12, "by_link": False, "movable": True, "groups": ["Kids"], "group_ids": ["kid001"]},
    {"id": "tg0001", "name": "Telegram", "route": "exit", "way": "exit", "items": 9, "by_link": False, "movable": False, "groups": []},
    {"id": "bk0001", "name": "Banks", "route": "direct", "way": "direct", "items": 24, "by_link": False, "movable": True, "groups": []},
    {"id": "ad0001", "name": "HaGeZi Pro++", "route": "block", "way": "block", "items": 0, "by_link": True, "movable": False, "groups": []},
]
OWNER_EXITS = [
    {"index": 1, "name": "exit1-nl", "state": "up", "country": "nl", "weight": 3, "share": 50, "users": 17, "online": 4, "off": False},
    {"index": 2, "name": "exit2-nl", "state": "down", "country": "nl", "weight": 1, "share": 17, "users": 0, "online": 0, "off": False},
    {"index": 4, "name": "exit4-de", "state": "up", "country": "de", "weight": 2, "share": 33, "users": 6, "online": 2, "off": False},
]
GROUPS = [
    {"id": "fam001", "name": "Family", "filter": "pro", "space": False, "users": 6, "online": 4},
    {"id": "kid001", "name": "Kids", "filter": "kids", "space": False, "users": 3, "online": 2},
    {"id": None, "name": "", "filter": None, "space": False, "users": 2, "online": 0},
]
JOURNAL = [
    {"t": 1764000100, "text": "Mode: everything foreign abroad", "bad": False, "who": "app", "device": "test_laptop"},
    {"t": 1764000000, "text": "Exit shares changed: exit1-nl 50%, exit4-de 33%", "bad": False, "who": "panel"},
    {"t": 1763990000, "text": "exit2-nl is down: its users moved", "bad": True, "who": None},
]
# a 1×1 PNG: the QR code of an invitation, as far as the screens care
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNgAAAAAgABSK+kcQAAAABJRU5ErkJggg=="


PEOPLE = [
    {"name": "test_laptop", "online": True, "paused": False, "seen": 1764000000, "expires_at": None, "exit": 1, "group": "fam001", "owner": True, "me": True},
    {"name": "mom_phone", "online": True, "paused": False, "seen": 1764000000, "expires_at": None, "exit": 1, "group": "fam001", "owner": False, "me": False},
    {"name": "dad_tv", "online": False, "paused": True, "seen": 1763000000, "expires_at": None, "exit": 4, "group": "fam001", "owner": False, "me": False},
]
NETWORK = {"mode": "list"}


def network():
    return {"ok": True, "mode": NETWORK["mode"], "single_server": False, "users": 11, "online": 6, "traffic": {"exit": 620, "direct": 310, "block": 70},
            "exits": OWNER_EXITS, "rules": RULES, "rules_v": "0123456789ab", "groups": GROUPS, "stale": False,
            "invite_base": "https://vpn.example.org", "me": "test_laptop", "owners": ["test_laptop"]}
# how the protection's choice is answered
FILTER = {"delay": 0.0, "refuse": False, "locked": False}


def me():
    pinned = STATE["pinned"]
    name = next((exit["name"] for exit in EXITS if exit["index"] == pinned), None)
    return {"ok": True, "name": "test_laptop", "address": "172.16.17.23", "owner": True, "expires_at": None,
            "exit": {"pinned": pinned, "group": None, "name": name},
            "group": {"name": "Дом", "filter": "kids" if FILTER["locked"] else "std", "space": False, "members": []},
            "filter": filter_choice(),
            "route": {"all": STATE["all"], "available": True}}


def filter_choice():
    if FILTER["locked"]:
        return {"applies": "kids", "own": None, "locked": True}
    return {"applies": STATE["own"] or "std", "own": STATE["own"], "locked": False}


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
        if OWNER and self.path == "/app/api/owner/network":
            return self.answer(200, network())
        if OWNER and self.path == "/app/api/owner/journal":
            return self.answer(200, {"ok": True, "items": JOURNAL})
        if OWNER and self.path.startswith("/app/api/owner/people"):
            print("people", self.path.partition("?")[2], flush=True)
            return self.answer(200, {"ok": True, "total": len(PEOPLE), "people": PEOPLE})
        self.answer(404, {"detail": "Not Found"})

    def do_POST(self):
        if self.path == "/test/filter":
            FILTER.update(json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")))))
            return self.answer(200, {"ok": True})
        if OWNER and self.path.startswith("/app/api/owner/"):
            return self.owner()
        if self.headers.get("X-Awgscale") != "1" or self.path not in ("/app/api/exit", "/app/api/filter", "/app/api/route"):
            return self.answer(400, {"detail": "bad request"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path == "/app/api/route":
            STATE["all"] = body["route"] == "all"
            print("route", body["route"], flush=True)
            return self.answer(200, {"ok": True})
        if self.path == "/app/api/filter":
            print("filter", body["filter"], flush=True)
            time.sleep(FILTER["delay"])
            if FILTER["locked"]:
                return self.answer(400, {"ok": False, "error": "защиту этого устройства задаёт взрослый: в детской группе её не поменять"})
            if FILTER["refuse"]:
                return self.answer(400, {"ok": False, "error": "Protection not applied: the blocker does not answer"})
            STATE["own"] = None if body["filter"] == "auto" else body["filter"]
            print("filter taken", body["filter"], flush=True)
            return self.answer(200, {"ok": True, "name": "test_laptop", "filter": filter_choice()})
        STATE["pinned"] = None if body["exit"] == "auto" else body["exit"]
        print("choose", body["exit"], flush=True)
        self.answer(200, {"ok": True})

    def owner(self):
        """The owner's changes, as the panel takes them: each is printed for screens.sh to check."""
        if self.headers.get("X-Awgscale") != "1":
            return self.answer(400, {"ok": False, "error": "bad request"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path == "/app/api/owner/rules":
            for change in body["changes"]:
                rule = next(rule for rule in RULES if rule["id"] == change["rule"])
                if "route" in change:
                    print("rule", change["rule"], change["route"], body.get("base"), flush=True)
                    rule["route"] = change["route"]
                    rule["way"] = rule["way"] if change["route"] == "off" else change["route"]
                if "groups" in change:
                    print("rule groups", change["rule"], ",".join(change["groups"]) or "-", flush=True)
                    rule["group_ids"] = change["groups"]
                    rule["groups"] = [next(group["name"] for group in GROUPS if group["id"] == id_) for id_ in change["groups"]]
            if body.get("order") is not None:
                print("order", ",".join(body["order"]), flush=True)
                RULES.sort(key=lambda rule: body["order"].index(rule["id"]))
            return self.answer(200, {"ok": True})
        if self.path == "/app/api/owner/mode":
            print("mode", body["mode"], flush=True)
            NETWORK["mode"] = body["mode"]
            return self.answer(200, {"ok": True})
        if self.path == "/app/api/owner/people/pause":
            print("pause", ",".join(body["names"]), body["on"], flush=True)
            for person in PEOPLE:
                if person["name"] in body["names"]:
                    person["paused"] = body["on"]
            return self.answer(200, {"ok": True})
        if self.path == "/app/api/owner/people/remove":
            print("remove", ",".join(body["names"]), flush=True)
            PEOPLE[:] = [person for person in PEOPLE if person["name"] not in body["names"]]
            return self.answer(200, {"ok": True})
        if self.path == "/app/api/owner/weights":
            print("weights", json.dumps(body["weights"], sort_keys=True), "base", json.dumps(body["base"], sort_keys=True), flush=True)
            for exit_ in OWNER_EXITS:
                exit_["weight"] = body["weights"][str(exit_["index"])]
                exit_["off"] = exit_["weight"] == 0
            return self.answer(200, {"ok": True})
        if self.path == "/app/api/owner/invite":
            print("invite", body.get("uses"), body.get("days"), body.get("group"), flush=True)
            return self.answer(200, {"ok": True, "id": "inv001", "token": "Zx3kZx3kZx3kZx3kZx3kZx"})
        if self.path == "/app/api/owner/invite/qr":
            print("invite qr", body["link"], flush=True)
            return self.answer(200, {"ok": True, "png": PNG})
        return self.answer(404, {"ok": False, "error": "not found"})

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
ThreadingHTTPServer((ADDRESS, 8088), Handler).serve_forever()
