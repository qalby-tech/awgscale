#!/usr/bin/env bash
# =============================================================================
# reboot-entry.sh — run ON THE OPERATOR MACHINE. Reboots the entry server under the safety
# net (stack/deadman.sh) and is the "word from outside" the net waits for.
#
#   ops/reboot-entry.sh check   the checks from outside, now; changes nothing
#   ops/reboot-entry.sh go      arm the net, reboot, check, confirm
#
# `go` must outlive the session that started it (this machine's DNS goes through
# the server being rebooted), so start it detached:
#   setsid nohup ops/reboot-entry.sh go >/dev/null 2>&1 &
# It writes what happened to logs/reboot-<time>.log in the project.
#
# The rule is one: all checks from outside pass → confirm. If they do not pass
# before the deadline, nothing is sent and the server rolls its boot path back
# by itself, ten minutes after it booted.
# =============================================================================
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
. "$HERE/site.sh"
site_env DOMAIN
ENTRY=$(ssh -G -F "$HERE/ssh_config" entry 2>/dev/null | awk '$1 == "hostname" {print $2}')   # ops/switch-entry.sh moves it
# the addresses the exits show to the world: asked from the server (the panel can add and remove exits)
EXITS=$(ssh -F "$(cd "$(dirname "$0")/.." && pwd)/ssh_config" -o BatchMode=yes entry 'for f in /etc/amnezia/amneziawg/awgk*.conf; do [ -e "$f" ] || continue; sed -n "s/^Endpoint *= *\([0-9.]*\):.*/\1/p" "$f"; done' 2>/dev/null | tr '\n' ' ')
[ -n "${EXITS// /}" ] || { echo "the exits' addresses could not be read from the entry" >&2; exit 1; }
CONF=$HERE/clients/zz_selftest.conf
DM=/opt/vpn-stack/boot/deadman.sh
# its own connections every time: a shared one would hang on the server that went away
SSH=(ssh -F "$HERE/ssh_config" -o ControlPath=none -o ControlMaster=no -o ConnectTimeout=8 -o BatchMode=yes entry)
GIVE_UP=540            # seconds after the reboot was sent; the server's own deadline is 10 min after it booted

say() { echo "$(date +%H:%M:%S) $*"; }
# Single connections from this machine to the server get lost now and then (the
# server answers, the answer does not arrive), so every question is asked up to
# five times: one lost packet must not look like a broken server.
web() {
    local i c
    for i in 1 2 3 4 5; do
        c=$(curl -s -m 8 -o /dev/null -w '%{http_code}' --resolve "$1:443:$ENTRY" "https://$1/" 2>/dev/null)
        if [ -n "$c" ] && [ "$c" != 000 ]; then break; fi
        sleep 3
    done
    echo "$c"
}

doh() {      # → the address the server's DNS gives for a listed name
    local q
    q=$(python3 -c "
import base64, struct, sys
q = b'\x12\x34' + struct.pack('>HHHHH', 0x0100, 1, 0, 0, 0) + b''.join(bytes([len(p)]) + p.encode() for p in sys.argv[1].split('.')) + b'\0' + struct.pack('>HH', 1, 1)
print(base64.urlsafe_b64encode(q).decode().rstrip('='))" "$1")
    local i r
    for i in 1 2 3 4 5; do
        r=$(curl -s -m 8 --resolve "$DOMAIN:443:$ENTRY" "https://$DOMAIN/dns-query?dns=$q" -H 'accept: application/dns-message' 2>/dev/null \
            | python3 -c "
import socket, sys
a = sys.stdin.buffer.read()
print(socket.inet_ntoa(a[-4:]) if len(a) > 16 else '')")
        if [ -n "$r" ]; then break; fi
        sleep 3
    done
    echo "$r"
}

vpn() {      # a throwaway client with the self-test user's config → lines "key=value"
    docker rm -f awg-rebootcheck >/dev/null 2>&1
    docker run -d --name awg-rebootcheck --cap-add NET_ADMIN --device /dev/net/tun --entrypoint sleep amnezia-awg2 300 >/dev/null 2>&1 || return 1
    local dns
    dns=$(awk -F' *= *' '/^DNS/{print $2}' "$CONF" | cut -d, -f1)
    grep -v '^DNS' "$CONF" | sed 's|, ::/0||' | docker exec -i awg-rebootcheck sh -c 'mkdir -p /etc/amnezia/amneziawg && umask 077 && cat > /etc/amnezia/amneziawg/awg0.conf'
    docker exec -e DNS="$dns" awg-rebootcheck sh -c '
apk add -q curl bind-tools 2>/dev/null
sed -i "s/cmd sysctl -q net.ipv4.conf.all.src_valid_mark=1/true/" "$(command -v awg-quick)"
awg-quick up awg0 >/dev/null 2>&1; sleep 4
echo "nameserver $DNS" > /etc/resolv.conf
echo "handshake=$(awg show awg0 latest-handshakes | awk "{print (\$2>0)?\"yes\":\"no\"}")"
echo "listed=$(curl -s -m 12 https://chatgpt.com/cdn-cgi/trace | sed -n "s/^ip=//p")"
echo "unlisted=$(curl -s -m 12 https://ifconfig.me)"
echo "dns=$(dig +short +time=5 example.org | head -1)"
echo "ad=$(dig +short +time=5 ad.doubleclick.net | head -1)"'
    docker rm -f awg-rebootcheck >/dev/null 2>&1
}

# All the checks from outside. → 0 only when every one passes.
outside() {
    local bad=0 v r
    one() { if [ "$2" = 0 ]; then echo "  ok   $1"; else echo "  FAIL $1"; bad=1; fi; }
    r=$(doh claude.ai);                 [ "$r" = "$ENTRY" ];                         one "DNS сервера отвечает и ведёт перечисленный сайт через сервер" $?
    r=$(web claude.ai);                 [ -n "$r" ] && [ "$r" != 000 ];              one "claude.ai открывается через сервер ($r)" $?
    r=$(web api.anthropic.com);         [ -n "$r" ] && [ "$r" != 000 ];              one "api.anthropic.com открывается через сервер ($r)" $?
    r=$(web "$DOMAIN");                 [ "$r" = 200 ];                              one "панель отвечает ($r)" $?
    v=$(vpn)
    grep -qx 'handshake=yes' <<<"$v";                                                one "клиент VPN подключается" $?
    r=$(sed -n 's/^listed=//p' <<<"$v");   [ -n "$r" ] && grep -qw -- "$r" <<<"$EXITS"; one "перечисленный сайт идёт через заграницу ($r)" $?
    r=$(sed -n 's/^unlisted=//p' <<<"$v"); [ "$r" = "$ENTRY" ];                      one "остальное идёт через Россию ($r)" $?
    [ -n "$(sed -n 's/^dns=//p' <<<"$v")" ];                                         one "DNS внутри VPN отвечает" $?
    [ "$(sed -n 's/^ad=//p' <<<"$v")" = 0.0.0.0 ];                                   one "реклама блокируется" $?
    return $bad
}

case "${1:-}" in
    check)
        outside; rc=$?
        echo "на сервере:"; "${SSH[@]}" "bash $DM status" || rc=1
        exit $rc
        ;;
    go)
        mkdir -p "$HERE/logs"
        exec >> "$HERE/logs/reboot-$(date +%Y%m%d-%H%M).log" 2>&1
        say "проверки до перезагрузки"
        outside || { say "снаружи не всё в порядке ещё ДО перезагрузки — перезагрузка отменена"; exit 1; }
        "${SSH[@]}" "bash $DM status" || { say "сервер сам видит неполадки — перезагрузка отменена"; exit 1; }
        old=$("${SSH[@]}" 'cat /proc/sys/kernel/random/boot_id')
        [ -n "$old" ] || { say "сервер не ответил — перезагрузка отменена"; exit 1; }
        "${SSH[@]}" "bash $DM arm" || { say "страховка не взвелась — перезагрузка отменена"; exit 1; }
        say "страховка взведена, перезагрузка"
        "${SSH[@]}" 'systemctl reboot' >/dev/null 2>&1
        sent=$(date +%s); confirmed=; booted=
        # a round of checks is started only if it can end before the server's deadline
        while [ $(( $(date +%s) - sent )) -lt $(( GIVE_UP - 150 )) ]; do
            sleep 20
            now=$("${SSH[@]}" 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null)
            if [ -z "$now" ] || [ "$now" = "$old" ]; then continue; fi
            [ -n "$booted" ] || say "сервер загрузился ($(( $(date +%s) - sent )) с после команды)"
            booted=1
            say "проверки снаружи"
            if outside; then
                for try in 1 2 3 4; do
                    "${SSH[@]}" "bash $DM confirm"; rc=$?
                    if [ $rc = 0 ]; then confirmed=1; break; fi
                    if [ $rc = 2 ]; then say "подтверждать поздно: страховка на сервере уже сработала или снята"; break; fi
                    sleep 8
                done
                if [ -n "$confirmed" ]; then say "ВСЁ В ПОРЯДКЕ: загрузка подтверждена, отката не будет"; fi
                break
            fi
        done
        if [ -z "$booted" ]; then
            now=$("${SSH[@]}" 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null)
            if [ "$now" = "$old" ]; then
                say "ПЕРЕЗАГРУЗКИ НЕ БЫЛО: команда не дошла — страховка снимается"
                "${SSH[@]}" "bash $DM disarm"
                exit 1
            fi
            say "сервер не отвечает по SSH"
        fi
        [ -n "$confirmed" ] || say "НЕ ПОДТВЕРЖДЕНО к сроку — сервер сам откатит путь загрузки (через 10 минут после своей загрузки)"
        # the server's own verdict, after its deadline has passed
        while [ $(( $(date +%s) - sent )) -lt 780 ]; do sleep 20; done
        say "что записал сторож на сервере:"
        "${SSH[@]}" "journalctl -t vpn-deadman -b --no-pager -o short-iso | tail -20; bash $DM status"
        if [ -z "$confirmed" ]; then say "проверки снаружи после отката:"; outside; fi
        say "конец"
        ;;
    *) echo "Usage: $0 check | go" >&2; exit 1 ;;
esac
