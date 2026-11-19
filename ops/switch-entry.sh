#!/usr/bin/env bash
# =============================================================================
# switch-entry.sh — run ON THE OPERATOR MACHINE. Moves the users from one entry server to
# another by hand: the name in their configs (DOMAIN of site.env) is pointed at the
# other server in Cloudflare, and this machine's ssh_config follows.
#
#   ops/switch-entry.sh status          where the name points, and what each entry looks like
#   ops/switch-entry.sh check <to>      every check of <to>, changes nothing
#   ops/switch-entry.sh to <to>         check <to>; only if all pass, switch to it
#   ops/switch-entry.sh to <to> force   switch even if checks fail (the current one is dead anyway)
#
# <to> is a host of ssh_config (entry-old) or an address. It must already run the
# whole stack with the same users (same awg0 key, obfuscation and peers) and its
# own links to the exits: this script moves the name, not the server.
#
# The Cloudflare token (DNS edit of the ZONE of site.env only) is read from
# ~/.config/awgscale/cloudflare-token (chmod 600); it is never put in the repo.
#
# After a switch:
#   * `entry` in ssh_config is the new server, the previous one becomes `entry-old`;
#   * clients that keep a resolved address reconnect (VPN off and on) — TTL is 30 s.
# =============================================================================
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
SSH_CONFIG=$HERE/ssh_config
. "$HERE/site.sh"
site_env DOMAIN ZONE
NAME=$DOMAIN
TOKEN_FILE=${CLOUDFLARE_TOKEN_FILE:-$HOME/.config/awgscale/cloudflare-token}
CF=https://api.cloudflare.com/client/v4
CONF=$HERE/clients/zz_selftest.conf
LINK_FRESH=180            # seconds: a link to an exit with an older handshake is down
# one connection per server for the whole run: the entry's firewall limits new SSH connections (ufw LIMIT)
SOCKETS=$(mktemp -d)
trap 'for s in "$SOCKETS"/*; do [ -S "$s" ] && ssh -o ControlPath="$s" -O exit x 2>/dev/null; done; rm -rf -- "$SOCKETS"' EXIT

say() { echo "$(date +%H:%M:%S) $*"; }
die() { echo "$*" >&2; exit 1; }

address_of() {            # ssh_config host or address → address
    if [[ $1 =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then echo "$1"; return; fi
    ssh -G -F "$SSH_CONFIG" "$1" 2>/dev/null | awk '$1 == "hostname" {print $2}'
}

on() {                    # on <address> <command>: as root on that server, with the entry's key
    local address=$1
    shift
    ssh -F "$SSH_CONFIG" -o HostName="$address" -o ControlPath="$SOCKETS/$address" -o ControlMaster=auto -o ControlPersist=120 \
        -o ConnectTimeout=8 -o BatchMode=yes entry "$@"
}

# ── Cloudflare ───────────────────────────────────────────────────────────────

cloudflare() {            # cloudflare <method> <path> [json body]
    [ -r "$TOKEN_FILE" ] || die "no Cloudflare token at $TOKEN_FILE"
    curl -s -m 20 -X "$1" "$CF$2" -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
        -H 'Content-Type: application/json' ${3:+--data "$3"}
}

record() {                # → "<zone id> <record id> <address>"
    local zone answer
    [ -r "$TOKEN_FILE" ] || die "no Cloudflare token at $TOKEN_FILE"
    zone=$(cloudflare GET "/zones?name=$ZONE" | jq -r 'if .success then .result[0].id // empty else empty end')
    [ -n "$zone" ] || die "Cloudflare: zone $ZONE not found (or the token cannot read it)"
    answer=$(cloudflare GET "/zones/$zone/dns_records?type=A&name=$NAME")
    [ "$(jq '.result | length' <<<"$answer")" = 1 ] || die "Cloudflare: expected exactly one A record for $NAME"
    echo "$zone $(jq -r '.result[0].id + " " + .result[0].content' <<<"$answer")"
}

# ── the checks of a server ───────────────────────────────────────────────────

doh() {                   # doh <address> <name> → what the DNS on that server answers for the name
    local q
    q=$(python3 -c "
import base64, struct, sys
q = b'\x12\x34' + struct.pack('>HHHHH', 0x0100, 1, 0, 0, 0) + b''.join(bytes([len(p)]) + p.encode() for p in sys.argv[1].split('.')) + b'\0' + struct.pack('>HH', 1, 1)
print(base64.urlsafe_b64encode(q).decode().rstrip('='))" "$2")
    curl -s -m 8 --resolve "$NAME:443:$1" "https://$NAME/dns-query?dns=$q" -H 'accept: application/dns-message' 2>/dev/null \
        | python3 -c "
import socket, sys
a = sys.stdin.buffer.read()
print(socket.inet_ntoa(a[-4:]) if len(a) > 16 else '')"
}

vpn() {                   # vpn <address> → "key=value" lines from a throwaway client of the self-test user aimed at that address
    local dns
    docker rm -f awg-switchcheck >/dev/null 2>&1
    docker run -d --name awg-switchcheck --cap-add NET_ADMIN --device /dev/net/tun --entrypoint sleep amnezia-awg2 300 >/dev/null 2>&1 || return 1
    dns=$(awk -F' *= *' '/^DNS/{print $2}' "$CONF" | cut -d, -f1)
    grep -v '^DNS' "$CONF" | sed -e 's|, ::/0||' -e "s|^\(Endpoint *= *\)[^:]*:|\1$1:|" \
        | docker exec -i awg-switchcheck sh -c 'mkdir -p /etc/amnezia/amneziawg && umask 077 && cat > /etc/amnezia/amneziawg/awg0.conf'
    docker exec -e DNS="$dns" awg-switchcheck sh -c '
apk add -q curl bind-tools 2>/dev/null
sed -i "s/cmd sysctl -q net.ipv4.conf.all.src_valid_mark=1/true/" "$(command -v awg-quick)"
awg-quick up awg0 >/dev/null 2>&1; sleep 4
echo "nameserver $DNS" > /etc/resolv.conf
echo "handshake=$(awg show awg0 latest-handshakes | awk "{print (\$2>0)?\"yes\":\"no\"}")"
echo "listed=$(curl -s -m 12 https://chatgpt.com/cdn-cgi/trace | sed -n "s/^ip=//p")"
echo "unlisted=$(curl -s -m 12 https://ifconfig.me)"'
    docker rm -f awg-switchcheck >/dev/null 2>&1
}

peers() {                 # peers <address> → a fingerprint of the users' peers (never the keys)
    on "$1" 'awg show awg0 peers | sort | sha256sum | cut -c1-12; awg show awg0 peers | wc -l' 2>/dev/null | paste -sd' '
}

check() {                 # check <address> [the address the name points at now] → 0 when every check passes
    local to=$1 now=${2:-} bad=0 r v exits stale mine theirs
    one() { if [ "$2" = 0 ]; then echo "  ok   $1"; else echo "  FAIL $1"; bad=1; fi; }
    on "$to" true;                                                                   one "SSH отвечает" $?
    on "$to" 'ip link show awg0 up 2>/dev/null | grep -q UP';                                    one "туннель для пользователей поднят" $?
    exits=$(on "$to" 'for f in /etc/amnezia/amneziawg/awgk*.conf; do [ -e "$f" ] || continue; sed -n "s/^Endpoint *= *\([0-9.]*\):.*/\1/p" "$f"; done' | paste -sd' ')
    stale=$(on "$to" "now=\$(date +%s); for f in /etc/amnezia/amneziawg/awgk*.conf; do [ -e \"\$f\" ] || continue; i=\$(basename \"\$f\" .conf); awg show \$i latest-handshakes 2>/dev/null | awk -v n=\$now '\$2 > 0 && n - \$2 <= $LINK_FRESH {ok = 1} END {exit !ok}' || echo \$i; done" | paste -sd' ')
    [ -n "$exits" ] && [ -z "$stale" ];                                              one "туннели к выходам живые (${exits:-нет}${stale:+; молчат: $stale})" $?
    r=$(on "$to" "docker ps --format '{{.Names}}' | wc -l");                         [ "${r:-0}" -ge 10 ]; one "контейнеры запущены (${r:-0})" $?
    if [ "$now" = "$to" ]; then
        echo "  --   пользователи не сравнены: это и есть нынешний вход"
    elif mine=$(peers "$now") && [ -n "$mine" ]; then
        theirs=$(peers "$to")
        [ "$mine" = "$theirs" ];                                                     one "пользователи те же, что на $now (${theirs#* } из ${mine#* })" $?
    else
        echo "  --   пользователи не сравнены: $now не отвечает"
    fi
    r=$(doh "$to" claude.ai);           [ "$r" = "$to" ];                            one "DNS сервера ведёт перечисленный сайт через сервер" $?
    r=$(curl -s -m 8 -o /dev/null -w '%{http_code}' --resolve "$NAME:443:$to" "https://$NAME/" 2>/dev/null)
    [ "$r" = 200 ];                                                                  one "панель отвечает с правильным сертификатом ($r)" $?
    if [ -r "$CONF" ]; then
        v=$(vpn "$to")
        grep -qx 'handshake=yes' <<<"$v";                                            one "клиент VPN подключается к $to" $?
        r=$(sed -n 's/^listed=//p' <<<"$v");   [ -n "$r" ] && grep -qw -- "$r" <<<"$exits"; one "перечисленный сайт идёт через заграницу ($r)" $?
        r=$(sed -n 's/^unlisted=//p' <<<"$v"); [ "$r" = "$to" ];                     one "остальное идёт через Россию ($r)" $?
    else
        echo "  FAIL нет $CONF — клиент не проверен"; bad=1
    fi
    return $bad
}

# ── after a switch: this machine follows ─────────────────────────────────────

follow() {                # follow <new> <previous>: `entry` ← new, `entry-old` ← previous
    python3 - "$SSH_CONFIG" "$1" "$2" <<'PY'
import re, sys
path, new, previous = sys.argv[1:]
text = open(path).read()
def point(text, host, address):
    block = re.compile(r"(?m)^(Host[ \t]+" + re.escape(host) + r"[ \t]*\n(?:[ \t]+.*\n)*?[ \t]+HostName[ \t]+)\S+")
    if not block.search(text):
        sys.exit(f"ssh_config: no Host {host} with a HostName")
    return block.sub(lambda match: match.group(1) + address, text, count=1)
text = point(text, "entry", new)
text = point(text, "entry-old", previous)
open(path, "w").write(text)
PY
}

case "${1:-}" in
    status)
        read -r _ _ now < <(record) || exit 1
        echo "в Cloudflare $NAME → $now; публичный DNS: $(dig +short +time=4 "$NAME" @1.1.1.1 | head -1)"
        for host in entry entry-old; do
            address=$(address_of "$host")
            mark=; [ "$address" = "$now" ] && mark=" ← пользователи здесь"
            echo "$host ($address)$mark: пользователей $(peers "$address" | cut -d' ' -f2), контейнеров $(on "$address" "docker ps -q | wc -l" 2>/dev/null || echo '?')"
        done
        ;;
    check)
        [ -n "${2:-}" ] || die "Usage: $0 check <host|address>"
        to=$(address_of "$2")
        now=$(address_of entry)
        say "проверка $2 ($to)"
        check "$to" "$now"
        ;;
    to)
        [ -n "${2:-}" ] || die "Usage: $0 to <host|address> [force]"
        to=$(address_of "$2")
        read -r zone id now < <(record) || exit 1
        [ "$now" != "$to" ] || die "$NAME уже указывает на $to"
        say "проверка $2 ($to) перед переключением с $now"
        if ! check "$to" "$now"; then
            [ "${3:-}" = force ] || die "не все проверки прошли — имя не переключено (переключить всё равно: $0 to $2 force)"
            say "не все проверки прошли, переключаю по force"
        fi
        answer=$(cloudflare PATCH "/zones/$zone/dns_records/$id" "{\"content\": \"$to\"}")
        [ "$(jq -r .success <<<"$answer")" = true ] || die "Cloudflare не принял: $(jq -c .errors <<<"$answer")"
        say "$NAME → $to в Cloudflare"
        follow "$to" "$now" || die "ssh_config не исправлен: поправьте Host entry / entry-old руками"
        say "ssh_config: entry = $to, entry-old = $now"
        for i in $(seq 1 12); do
            [ "$(dig +short +time=4 "$NAME" @1.1.1.1 | head -1)" = "$to" ] && { say "публичный DNS уже отдаёт $to"; break; }
            sleep 10
        done
        ;;
    *) die "Usage: $0 status | check <host|address> | to <host|address> [force]" ;;
esac
