# The questions a deploy asks a service before it believes that the service works.
# Sourced by stack/deploy.sh (on the entry server), by stack/switch-tunnel.sh and
# by tests/stack.sh, which asks the very same questions of the images on this
# machine: a gate the deploy relies on is a gate the test has run.
# bash and the system's bare python3 only. The token files are read relative to
# PROBE_STACK (the stack's directory on the server).
PROBE_STACK=${PROBE_STACK:-/opt/vpn-stack}
PROBE_PYTHON=${PROBE_PYTHON:-python3}
PROBE_DNS_AT=${PROBE_DNS_AT:-172.16.17.1}           # where the resolver must answer the users
PROBE_AGENT_SOCKET=${PROBE_AGENT_SOCKET:-/run/vpn-panel/agent.sock}

# probe_json <url> <token file> <python expression over `answer`>: the answer is JSON and the expression is true
probe_json() {
    "$PROBE_PYTHON" - "$1" "$2" "$3" <<'PY' 2>/dev/null
import json, sys, urllib.request
try:
    headers = {}
    if sys.argv[2]:
        headers["Authorization"] = "Bearer " + open(sys.argv[2]).read().strip()
    answer = json.load(urllib.request.urlopen(urllib.request.Request(sys.argv[1], headers=headers), timeout=3))
    sys.exit(0 if eval(sys.argv[3], {"answer": answer}) else 1)
except Exception:
    sys.exit(1)
PY
}

# the users service: the database and the kernel both answered it
users_answer() { probe_json http://127.0.0.1:8790/v1/health "$PROBE_STACK/users-secret/token" 'answer["ok"] is True'; }

# the watch service: running, the rules are in, the exits were probed at least once
# (both moments are ISO dates in the answer; null while it has not happened)
watch_answers() { probe_json http://127.0.0.1:8791/v1/health "$PROBE_STACK/watch-secret/token" 'answer["ok"] is True and bool(answer["applied"]) and bool(answer["last_tick"])'; }

# the exits service: it lists the exits it looks after
exits_answer() { probe_json http://127.0.0.1:8793/v1/nodes "$PROBE_STACK/exits-secret/token" 'answer["ok"] is True and isinstance(answer["nodes"], dict)'; }

# the resolver's keeper says the resolver listens where it must
keeper_answers() { probe_json http://127.0.0.1:8792/v1/health "$PROBE_STACK/resolver-secret/token" 'answer["listening"] is True'; }

# a real question over UDP, the way the users' devices ask
dns_answers() {
    "$PROBE_PYTHON" - "$PROBE_DNS_AT" <<'PY' 2>/dev/null
import os, socket, struct, sys
try:
    q = os.urandom(2) + struct.pack(">HHHHH", 0x0100, 1, 0, 0, 0) + b"".join(bytes([len(p)]) + p.encode() for p in "ya.ru".split(".")) + bytes(1) + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4); s.sendto(q, (sys.argv[1], 53))
    a = s.recv(2048)
    sys.exit(0 if a[:2] == q[:2] and a[3] & 15 == 0 and struct.unpack(">H", a[6:8])[0] > 0 else 1)
except Exception:
    sys.exit(1)
PY
}

resolver_asks() { keeper_answers && dns_answers; }

# the agent, over its socket, the way the panel and the bot ask it
agent_asks() {
    "$PROBE_PYTHON" - "$PROBE_AGENT_SOCKET" <<'PY' 2>/dev/null
import json, socket, sys
try:
    s = socket.socket(socket.AF_UNIX); s.settimeout(20); s.connect(sys.argv[1])
    s.sendall(b'{"args": ["state"]}\n'); s.shutdown(socket.SHUT_WR)
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk: break
        data += chunk
    sys.exit(0 if json.loads(data).get("ok") else 1)
except Exception:
    sys.exit(1)
PY
}

# the panel: its login page's API answers (401 without a session is an answer; no answer is not)
panel_answers() {
    "$PROBE_PYTHON" - "${1:-http://127.0.0.1:8787}" <<'PY' 2>/dev/null
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(sys.argv[1] + "/api/state", timeout=3)
    sys.exit(0)
except urllib.error.HTTPError as e:
    sys.exit(0 if e.code in (401, 403) else 1)
except Exception:
    sys.exit(1)
PY
}

# the smart DNS: its DoH port takes connections and turns a request without a code down itself
dnsgate_answers() {
    "$PROBE_PYTHON" - "${1:-http://127.0.0.1:8054}" <<'PY' 2>/dev/null
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(sys.argv[1] + "/dns-query/", timeout=3)
    sys.exit(0)
except urllib.error.HTTPError as e:
    sys.exit(0 if 400 <= e.code < 500 else 1)
except Exception:
    sys.exit(1)
PY
}

# a container that is running and has not been restarted by Docker since it was made
container_holds() { [ "$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$1" 2>/dev/null)" = "true 0" ]; }

# wait_for <seconds> <probe…>: asked once a second until it says yes
wait_for() {
    local seconds=$1 i
    shift
    for i in $(seq 1 "$seconds"); do
        if "$@"; then return 0; fi
        sleep 1
    done
    return 1
}
