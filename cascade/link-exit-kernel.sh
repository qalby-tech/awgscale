#!/usr/bin/env bash
# =============================================================================
# link-exit-kernel.sh — run ON THE OPERATOR MACHINE. Creates a DEDICATED AmneziaWG tunnel
# (kernel module on both ends) between the entry server and an exit server,
# independent of the Amnezia app's Docker container that may also run there.
#
#   cascade/link-exit-kernel.sh <exit-ssh-alias> <index> <udp-port>
#   cascade/link-exit-kernel.sh exit1 1 47211
#
# Prerequisite on the exit: packages amneziawg-dkms + amneziawg-tools and the
# module loaded (kernel 6.7+ for the 3.1 parameter set used here).
#
# Exit side : interface awgc,       10.77.<index>.1/30, NAT to the internet
# Entry side: interface awgk<index>, 10.77.<index>.2/32, Table = off
# Routing on the entry is NOT touched. To route through the new exit, add it to
# the cascade's settings — they live in the database, and cascade.conf on the
# entry is compiled from them (an edit by hand there is lost):
#   docker exec vpn-agent python -m awgscale.agent cascade-get     # the document
#   docker exec vpn-agent python -m awgscale.agent cascade-set <base64 of the changed document>
# Idempotent: existing keys and parameters are kept on re-runs.
# Secrets travel only through SSH pipes; nothing is written on the operator machine.
# =============================================================================
set -euo pipefail
EXIT=${1:?exit ssh alias}; IDX=${2:?index}; PORT=${3:?udp port}
HERE=$(cd "$(dirname "$0")/.." && pwd)
SSH=(ssh -F "$HERE/ssh_config" -o BatchMode=yes)
EIF="awgk${IDX}"; NET="10.77.${IDX}"
D=/etc/amnezia/amneziawg
EXIT_IP=$("${SSH[@]}" -G "$EXIT" | awk '$1=="hostname"{print $2}')
info() { echo "[klink:$EXIT] $*"; }

# ── entry: its key for this link ─────────────────────────────────────────────
ENTRY_PUB=$("${SSH[@]}" entry "umask 077; [ -s $D/${EIF}.key ] || awg genkey > $D/${EIF}.key; awg pubkey < $D/${EIF}.key")
[[ ${#ENTRY_PUB} -eq 44 ]] || { echo "bad entry public key" >&2; exit 1; }

# ── exit: server interface ───────────────────────────────────────────────────
"${SSH[@]}" "$EXIT" "ENTRY_PUB='$ENTRY_PUB' PORT='$PORT' NET='$NET' bash -s" <<'REMOTE'
set -euo pipefail
D=/etc/amnezia/amneziawg; mkdir -p $D; chmod 700 $D; umask 077
lsmod | grep -q '^amneziawg' || modprobe amneziawg
[ -s $D/awgc.key ] || awg genkey > $D/awgc.key
[ -s $D/awgc.psk ] || awg genpsk > $D/awgc.psk
# Obfuscation parameters: generated once, then kept (the entry must match them)
if [ ! -s $D/awgc.params ]; then
    r() { shuf -i "$1-$2" -n 1; }
    jmin=$(r 40 80); s1=$(r 15 150); s2=$(r 15 150); while [ $((s1 + 56)) -eq "$s2" ]; do s2=$(r 15 150); done
    cat > $D/awgc.params <<P
Jc = $(r 3 6)
Jmin = $jmin
Jmax = $((jmin + $(r 30 60)))
S1 = $s1
S2 = $s2
S3 = $(r 12 55)
S4 = $(r 12 27)
H1 = 1
H2 = 2
H3 = 3
H4 = 4
HeaderProtectionKey = $(head -c 32 /dev/urandom | base64)
ContentPaddingAddition = 32-128
P
fi
WAN=$(ip -4 route show default | grep -oP 'dev \K\S+' | head -1)
cat > $D/awgc.conf <<C
# Dedicated cascade tunnel: the entry server is the only peer.
[Interface]
PrivateKey = $(cat $D/awgc.key)
Address = ${NET}.1/30
ListenPort = ${PORT}
MTU = 1280
$(cat $D/awgc.params)
# Docker sets the FORWARD policy to DROP, so this traffic is accepted explicitly.
PostUp = iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -I FORWARD 1 -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -A POSTROUTING -s ${NET}.0/30 -o ${WAN} -j MASQUERADE; iptables -t mangle -A FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240; iptables -t mangle -A FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -D POSTROUTING -s ${NET}.0/30 -o ${WAN} -j MASQUERADE; iptables -t mangle -D FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240; iptables -t mangle -D FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240

[Peer]
PublicKey = ${ENTRY_PUB}
PresharedKey = $(cat $D/awgc.psk)
AllowedIPs = ${NET}.2/32
C
chmod 600 $D/awgc.conf
echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-awgc.conf; sysctl -q -p /etc/sysctl.d/99-awgc.conf
echo amneziawg > /etc/modules-load.d/amneziawg.conf
systemctl enable awg-quick@awgc >/dev/null 2>&1
systemctl restart awg-quick@awgc || { journalctl -u awg-quick@awgc -n 20 --no-pager; exit 1; }
echo "[exit] awgc up on udp/${PORT}, WAN ${WAN}"
REMOTE

# ── entry: client interface, built from the exit's real parameters ───────────
{
    echo "[Interface]"
    echo "PrivateKey = __KEY__"
    echo "Address = ${NET}.2/32"
    echo "MTU = 1280"
    echo "Table = off"
    "${SSH[@]}" "$EXIT" "cat $D/awgc.params"
    echo "__I1__"
    echo
    echo "[Peer]"
    echo "PublicKey = $("${SSH[@]}" "$EXIT" "awg pubkey < $D/awgc.key")"
    echo "PresharedKey = $("${SSH[@]}" "$EXIT" "cat $D/awgc.psk")"
    echo "Endpoint = ${EXIT_IP}:${PORT}"
    echo "AllowedIPs = 0.0.0.0/0"
    echo "PersistentKeepalive = 25"
} | "${SSH[@]}" entry "umask 077; k=\$(cat $D/${EIF}.key)
# same masking packet the installer gave this server's own clients (sent before the handshake)
i1=\$(grep -m1 '^I1 = ' $D/awg0.conf || true)
# written beside and moved into place: the links service must never read half a file
awk -v k=\"\$k\" -v i1=\"\$i1\" '{ if (\$0 ~ /__KEY__/) sub(/__KEY__/, k); if (\$0 == \"__I1__\") { if (i1 != \"\") print i1; next } print }' > $D/${EIF}.conf.new
chmod 600 $D/${EIF}.conf.new && mv -f $D/${EIF}.conf.new $D/${EIF}.conf
if [ -f /opt/vpn-stack/links.enabled ]; then
    # the exit links are the container's (vpn-links): an old link is taken away and the
    # service makes it again from the new file; the host unit is not used
    ip link del ${EIF} 2>/dev/null || true
    for i in \$(seq 1 30); do ip link show ${EIF} >/dev/null 2>&1 && break; sleep 1; done
    ip link show ${EIF} >/dev/null 2>&1 || { echo '[entry] vpn-links did not make ${EIF}' >&2; docker logs vpn-links --tail 8 2>&1 | cut -c1-200 >&2; exit 1; }
else
    systemctl enable awg-quick@${EIF} >/dev/null 2>&1
    systemctl restart awg-quick@${EIF} || { journalctl -u awg-quick@${EIF} -n 20 --no-pager; exit 1; }
fi
for i in \$(seq 1 15); do hs=\$(awg show ${EIF} latest-handshakes | awk '{print \$2}'); [ \"\${hs:-0}\" -gt 0 ] && break; ping -c1 -W1 -I ${EIF} ${NET}.1 >/dev/null 2>&1 || true; sleep 1; done
[ \"\${hs:-0}\" -gt 0 ] && echo '[entry] handshake OK on ${EIF}' || { echo '[entry] NO handshake on ${EIF}' >&2; awg show ${EIF}; exit 1; }"
info "linked: entry ${EIF} (${NET}.2) → ${EXIT} awgc (${NET}.1), udp/${PORT}"
