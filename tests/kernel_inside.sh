#!/usr/bin/env bash
# Runs INSIDE a throwaway container with its own network namespace (see
# tests/kernel.sh): a pretend entry server with dummy tunnels, on which the
# rules are installed by the real ip / ipset / iptables into the real kernel.
#   $1 = what is run:  service | service-twice | boot-* (see the case below)
# Prints everything the kernel now holds for the cascade, in a stable form.
set -euo pipefail
WHO=$1
D=/root/awg/cascade
mkdir -p "$D" /run/cascade /usr/local/bin

links() {
    if [ "$WHO" = scoped-packets ]; then        # the users' side is a veth here: packets are sent into it from its other end
        ip link add awg0 type veth peer name c0; ip link set c0 up
    else
        ip link add awg0 type dummy
    fi
    ip addr add 172.16.17.1/24 dev awg0; ip link set awg0 up
    ip link add awgk1 type dummy; ip addr add 10.77.1.2/32 dev awgk1;  ip link set awgk1 up
    ip link add awgk2 type dummy; ip addr add 10.77.2.2/32 dev awgk2;  ip link set awgk2 up
    # the installer's rule that isolates the users from each other
    iptables -A FORWARD -i awg0 -o awg0 -j DROP
}
# boot-*: the server has just started — no tunnels yet, nothing in /run
case "$WHO" in boot-*) rmdir /run/cascade 2>/dev/null || true ;; *) links ;; esac

K() { printf '%02dkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk=' "$1"; }
cat > /usr/local/bin/awg <<EOF
#!/bin/sh
[ "\$1 \$2 \$3" = "show awg0 allowed-ips" ] && printf '%s\t172.16.17.2/32\n%s\t172.16.17.3/32\n%s\t172.16.17.4/32\n%s\t172.16.17.5/32\n%s\t172.16.17.6/32\n' $(K 2) $(K 3) $(K 4) $(K 5) $(K 6)
exit 0
EOF
# exit 1 answers, exit 3 does not
cat > /usr/local/bin/ping <<'EOF'
#!/bin/sh
case "$*" in *"-I 10.77.1.2"*) exit 0 ;; esac
exit 1
EOF
chmod +x /usr/local/bin/awg /usr/local/bin/ping

cat > "$D/cascade.conf" <<'EOF'
CLIENT_IF="awg0"
CLIENT_SUBNET="172.16.17.0/24"
UDP_VIA_EXIT=1
EXITS=(
    "1:awgk1:10.77.1.2:exit1-nl:2"
    "3:awgk2:10.77.2.2:exit2-nl:1"
)
DNS_VIA_EXIT=(1.1.1.1 1.0.0.1 9.9.9.9)
VIA_EXIT_SUBNETS=(172.30.99.0/24)
PROBE_TARGETS=(1.1.1.1 9.9.9.9)
EXTRA_RU_NETS="203.0.113.0/24"
EOF
for i in $(seq 0 255); do for j in 0 64 128 192; do echo "5.$i.$j.0/18"; done; done > "$D/ru.zone"
printf 'chatgpt.com\n149.154.160.0/20\n*.example.org\n91.108.4.1\n' > "$D/domains-exit.txt"
printf '# Telegram\n91.108.56.0/22\n185.76.151.0/24\n' > "$D/nets-auto.txt"
printf 'add force_exit 1.2.3.4\nadd force_exit 5.6.7.8\n' > "$D/force_exit.save"
printf '172.16.17.2 3\n172.16.17.9 1\n172.16.17.4 7\n' > "$D/pins.map"
printf 'sp_ab12 172.16.17.2,%s 172.16.17.3,%s 172.16.17.9,%s\nsp_cd34 172.16.17.5,%s 172.16.17.6,%s\n' "$(K 2)" "$(K 3)" "$(K 9)" "$(K 5)" "$(K 6)" > "$D/spaces.sets"
[ "${MODE:-}" = geo ] && echo geo > "$D/route.mode"
# a single server: no exits, no country list (the gate must not need one), the same spaces
if [ "${MODE:-}" = single ] || [ "${MODE:-}" = single-with-exit ]; then
    cat > "$D/cascade.conf" <<'EOF2'
CLIENT_IF="awg0"
CLIENT_SUBNET="172.16.17.0/24"
UDP_VIA_EXIT=0
EXITS=(
)
DNS_VIA_EXIT=()
VIA_EXIT_SUBNETS=()
PROBE_TARGETS=()
EXTRA_RU_NETS=""
SINGLE_SERVER=1
EOF2
    rm -f "$D/ru.zone"
    # an exit named beside SINGLE_SERVER=1 is a mistake the gate refuses
    [ "$MODE" = single-with-exit ] && sed -i 's|^EXITS=($|EXITS=(\n    "1:awgk1:10.77.1.2:exit1:1"|' "$D/cascade.conf"
fi

# rules for some groups that go abroad or directly (watch/scoped.py), as the agent compiles them
case "$WHO" in
    scoped|boot-scoped|scoped-move|scoped-packets)
        printf 'a.example/force_exit\nm.a.example/force_exit,rs_aaaaaa\nd.example/rs_dddddd\n' > "$D/domains.ipset"
        printf 'rs_aaaaaa direct rg_g00001\nrs_dddddd exit rg_g00001 rg_g00002\n' > "$D/scoped.rules"
        printf 'rg_g00001 172.16.17.2 172.16.17.9\nrg_g00002 172.16.17.4\n' > "$D/scoped.members"
        printf 'add rs_aaaaaa 198.51.100.77\nadd rs_gone00 198.51.100.78\n' > "$D/scoped.save" ;;
    boot-garbage)
        printf 'a.example/force_exit\nm.a.example/force_exit,rs_aaaaaa\n' > "$D/domains.ipset"
        printf 'rs_aaaaaa sideways rg_g00001\n\001garbage\n' > "$D/scoped.rules"
        printf 'rg_g00001 not-an-address\n' > "$D/scoped.members" ;;
esac

# the watch service's engine (src/awgscale/watch), asked the way its own jobs ask it; standard library only
WATCH="env PYTHONPATH=/src/src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -S -c"
via_service() { $WATCH 'from awgscale.watch.cascade import Cascade; server = Cascade()
server.apply()
for _ in range(3): server.tick()
server.tick(probe=False)' >/dev/null; }

dump() {
    echo "== iptables"; iptables-save | grep -v '^#' | sed -E 's/\[[0-9]+:[0-9]+\]//'
    echo "== ipset";    ipset save | grep -v '^create' | sort
    echo "== ipset names"; ipset list -n | sort
    echo "== rules";    ip rule show
    echo "== routes";   for t in 201 203 298 299; do echo "table $t"; ip route show table $t 2>/dev/null || true; done
    echo "== health";   for f in /run/cascade/exit*; do [ -e "$f" ] && echo "$f $(cat "$f")"; done; echo "sig $(cat /run/cascade/peers.sig 2>/dev/null)"
}

# the boot gate, as cascade-apply.service runs it: one file (awgscale.watch.bundle, made here from the same sources) on the
# system's bare Python, nothing installed and no package on its path (-S: no site-packages at all, -I: nothing from the environment)
boot_service() {
    [ -s /tmp/gate.pyz ] || env PYTHONPATH=/src/src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -S -m awgscale.watch.bundle /tmp/gate.pyz || return 1
    (cd / && /usr/bin/python3 -S -I /tmp/gate.pyz apply >/dev/null 2>&1)
}
case "$WHO" in
    # the boot gate alone, on an empty kernel
    boot-service) boot_service; dump ;;
    # the whole boot: the gate, then the tunnels appear, then the service starts
    boot-then-service) boot_service; links; via_service; dump ;;
    # the boot once the users' tunnel is the container's (the awgscale.tunnel package): the gate, the
    # exit links, then the watch service runs with NO awg0 yet, then the tunnel service makes
    # it, then the watch service goes on. Printed: the kernel, then whether the watch passes held.
    boot-late-tunnel)
        boot_service
        ip link add awgk1 type dummy; ip addr add 10.77.1.2/32 dev awgk1; ip link set awgk1 up
        ip link add awgk2 type dummy; ip addr add 10.77.2.2/32 dev awgk2; ip link set awgk2 up
        early=ok; $WATCH 'from awgscale.watch.cascade import Cascade; server = Cascade()
server.apply()
for _ in range(3): server.tick()' >/dev/null 2>/tmp/early.err || early=FAILED
        cat > /tmp/awg0.conf <<'C'
[Interface]
PrivateKey = PRIVpppppppppppppppppppppppppppppppppppppp=
Address = 172.16.17.1/24
MTU = 1280
ListenPort = 58728
PostUp = iptables -I FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE; iptables -t mangle -A FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240; iptables -t mangle -A FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240; while iptables -D FORWARD -i %i -o %i -j DROP 2>/dev/null; do :; done; iptables -I FORWARD -i %i -o %i -j DROP
C
        # the tunnel service's own look, once: the gate is open, the interface is not there → it is made, with all five of its rules
        made=ok; TUNNEL_CONF=/tmp/awg0.conf TUNNEL_AWG=/usr/local/bin/awg TUNNEL_LINK_TYPE=dummy python3 -c 'import asyncio, sys
from awgscale.service.logs import setup_logging
from awgscale.tunnel.keeper import Keeper
from awgscale.tunnel.settings import get_settings
setup_logging()
async def main():
    keeper = Keeper(get_settings())
    before = (await keeper.look("awg0")).status
    await keeper.keep_all()
    after = (await keeper.look("awg0")).status
    # (the stand-in for awg of this suite knows no keys or ports: whether the interface matches its file is checked in tests/tunnel.sh)
    return bool(before.gate) and not before.exists and after.exists and after.rules_missing == 0 and not after.error
sys.exit(0 if asyncio.run(main()) else 1)' >/tmp/tunnel.out 2>&1 && grep -q "firewall rules put in: 5" /tmp/tunnel.out || made=FAILED
        late=ok; $WATCH 'from awgscale.watch.cascade import Cascade; server = Cascade()
server.apply()
for _ in range(3): server.tick()
server.tick(probe=False)' >/dev/null 2>/tmp/late.err || late=FAILED
        dump
        echo "WATCH-BEFORE-TUNNEL $early"; echo "TUNNEL-MADE $made"; echo "WATCH-AFTER-TUNNEL $late"
        if [ "$early$late" != okok ]; then cat /tmp/early.err /tmp/late.err 2>/dev/null | head -6 | cut -c1-200 | sed 's/^/WATCH-ERR /'; fi ;;
    # the boot once the exit links are the container's too: the gate, then the watch service runs
    # with NO interface at all, then the links service makes awgk1/awgk2, then the watch goes on.
    boot-late-links)
        boot_service
        early=ok; $WATCH 'from awgscale.watch.cascade import Cascade; server = Cascade()
server.apply()
for _ in range(2): server.tick()' >/dev/null 2>/tmp/early.err || early=FAILED
        verdict=ok; if ls /run/cascade/exit*.state >/dev/null 2>&1; then verdict=FAILED; fi
        closed=ok; if ip route show table 298 | grep -q . || ip route show table 201 | grep -q 'default dev'; then closed=FAILED; fi
        mkdir -p /tmp/links
        for n in 1 2; do printf '[Interface]\nPrivateKey = PRIVpppppppppppppppppppppppppppppppppppppp=\nAddress = 10.77.%s.2/32\nMTU = 1280\nTable = off\n\n[Peer]\nPublicKey = AAAAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=\nEndpoint = 198.51.100.%s:47211\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n' $n $n > /tmp/links/awgk$n.conf; done
        made=ok; TUNNEL_GLOB='awgk*' TUNNEL_DIR=/tmp/links TUNNEL_AWG=/usr/local/bin/awg TUNNEL_LINK_TYPE=dummy python3 -c 'import asyncio, sys
from awgscale.service.logs import setup_logging
from awgscale.tunnel.keeper import Keeper
from awgscale.tunnel.settings import get_settings
setup_logging()
async def main():
    keeper = Keeper(get_settings())
    names = keeper.interfaces()
    await keeper.keep_all()
    statuses = [(await keeper.look(name)).status for name in names]
    return len(names) == 2 and all(status.exists and not status.error for status in statuses)
sys.exit(0 if asyncio.run(main()) else 1)' >/dev/null 2>&1 || made=FAILED
        ip link add awg0 type dummy; ip addr add 172.16.17.1/24 dev awg0; ip link set awg0 up; iptables -A FORWARD -i awg0 -o awg0 -j DROP
        late=ok; $WATCH 'from awgscale.watch.cascade import Cascade; server = Cascade()
server.apply()
server.tick()' >/dev/null 2>/tmp/late.err || late=FAILED
        atonce=ok; [ "$(cat /run/cascade/exit1.state 2>/dev/null)" = up ] && ip route show table 298 | grep -q 'default dev awgk1' || atonce=FAILED
        $WATCH 'from awgscale.watch.cascade import Cascade; server = Cascade()
for _ in range(2): server.tick()
server.tick(probe=False)' >/dev/null 2>>/tmp/late.err || late=FAILED
        dump
        echo "WATCH-BEFORE-LINKS $early"; echo "NO-VERDICT-WITHOUT-LINK $verdict"; echo "CLOSED-WITHOUT-LINK $closed"; echo "LINKS-MADE $made"; echo "UP-AT-FIRST-PROBE $atonce"; echo "WATCH-AFTER-LINKS $late"
        if [ "$early$late" != okok ]; then cat /tmp/early.err /tmp/late.err 2>/dev/null | head -6 | cut -c1-200 | sed 's/^/WATCH-ERR /'; fi ;;
    # the gate must fail loudly: no settings — no rules, and the tunnel will not start
    boot-broken)  mv "$D/cascade.conf" "$D/cascade.conf.away"; if boot_service; then echo "EXIT 0"; else echo "EXIT nonzero"; fi ;;
    service) via_service; dump ;;
    # a single server: the gate at boot (no tunnels, no network to download anything), then the service
    boot-single) if boot_service; then echo "GATE ok"; else echo "GATE FAILED"; fi; links; via_service; dump ;;
    gate-only) if boot_service; then echo "EXIT 0"; else echo "EXIT nonzero"; fi ;;
    # a single server gets its first exit: the agent's new file (no SINGLE_SERVER, one exit, the seed's
    # cascade defaults), then the service goes on — the exit must come up and take the marked traffic
    single-to-cascade)
        via_service
        sed -i -e '/^SINGLE_SERVER=1$/d' -e 's|^EXITS=($|EXITS=(\n    "1:awgk1:10.77.1.2:exit1:99"|' -e 's|^PROBE_TARGETS=()$|PROBE_TARGETS=(1.1.1.1 9.9.9.9)|' "$D/cascade.conf"
        for i in $(seq 0 255); do echo "5.$i.0.0/16"; done > "$D/ru.zone"
        if via_service; then echo "SERVICE ok"; else echo "SERVICE FAILED"; fi
        dump ;;
    scoped) via_service; dump ;;
    boot-scoped) boot_service; dump ;;
    boot-garbage) if boot_service; then echo "GATE ok"; else echo "GATE FAILED"; fi; dump ;;
    # a person moves to another group: the pass changes the sets of the groups' people, never the chain
    scoped-move)
        via_service; iptables-save -t mangle | grep -v '^#' | sed -E 's/\[[0-9]+:[0-9]+\]//' > /tmp/chain.before
        printf 'rg_g00001 172.16.17.9\nrg_g00002 172.16.17.4 172.16.17.2\n' > "$D/scoped.members"
        $WATCH 'from awgscale.watch.cascade import Cascade; Cascade().tick(probe=False)' >/dev/null
        iptables-save -t mangle | grep -v '^#' | sed -E 's/\[[0-9]+:[0-9]+\]//' > /tmp/chain.after
        diff /tmp/chain.before /tmp/chain.after >/dev/null && echo "CHAIN-SAME"
        dump ;;
    # packets: a new connection from a person of a group and from somebody else, the mark conntrack keeps for each
    scoped-packets)
        via_service
        ip link set lo up; ip addr add 198.51.100.5/32 dev lo; ip addr add 198.51.100.6/32 dev lo      # local: delivered whatever the mark, so conntrack keeps the entry
        ipset add force_exit 198.51.100.5; ipset add rs_aaaaaa 198.51.100.5; ipset add rs_dddddd 198.51.100.6
        send() { python3 -S - "$@" <<'PY'
import socket, struct, sys
source, destination, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
udp = len(sys.argv) > 4
def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF
if udp:          # a datagram: its conntrack entry lives on without an answer (a SYN to nobody is closed by the RST)
    tcp = struct.pack("!HHHH", port, 3478, 12, 0) + b"ping"
    pseudo = socket.inet_aton(source) + socket.inet_aton(destination) + struct.pack("!BBH", 0, 17, len(tcp))
    tcp = tcp[:6] + struct.pack("!H", checksum(pseudo + tcp)) + tcp[8:]
else:
    tcp = struct.pack("!HHIIBBHHH", port, 443, 1, 0, 5 << 4, 0x02, 64240, 0, 0)
    pseudo = socket.inet_aton(source) + socket.inet_aton(destination) + struct.pack("!BBH", 0, 6, len(tcp))
    tcp = tcp[:16] + struct.pack("!H", checksum(pseudo + tcp)) + tcp[18:]
ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), port, 0, 64, 17 if udp else 6, 0, socket.inet_aton(source), socket.inet_aton(destination))
ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
mac = bytes.fromhex(open("/sys/class/net/awg0/address").read().strip().replace(":", ""))
own = bytes.fromhex(open("/sys/class/net/c0/address").read().strip().replace(":", ""))
with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as raw:
    raw.bind(("c0", 0))
    raw.send(mac + own + b"\x08\x00" + ip + tcp)
PY
        }
        # after the cascade's own chain: one counting rule per (source port, mark) — the mark the packet left with
        iptables -t mangle -N MARKS; iptables -t mangle -A PREROUTING -i awg0 -j MARKS
        for port in 40001 40002 40003 40004 40005 40006 40021 40022; do for m in 0x0 0x200 0x101 0x103 0x1ff; do
            for protocol in tcp udp; do iptables -t mangle -A MARKS -p "$protocol" --sport "$port" -m mark --mark "$m" -m comment --comment "$port=$m"; done
        done; done
        mark() { iptables -t mangle -L MARKS -v -x -n | awk -v port="$3" '$1 > 0 && $0 ~ "/\\* " port "=" { sub(/.*=/, ""); sub(/ .*/, ""); print "mark=" $0 }' | head -1; }
        send 172.16.17.2 198.51.100.5 40001; send 172.16.17.2 198.51.100.6 40002; send 172.16.17.3 198.51.100.5 40003; send 172.16.17.3 198.51.100.6 40004
        send 172.16.17.4 198.51.100.5 40005; send 172.16.17.4 198.51.100.6 40006
        send 172.16.17.2 198.51.100.5 40021 udp
        sleep 0.3
        echo "MEMBER-DIRECT $(mark 172.16.17.2 198.51.100.5 40001)"
        echo "MEMBER-EXIT $(mark 172.16.17.2 198.51.100.6 40002)"
        echo "OTHER-GLOBAL-EXIT $(mark 172.16.17.3 198.51.100.5 40003)"
        echo "OTHER-UNLISTED $(mark 172.16.17.3 198.51.100.6 40004)"
        echo "SECOND-GROUP-EXIT-ONLY $(mark 172.16.17.4 198.51.100.5 40005) $(mark 172.16.17.4 198.51.100.6 40006)"
        # the person leaves the group: their open connection keeps its way, a new one goes as everybody's
        printf 'rg_g00001 172.16.17.9\nrg_g00002 172.16.17.4\n' > "$D/scoped.members"
        $WATCH 'from awgscale.watch.cascade import Cascade; Cascade().tick(probe=False)' >/dev/null
        iptables -t mangle -Z MARKS
        send 172.16.17.2 198.51.100.5 40021 udp; send 172.16.17.2 198.51.100.5 40022 udp
        sleep 0.3
        echo "KEPT $(mark 172.16.17.2 198.51.100.5 40021)"
        echo "AFTER-MOVE $(mark 172.16.17.2 198.51.100.5 40022)" ;;
    service-twice) via_service; dump > /tmp/one; $WATCH 'from awgscale.watch.cascade import Cascade; server = Cascade(); server.apply(); server.tick(probe=False)' >/dev/null; dump > /tmp/two
             diff /tmp/one /tmp/two && echo SAME ;;
esac
