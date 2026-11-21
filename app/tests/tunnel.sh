#!/usr/bin/env bash
# =============================================================================
# app/tests/tunnel.sh — the computer's tunnel end to end, sealed off: containers
# on a Docker network with no way out, each in its own network namespace (this
# machine's routes and DNS are never touched).
#   server:  app/target/qalby/testserver with the entry's obfuscation and fresh
#            keys, and the app API stand-in on its tunnel address 172.16.17.1:8088
#   client:  qalby daemon at its usual socket, driven by qalby and by the app
#   noipv6:  a client on a machine with IPv6 switched off
# Run app/build.sh first: the app checked here is the one the .deb carries.
# =============================================================================
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=awgscale-app-build:local
RUN=awgtest-$$
APP=$HERE/target/release/awgscale-app
docker build -q -t "$IMAGE" "$HERE/docker" >/dev/null
"$HERE/qalby/build.sh" >/dev/null || { echo "tunnel: qalby does not build"; exit 1; }
[ -x "$APP" ] || { echo "tunnel: no app built (app/build.sh)"; exit 1; }
# the files the containers share (the configs, the logs) live in a volume of the run, gone with it
cleanup() {
    docker rm -f "$RUN-server" "$RUN-client" "$RUN-noipv6" >/dev/null 2>&1
    docker network rm "$RUN" >/dev/null 2>&1; docker volume rm "$RUN" >/dev/null 2>&1
}
[ -n "${KEEP:-}" ] || trap cleanup EXIT
docker network create --internal "$RUN" >/dev/null
docker volume create "$RUN" >/dev/null
start() {   # <name> <IPv6 off: 0|1> [more docker flags]
    local name=$1 off=$2; shift 2
    docker run -d --name "$RUN-$name" --network "$RUN" --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW \
        --cap-add CHOWN --cap-add SETUID --cap-add SETGID --device /dev/net/tun \
        --sysctl net.ipv6.conf.all.disable_ipv6=$off --sysctl net.ipv4.conf.all.src_valid_mark=1 \
        -v "$HERE/target/qalby":/qalby:ro -v "$HERE/tests":/tests:ro -v "$APP":/usr/bin/awgscale-app:ro -v "$RUN":/shared \
        -e AWGSCALE_HANDSHAKE_FRESH=8 "$@" "$IMAGE" sleep infinity >/dev/null
}
start server 0; start client 0; start noipv6 1
inside() { local who=$1; shift; docker exec "$RUN-$who" "$@"; }
fails=0
check() { if eval "$2" >/dev/null 2>&1; then echo "ok $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
daemon() { inside "$1" sh -c '/qalby/qalby daemon >> /shared/daemon-'"$1"'.log 2>&1 &'; sleep 1; }
status() { inside client /qalby/qalby status 2>&1; }

server_ip=$(inside server hostname -i | awk '{print $1}')
inside server /qalby/testserver make-confs /shared "$server_ip:51820"
inside server sh -c '/qalby/testserver serve /shared > /shared/server.log 2>&1 &'
inside server sh -c 'for i in $(seq 1 50); do ip addr show awgsrv0 2>/dev/null | grep -q 172.16.17.1 && break; sleep 0.1; done
    python3 /tests/standin.py 172.16.17.1 > /shared/standin.log 2>&1 &'
# a VPN of someone else's (wg-quick's rules) and the people of this computer: one may run the tunnel, one may not
inside client ip rule add table main suppress_prefixlength 0
inside client ip rule add not fwmark 4242 table 4242
inside client sh -c 'groupadd awgscale && useradd -M -G awgscale member && useradd -M outsider'
# a home router for the client: the server's container (a network with no way out has no default route)
inside client ip route add default via "$server_ip"
daemon client

up=$(inside client /qalby/qalby up /shared/client.conf 2>&1)
check "the tunnel is up after a handshake" "[[ '$up' == *'\"state\":\"up\"'* ]]"
check "all IPv4 goes by the tunnel's table" "inside client ip -4 route get 203.0.113.9 | grep -q 'dev awgscale0 table 51830'"
check "the server itself is reached around the tunnel" "inside client ip -4 route get $server_ip mark 51830 | grep -q 'dev eth0'"
check "IPv6 goes into the tunnel, not around it" "inside client ip -6 route show table 51830 | grep -q 'default dev awgscale0'"
check "a local network goes around the tunnel (by default)" "! inside client ip -4 route get 10.96.0.10 | grep -q awgscale0"
check "the network's own subnet stays in the tunnel" "inside client ip -4 route get 172.16.17.9 | grep -q 'dev awgscale0'"
check "the app API answers through the tunnel" "inside client curl -sf --max-time 5 http://172.16.17.1:8088/app/api/me | grep -q test_laptop"
check "the exits come through the tunnel too" "inside client curl -sf --max-time 5 -o /dev/null http://172.16.17.1:8088/app/api/exits"
check "without systemd-resolved the status says the DNS is not the network's" "[[ \$(status) == *'\"dns\":\"unmanaged\"'* ]]"
check "a member of the group may ask the service" "inside client setpriv --reuid=member --regid=member --init-groups /qalby/qalby status"
check "anybody else may not" "[[ \$(inside client setpriv --reuid=outsider --regid=outsider --init-groups /qalby/qalby status 2>&1) == *'нет прав'* ]]"
check "a config that would run a command is refused" \
    "printf '[Interface]\nPostUp = touch /shared/owned\n' | docker exec -i $RUN-client /qalby/qalby up - ; [[ \$(status) == *'\"state\":\"up\"'* ]] && ! inside client test -e /shared/owned"

# a packet made to leave by eth0, around the tunnel: it goes without the kill switch, not with it
around='python3 -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b\"eth0\"); s.sendto(b\"x\", (\"203.0.113.9\", 9))"'
check "without the kill switch a packet may leave around the tunnel" "inside client sh -c '$around'"
local='python3 -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b\"eth0\"); s.sendto(b\"x\", (\"10.96.0.10\", 9))"'
inside client sh -c 'AWGSCALE_KILL_SWITCH=1 AWGSCALE_AROUND=198.51.100.7 /qalby/qalby up /shared/client.conf' >/dev/null 2>&1
check "with the kill switch it may not" "! inside client sh -c '$around'"
check "with the kill switch a local network is still reached around it" "inside client sh -c '$local'"
check "an address of the person's list goes around the tunnel" "! inside client ip -4 route get 198.51.100.7 | grep -q awgscale0"
check "with the kill switch the tunnel still works" "inside client curl -sf --max-time 5 -o /dev/null http://172.16.17.1:8088/app/api/exits"
inside client /qalby/qalby down >/dev/null
check "down: no interface" "! inside client ip link show awgscale0"
check "down: no rules of the tunnel" "! inside client ip rule show | grep -qE '^3189[01]:'"
check "down: no firewall table, no kill switch" "! inside client nft list table inet awgscale && inside client sh -c '$around'"
check "down: the other VPN's rules are still there" "[ \$(inside client ip rule show | grep -cE 'suppress_prefixlength 0|lookup 4242') -eq 2 ]"

inside client /qalby/qalby up /shared/client.conf >/dev/null 2>&1
inside client pkill -9 -f "qalby daemon"; sleep 0.5
check "a killed service leaves its rules (as expected)" "inside client ip rule show | grep -qE '^3189[01]:'"
daemon client
check "the next start takes them away" "! inside client ip rule show | grep -qE '^3189[01]:' && ! inside client ip link show awgscale0"
check "and leaves the other VPN's rules alone" "[ \$(inside client ip rule show | grep -cE 'suppress_prefixlength 0|lookup 4242') -eq 2 ]"

inside server sh -c "sed 's/^PrivateKey = .*/PrivateKey = YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=/' /shared/client.conf > /shared/wrong.conf"
check "a key the server does not know: a plain refusal, the network given back" \
    "[[ \$(inside client /qalby/qalby up /shared/wrong.conf 2>&1) == *'сервер не ответил'* ]] && ! inside client ip link show awgscale0"

# IPv6 switched off on the machine: the tunnel still comes up, IPv4 only
daemon noipv6
check "IPv6 off: the tunnel comes up" "[[ \$(inside noipv6 /qalby/qalby up /shared/client.conf 2>&1) == *'\"state\":\"up\"'* ]]"
inside noipv6 /qalby/qalby down >/dev/null 2>&1

# the app the .deb carries, against the service: a made-up profile with the real config, one click
inside client sh -c 'mkdir -p /root/.local/share/tech.qalby.awgscale && python3 -c "
import json; conf = open(\"/shared/client.conf\").read()
json.dump({\"host\": \"vpn.example.org\", \"name\": \"test_laptop\", \"address\": \"172.16.17.15\", \"entry\": \"172.16.17.1\", \"owner\": True, \"conf\": conf},
          open(\"/root/.local/share/tech.qalby.awgscale/network.json\", \"w\"))"'
inside client sh -c 'Xvfb :9 -screen 0 420x820x24 >/dev/null 2>&1 & sleep 1
    DISPLAY=:9 WEBKIT_DISABLE_COMPOSITING_MODE=1 HOME=/root awgscale-app > /shared/app.log 2>&1 &'
sleep 6
inside client sh -c 'DISPLAY=:9 xdotool mousemove 210 200 click 1'
for i in $(seq 1 40); do [[ $(status) == *'"state":"up"'* ]] && break; sleep 0.5; done
sleep 3
inside client sh -c 'DISPLAY=:9 import -window root /shared/app-connected.png'
mkdir -p "$HERE/target/screens"; docker cp -q "$RUN-client:/shared/app-connected.png" "$HERE/target/screens/7-real-tunnel.png" 2>/dev/null
check "the app's button brings the service's tunnel up" "[[ \$(status) == *'\"state\":\"up\"'* ]]"
check "the app asked the network through the tunnel, as the device" "inside server grep -q 'get /app/api/me 172.16.17.15' /shared/standin.log"

# the server goes away: the tunnel is no longer called up
inside server pkill -f "testserver serve"
sleep 12
check "the server gone: the status no longer says up" "[[ \$(status) == *'\"state\":\"connecting\"'* ]]"

[ $fails -eq 0 ] && echo "app tunnel: ok" || { echo "app tunnel: $fails FAILED"; inside server sh -c 'tail -n 5 /shared/*.log'; exit 1; }
