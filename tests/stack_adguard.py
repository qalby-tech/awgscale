"""What the rules for some groups that go abroad or directly rely on in AdGuard Home, the pinned version itself
(tests/stack.sh runs this in a throwaway network namespace with the binary taken from the resolver's image):

  * a line of the file of sets may name several sets ("m.a.test/force_exit,rs_aaaaaa"), and the most specific
    name decides; a name on two lines is in both lines' sets;
  * a set the file names that is not there stops it from starting (why the watch service and the gate make them
    first, and the agent names them only after);
  * a client's own upstreams fill the sets as the global ones do (a group's own list);
  * `||name^$client=~'a'|~'b'` blocks for everybody but those addresses (a rule for groups above a block);
  * clients/update changes a client in place (its name too), and refuses what does not fit, leaving it as it was.

Prints "ok <what>" or "FAIL <what>" per check. Standard library only; run as root with NET_ADMIN."""
import json
import os
import random
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

BINARY = sys.argv[1] if len(sys.argv) > 1 else "/ag/AdGuardHome"
WORK = "/tmp/ag"
API = "http://127.0.0.1:3001/control/"
GLOBAL_BASE = 11        # the answers of the global upstream: 11.x.x.x
OWN_BASE = 22           # of a client's own upstream: 22.x.x.x
YAML = """http:
  address: 127.0.0.1:3001
users: []
dns:
  bind_hosts:
    - 127.0.0.1
  port: 53
  upstream_dns:
    - 127.0.0.1:5353
  bootstrap_dns:
    - 127.0.0.1:5353
  ipset_file: {ipset}
  cache_enabled: false
filtering:
  filtering_enabled: true
schema_version: 29
"""
FAILED = 0


def ok(condition, what, seen=None):
    global FAILED
    if condition:
        print("ok " + what)
    else:
        FAILED += 1
        print("FAIL " + what + ("" if seen is None else " → " + repr(seen)[:400]))
    sys.stdout.flush()


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True)


def upstream(port, base):
    """A resolver that answers every A question with base.<labels>.<hash>.1: the first number tells who answered."""
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", port))
    while True:
        data, peer = server.recvfrom(2048)
        question = data[12:]
        at = 0
        labels = []
        while question[at]:
            labels.append(question[at + 1:at + 1 + question[at]].decode())
            at += question[at] + 1
        end = 12 + at + 5
        kind = struct.unpack(">H", data[12 + at + 1:12 + at + 3])[0]
        flags = b"\x81\x80"
        if kind != 1:
            server.sendto(data[:2] + flags + b"\x00\x01\x00\x00\x00\x00\x00\x00" + data[12:end], peer)
            continue
        digest = sum(".".join(labels).encode()) % 250 + 1
        answer = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + bytes([base, len(labels), digest, 1])
        server.sendto(data[:2] + flags + b"\x00\x01\x00\x01\x00\x00\x00\x00" + data[12:end] + answer, peer)


def ask(name, source="127.0.0.1"):
    """→ the address answered, "blocked" (AdGuard's 0.0.0.0), or what went wrong."""
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind((source, 0))
    client.settimeout(3)
    query = struct.pack(">HHHHHH", random.randint(0, 65535), 0x0100, 1, 0, 0, 0)
    query += b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\x00" + struct.pack(">HH", 1, 1)
    client.sendto(query, ("127.0.0.1", 53))
    try:
        data, _ = client.recvfrom(2048)
    except socket.timeout:
        return "timeout"
    if not struct.unpack(">H", data[6:8])[0]:
        return "rcode%d" % (data[3] & 0xF)
    address = ".".join(str(byte) for byte in data[-4:])
    return "blocked" if address == "0.0.0.0" else address


def members(name):
    return sorted(line for line in sh("ipset", "list", name).stdout.splitlines() if line[:1].isdigit())


def flush(*names):
    for name in names:
        sh("ipset", "flush", name)


def api(path, payload=None):
    request = urllib.request.Request(API + path, data=None if payload is None else json.dumps(payload, separators=(",", ":")).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            return answer.status, answer.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def clients():
    return {client["name"]: client for client in json.loads(api("clients")[1]).get("clients") or []}


def start(ipset_text):
    sh("rm", "-rf", WORK)
    os.makedirs(WORK)
    with open(WORK + "/ipset.txt", "w") as handle:
        handle.write(ipset_text)
    with open(WORK + "/AdGuardHome.yaml", "w") as handle:
        handle.write(YAML.format(ipset=WORK + "/ipset.txt"))
    log = open(WORK + "/log", "w")
    process = subprocess.Popen([BINARY, "-c", WORK + "/AdGuardHome.yaml", "-w", WORK, "--no-check-update"], stdout=log, stderr=log)
    for _ in range(60):
        time.sleep(0.25)
        if process.poll() is not None:
            return process, False
        try:
            if api("status")[0] == 200:
                return process, True
        except OSError:
            pass
    return process, False


def stop(process):
    process.terminate()
    process.wait(10)


def client(name, ids, upstreams):
    return {"name": name, "ids": ids, "use_global_settings": True, "filtering_enabled": True, "parental_enabled": False, "safebrowsing_enabled": False,
            "safe_search": {"enabled": False}, "use_global_blocked_services": True, "blocked_services": [], "upstreams": upstreams,
            "upstreams_cache_enabled": True, "upstreams_cache_size": 1048576, "tags": [], "ignore_querylog": False, "ignore_statistics": False}


def main():
    sh("ip", "link", "set", "lo", "up")
    for address in ("10.9.0.2", "10.9.0.3", "10.9.0.4", "10.9.0.5"):
        sh("ip", "addr", "add", address + "/32", "dev", "lo")
    for name in ("force_exit", "force_direct", "rs_aaaaaa", "rs_bbbbbb"):
        sh("ipset", "create", name, "hash:ip", "-exist")
    threading.Thread(target=upstream, args=(5353, GLOBAL_BASE), daemon=True).start()
    threading.Thread(target=upstream, args=(5354, OWN_BASE), daemon=True).start()
    time.sleep(0.3)

    process, up = start("a.test/force_exit,rs_missing\n")
    ok(not up, "a set the file names that is not there: AdGuard Home does not start (the sets must exist first)")
    if up:
        stop(process)
    sh("ipset", "create", "rs_missing", "hash:ip", "-exist")
    process, up = start("a.test/force_exit,rs_missing\n")
    ok(up, "the same file once the set is made: it starts")
    if up:
        stop(process)
    sh("ipset", "destroy", "rs_missing")

    text = "a.test,x.test/force_exit\nm.a.test/force_exit,rs_aaaaaa\nc.test/rs_bbbbbb\ndup.test/force_exit\ndup.test/rs_bbbbbb\nd.test/force_direct\n"
    process, up = start(text)
    ok(up, "a file with lines that name several sets: it starts")
    if not up:
        print(open(WORK + "/log").read()[-1500:])
        return 1
    seen = {}
    for name in ("a.test", "m.a.test", "z.m.a.test", "c.test", "dup.test"):
        flush("force_exit", "force_direct", "rs_aaaaaa", "rs_bbbbbb")
        answer = ask(name)
        time.sleep(0.3)
        seen[name] = (answer, bool(members("force_exit")), bool(members("rs_aaaaaa")), bool(members("rs_bbbbbb")))
    ok(seen["m.a.test"][1:] == (True, True, False) and seen["z.m.a.test"][1:] == (True, True, False) and seen["a.test"][1:] == (True, False, False),
       "m.a.test (and what is under it) goes into both sets of its line; a.test only into its own — the most specific name decides", seen)
    ok(seen["c.test"][1:] == (False, False, True), "a name of a rule for groups only: only its rule's set", seen)
    ok(seen["dup.test"][1:] == (True, False, True), "a name on two lines: in both lines' sets", seen)

    added = api("clients/add", client("grp-rtest", ["10.9.0.2"], ["127.0.0.1:5353", "[/c.test/]127.0.0.1:5354"]))
    flush("rs_bbbbbb")
    own = ask("c.test", "10.9.0.2")
    time.sleep(0.3)
    ok(added[0] == 200 and own.startswith(f"{OWN_BASE}.") and members("rs_bbbbbb"), "a client's own upstreams answer its people and fill the sets as the global ones do", [added, own, members("rs_bbbbbb")])
    ok(ask("q.test", "10.9.0.2").startswith(f"{GLOBAL_BASE}."), "the rest of its names: its own spec's plain upstream", None)

    updated = api("clients/update", {"name": "grp-rtest", "data": client("grp-rtest", ["10.9.0.2", "10.9.0.3"], ["127.0.0.1:5354"])})
    now = clients().get("grp-rtest", {})
    ok(updated[0] == 200 and sorted(now.get("ids", [])) == ["10.9.0.2", "10.9.0.3"] and now.get("upstreams") == ["127.0.0.1:5354"] and ask("q.test", "10.9.0.3").startswith(f"{OWN_BASE}."),
       "clients/update {name, data}: the client changed in place (its addresses and upstreams)", [updated, now.get("ids"), now.get("upstreams")])
    renamed = api("clients/update", {"name": "grp-rtest", "data": client("grp-rtest-r", ["10.9.0.2", "10.9.0.3"], ["127.0.0.1:5354"])})
    ok(renamed[0] == 200 and "grp-rtest" not in clients() and "grp-rtest-r" in clients(), "clients/update renames a client in place (its people never leave a client)", renamed)
    api("clients/add", client("grp-other", ["10.9.0.5"], []))
    taken = api("clients/update", {"name": "grp-rtest-r", "data": client("grp-rtest-r", ["10.9.0.2", "10.9.0.5"], ["127.0.0.1:5354"])})
    ok(taken[0] != 200 and sorted(clients()["grp-rtest-r"]["ids"]) == ["10.9.0.2", "10.9.0.3"], "an address another client holds: refused, the client stays as it was", taken)
    huge = ["127.0.0.1:5354"] + ["[/site%d.example.net/]127.0.0.1:5354" % number for number in range(2600)]
    body = json.dumps({"name": "grp-rtest-r", "data": client("grp-rtest-r", ["10.9.0.2"], huge)}, separators=(",", ":")).encode()
    oversize = api("clients/update", {"name": "grp-rtest-r", "data": client("grp-rtest-r", ["10.9.0.2"], huge)})
    ok(len(body) > 65536 and oversize[0] != 200 and clients()["grp-rtest-r"]["upstreams"] == ["127.0.0.1:5354"] and sorted(clients()["grp-rtest-r"]["ids"]) == ["10.9.0.2", "10.9.0.3"],
       f"a request over 65 536 bytes ({len(body)}): refused, the client stays as it was", oversize)
    empty = api("clients/update", {"name": "grp-rtest-r", "data": client("grp-rtest-r", [], ["127.0.0.1:5354"])})
    print(f"note: a client with no addresses — clients/update answers {empty[0]} {empty[1][:80]!r}")

    rules = api("filtering/set_rules", {"rules": ["||blk.test^$client=~'10.9.0.2'|~'10.9.0.5'", "||only.test^$client='10.9.0.3'"]})
    time.sleep(0.5)
    blocked = {source: (ask("blk.test", source), ask("only.test", source)) for source in ("127.0.0.1", "10.9.0.2", "10.9.0.3", "10.9.0.5")}
    ok(rules[0] == 200 and blocked["127.0.0.1"][0] == "blocked" and blocked["10.9.0.3"][0] == "blocked" and blocked["10.9.0.2"][0] != "blocked" and blocked["10.9.0.5"][0] != "blocked",
       "||name^$client=~'a'|~'b': blocked for everybody but those two", blocked)
    ok(blocked["10.9.0.3"][1] == "blocked" and blocked["127.0.0.1"][1] != "blocked" and blocked["10.9.0.2"][1] != "blocked", "||name^$client='a': blocked for that one only", blocked)
    stop(process)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
