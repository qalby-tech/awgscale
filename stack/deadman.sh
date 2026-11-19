#!/usr/bin/env bash
# =============================================================================
# deadman.sh — the safety net for a reboot of the entry server.
#
# Whoever reboots the server after changing what it boots through may not be
# able to come back if that path is broken: this machine's own DNS goes through
# the entry. So before a reboot the net is armed; after the boot it waits for a
# word from outside ("I reached you and everything works"), and if the word does
# not come in time it puts things back to what the last CONFIRMED boot ran:
# the gate's code of that boot, the tunnels on the host's awg-quick units, and
# the containers the way back in depends on started again.
# There is no second implementation of the rules to fall back to: the rollback
# retries the proven code.
#
#   deadman.sh arm        before the reboot (valid for one boot, within VALID seconds)
#   deadman.sh confirm    from outside, after the boot: all is well, do nothing
#   deadman.sh fire       run by vpn-deadman.timer some minutes after every boot
#   deadman.sh disarm     changed my mind
#   deadman.sh status     armed or not, and what the checks say
#
# What `fire` does, in this order:
#   not armed                         → nothing
#   armed, but no reboot since then   → nothing (stays armed)
#   then it disarms itself — whatever follows happens once, never in a loop
#   armed too long ago                → nothing
#   confirmed in this boot            → nothing
#   otherwise                         → rollback, on the spot, without a reboot
#
# A host script on purpose (bash + systemd only): it must work when Docker,
# DNS and the stack's own code are what is broken.
# It cannot help if the server does not boot at all — that is the provider's console.
# =============================================================================
set -u
DIR=${DM_DIR:-/opt/vpn-stack/boot}                 # root-only, mounted in no container
RUN=${DM_RUN:-/run}                                # emptied by every boot: an old confirmation cannot count
UNITS=${DM_UNITS:-/etc/systemd/system}
BOOT_ID_FILE=${DM_BOOT_ID:-/proc/sys/kernel/random/boot_id}
VALID=${DM_VALID:-2400}                            # an arming older than this is ignored
DRY=${DM_DRY:-}                                    # 1: print the rollback's commands instead of running them
STACK=${DM_STACK:-/opt/vpn-stack}
AWG_DIR=${DM_AWG_DIR:-/etc/amnezia/amneziawg}       # the exit links are the files awgk*.conf there
CASCADE_CONF=${DM_CASCADE_CONF:-/root/awg/cascade/cascade.conf}   # SINGLE_SERVER=1 there: a server without exit links
ARMED=$DIR/deadman.armed
GATE=$DIR/gate.pyz                                  # what cascade-apply.service runs at boot (awgscale.watch.bundle)
PROVEN=$DIR/gate.pyz.proven                         # the copy of it a confirmed boot ran
LAST=$DIR/deadman.last
OK=$RUN/vpn-deadman.ok
DNS_AT=172.16.17.1

say() { echo "deadman: $*"; [ -n "$DRY" ] || logger -t vpn-deadman -- "$*" 2>/dev/null || true; }
run() { if [ -n "$DRY" ]; then echo "would run: $*"; else "$@"; fi; }
boot_id() { cat "$BOOT_ID_FILE"; }
field() { sed -n "s/^$1=//p" "$ARMED" 2>/dev/null | head -1; }

dns_answers() {
    python3 - "$DNS_AT" <<'PY' 2>/dev/null
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

link_names() {
    local f found=
    for f in "$AWG_DIR"/awgk*.conf; do [ -e "$f" ] || continue; f=${f##*/}; echo "${f%.conf}"; found=1; done
    # no files: the links of a cascade are missing (they must be up), a single server has none
    [ -n "$found" ] || grep -qx 'SINGLE_SERVER=1' "$CASCADE_CONF" 2>/dev/null || printf 'awgk1\nawgk2\n'
}

links_up() { local n; for n in $(link_names); do ip link show "$n" >/dev/null 2>&1 || return 1; done; }

# The tunnel service is asked whether it keeps the interfaces as the files say.
tunnel_check() { timeout 30 docker exec "$1" python -m awgscale.tunnel check; }

running() { [ "$(timeout 20 docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }

# What the server can tell about itself. Informational: the decision to roll
# back is made by the missing confirmation alone, not by these.
checks() {
    local bad=0 line
    one() { if eval "$2" >/dev/null 2>&1; then line="ok   $1"; else line="FAIL $1"; bad=1; fi; echo "  $line"; }
    one "client tunnel awg0 has its address"        "ip -4 addr show dev awg0 | grep -q 'inet $DNS_AT/'"
    one "exit tunnels are up"                       "links_up"
    one "routing rules are in (chain, sets, rules)" "iptables -t mangle -S CASCADE | grep -q . && ipset list -n | grep -qx ru && ip rule | grep -q 'lookup 298'"
    one "the exits are routed (not fail-closed)"    "ip route show table 201 | grep -q '^default dev' || ip route show table 203 | grep -q '^default dev'"
    one "DNS answers at $DNS_AT"                    "dns_answers"
    one "traefik and smartdns are running"          "running traefik && running smartdns"
    one "watch, resolver, agent are running"        "running vpn-watch && running vpn-resolver && running vpn-agent"
    if [ -f "$STACK/tunnel.enabled" ]; then
        one "the tunnel service keeps awg0 as the file says" "running vpn-tunnel && tunnel_check vpn-tunnel"
    fi
    if [ -f "$STACK/links.enabled" ]; then
        one "the links service keeps the exit links as the files say" "running vpn-links && tunnel_check vpn-links"
    fi
    return $bad
}

rollback() {
    say "ROLLBACK: no confirmation after the boot — the proven gate code, the tunnels back to the host units"
    # 1. the gate's code as the last confirmed boot ran it (the replaced one is kept beside it)
    if [ -f "$PROVEN" ] && ! cmp -s "$PROVEN" "$GATE"; then
        run cp -p "$GATE" "$GATE.replaced"
        run cp -p "$PROVEN" "$GATE"
    elif [ ! -f "$PROVEN" ]; then
        say "WARN: no proven copy of the gate code — the rules are applied by the code that is there"
    fi
    # 1a. the users' tunnel back to the host's awg-quick, if the server was switched to the tunnel
    #     service: the service is stopped (that leaves the interface), the interface it made is
    #     taken down the awg-quick way (with its firewall rules, so they are not doubled), and the
    #     host unit is the one that brings it up from now on
    if [ -f "$STACK/tunnel.enabled" ]; then
        say "the tunnel goes back from the container to awg-quick on the host"
        run timeout 60 docker stop -t 20 vpn-tunnel || true
        run mv -f "$STACK/tunnel.enabled" "$STACK/tunnel.enabled.rolled-back"
        run systemctl enable awg-quick@awg0.service || true
        if [ -n "$DRY" ] || { ip link show awg0 >/dev/null 2>&1 && ! systemctl is-active -q awg-quick@awg0.service; }; then
            run awg-quick down awg0 || run ip link del awg0 || true
        fi
    fi
    # 1b. the same for the links to the exits. What the container made is not trusted here (this runs
    #     because something is wrong): each such link is removed and made again by its host unit below
    local n
    if [ -f "$STACK/links.enabled" ]; then
        say "the exit links go back from the container to awg-quick on the host"
        run timeout 60 docker stop -t 20 vpn-links || true
        run mv -f "$STACK/links.enabled" "$STACK/links.enabled.rolled-back"
        for n in $(link_names); do
            run systemctl enable "awg-quick@$n.service" || true
            if [ -n "$DRY" ] || { ip link show "$n" >/dev/null 2>&1 && ! systemctl is-active -q "awg-quick@$n.service"; }; then
                run ip link del "$n" || true
            fi
        done
    fi
    # 2. the rules by the old code, then the tunnels (restarting the gate takes awg0 down: allowed here only)
    run systemctl restart cascade-apply.service || true
    for n in $(link_names); do run systemctl start "awg-quick@$n.service" || true; done
    run systemctl restart awg-quick@awg0.service || true
    # 3. somebody must route the exits: Docker, and in it the watch container
    if [ -n "$DRY" ] || ! systemctl is-active -q docker.service; then run systemctl start docker.service || true; fi
    if [ -n "$DRY" ] || ! running vpn-watch; then
        say "the watch container is not running — started"
        run timeout 90 docker start vpn-watch || true
    fi
    # 4. DNS: the resolver's container; if it stays silent it is restarted
    local i up=
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if [ -z "$DRY" ] && dns_answers; then up=1; break; fi
        [ -n "$DRY" ] || sleep 3
    done
    if [ -z "$up" ]; then
        say "DNS does not answer — the resolver container is restarted"
        run timeout 120 docker restart -t 40 vpn-resolver || true
    fi
    # 5. what the way back in depends on
    run timeout 90 docker start traefik smartdns || true
    if [ -z "$DRY" ]; then
        sleep 20
        if checks > "$DIR/deadman.result" 2>&1; then say "after the rollback: all checks pass"; else say "after the rollback: some checks FAIL — see $DIR/deadman.result"; fi
        cat "$DIR/deadman.result"
    fi
}

case "${1:-}" in
    arm)
        if [ -z "$DRY" ]; then
            systemctl is-enabled -q vpn-deadman.timer || { echo "vpn-deadman.timer is not enabled — not armed" >&2; exit 1; }
        fi
        printf 'boot=%s\nuntil=%s\n' "$(boot_id)" "$(( $(date +%s) + VALID ))" > "$ARMED.new" && mv -f "$ARMED.new" "$ARMED"
        say "armed for the next boot (valid $(( VALID / 60 )) min)"
        ;;
    confirm)
        # not armed any more = the deadline has passed (or it never was): the caller must not think it confirmed
        [ -f "$ARMED" ] || { echo "deadman: not armed — nothing to confirm" >&2; exit 2; }
        [ "$(field boot)" != "$(boot_id)" ] || { echo "deadman: no reboot since arming — not confirmed" >&2; exit 1; }
        : > "$OK" && say "confirmed from outside: no rollback"
        # this boot worked: its gate code is what a later rollback goes back to
        if [ -f "$GATE" ]; then cp -p "$GATE" "$PROVEN.new" && mv -f "$PROVEN.new" "$PROVEN"; fi
        ;;
    fire)
        [ -f "$ARMED" ] || exit 0
        if [ "$(field boot)" = "$(boot_id)" ]; then echo "deadman: armed, no reboot yet"; exit 0; fi
        until=$(field until)
        mv -f "$ARMED" "$LAST"                       # once: nothing below can happen twice
        if [ "$(date +%s)" -gt "${until:-0}" ]; then say "the arming is too old — ignored"; exit 0; fi
        if [ -f "$OK" ]; then say "the boot was confirmed from outside — nothing to do"; exit 0; fi
        rollback
        ;;
    disarm)
        if [ -f "$ARMED" ]; then mv -f "$ARMED" "$LAST"; say "disarmed"; else echo "deadman: not armed"; fi
        ;;
    status)
        if [ -f "$ARMED" ]; then echo "armed: yes (reboot since: $([ "$(field boot)" = "$(boot_id)" ] && echo no || echo yes); valid for $(( $(field until) - $(date +%s) )) s more; confirmed: $([ -f "$OK" ] && echo yes || echo no))"
        else echo "armed: no"; fi
        checks
        ;;
    *) echo "Usage: $0 arm | confirm | fire | disarm | status" >&2; exit 1 ;;
esac
