#!/usr/bin/env bash
# Runs ON an exit server: its side of the dedicated tunnel to the entry
# (interface awgc, the entry is the only peer). Env: ENTRY_PUB, PORT, NET
# (10.77.<index>). Keys and masking parameters are made once and then kept.
# Prints what the entry's side is built from, on lines that start with "@@".
set -euo pipefail
: "${ENTRY_PUB:?}" "${PORT:?}" "${NET:?}"
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
cat > $D/awgc.conf.new <<C
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
chmod 600 $D/awgc.conf.new
echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-awgc.conf; sysctl -q -p /etc/sysctl.d/99-awgc.conf
echo amneziawg > /etc/modules-load.d/amneziawg.conf
systemctl enable awg-quick@awgc >/dev/null 2>&1
# restarted only when something changed: a second run must not drop a working tunnel
if ! cmp -s $D/awgc.conf.new $D/awgc.conf || ! ip link show awgc >/dev/null 2>&1; then
    mv -f $D/awgc.conf.new $D/awgc.conf
    systemctl restart awg-quick@awgc || { echo "@@ERR туннель на выходе не поднялся: $(journalctl -u awg-quick@awgc -n 3 --no-pager -o cat 2>/dev/null | tr '\n' ' ' | cut -c1-200)"; exit 1; }
else
    rm -f $D/awgc.conf.new
fi
echo "@@PUB $(awg pubkey < $D/awgc.key)"
echo "@@PSK $(cat $D/awgc.psk)"
sed 's/^/@@PARAM /' $D/awgc.params
echo "@@OK awgc на udp/${PORT}, наружу через ${WAN}"
