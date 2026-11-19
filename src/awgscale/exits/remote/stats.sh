#!/usr/bin/env bash
# Runs ON an exit server: the small "how is this server doing" container for
# the panel (files sent before to /opt/vpn-exit-stats). Env: ADDR (the exit's
# address inside the tunnel), PORT.
set -euo pipefail
: "${ADDR:?}" "${PORT:?}"
ip -4 -o addr | grep -q " $ADDR/" || { echo "@@ERR на сервере нет адреса $ADDR — туннель не поднят"; exit 1; }
cd /opt/vpn-exit-stats
docker build -q -t vpn-exit-stats:local . >/dev/null 2>&1 || { echo "@@ERR образ мониторинга не собрался"; exit 1; }
docker rm -f vpn-exit-stats >/dev/null 2>&1 || true
docker run -d --name vpn-exit-stats --restart unless-stopped --network host --read-only --cap-drop ALL \
    --security-opt no-new-privileges --memory 64m --log-opt max-size=1m --log-opt max-file=2 \
    -e STATS_BIND="$ADDR" -e STATS_PORT="$PORT" -e STATS_NAME="$(hostname)" -v /etc/hostname:/host-root:ro vpn-exit-stats:local >/dev/null
sleep 2
[ "$(docker inspect -f '{{.State.Running}}' vpn-exit-stats 2>/dev/null)" = true ] || { echo "@@ERR контейнер мониторинга не запустился"; exit 1; }
echo "@@OK мониторинг запущен"
