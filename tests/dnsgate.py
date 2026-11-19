#!/usr/bin/env python3
"""The DNS gate (src/awgscale/dnsgate), run for real against a stand-in for the
resolver behind it (AdGuard Home): a small DNS server on loopback, over UDP and
TCP, that remembers what it was asked. Nothing live is touched.

    uv run python tests/dnsgate.py

Where the gate's answer is determined by the request alone, what a device gets
— status, content type, caching, body — must be byte for byte the answer in
tests/golden/dnsgate.json (the answers the gate has always given). The rest
checks what it does over time: the list and the codes are read the moment their
files change, admissions are kept and forgotten, the proxy passes on only what
it may.

One line per check, the last line is "N of M checks passed".
"""
import asyncio
import base64
import datetime
import hashlib
import http.client
import json
import os
import random
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="dnsgate-")
GOLDEN = json.load(open(os.path.join(HERE, "golden", "dnsgate.json")))
PROXY_IP = "203.0.113.9"
OWN_NAME = "vpn.example"
STATIC_IP = "198.51.100.200"
SITE_IP = "93.184.216.34"            # a global address: the proxy may connect to it
PASS = FAIL = 0


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:600])


def free_ports(n):
    """n different ports, free for both TCP and UDP: all picked before any is given back.
    Below the kernel's range for outgoing connections (32768 and up), so none of them can
    take a port between this pick and the gate's start; tried on every address, as the
    gate binds them."""
    held, ports = [], []
    pick = random.SystemRandom()
    while len(ports) < n:
        port = pick.randint(20000, 32000)
        if port in ports:
            continue
        t = socket.socket()
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            t.bind(("0.0.0.0", port))
            u.bind(("0.0.0.0", port))
        except OSError:
            t.close()
            u.close()
            continue
        held += [t, u]
        ports.append(port)
    for s in held:
        s.close()
    return ports


def wait(cond, seconds=10):
    until = time.time() + seconds
    while time.time() < until:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ── DNS messages, written by hand ────────────────────────────────────────────
T_A, T_AAAA, T_TXT, T_SVCB, T_HTTPS = 1, 28, 16, 64, 65


def qname(name):
    return b"".join(bytes([len(label)]) + label.encode() for label in name.split(".") if label) + b"\0"


def query(name, qtype=T_A, qid=0x1234, flags=0x0100, qdcount=1, qclass=1):
    return struct.pack("!HHHHHH", qid, flags, qdcount, 0, 0, 0) + qname(name) + struct.pack("!HH", qtype, qclass)


def read_question(msg):
    pos, labels = 12, []
    while msg[pos]:
        labels.append(msg[pos + 1:pos + 1 + msg[pos]].decode("ascii", "replace").lower())
        pos += 1 + msg[pos]
    return ".".join(labels), struct.unpack("!H", msg[pos + 1:pos + 3])[0], pos + 5


def a_record(ip, ttl=100):
    return b"\xc0\x0c" + struct.pack("!HHIH", T_A, 1, ttl, 4) + socket.inet_aton(ip)


# ── the stand-in for the resolver behind the gate ────────────────────────────
ASKED = []                 # (name, type, "udp" | "tcp")
SITE_ANSWERS = {           # name → A records the resolver gives for it
    "site.example": [(SITE_IP, 100), ("0.0.0.0", 100), ("10.1.2.3", 100), (PROXY_IP, 100)],
    "blocked.example": [("0.0.0.0", 100)],
}


def resolver_answer(msg, over):
    try:
        name, qtype, end = read_question(msg)
    except (IndexError, struct.error):
        return None
    ASKED.append((name, qtype, over))
    if name == "dead.example":
        return None
    records = b"".join(a_record(ip, ttl) for ip, ttl in SITE_ANSWERS.get(name, [("198.51.100.7", 300)])) if qtype == T_A else b""
    count = len(SITE_ANSWERS.get(name, [1])) if qtype == T_A else 0
    flags = 0x8180
    if name == "big.example" and over == "udp":
        flags |= 0x0200                         # truncated: ask again over TCP
        records, count = b"", 0
    if name == "big.example" and over == "tcp":
        records, count = b"".join(a_record(f"198.51.100.{i}") for i in range(1, 41)), 40
    return msg[:2] + struct.pack("!HHHHH", flags, 1, count, 0, 0) + msg[12:end] + records


class UdpResolver(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        reply = resolver_answer(data, "udp")
        if reply:
            sock.sendto(reply, self.client_address)


class TcpResolver(socketserver.BaseRequestHandler):
    def handle(self):
        head = self.request.recv(2)
        if len(head) < 2:
            return
        data = self.request.recv(struct.unpack("!H", head)[0])
        reply = resolver_answer(data, "tcp")
        if reply:
            self.request.sendall(struct.pack("!H", len(reply)) + reply)


socketserver.ThreadingUDPServer.allow_reuse_address = True
socketserver.ThreadingTCPServer.allow_reuse_address = True
UPSTREAM_PORT, NEW_PORT, NEW_SNI = free_ports(3)
for server in (socketserver.ThreadingUDPServer(("127.0.0.1", UPSTREAM_PORT), UdpResolver), socketserver.ThreadingTCPServer(("127.0.0.1", UPSTREAM_PORT), TcpResolver)):
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

# ── the files the agent writes ───────────────────────────────────────────────
LIST_FILE = os.path.join(TMP, "lists", "domains-exit.txt")
TOKENS_FILE = os.path.join(TMP, "lists", "dns-tokens.json")
os.makedirs(os.path.join(TMP, "lists"))
LIST = """# the shared list
example.com
*.wild.example
rr*.video.example
*-pa.api.example
site.example
blocked.example
dead.example
Mixed.Case.Example.
vpn.example
10.20.0.0/16
192.0.2.7
"""


def replace(path, text):
    """The way the agent writes: beside, then moved into place."""
    with open(path + ".new", "w") as f:
        f.write(text)
    os.replace(path + ".new", path)


def write_tokens(codes):
    replace(TOKENS_FILE, json.dumps({"tokens": {code: {"name": name, "created": 1} for code, name in codes.items()}}))


replace(LIST_FILE, LIST)
write_tokens({"code-tv": "tv_kitchen", "code-pad": "pad"})


# ── the gate, as a process ───────────────────────────────────────────────────
def gate_env(port, sni_port, data):
    os.makedirs(data, exist_ok=True)
    env = {"PROXY_IP": PROXY_IP, "UPSTREAM": f"127.0.0.1:{UPSTREAM_PORT}", "LIST_FILE": LIST_FILE, "TOKENS_FILE": TOKENS_FILE, "DATA_DIR": data,
           "STATIC_ALLOW": STATIC_IP, "ALLOW_HOURS": "6", "OWN_NAME": "VPN.example", "PORT": str(port), "SNI_PORT": str(sni_port)}
    return dict(os.environ, PYTHONUNBUFFERED="1", **{"DNSGATE_" + k: v for k, v in env.items()})


NEW_DATA = os.path.join(TMP, "new-data")
NEW = None


def start_new(**more):
    """The gate, its settings named DNSGATE_<NAME> in the environment."""
    global NEW
    NEW = subprocess.Popen([sys.executable, "-m", "awgscale.dnsgate"], env=dict(gate_env(NEW_PORT, NEW_SNI, NEW_DATA), **{"DNSGATE_SAVE_SECONDS": "1", **more}),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return wait(lambda: listens(NEW_PORT) and listens(NEW_SNI), 20)


def listens(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def ask(port, method, path, body=None, ip="203.0.113.50", headers=None):
    """→ (status, content type, cache-control, body) — everything a device sees of an answer."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.request(method, path, body=body, headers={"X-Real-Ip": ip, **(headers or {})})
    r = c.getresponse()
    out = (r.status, r.getheader("Content-Type"), r.getheader("Cache-Control"), r.read())
    c.close()
    return out


def b64(msg):
    return base64.urlsafe_b64encode(msg).decode().rstrip("=")


def doh(port, msg, code="code-tv", ip="203.0.113.50", post=False):
    path = "/dns-query" + ("/" + code if code else "")
    if post:
        return ask(port, "POST", path, msg, ip, {"Content-Type": "application/dns-message"})
    return ask(port, "GET", path + "?dns=" + b64(msg), ip=ip)


def answers(reply):
    """The A records of a reply, as text."""
    name, _, pos = read_question(reply)
    out = []
    for _ in range(struct.unpack("!H", reply[6:8])[0]):
        rtype, _, _, rdlen = struct.unpack("!HHIH", reply[pos + 2:pos + 12])
        if rtype == T_A:
            out.append(socket.inet_ntoa(reply[pos + 12:pos + 16]))
        pos += 12 + rdlen
    return out


# ── a TLS ClientHello and the PROXY header Traefik puts before it ────────────
def client_hello(name):
    sni = name.encode()
    ext = struct.pack("!HH", 0xFF01, 1) + b"\0" + struct.pack("!HHHBH", 0, len(sni) + 5, len(sni) + 3, 0, len(sni)) + sni
    body = b"\x03\x03" + bytes(32) + b"\x00" + struct.pack("!H", 2) + b"\x13\x01" + b"\x01\x00" + struct.pack("!H", len(ext)) + ext
    shake = b"\x01" + struct.pack("!I", len(body))[1:] + body
    return b"\x16\x03\x01" + struct.pack("!H", len(shake)) + shake


def proxy_v1(ip):
    return f"PROXY TCP4 {ip} 192.0.2.1 50000 443\r\n".encode()


def proxy_v2(ip, command=1, family=0x11):
    body = socket.inet_aton(ip) + socket.inet_aton("192.0.2.1") + struct.pack("!HH", 50000, 443)
    return b"\r\n\r\n\x00\r\nQUIT\n" + struct.pack("!BBH", 0x20 | command, family, len(body)) + body


def knock(sni_port, payload, name=None):
    """Sends `payload` to a proxy and waits for it to end the connection. → did the proxy ask the resolver for `name` meanwhile"""
    before = len(ASKED)
    with socket.create_connection(("127.0.0.1", sni_port), timeout=5) as s:
        s.sendall(payload)
        s.settimeout(12)
        try:
            while s.recv(4096):
                pass
        except OSError:
            pass
    time.sleep(0.2)
    return any(a[0] == name for a in ASKED[before:])


# ── the checks ───────────────────────────────────────────────────────────────
def t_same_answers():
    print("\n— ответы, которые шлюз давал всегда")
    big = bytes(4097)
    cases = [
        ("listed A → the proxy's address", lambda p: doh(p, query("example.com"))),
        ("subdomain of a listed domain", lambda p: doh(p, query("a.b.example.com"))),
        ("upper case and a trailing dot in the list", lambda p: doh(p, query("x.MIXED.case.example"))),
        ("*.domain entry", lambda p: doh(p, query("deep.wild.example"))),
        ("glob inside a label", lambda p: doh(p, query("rr5---sn.video.example"))),
        ("glob at the start of a label", lambda p: doh(p, query("maps-pa.api.example"))),
        ("glob that does not match", lambda p: doh(p, query("video.example"))),
        ("listed AAAA → empty", lambda p: doh(p, query("example.com", T_AAAA))),
        ("listed HTTPS → empty", lambda p: doh(p, query("example.com", T_HTTPS))),
        ("listed SVCB → empty", lambda p: doh(p, query("example.com", T_SVCB))),
        ("listed TXT → the resolver's answer", lambda p: doh(p, query("example.com", T_TXT))),
        ("recursion not desired is copied", lambda p: doh(p, query("example.com", flags=0))),
        ("not listed → the resolver's answer", lambda p: doh(p, query("other.example"))),
        ("an address line of the list is not a name", lambda p: doh(p, query("7.2.0.192"))),
        ("truncated → asked again over TCP", lambda p: doh(p, query("big.example"))),
        ("two questions → forwarded", lambda p: doh(p, query("example.com", qdcount=2))),
        ("a response, not a query → forwarded", lambda p: doh(p, query("example.com", flags=0x8100))),
        ("not the internet class → forwarded", lambda p: doh(p, query("example.com", qclass=3))),
        ("POST", lambda p: doh(p, query("example.com"), post=True)),
        ("POST, not listed", lambda p: doh(p, query("other.example"), post=True)),
        ("no code", lambda p: doh(p, query("example.com"), code=None)),
        ("unknown code", lambda p: doh(p, query("example.com"), code="nope")),
        ("no code, an address that needs none", lambda p: doh(p, query("example.com"), code=None, ip=STATIC_IP)),
        ("unknown code, an address that needs none", lambda p: doh(p, query("example.com"), code="nope", ip=STATIC_IP)),
        ("trailing slash after the code", lambda p: ask(p, "GET", "/dns-query/code-tv/?dns=" + b64(query("example.com")))),
        ("trailing slash, no code", lambda p: ask(p, "GET", "/dns-query/?dns=" + b64(query("example.com")), ip=STATIC_IP)),
        ("one part too many", lambda p: ask(p, "GET", "/dns-query/code-tv/more?dns=" + b64(query("example.com")))),
        ("another path", lambda p: ask(p, "GET", "/other")),
        ("the root", lambda p: ask(p, "GET", "/", ip=STATIC_IP)),
        ("no dns parameter", lambda p: ask(p, "GET", "/dns-query/code-tv")),
        ("empty dns parameter", lambda p: ask(p, "GET", "/dns-query/code-tv?dns=")),
        ("dns parameter that is not base64", lambda p: ask(p, "GET", "/dns-query/code-tv?dns=a")),
        ("no dns parameter, not admitted: 404 comes first", lambda p: ask(p, "GET", "/dns-query/nope")),
        ("POST without a body", lambda p: ask(p, "POST", "/dns-query/code-tv", b"")),
        ("POST longer than a DNS message", lambda p: ask(p, "POST", "/dns-query/code-tv", big)),
        ("POST, not admitted", lambda p: ask(p, "POST", "/dns-query/nope", query("example.com"))),
        ("the resolver does not answer", lambda p: doh(p, query("dead.example", T_TXT))),
    ]
    for what, request in cases:
        status, ctype, cache, body = request(NEW_PORT)
        new = [status, ctype, cache, base64.b64encode(body).decode()]
        ok(new == GOLDEN["answers"][what], what + f" ({status})", {"expected": GOLDEN["answers"][what], "new": new})
    status, ctype, cache, body = doh(NEW_PORT, query("example.com"))
    ok((status, ctype, cache) == (200, "application/dns-message", "max-age=60") and answers(body) == [PROXY_IP] and body[:2] == b"\x12\x34", "ответ устройству: 200, application/dns-message, max-age=60, адрес прокси, тот же номер запроса", (status, ctype, cache, body))
    status, ctype, cache, body = doh(NEW_PORT, query("big.example"))
    ok(len(answers(body)) == 40 and ("big.example", T_A, "tcp") in ASKED, "обрезанный ответ переспрошен по TCP и отдан целиком", len(answers(body)))
    ok(doh(NEW_PORT, query("nope.example"), code=None)[:2] == (404, "text/plain") and doh(NEW_PORT, query("dead.example", T_TXT))[0] == 502, "без кода — 404 text/plain; блокировщик молчит — 502")
    ok(ask(NEW_PORT, "PUT", "/dns-query/code-tv")[0] == 405 and ask(NEW_PORT, "GET", "/docs", ip=STATIC_IP)[0] == 404, "чужой метод — 405; описания API наружу нет")


def t_files():
    print("\n— список и коды читаются в момент изменения")
    ok(answers(doh(NEW_PORT, query("fresh.example"))[3]) == ["198.51.100.7"], "имени нет в списке — отвечает блокировщик")
    replace(LIST_FILE, LIST + "fresh.example\n")
    ok(wait(lambda: answers(doh(NEW_PORT, query("fresh.example"))[3]) == [PROXY_IP], 2), "список заменён — имя сразу идёт через прокси (без опроса: до 2 с)")
    write_tokens({"code-tv": "tv_kitchen", "code-pad": "pad", "code-new": "phone"})
    ok(wait(lambda: doh(NEW_PORT, query("example.com"), code="code-new")[0] == 200, 2), "код добавлен — устройство сразу принято")
    replace(TOKENS_FILE, "{not json")
    time.sleep(0.5)
    ok(doh(NEW_PORT, query("example.com"), code="code-new")[0] == 200, "файл кодов испорчен — действуют прежние коды")
    os.remove(LIST_FILE)
    time.sleep(0.5)
    ok(answers(doh(NEW_PORT, query("fresh.example"))[3]) == [PROXY_IP], "файл списка исчез — действует прежний список")
    replace(LIST_FILE, LIST)
    write_tokens({"code-tv": "tv_kitchen", "code-pad": "pad"})
    ok(wait(lambda: doh(NEW_PORT, query("example.com"), code="code-new")[0] == 404 and answers(doh(NEW_PORT, query("fresh.example"))[3]) == ["198.51.100.7"], 2), "код отозван, имя убрано — действует сразу")


def t_proxy():
    print("\n— прокси: кого и куда пускает")
    doh(NEW_PORT, query("example.com"), code="code-pad", ip="203.0.113.77")       # this address has shown a valid code
    hello = client_hello("site.example")
    cases = [
        ("принятый адрес, имя из списка", proxy_v1("203.0.113.77") + hello, "site.example", True),
        ("заголовок PROXY v2", proxy_v2("203.0.113.77") + client_hello("www.site.example"), "www.site.example", True),
        ("адрес, которому код не нужен", proxy_v1(STATIC_IP) + client_hello("a.site.example"), "a.site.example", True),
        ("адрес, который код не показывал", proxy_v1("203.0.113.78") + hello, "site.example", False),
        ("имя не из списка", proxy_v1("203.0.113.77") + client_hello("other.example"), "other.example", False),
        ("собственное имя сервера", proxy_v1("203.0.113.77") + client_hello("vpn.example"), "vpn.example", False),
        ("PROXY v2: проверка балансировщика, а не соединение", proxy_v2("203.0.113.77", command=0) + client_hello("b.site.example"), "b.site.example", False),
        ("PROXY v2: не TCP поверх IPv4", proxy_v2("203.0.113.77", family=0x21) + client_hello("c.site.example"), "c.site.example", False),
        ("PROXY v1 не TCP4", b"PROXY TCP6 ::1 ::1 1 2\r\n" + client_hello("d.site.example"), "d.site.example", False),
        ("без заголовка PROXY", client_hello("e.site.example"), "e.site.example", False),
        ("не TLS", proxy_v1("203.0.113.77") + b"GET / HTTP/1.1\r\nHost: f.site.example\r\n\r\n", "f.site.example", False),
        ("TLS без имени сервера", proxy_v1("203.0.113.77") + b"\x16\x03\x01\x00\x05\x01\x00\x00\x01\x00", "", False),
    ]
    for what, payload, name, expected in cases:
        new = knock(NEW_SNI, payload, name)
        ok(new == expected, what + (": прокси узнаёт настоящий адрес сайта" if expected else ": соединение закрыто, блокировщик не спрошен"), new)
    ok(not knock(NEW_SNI, proxy_v1("203.0.113.77") + hello, "site.example"), "адрес сайта помнится: за вторым соединением блокировщик не спрашивается")
    ok(not knock(NEW_SNI, proxy_v1("203.0.113.77") + client_hello("blocked.example"), "none"), "у сайта только заблокированный адрес (0.0.0.0) — соединение закрыто")


def t_state():
    print("\n— приём адресов: между перезапусками и после отзыва кода")
    doh(NEW_PORT, query("example.com"), code="code-tv", ip="203.0.113.60")
    doh(NEW_PORT, query("example.com"), code="code-tv", ip="203.0.113.61")
    path = os.path.join(NEW_DATA, "state.json")
    time.sleep(1.6)
    state = json.load(open(path))
    tv = state["seen"]["tv_kitchen"]
    ok(tv["ip"] == "203.0.113.61" and isinstance(tv["last"], int) and abs(tv["last"] - time.time()) < 10 and tv["queries"] >= 2, "файл состояния: последнее обращение устройства — адрес, время числом, счётчик", tv)
    until = state["allowed"]["203.0.113.60"]
    ok(isinstance(until, float) and abs(until - time.time() - 6 * 3600) < 15 and isinstance(state["saved"], int) and "203.0.113.61" in state["allowed"], "…и до какого времени прокси принимает каждый адрес (6 часов от обращения)", state["allowed"])
    ok(STATIC_IP not in state["allowed"] and set(state["seen"]) == {"tv_kitchen", "pad"}, "адрес без кода в файл не попадает; устройства — только с действующими кодами", state)
    NEW.terminate()
    NEW.wait(10)
    ok(start_new() and knock(NEW_SNI, proxy_v1("203.0.113.60") + client_hello("g.site.example"), "g.site.example"), "после перезапуска адрес по-прежнему принят")
    write_tokens({"code-pad": "pad"})
    time.sleep(2.2)
    state = json.load(open(path))
    ok("tv_kitchen" not in state["seen"] and "203.0.113.61" not in state["allowed"], "код отозван — устройство забыто вместе с приёмом его последнего адреса", state)
    ok(not knock(NEW_SNI, proxy_v1("203.0.113.61") + client_hello("h.site.example"), "h.site.example"), "…и прокси этот адрес больше не пускает")

    def shape(st):
        return (sorted(st), sorted({type(v).__name__ for v in st["allowed"].values()}), sorted({k: type(v).__name__ for d in st["seen"].values() for k, v in d.items()}.items()))
    expected = (["allowed", "saved", "seen"], ["float"], [("ip", "str"), ("last", "int"), ("queries", "int")])
    ok(shape(json.load(open(path))) == expected, "файл состояния всё того же вида: агент читает его как раньше", shape(json.load(open(path))))
    write_tokens({"code-tv": "tv_kitchen", "code-pad": "pad"})


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def t_same_functions():
    print("\n— разбор сообщений: на тысячах сообщений то же, что всегда (tests/golden/dnsgate.json)")
    from ipaddress import IPv4Address

    from awgscale.dnsgate import tls, wire
    from awgscale.dnsgate.sites import SiteRules
    rng = random.Random(7)

    def mutated(sample):
        data = bytearray(sample)
        for _ in range(rng.randint(1, 4)):
            choice = rng.random()
            if choice < 0.5 and data:
                data[rng.randrange(len(data))] = rng.randrange(256)
            elif choice < 0.75 and data:
                del data[rng.randrange(len(data)):]
            else:
                data += bytes(rng.randrange(256) for _ in range(rng.randint(1, 8)))
        return bytes(data)

    def new_question(msg):
        q = wire.parse_question(msg)
        return None if q is None else (q.name, q.record_type, q.end)
    queries = [query(n, t, flags=f, qdcount=c, qclass=k) for n in ("example.com", "a.b.c.example", "x", "ÿ.example") for t in (T_A, T_AAAA, T_HTTPS, T_TXT) for f in (0x0100, 0, 0x8100, 0x0800) for c in (1, 2) for k in (1, 3)]
    samples = queries + [mutated(rng.choice(queries)) for _ in range(3000)] + [b"", b"\0" * 16, b"\0" * 17]
    ok(digest(repr([new_question(m) for m in samples])) == GOLDEN["questions_sha256"], f"вопрос из запроса: {len(samples)} сообщений, включая испорченные, разобраны как всегда")
    replies = []
    for m in queries:
        q = new_question(m)
        if q:
            for with_a in (True, False):
                replies.append(wire.answer(m, wire.Question(*q), IPv4Address(PROXY_IP) if with_a else None).hex())
    ok(replies and digest("".join(replies)) == GOLDEN["replies_sha256"], f"собственный ответ шлюза: {len(replies)} ответов те же байт в байт")
    hellos = [client_hello(n) for n in ("site.example", "A.Site.Example.", "x" * 60 + ".example", "")]
    samples = hellos + [mutated(rng.choice(hellos)) for _ in range(3000)]
    ok(digest(repr([tls.parse_sni(h) for h in samples])) == GOLDEN["sni_sha256"], f"имя сервера из ClientHello: {len(samples)} записей, включая испорченные, разобраны как всегда")
    lists = [LIST, "", "# only a comment\n\n", "*.a.example\n*a.example\nA.EXAMPLE.\n1.2.3.4\n1.2.3.0/24\n9.example\n", "b*.c*.example\n*\n*.*\n"]
    names = ["example.com", "a.example", "x.a.example", "xa.example", "ba.cb.example", "9.example", "x.9.example", "1.2.3.4", "com", "", "deep.wild.example", "wild.example", "rr1.video.example", "anything.at.all"]
    seen = []
    for list_text in lists:
        rules = SiteRules.parse(list_text)
        seen.append({"domains": sorted(rules.domains), "patterns": list(rules.patterns), "listed": [n for n in names if rules.lists(n)]})
    ok(seen == GOLDEN["lists"], f"список сайтов: {len(lists)} списков × {len(names)} имён — те же правила и те же ответы", seen)
    reply = resolver_answer(query("site.example"), "udp")
    addresses = [str(r.address) for r in wire.address_records(reply) if r.address.is_global and str(r.address) != PROXY_IP]
    ok(addresses == [SITE_IP], "настоящие адреса сайта: из ответа блокировщика взят единственный годный адрес", addresses)
    ok(wire.address_query("a.example", 0x4242)[2:] == query("a.example")[2:] and wire.address_query("a.example", 0x14242)[:2] == b"\x42\x42", "запрос адреса сайта — обычный рекурсивный запрос A")


def t_parts():
    print("\n— части по отдельности")
    from awgscale.dnsgate.admission import Admission
    from awgscale.dnsgate.proxy import SniProxy
    from awgscale.dnsgate.settings import DnsgateSettings
    from awgscale.dnsgate.sites import SiteList
    from awgscale.dnsgate.upstream import Upstream
    data = os.path.join(TMP, "parts-data")
    os.makedirs(data)
    settings = DnsgateSettings(proxy_ip=PROXY_IP, upstream=f"127.0.0.1:{UPSTREAM_PORT}", list_file=LIST_FILE, tokens_file=TOKENS_FILE, data_dir=data, static_allow=STATIC_IP, allow_hours=6, own_name="VPN.example")
    ok(settings.static_allow == frozenset({STATIC_IP}) and settings.own_name == "vpn.example" and settings.upstream_port == UPSTREAM_PORT and settings.allow_for == datetime.timedelta(hours=6), "настройки: список адресов через пробел, имя в нижнем регистре, срок приёма")
    now = [datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)]
    admission = Admission(settings, clock=lambda: now[0])
    admission.load_codes()
    ok(not admission.admit("203.0.113.1", None) and not admission.admit("203.0.113.1", "nope") and not admission.proxy_admits("203.0.113.1"), "без кода и с чужим кодом — отказ, прокси адрес не принимает")
    ok(admission.admit("203.0.113.1", "code-tv") and admission.proxy_admits("203.0.113.1"), "с кодом — принят, и прокси принимает этот адрес")
    now[0] += datetime.timedelta(hours=5, minutes=59)
    still = admission.proxy_admits("203.0.113.1")
    now[0] += datetime.timedelta(minutes=2)
    ok(still and not admission.proxy_admits("203.0.113.1"), "приём адреса длится 6 часов от последнего обращения, не дольше")
    ok(admission.admit(STATIC_IP, None) and admission.proxy_admits(STATIC_IP), "адресу из постоянного списка код не нужен")
    admission.admit("203.0.113.2", "code-pad")
    asyncio.run(admission.save_state())
    again = Admission(settings, clock=lambda: now[0])
    again.load_state()
    ok(again.proxy_admits("203.0.113.2") and not again.proxy_admits("203.0.113.1"), "сохранённое состояние: действующий приём восстановлен, истёкший — нет")
    with open(settings.state_file, "w") as f:
        f.write("{broken")
    broken = Admission(settings, clock=lambda: now[0])
    broken.load_state()
    ok(not broken.proxy_admits("203.0.113.2"), "испорченный файл состояния — шлюз стартует с пустым")

    async def piping():
        """An admitted connection to a listed name reaches the site: the hello first, then both directions as they are."""
        got = {}

        async def site(reader, writer):
            got["hello"] = await reader.readexactly(len(client_hello("site.example")))
            writer.write(b"server hello")
            await writer.drain()
            got["after"] = await reader.readexactly(4)
            writer.close()
        site_server = await asyncio.start_server(site, "127.0.0.1", 0)
        site_port = site_server.sockets[0].getsockname()[1]

        async def connect(host, port):
            got["asked"] = (host, port)
            return await asyncio.open_connection("127.0.0.1", site_port)
        sites = SiteList(settings.list_file)
        sites.load()
        proxy = SniProxy(settings, admission, sites, Upstream(settings), connect=connect)
        front = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", front.sockets[0].getsockname()[1])
        writer.write(proxy_v1(STATIC_IP) + client_hello("site.example"))
        got["reply"] = await asyncio.wait_for(reader.readexactly(12), 5)
        writer.write(b"data")
        await writer.drain()
        got["closed"] = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        front.close()
        site_server.close()
        return got
    got = asyncio.run(piping())
    ok(got.get("asked") == (SITE_IP, 443), "прокси соединяется с единственным годным адресом сайта, порт 443 (0.0.0.0, частный и свой адрес отброшены)", got.get("asked"))
    ok(got.get("hello") == client_hello("site.example") and got.get("reply") == b"server hello" and got.get("after") == b"data" and got.get("closed") == b"", "сайту уходит ClientHello как есть (без заголовка PROXY), дальше данные идут в обе стороны; сайт закрыл — закрыто и у клиента", {k: v for k, v in got.items() if k != "hello"})


def t_switch_over():
    print("\n— старт на сохранённом состоянии и отказ части")
    NEW.terminate()
    NEW.wait(10)
    now = time.time()
    saved = {"allowed": {"203.0.113.90": now + 6 * 3600 - 5}, "saved": int(now) - 5,
             "seen": {"pad": {"ip": "203.0.113.90", "last": int(now) - 5, "queries": 2}, "tv_kitchen": {"ip": "203.0.113.61", "last": int(now) - 10, "queries": 29}}}
    replace(os.path.join(NEW_DATA, "state.json"), json.dumps(saved))
    ok(start_new() and knock(NEW_SNI, proxy_v1("203.0.113.90") + client_hello("i.site.example"), "i.site.example"), "шлюз стартует на сохранённом файле состояния: принятый в нём адрес принят")
    time.sleep(1.6)
    kept = json.load(open(os.path.join(NEW_DATA, "state.json")))
    ok(kept["seen"]["pad"]["ip"] == "203.0.113.90" and kept["seen"]["pad"]["queries"] >= 1, "…и счётчики устройств из файла сохранены", kept["seen"])
    NEW.terminate()
    NEW.wait(10)
    busy = socket.socket()
    busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    busy.bind(("0.0.0.0", NEW_SNI))
    busy.listen(1)
    start_new()
    ok(wait(lambda: NEW.poll() is not None, 15) and NEW.returncode == 1, "прокси не смог занять свой порт — процесс завершается с ошибкой, а не отвечает на DNS без прокси", NEW.poll())
    busy.close()
    from awgscale.service.tasks import ended_by_itself, vital

    class Server:
        should_exit = False

    async def fails():
        raise RuntimeError("boom")

    async def scenario():
        server, loop = Server(), asyncio.get_running_loop()
        failed, cancelled = loop.create_task(fails()), loop.create_task(asyncio.sleep(60))
        vital(cancelled, server, "a cancelled task")
        cancelled.cancel()
        await asyncio.sleep(0.05)
        after_cancel = server.should_exit
        vital(failed, server, "a failed task")
        await asyncio.sleep(0.05)
        return after_cancel, server.should_exit, ended_by_itself(failed), ended_by_itself(cancelled)
    logging_off = __import__("logging").disable(50)
    ok(asyncio.run(scenario()) == (False, True, True, False), "общее правило сервисов: необходимая задача завершилась сама — сервер останавливается; отменённая при выходе — нет")
    __import__("logging").disable(0)


def main():
    try:
        ok(start_new(), "шлюз запущен")
        t_same_answers()
        t_files()
        t_proxy()
        t_state()
        t_same_functions()
        t_parts()
        t_switch_over()
    finally:
        if NEW and NEW.poll() is None:
            NEW.kill()
        if FAIL and NEW:
            print(NEW.stdout.read()[-3000:])
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
