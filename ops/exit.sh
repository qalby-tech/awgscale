#!/usr/bin/env bash
# =============================================================================
# exit.sh — run ON THE OPERATOR MACHINE. The exits of the cascade from a terminal: the same
# requests the panel sends, to the same agent on the entry server.
#
#   ops/exit.sh list                          the exits, the servers behind them, the last job
#   ops/exit.sh add <address> <name> [percent]  a rented server becomes an exit with that share of the users, in
#                                                  percent (default: an equal part); asks for its root password;
#                                                  empty = the key shown by `list` is on the server already)
#   ops/exit.sh rename <number> <name>
#   ops/exit.sh update <number> [reboot]      packages and settings; with `reboot` the server is restarted
#   ops/exit.sh remove <number> [force]       out of the cascade and off the entry
#   ops/exit.sh watch                         follow the running job to its end
#
# The password is read without echo and travels inside the SSH connection to the
# agent, beside the command (never as an argument, never written anywhere).
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
SSH=(ssh -F "$HERE/ssh_config" -o BatchMode=yes entry)
# on the entry: one JSON request from stdin → the agent's socket → its answer
ASK='python3 -c "
import json, socket, sys
c = socket.socket(socket.AF_UNIX); c.settimeout(300); c.connect(\"/run/vpn-panel/agent.sock\")
c.sendall(sys.stdin.read().encode() + b\"\n\"); c.shutdown(socket.SHUT_WR)
buf = b\"\"
while True:
    chunk = c.recv(65536)
    if not chunk: break
    buf += chunk
print(buf.decode().strip().splitlines()[-1])"'
ask() { "${SSH[@]}" "$ASK"; }                       # stdin: the request
req() { python3 -c 'import json, sys; print(json.dumps({"args": sys.argv[1:], "by": "panel"}))' "$@"; }
show() {
    python3 -c '
import json, sys
r = json.load(sys.stdin)
if not r.get("ok"):
    print("ОТКАЗ: " + str(r.get("error", r)) + (" (повторите с force)" if r.get("confirm") else "")); sys.exit(1)
for k in ("name", "index", "address", "note"):
    if r.get(k) is not None: print(f"{k}: {r[k]}")
print("принято")'
}
job() {
    python3 -c '
import json, sys
j = (json.load(sys.stdin) or {}).get("job")
if not j: print("операций не было"); sys.exit(0)
mark = {"done": "✓", "run": "…", "fail": "✕", "skip": "–", "wait": "·"}
print({"add": "подключение", "update": "обновление", "clean": "уборка"}.get(j["kind"], j["kind"]), j.get("name") or j.get("address"), "—", {"running": "идёт", "done": "готово", "failed": "НЕ УДАЛОСЬ"}[j["state"]])
for s in j.get("steps", []): print("  ", mark.get(s["state"], "·"), s["title"])
if j.get("error"): print("  ошибка:", j["error"])
sys.exit(3 if j["state"] == "running" else 0)'
}

case "${1:-}" in
    list)
        req state | ask | python3 -c '
import json, sys
exits = json.load(sys.stdin).get("exits", [])
total = sum(e.get("weight") or 0 for e in exits) or 1
for e in exits:
    print("%2d  %-20s %-8s доля %d%%  пользователей %d  %s" % (e["index"], e["name"], e["state"], round(100 * (e.get("weight") or 0) / total), len(e.get("clients", [])), (e.get("endpoint") or "").split(":")[0]))'
        r=$(req exits-get | ask)
        python3 -c '
import json, sys
r = json.loads(sys.argv[1])
for i, n in sorted((r.get("nodes") or {}).items(), key=lambda x: int(x[0])):
    print("    выход %s: сервер %s — %s" % (i, n.get("address"), "управляется панелью" if n.get("managed") else "настроен вручную"))
print("ключ входа для новых серверов (если без пароля):"); print("   ", r.get("key", ""))' "$r"
        job <<<"$r" || true ;;
    add)
        addr=${2:?address}; name=${3:?name}; share=${4:-0}
        read -rsp "Пароль root сервера $addr (пусто — ключ уже на сервере): " pw; echo
        b64=$(python3 -c 'import base64, json, sys; print(base64.b64encode(json.dumps({"address": sys.argv[1], "name": sys.argv[2], **({"weight": int(sys.argv[3])} if int(sys.argv[3]) else {})}).encode()).decode())' "$addr" "$name" "$share")
        PW="$pw" python3 -c 'import json, os, sys; print(json.dumps({"args": ["exit-add", sys.argv[1]], "by": "panel", **({"secret": os.environ["PW"]} if os.environ.get("PW") else {})}))' "$b64" | ask | show
        unset pw
        echo "ход операции: $0 watch" ;;
    rename) req exit-rename "${2:?number}" "${3:?name}" | ask | show ;;
    update) if [ "${3:-}" = reboot ]; then req exit-update "${2:?number}" reboot; else req exit-update "${2:?number}"; fi | ask | show; echo "ход операции: $0 watch" ;;
    remove) if [ "${3:-}" = force ]; then req exit-remove "${2:?number}" force; else req exit-remove "${2:?number}"; fi | ask | show ;;
    watch)
        while true; do
            out=$(req exits-get | ask | job) && rc=0 || rc=$?
            echo "$out"; [ "$rc" = 3 ] || break
            echo; python3 -c 'import time; time.sleep(6)'
        done ;;
    *) sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
