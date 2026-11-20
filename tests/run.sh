#!/usr/bin/env bash
# Browser tests of the panel page against the stub. Nothing live is touched.
#   tests/run.sh                 every tests/*.test.js, then the Python unit tests
#   tests/run.sh users sites     only tests/users.test.js and tests/sites.test.js
# Several runs can go at once: SLOT=1 tests/run.sh users   (ports 38787+SLOT and 39222+SLOT)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SLOT=${SLOT:-0}
# the browser tests drive Chromium with puppeteer-core (tests/package.json), installed once beside them
[ -n "${PUPPETEER:-}" ] || [ -d "$(dirname "$0")/node_modules/puppeteer-core" ] || npm install --prefix "$(dirname "$0")" --no-audit --no-fund >/dev/null
PORT=$((38787 + SLOT)); CDPPORT=$((39222 + SLOT)); NAME=paneltest-chrome-$SLOT
export BASE=http://127.0.0.1:$PORT CDP=http://127.0.0.1:$CDPPORT
# The page is built first (types are checked on the way); SKIP_BUILD=1 tests the build that is already in web/out.
if [ -z "${SKIP_BUILD:-}" ]; then "$HERE/../web/build.sh" >/dev/null || { echo "the page does not build: web/build.sh"; exit 2; }; fi
[ -s "$HERE/../web/out/index.html" ] || { echo "no built page in web/out: run web/build.sh"; exit 2; }
python3 "$HERE/stub.py" "$PORT" & STUB=$!
docker stop "$NAME" >/dev/null 2>&1
docker run -d --rm --name "$NAME" --network host chromedp/headless-shell --remote-debugging-address=127.0.0.1 --remote-debugging-port=$CDPPORT >/dev/null
trap 'kill $STUB 2>/dev/null; docker stop "$NAME" >/dev/null 2>&1' EXIT
for i in $(seq 1 40); do curl -s -o /dev/null "$BASE/" && curl -s -o /dev/null "$CDP/json/version" && break; sleep 0.25; done
if [ $# -gt 0 ]; then FILES=(); for t in "$@"; do FILES+=("$HERE/$t.test.js"); done; else FILES=("$HERE"/*.test.js); fi
rc=0
for f in "${FILES[@]}"; do
    echo "── $(basename "$f")"
    curl -s -o /dev/null -X POST -H 'Content-Type: application/json' -d '{"reset":true}' "$BASE/__ctl"
    timeout 300 node "$f" || rc=1
done
if [ $# -eq 0 ] && ls "$HERE"/test_*.py >/dev/null 2>&1; then
    # through uv: the agent's suites ask the package (awgscale.agent) for its daemon and its clients
    for f in "$HERE"/test_*.py "$HERE"/agent_clients.py "$HERE"/agent_journal.py "$HERE"/agent_cascade.py "$HERE"/agent_users.py "$HERE"/agent_block.py "$HERE"/agent_dns.py "$HERE"/agent_state.py "$HERE"/agent_people.py; do echo "── $(basename "$f")"; (cd "$HERE/.." && uv run python "$f") || rc=1; done
    # spaces and invitations of the agent, its daemon, and the edge cases of its services
    for f in "$HERE"/agent_spaces.py "$HERE"/agent_invites.py "$HERE"/agent_app.py "$HERE"/agent_daemon.py "$HERE"/agent_edges.py; do echo "── $(basename "$f")"; (cd "$HERE/.." && uv run python "$f") || rc=1; done
fi
# The settings store against a real, throwaway Postgres (in a container).
if [ $# -eq 0 ]; then
    PG=pgtest-$SLOT; PGPORT=$((35432 + SLOT))
    docker stop "$PG" >/dev/null 2>&1
    docker run -d --rm --name "$PG" -e POSTGRES_USER=vpn -e POSTGRES_DB=vpn -e POSTGRES_PASSWORD=testpw -p 127.0.0.1:$PGPORT:5432 postgres:17-alpine >/dev/null
    for i in $(seq 1 40); do docker exec "$PG" psql -U vpn -d vpn -c 'select 1' >/dev/null 2>&1 && break; sleep 0.5; done
    echo "── agent_store.py"
    (cd "$HERE/.." && PG_DSN="host=127.0.0.1 port=$PGPORT dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=testpw uv run python tests/agent_store.py | grep -vE '^ok  ') || rc=1
    # the agent's exits as servers (with the database: also the groups kept on a removed exit)
    echo "── agent_exits.py"
    (cd "$HERE/.." && PG_DSN="host=127.0.0.1 port=$PGPORT dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=testpw uv run python tests/agent_exits.py | grep -vE '^ok  ') || rc=1
    echo "── agent_invites_store.py"
    (cd "$HERE/.." && PG_DSN="host=127.0.0.1 port=$PGPORT dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=testpw uv run python tests/agent_invites_store.py | grep -vE '^ok  ') || rc=1
    echo "── agent_people_store.py"
    (cd "$HERE/.." && PG_DSN="host=127.0.0.1 port=$PGPORT dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=testpw uv run python tests/agent_people_store.py | grep -vE '^ok  ') || rc=1
    echo "── agent_shell.py"
    (cd "$HERE/.." && PG_DSN="host=127.0.0.1 port=$PGPORT dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=testpw uv run python tests/agent_shell.py | grep -vE '^ok  ') || rc=1
    # sites and routing
    echo "── agent_sites.py"
    (cd "$HERE/.." && PG_DSN="host=127.0.0.1 port=$PGPORT dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=testpw uv run python tests/agent_sites.py | grep -vE '^ok  ') || rc=1
    echo "── pg_users.py"
    docker exec "$PG" psql -q -U vpn -d vpn -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public' >/dev/null
    docker build -q -t vpn-users:test -f "$HERE/../docker/Dockerfile" "$HERE/.." >/dev/null 2>&1
    docker run --rm --network host -v "$HERE/..":/src:ro -e PG_DSN="host=127.0.0.1 port=$PGPORT dbname=vpn user=vpn connect_timeout=3" -e PG_PASSWORD=testpw \
        vpn-users:test python3 /src/tests/pg_users.py | grep -vE '^20[0-9-]+T' || rc=1
    docker stop "$PG" >/dev/null 2>&1
    # The watch service: on stand-ins for the kernel tools, then in a real
    # kernel (a throwaway network namespace).
    echo "── watch_service.py"
    (cd "$HERE/.." && uv run -q python tests/watch_service.py) | grep -vE '^ok ' || rc=1
    echo "── deadman.sh"
    bash "$HERE/deadman.sh" | grep -vE '^ok ' || rc=1
    echo "── tunnel.sh"
    bash "$HERE/tunnel.sh" | grep -vE '^ok ' || rc=1
    echo "── exits_service.py"
    (cd "$HERE/.." && uv run -q python tests/exits_service.py) </dev/null 2>&1 | grep -vE '^ok ' || rc=1
    echo "── keeper.py"
    (cd "$HERE/.." && uv run -q python tests/keeper.py) | grep -vE '^ok ' || rc=1
    echo "── dnsgate.py"
    (cd "$HERE/.." && uv run -q python tests/dnsgate.py) | grep -vE '^ok ' || rc=1
    echo "── bot.py"
    (cd "$HERE/.." && uv run -q python tests/bot.py 2>/dev/null) | grep -vE '^ok ' || rc=1
    echo "── panel_server.py"
    (cd "$HERE/.." && uv run -q python tests/panel_server.py) | grep -vE '^ok ' || rc=1
    echo "── app/check.sh"
    bash "$HERE/../app/check.sh" || rc=1
    echo "── app/build.sh check"
    bash "$HERE/../app/build.sh" check || rc=1
    echo "── app/tests/screens.sh"
    bash "$HERE/../app/tests/screens.sh" || rc=1
    echo "── kernel.sh"
    bash "$HERE/kernel.sh" | grep -vE '^ok ' || rc=1
    # The deploy: what stack/deploy.sh does on the server (against stand-ins), then the
    # images it ships, started the way stack/compose.yml starts them, in a throwaway namespace.
    echo "── deploy_remote.sh"
    bash "$HERE/deploy_remote.sh" | grep -vE '^ok ' || rc=1
    echo "── stack.sh"
    bash "$HERE/stack.sh" | grep -vE '^ok ' || rc=1
fi
exit $rc
