#!/usr/bin/env bash
# Runs INSIDE a throwaway container with its own network namespace (see
# tests/tunnel.sh): the tunnel service against the real ip / iptables, with a
# dummy link in place of the amneziawg one and a stand-in for the `awg` tool.
set -u
# what is tested: the service as the container runs it (TUNNEL_RUN), by default the package
RUN=${TUNNEL_RUN:-python -m awgscale.tunnel}
T=/tmp/t; mkdir -p $T /usr/local/bin
export TUNNEL_CONF=$T/awg0.conf TUNNEL_AWG=/usr/local/bin/awg TUNNEL_LINK_TYPE=dummy TUNNEL_PASS=0.3 PYTHONUNBUFFERED=1
ok() { if [ "$1" = 0 ]; then echo "ok   $2"; else echo "FAIL $2"; fi; }
POSTUP='iptables -I FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE; iptables -t mangle -A FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240; iptables -t mangle -A FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240; while iptables -D FORWARD -i %i -o %i -j DROP 2>/dev/null; do :; done; iptables -I FORWARD -i %i -o %i -j DROP'
conf() {   # $1 = port, $2 = PostUp
    cat > $TUNNEL_CONF <<C
[Interface]
PrivateKey = PRIVpppppppppppppppppppppppppppppppppppppp=
Address = 172.16.17.1/24
MTU = 1280
ListenPort = $1
PostUp = $2
PostDown = whatever is here is never run
Jc = 5
H1 = 1

[Peer]
#_Name = alice
PublicKey = AAAAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
PresharedKey = PSKKkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk=
AllowedIPs = 172.16.17.2/32
C
}
# the stand-in: remembers what setconf was given, answers what the kernel "has"
cat > /usr/local/bin/awg <<'A'
#!/bin/sh
case "$1" in
  setconf) cat > /tmp/t/setconf.in; echo x >> /tmp/t/setconf.calls; cat /tmp/t/port.next > /tmp/t/port.kernel 2>/dev/null; exit 0 ;;
  pubkey)  cat >/dev/null; echo "PUBofPRIV=" ;;
  show)    case "$3" in listen-port) cat /tmp/t/port.kernel ;; public-key) cat /tmp/t/pub.kernel ;; esac ;;
esac
A
chmod +x /usr/local/bin/awg
reset() {
    ip link del awg0 2>/dev/null; iptables -F; iptables -X 2>/dev/null; iptables -t nat -F; iptables -t mangle -F; iptables -t mangle -X 2>/dev/null
    : > $T/setconf.calls; echo 58728 > $T/port.next; echo 58728 > $T/port.kernel; echo "PUBofPRIV=" > $T/pub.kernel; conf 58728 "$POSTUP"
}
gate() { iptables -t mangle -N CASCADE; iptables -t mangle -A PREROUTING -s 172.16.17.0/24 -i awg0 -j CASCADE; }
jumps() { for c in CASCADE-PEERS DOCKER-USER DOCKER-FORWARD; do iptables -N $c; done; iptables -A FORWARD -i awg0 -o awg0 -j CASCADE-PEERS; iptables -A FORWARD -j DOCKER-USER; iptables -A FORWARD -j DOCKER-FORWARD; iptables -A FORWARD -j ACCEPT -m comment --comment ufw-stand-in; }
dump() { iptables -S FORWARD; iptables -t nat -S POSTROUTING; iptables -t mangle -S FORWARD; ip -4 -o addr show dev awg0 2>/dev/null | awk '{print $2, $4}'; ip -o link show dev awg0 2>/dev/null | grep -o 'mtu [0-9]*'; }
start() { $RUN > $T/log 2>&1 & PID=$!; }
stop() { kill -TERM $PID; wait $PID; RC=$?; }
check() { $RUN check > $T/check.json; CRC=$?; }
calls() { wc -c < $T/setconf.calls; }

# 1. no rules, no tunnel
reset; start; sleep 1.5
! ip link show awg0 >/dev/null 2>&1 && grep -q "no rules, no tunnel" $T/log; ok $? "правил каскада в ядре нет — интерфейс не создаётся, сервис ждёт"
check; [ $CRC = 1 ] && grep -q '"exists": false' $T/check.json && grep -q '"gate": false' $T/check.json; ok $? "check в этом состоянии: интерфейса нет, правил нет, код 1"
# 2. the rules appear → the interface is made, the way awg-quick makes it
gate; jumps; sleep 1.5
ip -4 -o addr show dev awg0 | grep -q '172.16.17.1/24' && ip -o link show dev awg0 | grep -q 'mtu 1280' && ip -o link show dev awg0 | grep -q 'UP'; ok $? "правила появились — интерфейс создан: адрес, MTU, включён"
grep -q '^PrivateKey' $T/setconf.in && grep -q '^ListenPort = 58728' $T/setconf.in && grep -q '^Jc = 5' $T/setconf.in && grep -q '^#_Name = alice' $T/setconf.in && grep -q '^AllowedIPs = 172.16.17.2/32' $T/setconf.in \
    && ! grep -qE '^(Address|MTU|PostUp|PostDown)' $T/setconf.in && [ "$(calls)" = 2 ]; ok $? "awg setconf получил файл без строк awg-quick (Address, MTU, PostUp, PostDown), с ключами и пирами — один раз"
want="-P FORWARD ACCEPT
-A FORWARD -i awg0 -o awg0 -j CASCADE-PEERS
-A FORWARD -j DOCKER-USER
-A FORWARD -j DOCKER-FORWARD
-A FORWARD -i awg0 -o awg0 -j DROP
-A FORWARD -i awg0 -j ACCEPT
-A FORWARD -m comment --comment ufw-stand-in -j ACCEPT"
[ "$(iptables -S FORWARD)" = "$want" ]; ok $? "правила в FORWARD встали как на сервере сегодня: ниже переходов каскада и Docker, изоляция выше разрешения"
[ "$(iptables -t nat -S POSTROUTING | grep -c -- '-o eth0 -j MASQUERADE')" = 1 ] && [ "$(iptables -t mangle -S FORWARD | grep -c 'TCPMSS --set-mss 1240')" = 2 ]; ok $? "NAT наружу и оба правила размера пакета на месте"
check; [ $CRC = 0 ] && grep -q '"matches": true' $T/check.json && grep -q '"rules_missing": 0' $T/check.json; ok $? "check: всё как в файле, код 0"
dump > $T/made
# 3. stopping leaves everything
stop; dump > $T/after-stop
[ $RC = 0 ] && diff -q $T/made $T/after-stop >/dev/null && grep -q "left as they are" $T/log; ok $? "SIGTERM: выход с кодом 0, интерфейс и правила остались как были"
# 4. started again: taken over, nothing set, nothing added
start; sleep 1.5; dump > $T/adopted
diff -q $T/made $T/adopted >/dev/null && [ "$(calls)" = 2 ] && grep -q "taken over as it is" $T/log; ok $? "повторный запуск: интерфейс принят как есть — setconf не вызывался, правила не задвоились"
# 5. a rule lost → put back, in its place
iptables -D FORWARD -i awg0 -o awg0 -j DROP; iptables -t mangle -D FORWARD -o awg0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240; sleep 1.5
[ "$(iptables -S FORWARD)" = "$want" ] && [ "$(iptables -t mangle -S FORWARD | grep -c 'TCPMSS')" = 2 ] && grep -q "rules put back: 2" $T/log; ok $? "пропавшие правила возвращены на свои места (изоляция снова выше разрешения)"
# 6. the interface gone → made again
ip link del awg0; sleep 1.5
ip -4 -o addr show dev awg0 2>/dev/null | grep -q '172.16.17.1/24' && [ "$(calls)" = 4 ] && [ "$(iptables -S FORWARD)" = "$want" ]; ok $? "интерфейс исчез — создан заново, правила не задвоились"
# 7. present but not what the file says → nothing is touched
echo 40000 > $T/port.kernel; n=$(calls); dump > $T/before-diff; sleep 1.5; dump > $T/after-diff
grep -q "NOT what the file says (порт)" $T/log && diff -q $T/before-diff $T/after-diff >/dev/null && [ "$(calls)" = "$n" ]; ok $? "интерфейс есть, но порт не тот, что в файле — сказано громко, ничего не изменено"
check; [ $CRC = 1 ] && grep -q '"matches": false' $T/check.json && ! grep -q 'PRIV\|PSKK' $T/check.json $T/log; ok $? "check: код 1; ни в выводе, ни в журнале нет ключей"
echo "OTHERKEY=" > $T/pub.kernel; echo 58728 > $T/port.kernel; sleep 1.5
grep -q "NOT what the file says (ключ)" $T/log; ok $? "то же при другом ключе"
stop
# 8. the gate checked only for making: an interface that exists is kept even with the rules gone
reset; gate; start; sleep 1.5; iptables -t mangle -F PREROUTING; sleep 1
ip link show awg0 >/dev/null 2>&1; ok $? "правила каскада пропали при живом интерфейсе — интерфейс не трогается (пользователи не отключаются)"
stop
# 9. a PostUp it does not know → refused, nothing made
reset; gate; conf 58728 "iptables -I FORWARD -i %i -j ACCEPT; curl example.org | sh"; start; sleep 1.5
! ip link show awg0 >/dev/null 2>&1 && grep -q "PostUp" $T/log && [ "$(calls)" = 0 ] && [ "$(iptables -S FORWARD | wc -l)" = 1 ]; ok $? "незнакомый PostUp — отказ: интерфейс не создан, ни одна строка из файла не исполнена"
stop
# 10. Table/DNS in the file → refused
reset; gate; sed -i 's/^MTU = 1280/MTU = 1280\nTable = off/' $TUNNEL_CONF; start; sleep 1.5
! ip link show awg0 >/dev/null 2>&1 && grep -q "Table" $T/log; ok $? "файл с Table (туннель другого рода) — отказ"
stop
# 11. made before the cascade's and Docker's jumps exist: they come later and go on top
reset; gate; start; sleep 1.5
for c in DOCKER-USER DOCKER-FORWARD; do iptables -N $c; done; iptables -I FORWARD 1 -j DOCKER-FORWARD; iptables -I FORWARD 1 -j DOCKER-USER
iptables -N CASCADE-PEERS; iptables -I FORWARD 1 -i awg0 -o awg0 -j CASCADE-PEERS; sleep 1
[ "$(iptables -S FORWARD | sed -n 2,6p)" = "$(echo "$want" | sed -n 2,6p)" ]; ok $? "туннель раньше Docker и сторожа: когда те приходят, порядок тот же"
stop
# 12. the link cannot be made (no module) → waits, then makes it
reset; gate; export TUNNEL_LINK_TYPE=nosuchmodule; start; sleep 1.5
! ip link show awg0 >/dev/null 2>&1 && grep -q "kernel module" $T/log; ok $? "модуля ядра нет — интерфейс не создан, сервис ждёт и не падает"
kill -0 $PID 2>/dev/null; ok $? "…и продолжает работать"
stop


# ── what the kernel announces is acted on at once, not at the next look on the clock ──
# (the looks on the clock are a minute apart here: whatever happens within a second is the kernel's event)
export TUNNEL_LINK_TYPE=dummy TUNNEL_PASS=60
reset; gate; jumps; start; sleep 1.5
ip link show awg0 >/dev/null 2>&1; ok $? "первый взгляд — сразу при запуске, не через минуту: интерфейс создан"
n=$(calls); ip link del awg0; sleep 1.2
ip -4 -o addr show dev awg0 2>/dev/null | grep -q '172.16.17.1/24' && [ "$(calls)" = $((n + 2)) ] && [ "$(iptables -S FORWARD)" = "$want" ]; ok $? "интерфейс удалён — ядро сообщило, он создан заново за секунду, без опроса; правила не задвоились"
n=$(calls); t=$(grep -c 'taken over as it is' $T/log); ip link set awg0 down; sleep 1.2
grep -q "NOT what the file says (интерфейс выключен)" $T/log && [ "$(calls)" = "$n" ] && ! ip -o link show dev awg0 | grep -q '[<,]UP[,>]'; ok $? "интерфейс выключили — сказано сразу, ничего не изменено"
ip link set awg0 up; sleep 1.2
[ "$(grep -c 'taken over as it is' $T/log)" = $((t + 1)) ] && [ "$(calls)" = "$n" ]; ok $? "включили обратно — снова принят как есть"
ip link add other0 type dummy; ip link del other0; sleep 0.8
[ "$(calls)" = "$n" ] && ! grep -q other0 $T/log; ok $? "события о чужих интерфейсах сервис не касаются"
iptables -D FORWARD -i awg0 -o awg0 -j DROP; sleep 1.5
! iptables -C FORWARD -i awg0 -o awg0 -j DROP 2>/dev/null; ok $? "о пропавшем правиле файрвола ядро не сообщает: его возвращает только взгляд по часам (через минуту — не сейчас)"
stop
[ $RC = 0 ] && grep -q "left as they are" $T/log; ok $? "SIGTERM и здесь: выход с кодом 0"
# the events cannot be listened to: said once, and the looks on the clock keep everything
export TUNNEL_PASS=0.3 TUNNEL_IP=/nonexistent/ip TUNNEL_RETRY_SECONDS=0.3
reset; gate; jumps; start; sleep 1.5; ip link del awg0; sleep 1.5
ip link show awg0 >/dev/null 2>&1 && [ "$(grep -c 'link events cannot be listened to' $T/log)" = 1 ] && kill -0 $PID 2>/dev/null; ok $? "слушать события нечем — сказано один раз, сервис жив и держит интерфейс взглядами по часам"
stop
unset TUNNEL_IP TUNNEL_RETRY_SECONDS

# ── the links to the exits (TUNNEL_GLOB): one process, every awgk*.conf ───────
unset TUNNEL_CONF; export TUNNEL_LINK_TYPE=dummy TUNNEL_GLOB='awgk*' TUNNEL_DIR=$T/links
lconf() {   # $1 = name, $2 = address, $3 = the Table line
    cat > $TUNNEL_DIR/$1.conf <<C
[Interface]
PrivateKey = PRIVpppppppppppppppppppppppppppppppppppppp=
Address = $2
MTU = 1280
$3
Jc = 5

[Peer]
PublicKey = AAAAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
PresharedKey = PSKKkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk=
Endpoint = 198.51.100.7:47212
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
C
}
lreset() {
    for i in awg0 awgk1 awgk2 awgk3 awgx1; do ip link del $i 2>/dev/null; done
    iptables -F; iptables -X 2>/dev/null; iptables -t nat -F; iptables -t mangle -F; iptables -t mangle -X 2>/dev/null
    mkdir -p $TUNNEL_DIR; for f in $TUNNEL_DIR/*.conf; do [ -e "$f" ] && mv -f "$f" $T/gone.conf; done
    : > $T/setconf.calls; echo "PUBofPRIV=" > $T/pub.kernel
    lconf awgk1 10.77.1.2/32 "Table = off"; lconf awgk2 10.77.2.2/32 "Table = off"; lconf awgx1 10.99.1.2/32 "Table = off"
}
idx() { ip -o link show "$1" 2>/dev/null | cut -d: -f1; }
fw() { iptables-save | grep -c '^-A' || true; }

lreset; start; sleep 1.5
ip -4 -o addr show dev awgk1 | grep -q '10.77.1.2/32' && ip -4 -o addr show dev awgk2 | grep -q '10.77.2.2/32' && ip -o link show dev awgk2 | grep -q 'mtu 1280' && ip -o link show dev awgk2 | grep -q 'UP'
ok $? "связи с выходами: обе созданы без ожидания правил каскада — адрес, MTU, включены"
! ip link show awgx1 >/dev/null 2>&1; ok $? "файл с другим именем (awgx1) не тронут"
[ "$(fw)" = 0 ] && [ -z "$(ip route show default)" ]; ok $? "ни одного правила файрвола и ни одного маршрута сервис не поставил (маршруты — дело сторожа)"
grep -q '^Endpoint = 198.51.100.7:47212' $T/setconf.in && grep -q '^PersistentKeepalive = 25' $T/setconf.in && grep -q '^Jc = 5' $T/setconf.in && ! grep -qiE '^(Address|MTU|Table)' $T/setconf.in && [ "$(calls)" = 4 ]
ok $? "awg setconf: адрес выхода и keepalive переданы, строк awg-quick (Address, MTU, Table) нет — по одному разу на связь"
check; [ $CRC = 0 ] && [ "$(grep -o '"matches": true' $T/check.json | wc -l)" = 2 ]; ok $? "check: обе связи как в файлах, код 0"
a=$(idx awgk1); b=$(idx awgk2); stop; n=$(calls); start; sleep 1.5
[ $RC = 0 ] && [ "$(idx awgk1)" = "$a" ] && [ "$(idx awgk2)" = "$b" ] && [ "$(calls)" = "$n" ] && [ "$(grep -c 'taken over as it is' $T/log)" = 2 ]; ok $? "остановка оставляет связи; повторный запуск принимает обе как есть"
lconf awgk3 10.77.3.2/32 "Table = off"; sleep 1.5
ip -4 -o addr show dev awgk3 2>/dev/null | grep -q '10.77.3.2/32'; ok $? "появился файл нового выхода — связь создана без перезапуска сервиса"
ip link del awgk1; sleep 1.5
ip -4 -o addr show dev awgk1 2>/dev/null | grep -q '10.77.1.2/32' && [ "$(idx awgk2)" = "$b" ]; ok $? "одна связь исчезла — создана заново, вторая не тронута"
echo "OTHERKEY=" > $T/pub.kernel; n=$(calls); sleep 1.5
grep -q "awgk2 is there but is NOT what the file says (ключ)" $T/log && [ "$(calls)" = "$n" ] && [ "$(idx awgk2)" = "$b" ]; ok $? "связь есть, но ключ не тот, что в файле — сказано, ничего не изменено"
check; [ $CRC = 1 ] && ! grep -q 'PRIV\|PSKK' $T/check.json $T/log; ok $? "check: код 1; ключей нет ни в выводе, ни в журнале"
stop
lreset; lconf awgk1 10.77.1.2/32 "# no table line"; start; sleep 1.5
! ip link show awgk1 >/dev/null 2>&1 && ip link show awgk2 >/dev/null 2>&1 && grep -q "Table = off" $T/log; ok $? "файл связи без Table = off — отказ (иначе нужен был бы маршрут по умолчанию); вторая связь создана"
check; [ $CRC = 1 ]; ok $? "check при этом: код 1"
stop
lreset; sed -i 's/^MTU = 1280/MTU = 1280\nPostUp = iptables -F/' $TUNNEL_DIR/awgk2.conf; start; sleep 1.5
! ip link show awgk2 >/dev/null 2>&1 && grep -q "PostUp" $T/log; ok $? "файл связи с PostUp — отказ, ничего не исполнено"
stop
export TUNNEL_PASS=60
lreset; start; sleep 1.5; b=$(idx awgk2); n=$(calls); ip link del awgk1; sleep 1.2
ip -4 -o addr show dev awgk1 2>/dev/null | grep -q '10.77.1.2/32' && [ "$(idx awgk2)" = "$b" ] && [ "$(calls)" = $((n + 2)) ]; ok $? "связь удалена — по событию ядра создана заново за секунду, вторая не тронута"
ip link add awgx1 type dummy; sleep 0.8
[ "$(calls)" = $((n + 2)) ] && ! ip -4 -o addr show dev awgx1 | grep -q 10.99; ok $? "событие о интерфейсе, чей файл не подходит под шаблон, ничего не запускает"
ip link del awgx1; stop
export TUNNEL_PASS=0.3
s=$($RUN strip-sum awgk1); $RUN strip-sum awgx1 >/dev/null 2>&1; r=$?
[ ${#s} = 64 ] && [ $r = 1 ]; ok $? "strip-sum по имени связи; чужое имя — отказ"
lreset; for f in $TUNNEL_DIR/*.conf; do mv -f "$f" $T/gone.conf; done; check
[ $CRC = 1 ]; ok $? "ни одного файла связи — check не говорит «всё хорошо»"
