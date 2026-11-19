#!/usr/bin/env bash
# =============================================================================
# exit-firewall.sh — installed ON an exit server as /usr/local/sbin/exit-firewall.sh
# (deployed by the exits service's firewall step: awgscale.exits.steps).
#
# Default-deny for connections TO the server itself, IPv4 and IPv6. Allowed:
# SSH, the dedicated cascade tunnel's UDP port, and what is listed in
# /etc/exit-firewall.conf. Replies, loopback, ICMP and traffic from the
# server's own containers are always allowed.
#
# NOT affected: ports published by Docker (the Amnezia app's VPN container) —
# Docker forwards those before the INPUT chain is consulted — and forwarded
# VPN traffic.
#
#   exit-firewall.sh apply | remove | status
# =============================================================================
set -euo pipefail
CHAIN="EXIT-GUARD"
TCP_PORTS="22"; UDP_PORTS=""; EXTRA_V4=()
# shellcheck disable=SC1091
[[ -f /etc/exit-firewall.conf ]] && source /etc/exit-firewall.conf

build() {   # $1 = iptables | ip6tables
    local ipt=$1 p r
    $ipt -N "$CHAIN" 2>/dev/null || $ipt -F "$CHAIN"
    $ipt -A "$CHAIN" -i lo -j RETURN
    $ipt -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    if [[ $ipt == iptables ]]; then
        $ipt -A "$CHAIN" -p icmp -j RETURN
        $ipt -A "$CHAIN" -p udp --sport 67 --dport 68 -j RETURN            # DHCP
        # the server's own containers talking to the host
        for r in docker0 "br-+" amn0; do $ipt -A "$CHAIN" -i "$r" -j RETURN; done
        for r in "${EXTRA_V4[@]:-}"; do [[ -n "$r" ]] && $ipt -A "$CHAIN" $r -j RETURN; done
    else
        $ipt -A "$CHAIN" -p ipv6-icmp -j RETURN                             # neighbour discovery, RA
        $ipt -A "$CHAIN" -p udp --dport 546 -j RETURN                       # DHCPv6 client
    fi
    for p in ${TCP_PORTS//,/ }; do $ipt -A "$CHAIN" -p tcp --dport "$p" -j RETURN; done
    for p in ${UDP_PORTS//,/ }; do $ipt -A "$CHAIN" -p udp --dport "$p" -j RETURN; done
    $ipt -A "$CHAIN" -j DROP
    $ipt -C INPUT -j "$CHAIN" 2>/dev/null || $ipt -I INPUT 1 -j "$CHAIN"
}

unbuild() {
    local ipt=$1
    while $ipt -D INPUT -j "$CHAIN" 2>/dev/null; do :; done
    $ipt -F "$CHAIN" 2>/dev/null || true
    $ipt -X "$CHAIN" 2>/dev/null || true
}

case "${1:-}" in
    apply)  build iptables; build ip6tables; echo "exit firewall applied (tcp: $TCP_PORTS; udp: ${UDP_PORTS:-none})" ;;
    remove) unbuild iptables; unbuild ip6tables; echo "exit firewall removed" ;;
    status) iptables -nvL "$CHAIN" 2>/dev/null || echo "not applied (IPv4)"; ip6tables -nvL "$CHAIN" 2>/dev/null | head -12 || echo "not applied (IPv6)" ;;
    *) echo "Usage: $0 apply | remove | status" >&2; exit 1 ;;
esac
