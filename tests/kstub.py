#!/usr/bin/env python3
"""A stand-in for the kernel tools (ip, ipset, iptables, iptables-restore, awg,
ping, conntrack), installed under each of those names for tests/watch_service.py.
It answers the questions from $KSTUB/scenario.json and writes every call —
tool, arguments, what was piped in — to $KSTUB/log, one JSON object per line."""
import json
import os
import sys

tool = os.path.basename(sys.argv[0])
a = sys.argv[1:]
base = os.environ["KSTUB"]
with open(f"{base}/scenario.json") as f:
    sc = json.load(f)
piped = ""
if tool == "iptables-restore" or (tool == "ipset" and a[:1] == ["restore"]):
    piped = sys.stdin.read()
with open(f"{base}/log", "a") as f:
    f.write(json.dumps({"tool": tool, "argv": a, "stdin": piped}) + "\n")

rc, out = 0, ""
sets = sc.get("sets", {})
if tool == "ip":
    if a[:2] == ["link", "show"]:
        rc = 0 if a[2] in sc.get("links", []) else 1
elif tool == "ipset":
    if a[:1] == ["list"]:
        if a[1:] == ["-n"]:
            out = "".join(n + "\n" for n in sets)
        elif a[-1] == "-t":
            out = f"Name: {a[1]}\nNumber of entries: {len(sets.get(a[1], []))}\n"
            rc = 0 if a[1] in sets else 1
        else:
            out = f"Name: {a[1]}\nMembers:\n" + "".join(m + "\n" for m in sets.get(a[1], []))
            rc = 0 if a[1] in sets else 1
    elif a[:1] == ["test"]:
        rc = 0 if a[2] in sets.get(a[1], []) else 1
    elif a[:1] == ["save"]:
        out = f"create {a[1]} hash:ip\n" + "".join(f"add {a[1]} {m}\n" for m in sets.get(a[1], []))
    elif a[:1] == ["restore"] and sc.get("ipset_restore_fails"):
        rc = 1
elif tool == "iptables":
    if "-C" in a:
        rc = 0 if " ".join(a) in sc.get("present", []) else 1
    elif "-nL" in a:
        rc = 0 if sc.get("peerchain") else 1
    elif "-S" in a:
        out = "".join(l + "\n" for l in sc.get("forward", []))
    elif "-D" in a:
        rc = 1
elif tool == "iptables-restore":
    if sc.get("restore_fails") and sc["restore_fails"] in piped:
        rc = 1
elif tool == "awg":
    if a[-1] == "allowed-ips":
        out = "".join(f"{k}\t{' '.join(ips)}\n" for k, ips in sc.get("peers", []))
    elif a[-1] == "latest-handshakes":
        out = "KEY\t1794500000\n"
    elif a[-1] == "transfer":
        out = "KEY\t111\t222\n"
elif tool == "ping":
    rc = 0 if a[a.index("-I") + 1] in sc.get("alive", []) else 1
sys.stdout.write(out)
sys.exit(rc)
