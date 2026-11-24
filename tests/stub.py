#!/usr/bin/env python3
"""A stand-in for the panel server, for browser tests: serves the REAL page
(the build in web/out) and answers its API from memory with made-up users. It
behaves like the server and the agent together where the page depends on it:
versions and refusal of stale saves, validation of names, users that appear in
the state only on the next poll, a journal. Nothing here touches a real server.

    stub.py <port> [path to the built page, web/out]

Test controls (not part of the real API):
    POST /__ctl  {"reset": true}                 back to the starting data
                 {"set": {<state key>: value}}   e.g. {"set": {"exits_down": [3]}}, {"set": {"single": true}} (a server without exits), {"set": {"traffic_empty": true}}, {"set": {"traffic_x": 2}}, {"set": {"traffic_fail": true}}, {"set": {"countries": {"1": "nl", "3": null}}}
                 {"fail": "/api/path"}           the next request to that path answers 400 with an error
                 {"many": N}                     N more users in no group (bulk_0000…), never connected
    GET  /__log                                   every request made so far (method, path, body)
"""
import copy, datetime, hashlib, json, os, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 38787
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else REPO + "/web/out")
# the same policy the panel server sends with the page: a build that needs more than it allows must fail here, not on the server
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")
TYPES = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".txt": "text/plain", ".json": "application/json",
         ".woff2": "font/woff2", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".map": "application/json"}
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}$")
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
LOCK = threading.Lock()
INVITE_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; "
              "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")        # the same as the panel sends (src/awgscale/panel/routes/invites.py)


def version(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def fresh():
    now = int(time.time())
    users = [  # name, last octet, online, exit, rx, tx, down_bps, up_bps, expires in days
        ("anna_phone", 2, True, 1, 4.1e9, 5.7e9, 1.2e6, 2.0e5, None), ("tv_gostinaya", 3, True, 3, 9e9, 1.03e11, 3.6e7, 9.0e5, None),
        ("olga_ipad", 4, True, 3, 1.1e9, 2.0e9, 8.0e5, 6.0e4, 6), ("ivan_laptop", 5, True, 1, 9e9, 1.2e10, 0, 0, None),
        ("petr_phone", 6, False, 1, 3e8, 5.4e8, 0, 0, None), ("max_phone", 7, False, 3, 2e9, 4.4e9, 0, 0, None),
        ("printer_office", 8, False, 1, 5e6, 1e7, 0, 0, None), ("router_dacha", 9, True, 1, 2.0e10, 2.8e10, 2.4e6, 3.0e5, None),
        ("tv_dacha", 10, False, 3, 4e9, 8.7e9, 0, 0, None), ("guest_1", 11, False, 1, 1e8, 2.1e8, 0, 0, 2), ("new_phone", 12, False, None, 0, 0, 0, 0, None)]
    seen = {"petr_phone": 86400, "max_phone": 300, "printer_office": 12 * 86400, "tv_dacha": 3 * 3600, "guest_1": 2 * 86400}
    clients = [{"name": n, "ip": f"172.16.17.{o}", "suspended": False, "expires_at": now + d * 86400 if d else None,
                "endpoint": f"203.0.113.{o}:5{o:04d}" if on or n in seen else None,
                "handshake": now - 20 if on else (now - seen[n] if n in seen else 0), "rx": int(rx), "tx": int(tx),
                "exit": ex, "pin": None, "online": on, "down_bps": float(dn), "up_bps": float(up)} for n, o, on, ex, rx, tx, dn, up, d in users]
    return {
        "clients": clients, "weights": {1: 2, 3: 1}, "countries": {"1": "nl", "3": None}, "exits_down": [], "next_ip": 20, "pending": [],
        "ugroups": [{"id": "g00001", "name": "Дом", "space": False, "exit": None, "filter": "std", "members": ["anna_phone", "tv_gostinaya", "olga_ipad", "max_phone"]},
                    {"id": "g00002", "name": "Офис", "space": False, "exit": None, "filter": "std", "members": ["ivan_laptop", "petr_phone", "printer_office"]},
                    {"id": "g00003", "name": "Дача", "space": True, "exit": None, "filter": "std", "members": ["router_dacha", "tv_dacha"]}],
        "spaces": [{"id": "s00001", "name": "Игры", "on": True, "members": ["ivan_laptop", "max_phone"]}],
        "sites": [{"id": "a00001", "name": "YouTube", "on": True, "items": ["youtube.com", "googlevideo.com", "ytimg.com"]},
                  {"id": "a00002", "name": "Telegram", "on": True, "items": ["telegram.org", "t.me"], "auto": "telegram"},
                  {"id": "a00003", "name": "ИИ", "on": True, "items": ["openai.com", "chatgpt.com", "anthropic.com", "claude.ai", "*.perplexity.ai"]},
                  {"id": "a00004", "name": "Работа", "on": False, "items": ["notion.so", "canva.com", "10.20.0.0/16"]},
                  {"id": "a00005", "name": "Список друга", "on": True, "url": "https://example.org/list.txt", "items": ["a.example", "b.example", "shop.example"], "updated": now - 3600, "error": ""}],
        "block_groups": [{"id": "b00001", "name": "Свои", "on": True, "items": ["casino.example", "ads.example"]}],
        "block_lists": [{"id": 1, "title": "Реклама", "name": "AdGuard DNS filter", "url": "https://example.org/adguard.txt", "custom": False, "on": True, "rules": 190777, "updated": "2026-11-12T05:00:00Z", "day": 5123},
                        {"id": 2, "title": "AdAway", "name": "AdAway Default Blocklist", "url": "https://example.org/adaway.txt", "custom": False, "on": True, "rules": 6540, "updated": "2026-11-12T05:00:00Z", "day": 310}],
        "safebrowsing": True, "dns_devices": [{"name": "tv_kitchen", "code": "k3x9q2w8e7r6", "created": now - 86400, "last": now - 600, "count": 1200}],
        "journal": [{"t": now - 7200, "text": "Создан пользователь new_phone", "bad": False, "kind": "change", "who": "panel"},
                    {"t": now - 3600, "text": "Выход exit2-nl недоступен", "bad": True, "kind": "exit", "who": "system"},
                    {"t": now - 3300, "text": "Выход exit2-nl снова работает (не отвечал 5 мин)", "bad": False, "kind": "exit", "who": "system"}],
        "traffic_empty": False, "traffic_x": 1,     # the history: nothing collected yet / every figure multiplied (as if it had grown)
        "traffic_fail": False,                      # …/ the request for it fails, every time while this is set
        "invites": [], "log": [], "fail": None,
    }


S = fresh()


def jadd(text, bad=False, kind="change"):
    S["journal"].append({"t": int(time.time()), "text": text, "bad": bad, "kind": kind, "who": "panel"})


def state():
    now = int(time.time())
    # a user created a moment ago shows up one poll later, as on the real server (its state is a few seconds old)
    S["pending"] = [p for p in S["pending"] if p[1] > time.time() or S["clients"].append(p[0])]
    exits = [{"index": 1, "name": "exit1-nl", "iface": "awgk1", "weight": S["weights"][1], "endpoint": "198.51.100.38:47211"},
             {"index": 3, "name": "exit2-nl", "iface": "awgk2", "weight": S["weights"][3], "endpoint": "198.51.100.163:47212"}]
    for e in exits:
        mine = [c for c in S["clients"] if c["exit"] == e["index"]]
        up = e["index"] not in S["exits_down"]
        e.update(state="up" if up else "down", handshake=now - 30 if up else now - 900, rx=10 ** 9, tx=10 ** 10, iface_rx=10 ** 9, iface_tx=10 ** 10,
                 **({"sys": {"load1": 0.05, "cpus": 1, "mem_total": 2 * 10 ** 9, "mem_used": 5 * 10 ** 8, "uptime": 300000, "disk_pct": 26}} if up and e["index"] == 3 else {}),
                 users=len(mine), online=sum(1 for c in mine if c["online"]),
                 down_bps=sum(c["down_bps"] for c in mine) if up else 0.0, up_bps=sum(c["up_bps"] for c in mine) if up else 0.0)
    on = [c for c in S["clients"] if c["online"]]
    hist = [{"t": now - 5 * (120 - i), "down": 2e7 + 1.5e7 * ((i * 7) % 11) / 10, "up": 1e6 + 9e5 * ((i * 5) % 7) / 6} for i in range(120)]
    return {"ok": True, "time": now, "ui": "t",
            "entry": {"hostname": "entry", "load1": 0.3, "cpus": 2, "mem_total": 2 * 10 ** 9, "mem_used": 9 * 10 ** 8, "uptime": 170000, "disk_pct": 41,
                      "wan_if": "eth0", "wan_rx": 10 ** 11, "wan_tx": 10 ** 11, "listen_port": 58728, "client_if_up": True},
            "exits": [] if S.get("single") else exits, "ru_networks": 8664,
            "routing": {"mode": S.get("mode", "list"), "udp_via_exit": True, "exit_nets": 11, "exit_domains": 13, "exit_patterns": 1, "direct_domains": 0, "exit_ips": 1336, "direct_ips": 0,
                        **({"home_country": S["country"]} if S.get("country", "ru") != "ru" else {}), **({"single_server": True} if S.get("single") else {})},
            "users": counted(now),
            "totals": {"online": len(on), "clients": len(S["clients"]), "down_bps": sum(c["down_bps"] for c in on), "up_bps": sum(c["up_bps"] for c in on)},
            "history": hist,
            "dns": {"ok": True, "queries": 48211, "blocked": 7990, "safe": 3, "avg_ms": 11.2,
                    "top_blocked": [{"domain": "ads.example", "count": 900}, {"domain": "track.example", "count": 400}],
                    "top_clients": [{"ip": "172.16.17.3", "count": 9000}],
                    "top_sites": [{"name": "Яндекс", "count": 5000}, {"name": "ВКонтакте", "count": 2100}], "top_sites_total": 20000,
                    "top_exit": [{"name": "YouTube", "count": 4000}, {"name": "Telegram", "count": 1500}], "top_exit_total": 9000},
            "ugroups_v": version(S["ugroups"]), "sites_v": version(S["sites"]), "spaces_v": version(S["spaces"])}


def group_of(name):
    return next((g["id"] for g in S["ugroups"] if name in g["members"]), None)


def counted(now):
    """The users as the agent counts them at a look: never listed in the state."""
    cs = S["clients"]

    def count(members):
        return {"users": len(members), "online": sum(1 for c in members if c["online"]), "paused": sum(1 for c in members if c["suspended"]), "down_bps": sum(c["down_bps"] for c in members),
                "up_bps": sum(c["up_bps"] for c in members), "rx": sum(c["rx"] for c in members), "tx": sum(c["tx"] for c in members)}
    by_exit = sorted({c["exit"] for c in cs if c["exit"] is not None})
    return {"users": len(cs), "online": sum(1 for c in cs if c["online"]), "paused": sum(1 for c in cs if c["suspended"]), "live": len(cs),
            "never": sum(1 for c in cs if not c["handshake"]), "expiring": sum(1 for c in cs if c["expires_at"] and now < c["expires_at"] <= now + 86400),
            "rx": sum(c["rx"] for c in cs), "tx": sum(c["tx"] for c in cs), "down_bps": sum(c["down_bps"] for c in cs), "up_bps": sum(c["up_bps"] for c in cs),
            "groups": [{"id": g["id"], **count([c for c in cs if group_of(c["name"]) == g["id"]])} for g in S["ugroups"]],
            "ungrouped": count([c for c in cs if group_of(c["name"]) is None]),
            "exits": [{"index": i, **count([c for c in cs if c["exit"] == i])} for i in by_exit]}


ORDERS = {
    "address": lambda c: [int(p) for p in c["ip"].split(".")],
    "name": lambda c: c["name"].lower(),
    "seen": lambda c: -(c["handshake"] or float("-inf")),
    "traffic": lambda c: -(c["rx"] + c["tx"]),
    "speed": lambda c: -(c["down_bps"] + c["up_bps"]),
    "expires": lambda c: c["expires_at"] or float("inf"),
}
STATUSES = ("all", "online", "offline", "paused", "expiring", "stale", "never")


def clients_page(q, names):
    """GET /api/clients: what the agent's `clients` answers — searched, filtered, ordered, one page and the total."""
    now = int(time.time())
    try:
        limit = int(q.get("limit", 50))
        offset = int(q.get("offset", 0))
        within = int(q.get("within", 86400))
    except ValueError:
        return 422, {"detail": "bad"}
    status = q.get("status", "all")
    order = q.get("sort", "address")
    if not 0 <= limit <= 100 or offset < 0 or status not in STATUSES or order not in ORDERS:
        return 422, {"detail": "bad"}
    test = {
        "all": lambda c: True, "online": lambda c: c["online"], "offline": lambda c: not c["online"], "paused": lambda c: c["suspended"],
        "expiring": lambda c: bool(c["expires_at"]) and now < c["expires_at"] <= now + within, "stale": lambda c: not c["handshake"] or c["handshake"] < now - within,
        "never": lambda c: not c["handshake"],
    }[status]
    needle = q.get("q", "").strip().lower()
    group = q.get("group")
    chosen = [c for c in S["clients"] if test(c) and (not needle or needle in c["name"].lower() + " " + c["ip"])
              and (group is None or group_of(c["name"]) == (None if group == "none" else group))
              and ("exit" not in q or str(c["exit"]) == q["exit"]) and (not names or c["name"] in names)]
    chosen.sort(key=ORDERS["address"])
    chosen.sort(key=ORDERS[order], reverse=q.get("desc") == "true")
    page = [{**copy.deepcopy(c), "group": group_of(c["name"])} for c in chosen[offset:offset + limit]]
    return 200, {"ok": True, "time": now, "total": len(chosen), "offset": offset, "clients": page}


def traffic():
    """The history as the agent's `traffic` answers it, made up from the users'
    counters so that it is the same on any day: on day i of the 30 (29 is today)
    a user downloads tx/1000 × (7i mod 10 + 1) — the tallest days are 7, 17 and
    27, today is 4 of 10 — and in hour j of the 24 tx/10000 × (j mod 3 + 1);
    upload likewise from rx. Whoever has no counters (new_phone) has no history."""
    now, msk = int(time.time()), datetime.timezone(datetime.timedelta(hours=3))
    at = lambda back, fmt: datetime.datetime.fromtimestamp(now - back, msk).strftime(fmt)
    users, x = {}, S["traffic_x"]
    for c in [] if S["traffic_empty"] else S["clients"]:
        if c["rx"] or c["tx"]:
            days = [[c["tx"] // 1000 * ((7 * i) % 10 + 1) * x, c["rx"] // 1000 * ((7 * i) % 10 + 1) * x] for i in range(30)]
            hours = [[c["tx"] // 10000 * (j % 3 + 1) * x, c["rx"] // 10000 * (j % 3 + 1) * x] for j in range(24)]
            users[c["name"]] = {"days": days, "hours": hours, "totals": {"day": [sum(v[0] for v in hours), sum(v[1] for v in hours)], "month": [sum(v[0] for v in days), sum(v[1] for v in days)]}}
    return {"ok": True, "time": now, "days": [at(86400 * i, "%Y-%m-%d") for i in range(29, -1, -1)], "hours": [at(3600 * i, "%Y-%m-%dT%H") for i in range(23, -1, -1)], "users": users}


def known():
    return {c["name"] for c in S["clients"]} | {p[0]["name"] for p in S["pending"]}


def ugroups_payload():
    return {"ok": True, "limit": 30, "v": version(S["ugroups"]), "groups": copy.deepcopy(S["ugroups"])}


def sites_payload(**extra):
    return {"ok": True, "mode": S.get("mode", "list"), "limit": 500, "max_lists": 4, "v": version(S["sites"]), "auto": {"telegram": {"nets": 17}}, "groups": copy.deepcopy(S["sites"]), **extra}


def spaces_payload():
    return {"ok": True, "limit": 20, "v": version(S["spaces"]), "spaces": copy.deepcopy(S["spaces"])}


def block_payload():
    return {"ok": True, "lists": copy.deepcopy(S["block_lists"]), "max_lists": 6, "safebrowsing": {"on": S["safebrowsing"], "day": 3},
            "groups": copy.deepcopy(S["block_groups"]), "top": [{"domain": "ads.example", "count": 900}], "queries": 48211, "blocked": 7990, "v": version(S["block_groups"])}


def new_id(prefix, items):
    n = 100
    while f"{prefix}{n:05d}" in {i.get("id") for i in items}:
        n += 1
    return f"{prefix}{n:05d}"


def conflict(payload, what):
    return 400, dict(payload, ok=False, conflict=True, error=f"{what} изменились в другом окне — показан новый список")


def invites_public():
    names = {g["id"]: g["name"] for g in S["ugroups"]}
    return [{k: v for k, v in dict(i, group_name=names.get(i["group"])).items() if k != "token"} for i in S["invites"] if i["left"] > 0]


def invite_routes(method, path, d):
    """The public page behind an invitation link, as the real server answers it."""
    m = re.fullmatch(r"/i/([A-Za-z0-9_-]{22})(/info)?/?", path)
    if not m or (method == "POST" and m.group(2)):
        return 404, {"ok": False, "error": "not found"}
    it = next((i for i in S["invites"] if i["token"] == m.group(1) and i["left"] > 0), None)
    if method == "GET" and not m.group(2):
        return 200, open(REPO + "/panel/invite.html", "rb").read()
    if not it:
        return 404, {"ok": False, "error": "Ссылка недействительна или уже использована"}
    group = next((g for g in S["ugroups"] if g["id"] == it["group"]), None)
    if method == "GET":
        return 200, {"ok": True, "group": group["name"] if group else None, "left": it["left"], "total": it["total"], "expires_at": it["expires_at"]}
    name = str(d.get("name", "")) if isinstance(d, dict) else ""
    if not NAME_RE.match(name):
        return 400, {"ok": False, "error": "Имя: латинские буквы, цифры, _ и -, до 32 символов"}
    code, res = post("/api/clients", {"name": name})
    if code != 200:
        return 400, {"ok": False, "error": "Такое имя уже занято — выберите другое"}
    it["left"] -= 1; it["used"].append(name)
    if group:
        group["members"].append(name)
    px = QR_PX
    return 200, {"ok": True, "name": name, "uri": "vpn://" + "B" * 80, "uri_png": px, "conf": "[Interface]\nPrivateKey = test\n", "conf_png": px, "left": it["left"], "group": group["name"] if group else None}


def post(path, d):
    if path == "/api/invites/qr":
        link = d.get("link")
        if not (isinstance(link, str) and re.fullmatch(r"https?://[A-Za-z0-9.:-]+/i/[A-Za-z0-9_-]{22}", link)):
            return 400, {"ok": False, "error": "некорректные данные"}
        return 200, {"ok": True, "png": QR_PX}
    if path == "/api/invites":
        g, uses, days = d.get("group"), d.get("uses"), d.get("days")
        if not (isinstance(uses, int) and 1 <= uses <= 10 and isinstance(days, int) and 1 <= days <= 30 and (g is None or any(x["id"] == g for x in S["ugroups"]))):
            return 400, {"ok": False, "error": "некорректные данные"}
        token = hashlib.sha256(f"{len(S['invites'])}{time.time()}".encode()).hexdigest()[:22]
        S["invites"].append({"id": new_id("i", S["invites"])[:6], "token": token, "group": g, "left": uses, "total": uses, "expires_at": int(time.time()) + days * 86400, "created": int(time.time()), "used": []})
        return 200, {"ok": True, "id": S["invites"][-1]["id"], "token": token, "invites": invites_public()}
    if path == "/api/login":
        return 200, {"ok": True}
    if path == "/api/logout":
        return 200, {"ok": True}
    if path == "/api/clients":
        name = str(d.get("name", ""))
        if not NAME_RE.match(name):
            return 400, {"ok": False, "error": "имя: латиница, цифры, _ и -, до 32 символов"}
        if name in known():
            return 400, {"ok": False, "error": "пользователь с таким именем уже есть"}
        exp = {"1d": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "1h": 3600}.get(d.get("expires") or "")
        S["next_ip"] += 1
        c = {"name": name, "ip": f"172.16.17.{S['next_ip']}", "suspended": False, "expires_at": int(time.time()) + exp if exp else None, "endpoint": None,
             "handshake": 0, "rx": 0, "tx": 0, "exit": 1, "online": False, "down_bps": 0.0, "up_bps": 0.0}
        S["pending"].append((c, time.time() + 4))
        jadd(f"Создан пользователь {name}")
        return 200, {"ok": True, "result": {"ok": True}}
    if path == "/api/clients/exit":
        names, idx = d.get("names"), d.get("exit")
        if not (isinstance(names, list) and names and all(n in known() for n in names) and (idx is None or idx in S["weights"])):
            return 400, {"ok": False, "error": "нет такого выхода" if idx is not None and idx not in S["weights"] else "некорректные данные"}
        for c in S["clients"]:
            if c["name"] in names:
                c["pin"] = idx
                if idx is not None:
                    c["exit"] = idx
        return 200, {"ok": True, "names": names, "exit": idx}
    if path == "/api/exits/add":
        if not (isinstance(d.get("address"), str) and isinstance(d.get("name"), str)) or d.get("name") in ("exit1-nl", "exit2-nl"):
            return 400, {"ok": False, "error": "выход с именем " + str(d.get("name")) + " уже есть"}
        S["exit_job"] = {"id": "1", "kind": "add", "index": 4, "name": d["name"], "address": d["address"], "state": "running", "error": None, "started": int(time.time()), "finished": None,
                         "steps": [{"key": "login", "title": "Вход на сервер", "state": "done"}, {"key": "prepare", "title": "Проверка и установка пакетов", "state": "run"}, {"key": "link", "title": "Туннель", "state": "wait"}]}
        return 200, {"ok": True, "index": 4, "name": d["name"], "address": d["address"]}
    if path == "/api/exits/country":
        # where an exit is: a code, or "auto" — the agent finds it by the exit's address; the stand-in answers per exit
        index, country = d.get("index"), d.get("country")
        if index not in S["weights"]:
            return 400, {"ok": False, "error": "нет такого выхода"}
        if not (isinstance(country, str) and re.fullmatch(r"[a-z]{2}|auto", country)):
            return 400, {"ok": False, "error": "страна выхода: код ISO из двух строчных латинских букв или auto"}
        name = {1: "exit1-nl", 3: "exit2-nl"}[index]
        if country == "auto":
            # found by its address for exit2-nl; not found for exit1-nl, whose stored country then stays (as the agent does)
            found = {1: None, 3: "nl"}[index]
            if found is None:
                kept = S["countries"].get(str(index))
                said = f"оставлена прежняя ({kept.upper()})" if kept else "страны по-прежнему нет"
                jadd(f"Выход {name}: страну по адресу найти не удалось — {said}")
                return 200, {"ok": True, "already": True, "kept": True, "index": index, "name": name, "country": kept, "note": f"страна по адресу сервера не найдена — {said}"}
            S["countries"][str(index)] = found
            jadd(f"Выход {name}: страна {found.upper()} найдена по адресу")
            return 200, {"ok": True, "index": index, "name": name, "country": found}
        S["countries"][str(index)] = country
        jadd(f"Выход {name}: страна {country.upper()}")
        return 200, {"ok": True, "index": index, "name": name, "country": country}
    if path in ("/api/exits/rename", "/api/exits/remove", "/api/exits/update"):
        if d.get("index") not in S["weights"]:
            return 400, {"ok": False, "error": "нет такого выхода"}
        if path.endswith("/remove") and not d.get("force") and S.get("exit_confirm"):
            return 409, {"ok": False, "confirm": True, "error": "остальные выходы сейчас не работают: без этого у пользователей не будет зарубежного трафика"}
        return 200, {"ok": True, "index": d["index"]}
    if path == "/api/exits/weights":
        w, base = d.get("weights"), d.get("base")
        have = {str(k): v for k, v in S["weights"].items()}
        if not (isinstance(w, dict) and w and all(k in have and type(v) is int and 1 <= v <= 100 for k, v in w.items())) or (set(w) == set(have) and sum(w.values()) != 100):
            return 400, {"ok": False, "error": "доли должны давать в сумме 100%"}
        if base is not None and base != have:
            return 409, {"ok": False, "conflict": True, "error": "доли выходов уже изменены в другом окне — показаны новые", "weights": have}
        for k, v in w.items():
            S["weights"][int(k)] = v
        return 200, {"ok": True}
    if path in ("/api/clients/remove", "/api/clients/expire", "/api/clients/suspend"):
        names = d.get("names")
        if not (isinstance(names, list) and 1 <= len(names) <= 60 and all(isinstance(n, str) and NAME_RE.match(n) for n in names)):
            return 400, {"ok": False, "error": "некорректные данные"}
        if path.endswith("/remove"):
            gone = [n for n in names if n in known()]
            S["clients"] = [c for c in S["clients"] if c["name"] not in gone]
            S["pending"] = [p for p in S["pending"] if p[0]["name"] not in gone]
            for g in S["ugroups"] + S["spaces"]:
                g["members"] = [m for m in g["members"] if m not in gone]
            if gone:
                jadd("Удалён пользователь " + ", ".join(gone))
            left = [n for n in names if n not in gone]
            return (200, {"ok": True, "removed": gone}) if not left else (400, {"ok": False, "error": "не удалены: " + ", ".join(left), "removed": gone})
        missing = [n for n in names if n not in known()]
        if missing:
            return 400, {"ok": False, "error": "нет такого пользователя: " + ", ".join(missing)}
        if path.endswith("/expire"):
            exp = d.get("expires", "")
            secs = {"": None, "1d": 86400, "7d": 7 * 86400, "30d": 30 * 86400}.get(exp, "bad")
            if secs == "bad":
                return 400, {"ok": False, "error": "срок: например 12h, 7d, 4w"}
            for c in S["clients"]:
                if c["name"] in names:
                    c["expires_at"] = int(time.time()) + secs if secs else None
            jadd(("Срок снят: " if not secs else f"Срок {exp}: ") + ", ".join(names))
            return 200, {"ok": True, "names": names, "expires": exp or None}
        if not isinstance(d.get("on"), bool):
            return 400, {"ok": False, "error": "некорректные данные"}
        for c in S["clients"]:
            if c["name"] in names:
                c["suspended"] = d["on"]
        jadd(("Приостановлен: " if d["on"] else "Возобновлён: ") + ", ".join(names))
        return 200, {"ok": True, "mode": "on" if d["on"] else "off", "names": names, "paused": [c["name"] for c in S["clients"] if c["suspended"]]}
    if path == "/api/country":
        country = d.get("country")
        if not isinstance(country, str) or len(country) != 2 or not country.isascii() or not country.islower():
            return 400, {"ok": False, "error": "некорректный код страны"}
        if country != S.get("country", "ru"):
            S["country"] = country
            jadd("Домашняя страна: " + country.upper())
        return 200, {"ok": True}
    if path == "/api/mode":
        if d.get("mode") not in ("list", "geo"):
            return 400, {"ok": False, "error": "некорректный режим"}
        if S.get("mode_fail"):
            return 400, {"ok": False, "error": "не удалось применить — возвращён прежний режим"}
        if d["mode"] != S.get("mode", "list"):
            S["mode"] = d["mode"]
            jadd("Режим: " + ("через заграницу только по списку" if d["mode"] == "list" else "через заграницу всё зарубежное"))
        return 200, sites_payload()
    if path == "/api/ugroups":
        if d.get("base") != version(S["ugroups"]):
            return conflict(ugroups_payload(), "группы")
        raw, out, names, placed = d.get("groups"), [], set(), {}
        if not isinstance(raw, list) or len(raw) > 30:
            return 400, {"ok": False, "error": "некорректные данные"}
        old = {g["id"]: g for g in S["ugroups"]}
        for g in raw:
            name = " ".join(str(g.get("name", "")).split())[:40]
            if not name:
                return 400, {"ok": False, "error": "название группы: от 1 до 40 символов"}
            if name.lower() in names or name.lower() == "без группы":
                return 400, {"ok": False, "error": f"группа «{name}» уже есть"}
            names.add(name.lower())
            mine = []
            for m in g.get("members", []):
                if m in known() and m not in mine:
                    if m in placed:
                        return 400, {"ok": False, "error": f"пользователь {m} указан в двух группах: «{placed[m]}» и «{name}»"}
                    placed[m] = name
                    mine.append(m)
            gid = g.get("id") if g.get("id") in old and g.get("id") not in {x["id"] for x in out} else new_id("g", S["ugroups"] + out)
            was = old.get(g.get("id"), {})
            ex = g["exit"] if "exit" in g else was.get("exit")
            if ex not in (None, 1, 3):
                return 400, {"ok": False, "error": f"{name}: такого выхода нет"}
            flt = g["filter"] if "filter" in g else was.get("filter", "std")
            if flt not in ("std", "off", "kids", "pro", "proplus", "ultimate"):
                return 400, {"ok": False, "error": f"{name}: такого режима фильтрации нет"}
            group = {"id": gid, "name": name, "space": bool(g.get("space")), "exit": ex, "filter": flt}
            if flt == "kids":                                   # as the agent: sent, as stored, or the defaults for a group just switched
                lists = g["kids_lists"] if "kids_lists" in g else was.get("kids_lists", []) if was.get("filter") == "kids" else ["nsfw", "gambling", "bypass"]
                if not isinstance(lists, list) or any(x not in ("nsfw", "gambling", "bypass", "social") for x in lists):
                    return 400, {"ok": False, "error": f"{name}: таких списков детского режима нет"}
                group["kids_lists"] = [x for x in ("nsfw", "gambling", "bypass", "social") if x in lists]
            group["members"] = mine
            out.append(group)
        S["ugroups"] = out
        for c in S["clients"]:                                  # a pinned group moves its users, as the watchdog would
            g = next((x for x in out if c["name"] in x["members"]), None)
            if g and g["exit"] is not None:
                c["exit"] = g["exit"]
        jadd("Изменены группы")
        return 200, ugroups_payload()
    if path == "/api/spaces":
        if d.get("base") != version(S["spaces"]):
            return conflict(spaces_payload(), "пространства")
        out = []
        for s in d.get("spaces") or []:
            name = " ".join(str(s.get("name", "")).split())[:40]
            if not name:
                return 400, {"ok": False, "error": "название: от 1 до 40 символов"}
            ids = {x["id"] for x in S["spaces"]}
            out.append({"id": s.get("id") if s.get("id") in ids else new_id("s", S["spaces"] + out), "name": name, "on": bool(s.get("on")),
                        "members": [m for m in s.get("members", []) if m in known()]})
        S["spaces"] = out
        return 200, spaces_payload()
    if path == "/api/sites":
        if d.get("base") != version(S["sites"]):
            return conflict(sites_payload(), "сайты")
        old, out = {g["id"]: g for g in S["sites"]}, []
        for g in d.get("groups") or []:
            name = " ".join(str(g.get("name", "")).split())[:40]
            if not name:
                return 400, {"ok": False, "error": "название группы: от 1 до 40 символов"}
            was = old.get(g.get("id"))
            if g.get("url"):
                out.append(dict(was, name=name, on=bool(g.get("on"))) if was else
                           {"id": new_id("a", S["sites"] + out), "name": name, "on": bool(g.get("on")), "url": g["url"], "items": ["x.example", "y.example"], "updated": int(time.time()), "error": ""})
            else:
                out.append({"id": was["id"] if was else new_id("a", S["sites"] + out), "name": name, "on": bool(g.get("on")), "items": list(g.get("items", [])),
                            **({"auto": was["auto"]} if was and was.get("auto") else {}), **({"direct": True} if was and was.get("direct") else {})})
        lost = [g["name"] for g in S["sites"] if g.get("auto") and g["id"] not in {x["id"] for x in out}]
        if lost:
            return 400, {"ok": False, "error": f"группу «{lost[0]}» нельзя удалить: её сети обновляются сами. Её можно выключить."}
        routed = lambda gs: sorted(i for g in gs if g["on"] for i in g["items"])
        applied = routed(out) != routed(S["sites"])
        S["sites"] = out
        jadd("Изменены сайты")
        return 200, sites_payload(applied=applied)
    if path == "/api/rules/route":
        changes = d.get("changes") or []
        order = d.get("order")
        if not isinstance(changes, list) or any(not isinstance(c, dict) or c.get("route") not in ("exit", "direct", "block", "off", None) or (c.get("route") is None and c.get("groups") is None) for c in changes) or (not changes and order is None):
            return 400, {"ok": False, "error": "некорректные данные"}
        if d.get("base") and d["base"] != version(rules_list()):
            return 409, {"ok": False, "conflict": True, "error": "правила изменились в другом окне — показан новый список", **rules_payload()}
        before = (copy.deepcopy(S["sites"]), copy.deepcopy(S["block_groups"]), S.get("order"))
        out = []
        for change in changes:
            done = route_rule(change.get("rule"), change.get("route"), change.get("groups"))
            if isinstance(done, tuple):                 # one refused: none is kept
                S["sites"], S["block_groups"], S["order"] = before
                return done
            out.append(done)
        if order is not None:
            if sorted(order) != sorted(rule["id"] for rule in rules_list()):
                S["sites"], S["block_groups"], S["order"] = before
                return 400, {"ok": False, "error": "порядок правил устарел — обновите страницу"}
            S["order"] = list(order)
        said = [f"«{r['name']}» — " + {"exit": "за границу", "direct": "напрямую", "block": "блокируется", "off": "выключено"}[r["route"]] for r in out]
        jadd("Правила: " + "; ".join(said + (["изменён порядок"] if order is not None else [])))
        return 200, {"ok": True, "rules": out, "ordered": order is not None, "v": version(rules_list())}
    if path == "/api/sites/refresh":
        g = next((x for x in S["sites"] if x["id"] == d.get("id") and x.get("url")), None)
        if not g:
            return 400, {"ok": False, "error": "такого списка нет"}
        g["updated"] = int(time.time())
        return 200, sites_payload(applied=False)
    if path == "/api/block":
        if d.get("base") and d["base"] != version(S["block_groups"]):
            return 409, {"ok": False, "conflict": True, "error": "блокировки изменились в другом окне — показан новый список", **{k: v for k, v in block_payload().items() if k != "ok"}}
        if isinstance(d.get("groups"), list):
            S["block_groups"] = [{"id": g.get("id") or new_id("b", S["block_groups"]), "name": str(g.get("name", ""))[:40], "on": bool(g.get("on", True)), "items": list(g.get("items", []))} for g in d["groups"]]
        if isinstance(d.get("safebrowsing"), bool):
            S["safebrowsing"] = d["safebrowsing"]
        return 200, block_payload()
    if path == "/api/block/lists":
        for l in d.get("lists") or []:
            cur = next((x for x in S["block_lists"] if x["id"] == l.get("id")), None)
            if cur:
                cur["on"] = bool(l.get("on", cur["on"]))
        if d.get("add"):
            S["block_lists"].append({"id": max(x["id"] for x in S["block_lists"]) + 1, "title": d["add"].get("name", "Список"), "name": d["add"].get("name", "Список"),
                                     "url": d["add"].get("url", ""), "custom": True, "on": True, "rules": 1234, "updated": "2026-11-12T10:00:00Z", "day": 0})
        if d.get("remove") is not None:
            S["block_lists"] = [x for x in S["block_lists"] if x["id"] != d["remove"] or not x["custom"]]
        return 200, block_payload()
    if path == "/api/dns":
        name = str(d.get("name", ""))
        if not NAME_RE.match(name):
            return 400, {"ok": False, "error": "имя: латиница, цифры, _ и -, до 32 символов"}
        if any(x["name"] == name for x in S["dns_devices"]):
            return 400, {"ok": False, "error": "такое имя уже есть"}
        S["dns_devices"].append({"name": name, "code": hashlib.sha256(name.encode()).hexdigest()[:12], "created": int(time.time()), "last": 0, "count": 0})
        return 200, {"ok": True, "devices": copy.deepcopy(S["dns_devices"])}
    return 404, {"ok": False, "error": "not found"}


def rules_list():
    """The rules as one list, as the agent keeps them: the groups of sites, then the block groups — or the order
    stored by the canvas — each with where it leads."""
    rules = [dict(g, route="direct" if g.get("direct") else "exit") for g in S["sites"]] + [dict(g, route="block") for g in S["block_groups"]]
    for rule in rules:
        rule.pop("direct", None)
    order = S.get("order")
    if order:
        place = {rule_id: index for index, rule_id in enumerate(order)}
        rules.sort(key=lambda rule: place.get(rule["id"], len(place)))
    return rules


def covers(entry, host):
    return host == entry or host.endswith("." + entry)


def covered_of(rules):
    """What a rule above covers (agent/rules/compile.py, for names without patterns)."""
    routing, blocking, out = [], [], {}
    for rule in rules:
        if not rule.get("on") or rule.get("url"):
            continue
        names = [item for item in rule["items"] if "/" not in item]
        above = routing if rule["route"] != "block" else blocking
        for name in names:
            hit = next((by for entry, by in (above if rule["route"] != "block" else routing + blocking) if covers(entry, name)), None)
            if hit:
                out.setdefault(rule["id"], {})[name] = hit
        (routing if rule["route"] != "block" else blocking).extend((name, rule["name"]) for name in names)
    return out


def rules_payload():
    rules = rules_list()
    return {"rules": rules, "covered": covered_of(rules), "v": version(rules)}


def route_rule(rule, route, groups=None):
    """One rule sent where it should lead and for whom, as the agent does it (rules/service.py): the same rule, the same id. → its answer, or (400, refusal)."""
    site = next((g for g in S["sites"] if g["id"] == rule), None)
    blocked = next((g for g in S["block_groups"] if g["id"] == rule), None)
    held = site or blocked
    if held is None:
        return 400, {"ok": False, "error": "такого правила больше нет — обновите страницу"}
    names = {g["id"]: g["name"] for g in S["ugroups"] if g.get("id")}
    if groups is not None:
        if any(group not in names for group in groups):
            return 400, {"ok": False, "error": "такой группы людей больше нет — обновите страницу"}
        if groups:
            held["groups"] = sorted(set(groups))
        else:
            held.pop("groups", None)
    if route is None:
        route = ("off" if not held.get("on") else ("block" if blocked is not None else ("direct" if held.get("direct") else "exit")))
    if held.get("groups") and route in ("exit", "direct"):
        return 400, {"ok": False, "error": f"«{held['name']}»: для отдельных групп людей пока можно только блокировать"}
    if route in ("direct", "block") and (held.get("url") or held.get("auto") or any("/" in item for item in held["items"])):
        return 400, {"ok": False, "error": f"«{held['name']}»: напрямую и в блокировку направляются только сайты"}
    if route == "off":
        held["on"] = False
    elif route == "block" and site is not None:
        S["block_groups"].append({"id": site["id"], "name": site["name"], "on": True, "items": list(site["items"]), **({"groups": site["groups"]} if site.get("groups") else {})})
        S["sites"] = [g for g in S["sites"] if g is not site]
    elif route in ("exit", "direct") and blocked is not None:
        S["sites"].append({"id": blocked["id"], "name": blocked["name"], "on": True, "items": list(blocked["items"]), **({"direct": True} if route == "direct" else {})})
        S["block_groups"] = [g for g in S["block_groups"] if g is not blocked]
    else:
        held["on"] = True
        if site is not None:
            if route == "direct":
                site["direct"] = True
            else:
                site.pop("direct", None)
    current = next((g for g in S["sites"] + S["block_groups"] if g["id"] == rule), held)
    return {"rule": rule, "name": held["name"], "route": route, "groups": [names.get(g, g) for g in current.get("groups", [])]}


def get(path, q, names=()):
    if path == "/api/exits":
        return 200, {"ok": True, "nodes": {"1": {"address": "198.51.100.38", "managed": True}, "3": {"address": "198.51.100.163", "managed": False}},
                     "job": S.get("exit_job"), "busy": False, "key": "ssh-ed25519 AAAA vpn-entry-exits", "countries": dict(S["countries"]), "stored": dict(S["countries"])}
    if path == "/api/state":
        return 200, state()
    if path == "/api/clients":
        state()                                                  # the users created a moment ago come in, as the agent's next look would bring them
        return clients_page(q, names)
    if path == "/api/ugroups":
        return 200, ugroups_payload()
    if path == "/api/sites":
        return 200, sites_payload()
    if path == "/api/spaces":
        return 200, spaces_payload()
    if path == "/api/block":
        return 200, block_payload()
    if path == "/api/rules":
        return 200, {"ok": True, **rules_payload()}
    if path == "/api/dns":
        return 200, {"ok": True, "devices": copy.deepcopy(S["dns_devices"])}
    if path == "/api/invites":
        return 200, {"ok": True, "limit": 40, "invites": invites_public()}
    if path == "/api/traffic":
        return (500, {"ok": False, "error": "агент не ответил (проверочный отказ)"}) if S["traffic_fail"] else (200, traffic())
    if path == "/api/journal":
        return 200, {"ok": True, "items": list(reversed(S["journal"]))[:300]}
    if path == "/api/diag":
        c = next((x for x in S["clients"] if x["name"] == q.get("name")), None)
        if not c:
            return 400, {"ok": False, "error": "такого пользователя нет"}
        items = [{"state": "bad", "text": "Приостановлен: ключ на месте, но трафик не пропускается"}] if c["suspended"] else []
        items.append({"state": "ok", "text": "На связи, последняя связь 20 с назад"} if c["online"] else
                     {"state": "bad" if not c["handshake"] else "warn", "text": "Ни разу не подключался" if not c["handshake"] else "Не на связи 1 ч: VPN на устройстве выключен или у него нет интернета"})
        items.append({"state": "ok", "text": "Выход exit1-nl работает"})
        return 200, {"ok": True, "name": c["name"], "items": items}
    if path in ("/api/check", "/api/probe"):
        host = q.get("host", "")
        if not HOST_RE.match(host):
            return 400, {"ok": False, "error": "некорректное имя"}
        if path == "/api/probe":
            bad = "blocked" in host
            return 200, {"ok": True, "host": host, "ways": [
                {"via": "direct", "name": "Напрямую", "state": "refused" if bad else "ok", "code": 403 if bad else 200, "ms": 120, "text": "сайт отказывает (403)" if bad else "открывается"},
                {"via": "exit", "name": "exit1-nl", "state": "ok", "code": 200, "ms": 250, "text": "открывается"},
                {"via": "exit", "name": "exit2-nl", "state": "ok", "code": 200, "ms": 310, "text": "открывается"}]}
        hit = next(((g, i) for g in S["sites"] if g["on"] and not g.get("url") for i in g["items"] if host == i or host.endswith("." + i)), None)
        via = {"via": "exit", "group": hit[0]["name"], "rule": hit[1]} if hit else {"via": "direct"}
        blk = next(({"kind": "own", "id": None, "gid": g["id"], "list": g["name"], "rule": f"||{i}^"} for g in S["block_groups"] for i in g["items"] if host == i or host.endswith("." + i)), None)
        return 200, {"ok": True, "host": host, "vpn": via, "dns": via, "blocked": blk}
    m = re.fullmatch(r"/api/clients/([^/]+)/qr", path)
    if m:
        if m.group(1) not in known():
            return 404, {"ok": False, "error": "такого пользователя нет"}
        px = QR_PX
        return 200, {"ok": True, "name": m.group(1), "uri": "vpn://" + "A" * 80, "uri_png": px, "conf": "[Interface]\nPrivateKey = test\n", "conf_png": px}
    return 404, {"ok": False, "error": "not found"}


def delete(path):
    m = re.fullmatch(r"/api/clients/([^/]+)", path)
    if m:
        return post("/api/clients/remove", {"names": [m.group(1)]})
    m = re.fullmatch(r"/api/invites/([a-z0-9]+)", path)
    if m:
        S["invites"] = [i for i in S["invites"] if i["id"] != m.group(1)]
        return 200, {"ok": True, "invites": invites_public()}
    m = re.fullmatch(r"/api/dns/([^/]+)", path)
    if m:
        S["dns_devices"] = [x for x in S["dns_devices"] if x["name"] != m.group(1)]
        return 200, {"ok": True, "devices": copy.deepcopy(S["dns_devices"])}
    return 404, {"ok": False, "error": "not found"}



def _png(side):           # a white square of the size of a real code: 121 modules, 4 px each
    import struct, zlib
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    row = b"\0" + b"\xff" * ((side + 7) // 8)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 1, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * side)) + chunk(b"IEND", b"")


QR_PX = __import__("base64").b64encode(_png(484)).decode()



class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store")
        if getattr(self, "csp", None):
            self.send_header("Content-Security-Policy", self.csp); self.csp = None
        self.end_headers(); self.wfile.write(b)

    def handle_api(self, method):
        global S
        path, _, qs = self.path.partition("?")
        q = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        q = {k: __import__("urllib.parse").parse.unquote_plus(v) for k, v in q.items()}
        names = __import__("urllib.parse").parse.parse_qs(qs).get("names", [])
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            body = {}
        with LOCK:
            if path == "/__ctl":
                if body.get("reset"):
                    S = fresh()
                for k, v in (body.get("set") or {}).items():
                    S[k] = v
                if "fail" in body:
                    S["fail"] = body["fail"]
                for number in range(int(body.get("many") or 0)):
                    S["clients"].append({"name": f"bulk_{number:04d}", "ip": f"172.17.{number // 250}.{number % 250 + 2}", "suspended": False, "expires_at": None,
                                         "endpoint": None, "handshake": 0, "rx": 0, "tx": 0, "exit": None, "pin": None, "online": False, "down_bps": 0.0, "up_bps": 0.0})
                return self.send(200, {"ok": True})
            if path == "/__log":
                return self.send(200, S["log"])
            if path.startswith("/i/"):
                S["log"].append({"method": method, "path": path, "query": q, "body": body})
                if method == "POST" and self.headers.get("X-Invite") != "1":
                    return self.send(429, {"ok": False, "error": "слишком много запросов"})
                code, res = invite_routes(method, path, body)
                self.csp = INVITE_CSP if isinstance(res, bytes) else None
                return self.send(code, res, "text/html" if isinstance(res, bytes) else "application/json")
            if path.startswith("/api/"):
                S["log"].append({"method": method, "path": path, "query": q, "body": body})
                if S["fail"] == path and method != "GET":
                    S["fail"] = None
                    return self.send(400, {"ok": False, "error": "сервер отказал (проверочный отказ)"})
                code, res = get(path, q, names) if method == "GET" else delete(path) if method == "DELETE" else post(path, body)
                return self.send(code, res)
        if method == "GET" and path in ("/", "/index.html"):
            self.csp = CSP
            return self.send(200, open(WEB + "/index.html", "rb").read().replace(b"__UI_ID__", b"t"), "text/html")
        if method == "GET" and path.startswith("/_next/"):   # the files of the build under /_next/, as the panel server hands them out
            full = os.path.realpath(WEB + path)
            if full.startswith(WEB + os.sep) and os.path.isfile(full):
                return self.send(200, open(full, "rb").read(), TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send(404, {"ok": False, "error": "not found"})

    def do_GET(self):
        self.handle_api("GET")

    def do_POST(self):
        self.handle_api("POST")

    def do_DELETE(self):
        self.handle_api("DELETE")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
