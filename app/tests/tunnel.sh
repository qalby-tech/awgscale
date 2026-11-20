#!/usr/bin/env bash
# =============================================================================
# app/tests/tunnel.sh — the computer's tunnel end to end, sealed off: two
# containers on a Docker network with no way out, each in its own network
# namespace (this machine's routes and DNS are never touched).
#   server: app/target/qalby/testserver, the entry's obfuscation, fresh keys,
#           and the app API stand-in on its tunnel address 172.16.17.1:8088
#   client: qalby daemon + qalby up with the config the panel would hand out
# Checked: the handshake, all traffic by the tunnel's table, the app API through
# the tunnel, down gives the network back, a killed service leaves nothing.
# =============================================================================
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=awgscale-app-build:local
RUN=awgtest-$$
docker build -q -t "$IMAGE" "$HERE/docker" >/dev/null
"$HERE/qalby/build.sh" >/dev/null || { echo "tunnel: qalby does not build"; exit 1; }
# the files both containers share (the configs, the logs) live in a volume of the run, gone with it
cleanup() { docker rm -f "$RUN-server" "$RUN-client" >/dev/null 2>&1; docker network rm "$RUN" >/dev/null 2>&1; docker volume rm "$RUN" >/dev/null 2>&1; }
[ -n "${KEEP:-}" ] || trap cleanup EXIT
docker network create --internal "$RUN" >/dev/null
docker volume create "$RUN" >/dev/null
start() {
    docker run -d --name "$RUN-$1" --network "$RUN" --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW --device /dev/net/tun \
        --sysctl net.ipv6.conf.all.disable_ipv6=0 --sysctl net.ipv4.conf.all.src_valid_mark=1 -v "$HERE/target/qalby":/qalby:ro -v "$HERE/tests":/tests:ro -v "$RUN":/shared \
        "$IMAGE" sleep infinity >/dev/null
}
start server; start client
inside() { local who=$1; shift; docker exec "$RUN-$who" "$@"; }
fails=0
check() { if eval "$2" >/dev/null 2>&1; then echo "ok $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

server_ip=$(inside server hostname -i | awk '{print $1}')
inside server /qalby/testserver make-confs /shared "$server_ip:51820"
inside server sh -c '/qalby/testserver serve /shared > /shared/server.log 2>&1 &'
inside server sh -c 'for i in $(seq 1 50); do ip addr show awgsrv0 2>/dev/null | grep -q 172.16.17.1 && break; sleep 0.1; done; python3 /tests/standin.py 172.16.17.1 > /shared/standin.log 2>&1 &'
inside client sh -c 'AWGSCALE_SOCKET=/run/awgscale/control.sock /qalby/qalby daemon > /shared/daemon.log 2>&1 &'
sleep 1

up=$(inside client /qalby/qalby up /shared/client.conf 2>&1)
check "the tunnel is up after a handshake" "echo '$up' | grep -q '\"state\":\"up\"'"
check "all IPv4 goes by the tunnel's table" "inside client ip -4 route get 203.0.113.9 | grep -q 'dev awgscale0 table 51830'"
check "the server itself is reached around the tunnel" "inside client ip -4 route get $server_ip mark 51830 | grep -qv awgscale0"
check "IPv6 goes into the tunnel, not around it" "inside client ip -6 route show table 51830 | grep -q 'default dev awgscale0'"
check "the app API answers through the tunnel" "inside client curl -sf --max-time 5 http://172.16.17.1:8088/app/api/me | grep -q test_laptop"
check "the exits come through the tunnel too" "inside client curl -sf --max-time 5 -o /dev/null http://172.16.17.1:8088/app/api/exits"
check "status says up" "inside client /qalby/qalby status | grep -q '\"state\":\"up\"'"
check "a config that would run a command is refused" "! printf '[Interface]\nPostUp = touch /shared/owned\n' | inside client /qalby/qalby up - ; ! inside client test -e /shared/owned"

inside client /qalby/qalby down >/dev/null
check "down: no interface" "! inside client ip link show awgscale0"
check "down: no rules" "! inside client ip rule show | grep -q 51830"
check "down: no firewall table" "! inside client nft list table inet awgscale"

inside client /qalby/qalby up /shared/client.conf >/dev/null 2>&1
inside client sh -c 'pkill -9 -f "qalby daemon"'; sleep 0.5
check "a killed service leaves its rules (as expected)" "inside client ip rule show | grep -q 51830"
inside client sh -c 'AWGSCALE_SOCKET=/run/awgscale/control.sock /qalby/qalby daemon >> /shared/daemon.log 2>&1 &'; sleep 1
check "the next start takes them away" "! inside client ip rule show | grep -q 51830 && ! inside client ip link show awgscale0"

inside server sh -c "sed 's/^PrivateKey = .*/PrivateKey = YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=/' /shared/client.conf > /shared/wrong.conf"
check "a key the server does not know: a plain refusal, the network given back" \
    "[[ \$(inside client /qalby/qalby up /shared/wrong.conf 2>&1) == *'сервер не ответил'* ]] && ! inside client ip link show awgscale0"

[ $fails -eq 0 ] && echo "app tunnel: ok" || { echo "app tunnel: $fails FAILED"; inside server sh -c "tail -n 5 /shared/*.log"; exit 1; }
