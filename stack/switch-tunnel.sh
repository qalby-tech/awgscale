#!/usr/bin/env bash
# =============================================================================
# switch-tunnel.sh — run ON THE OPERATOR MACHINE. Hands a tunnel on the entry server from
# the host's awg-quick units to a container, or back.
#
#   stack/switch-tunnel.sh status
#   stack/switch-tunnel.sh on  [awg0|links]   the container takes the running interface(s) over
#   stack/switch-tunnel.sh off [awg0|links]   the host units are in charge again
#
#   awg0  (the default) the users' tunnel   → container vpn-tunnel
#   links the tunnels to the exits (awgk*)  → container vpn-links
#
# NEITHER direction disconnects anybody: the interfaces that are running stay
# exactly as they are. What changes is who brings them up after the NEXT boot:
#   on  — the container is started (it takes the interfaces over and checks them
#         against the files), and only if it reports everything as it should be
#         the host units are switched off for the next boot (disabled, NOT stopped);
#   off — the host units are enabled again (not started: the interfaces are there),
#         the container is stopped (that leaves the interfaces).
# The new boot order is proven only by a reboot — do that one under the safety
# net (ops/reboot-entry.sh go): its rollback puts the tunnels back to the
# host units by itself.
# Run stack/deploy.sh tunnel (or links) first: it ships the service's image
# (awgscale-tunnel:local) and takes the compose file that names it into use.
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
SSH=(ssh -F "$HERE/ssh_config" -o BatchMode=yes entry)
WHAT=${2:-awg0}
case "$WHAT" in awg0|links) ;; *) echo "Usage: $0 status | on [awg0|links] | off [awg0|links]" >&2; exit 1 ;; esac

if [ "$WHAT" = links ] && [ "${1:-}" != status ]; then
case "${1:-}" in
    on)
        "${SSH[@]}" 'set -e; cd /opt/vpn-stack
docker image inspect awgscale-tunnel:local >/dev/null 2>&1 && grep -q "image: awgscale-tunnel:local" compose.yml || { echo "the links service is not on the server — run stack/deploy.sh links first" >&2; exit 1; }
image_ipt=$(docker run --rm --network none --entrypoint iptables awgscale-tunnel:local --version)
[ "$(iptables --version)" = "$image_ipt" ] || { echo "iptables in the image ($image_ipt) is not the server'"'"'s — not switched" >&2; exit 1; }
names=$(for f in /etc/amnezia/amneziawg/awgk*.conf; do [ -e "$f" ] || continue; f=${f##*/}; echo "${f%.conf}"; done)
if [ -z "$names" ]; then
    # a server with no exit yet (a single server about to get its first one): nothing to take
    # over — the container starts empty and makes each link when the exits service writes its file
    export COMPOSE_PROFILES=links
    docker compose up -d --no-deps links 2>&1 | grep -vE "Running|Pulled|Pulling" || true
    # its check says "nothing to keep is not fine": with no link yet, staying up is the test
    sleep 8
    [ "$(docker inspect -f "{{.State.Running}} {{.RestartCount}}" vpn-links 2>/dev/null)" = "true 0" ] || { echo "vpn-links does not stay up" >&2; docker logs vpn-links --tail 10 2>&1 | cut -c1-200 >&2; docker compose stop -t 20 links >/dev/null 2>&1 || true; exit 1; }
    : > links.enabled
    echo "switched: vpn-links runs with no exit link yet; it makes each one when its file appears"
    exit 0
fi
for n in $names; do ip link show "$n" >/dev/null 2>&1 || { echo "$n is not up — this script only hands over running links" >&2; exit 1; }; done
before=$(for n in $names; do ip -o link show "$n" | cut -d: -f1; done | tr "\n" " ")
export COMPOSE_PROFILES=links
docker compose up -d --no-deps links 2>&1 | grep -vE "Running|Pulled|Pulling" || true
stop_it() { docker compose stop -t 20 links >/dev/null 2>&1 || true; }
ok=0
for i in $(seq 1 30); do
    if docker exec vpn-links python -m awgscale.tunnel check >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
done
if [ "$ok" != 1 ]; then
    echo "the container does NOT report the exit links as the files say — not switched; the container is stopped, nothing else was touched:" >&2
    docker exec vpn-links python -m awgscale.tunnel check 2>&1 | cut -c1-400 >&2 || true
    docker logs vpn-links --tail 10 2>&1 | cut -c1-200 >&2 || true
    stop_it; exit 1
fi
# what the container would give `awg setconf` at the next boot must be what awg-quick gives it today
for n in $names; do
    mine=$(docker exec vpn-links python -m awgscale.tunnel strip-sum "$n")
    theirs=$(awg-quick strip "$n" | python3 -c "import sys, hashlib; print(hashlib.sha256(sys.stdin.read().rstrip(chr(10)).encode()).hexdigest())")
    if [ -z "$mine" ] || [ "$mine" != "$theirs" ]; then
        echo "the container reads $n.conf differently from awg-quick — not switched; the container is stopped, nothing else was touched" >&2
        stop_it; exit 1
    fi
done
[ "$(for n in $names; do ip -o link show "$n" | cut -d: -f1; done | tr "\n" " ")" = "$before" ] || { echo "the links are not the same interfaces as before — STOP and look" >&2; exit 1; }
: > links.enabled
for n in $names; do systemctl disable "awg-quick@$n.service" >/dev/null 2>&1; done
echo "switched: vpn-links keeps" $names "(taken over as they are); the host units are off for the next boot"
docker logs vpn-links --tail 4 2>&1 | cut -c1-200'
        ;;
    off)
        "${SSH[@]}" 'set -e; cd /opt/vpn-stack
for f in /etc/amnezia/amneziawg/awgk*.conf; do [ -e "$f" ] || continue; f=${f##*/}; systemctl enable "awg-quick@${f%.conf}.service" >/dev/null 2>&1; done
if [ -f links.enabled ]; then mv -f links.enabled links.enabled.off; fi
COMPOSE_PROFILES=links docker compose stop -t 20 links >/dev/null 2>&1 || true
echo "back: the host units bring the exit links up at the next boot; the container is stopped"
for f in /etc/amnezia/amneziawg/awgk*.conf; do [ -e "$f" ] || continue; f=${f##*/}; n=${f%.conf}; echo "link $n: $(ip link show "$n" >/dev/null 2>&1 && echo present || echo ABSENT), unit $(systemctl is-active "awg-quick@$n.service" 2>&1)"; done'
        ;;
    *) echo "Usage: $0 status | on [awg0|links] | off [awg0|links]" >&2; exit 1 ;;
esac
exit 0
fi

case "${1:-}" in
    status)
        "${SSH[@]}" 'cd /opt/vpn-stack
echo "switched to the container: $([ -f tunnel.enabled ] && echo yes || echo no)"
echo "host unit awg-quick@awg0: $(systemctl is-enabled awg-quick@awg0.service 2>&1), $(systemctl is-active awg-quick@awg0.service 2>&1)"
echo "container vpn-tunnel: $(docker inspect -f "{{.State.Status}}" vpn-tunnel 2>/dev/null || echo none)"
echo "interface awg0: $(ip -4 -o addr show dev awg0 2>/dev/null | awk "{print \$4}" || true) $(ip link show awg0 >/dev/null 2>&1 && echo present || echo ABSENT)"
echo "exit links switched to the container: $([ -f links.enabled ] && echo yes || echo no)"
echo "container vpn-links: $(docker inspect -f "{{.State.Status}}" vpn-links 2>/dev/null || echo none)"
for f in /etc/amnezia/amneziawg/awgk*.conf; do [ -e "$f" ] || continue; f=${f##*/}; n=${f%.conf}; echo "link $n: $(ip link show "$n" >/dev/null 2>&1 && echo present || echo ABSENT); host unit $(systemctl is-enabled "awg-quick@$n.service" 2>&1), $(systemctl is-active "awg-quick@$n.service" 2>&1)"; done'
        ;;
    on)
        "${SSH[@]}" 'set -e; cd /opt/vpn-stack
docker image inspect awgscale-tunnel:local >/dev/null 2>&1 && grep -q "image: awgscale-tunnel:local" compose.yml || { echo "the tunnel service is not on the server — run stack/deploy.sh tunnel first" >&2; exit 1; }
ip link show awg0 >/dev/null 2>&1 || { echo "awg0 is not up — this script only hands over a running tunnel" >&2; exit 1; }
before=$(ip -o link show awg0 | cut -d: -f1)
export COMPOSE_PROFILES=tunnel
image_ipt=$(docker run --rm --network none --entrypoint iptables awgscale-tunnel:local --version)
[ "$(iptables --version)" = "$image_ipt" ] || { echo "iptables in the image ($image_ipt) is not the server'"'"'s — not switched" >&2; exit 1; }
docker compose up -d --no-deps tunnel 2>&1 | grep -vE "Running|Pulled|Pulling" || true
ok=0
for i in $(seq 1 30); do
    if docker exec vpn-tunnel python -m awgscale.tunnel check >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
done
if [ "$ok" != 1 ]; then
    echo "the container does NOT report the tunnel as the file says — not switched; the container is stopped, nothing else was touched:" >&2
    docker exec vpn-tunnel python -m awgscale.tunnel check 2>&1 | cut -c1-300 >&2 || true
    docker logs vpn-tunnel --tail 10 2>&1 | cut -c1-200 >&2 || true
    docker compose stop -t 20 tunnel >/dev/null 2>&1 || true
    exit 1
fi
# what the container would give `awg setconf` at the next boot must be what awg-quick gives it today
mine=$(docker exec vpn-tunnel python -m awgscale.tunnel strip-sum)
theirs=$(awg-quick strip awg0 | python3 -c "import sys, hashlib; print(hashlib.sha256(sys.stdin.read().rstrip(chr(10)).encode()).hexdigest())")
if [ -z "$mine" ] || [ "$mine" != "$theirs" ]; then
    echo "the container reads awg0.conf differently from awg-quick — not switched; the container is stopped, nothing else was touched" >&2
    docker compose stop -t 20 tunnel >/dev/null 2>&1 || true
    exit 1
fi
[ "$(ip -o link show awg0 | cut -d: -f1)" = "$before" ] || { echo "awg0 is not the same interface as before — STOP and look" >&2; exit 1; }
: > tunnel.enabled
systemctl disable awg-quick@awg0.service >/dev/null 2>&1
echo "switched: vpn-tunnel keeps awg0 (taken over as it is); the host unit is off for the next boot"
docker logs vpn-tunnel --tail 3 2>&1 | cut -c1-200'
        ;;
    off)
        "${SSH[@]}" 'set -e; cd /opt/vpn-stack
systemctl enable awg-quick@awg0.service >/dev/null 2>&1
if [ -f tunnel.enabled ]; then mv -f tunnel.enabled tunnel.enabled.off; fi
COMPOSE_PROFILES=tunnel docker compose stop -t 20 tunnel >/dev/null 2>&1 || true
echo "back: the host unit brings awg0 up at the next boot; the container is stopped"
echo "interface awg0: $(ip link show awg0 >/dev/null 2>&1 && echo present || echo ABSENT)"
# an interface made by the container is not known to the host unit as its own: say so
if ip link show awg0 >/dev/null 2>&1 && ! systemctl is-active -q awg-quick@awg0.service; then
    echo "NOTE: awg0 was made by the container, so the host unit shows inactive. It works; the unit takes over at the next boot."
fi'
        ;;
    *) echo "Usage: $0 status | on [awg0|links] | off [awg0|links]" >&2; exit 1 ;;
esac
