#!/usr/bin/env bash
# The rules in a real kernel: the watch service installs the cascade into a
# throwaway network namespace (a container, no network), and what the kernel
# then holds — iptables, ipsets, rules, routes, health — is compared with what
# it held when the service was checked (tests/golden/kernel-*.txt; the 1024
# Russian networks are counted, not listed). Also: the service twice (a
# restart) must change nothing, and the boot orders must end in the same kernel.
# A change of the rules on purpose: run the scenario, look at the difference,
# and replace the golden file with the new dump (minus the `add ru ` lines).
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT=$(mktemp -d)
# One image for both services of these scenarios, the watch service and the tunnel service (the awgscale
# package) — the project's own recipe with the kernel tools the watch service drives. The boot gate is run
# the way the server runs it: as one file (awgscale.watch.bundle) on the bare system Python.
IMAGE=${KERNEL_TEST_IMAGE:-vpn-watch:test}
docker build -q -f "$HERE/docker/Dockerfile" --build-arg APT="iptables ipset iproute2 conntrack iputils-ping" -t "$IMAGE" "$HERE" >/dev/null
# the very privileges of the service in compose.yml: nothing but these two capabilities, a read-only root
inside() { docker run --rm --network none --read-only --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW --security-opt no-new-privileges:true \
    --tmpfs /root/awg/cascade:exec --tmpfs /run/cascade --tmpfs /usr/local/bin:exec --tmpfs /tmp -e MODE="${2:-}" -v "$HERE:/src:ro" "$IMAGE" bash /src/tests/kernel_inside.sh "$1"; }
pass=0; fail=0
ok() { if [ "$1" = 0 ]; then pass=$((pass + 1)); echo "ok   $2"; else fail=$((fail + 1)); echo "FAIL $2"; fi; }

# same() <dump> <golden name> <what>: the kernel as it was when the service was checked
same() {
    if diff <(grep -v '^add ru ' "$1") "$HERE/tests/golden/kernel-$2.txt" > "$OUT/diff.$2"; then ok 0 "$3 ($(grep -c . "$1") строк правил, наборов, маршрутов, здоровья)"
    else ok 1 "$3: ядро отличается от tests/golden/kernel-$2.txt"; head -30 "$OUT/diff.$2"; fi
}
for mode in list geo; do
    inside service "$mode" > "$OUT/service.$mode"
    same "$OUT/service.$mode" "$mode" "режим $mode: ядро после сервиса то же, что записано"
done
s="$OUT/service.list"
grep -q -- '-A CASCADE -p udp -m udp ! --dport 443 -m set ! --match-set ru dst -g CASCADE-MARK' "$s" && ! grep -q -- '-A CASCADE -g CASCADE-MARK' "$s"; ok $? "список: настоящий iptables принял правила, помечается только список и UDP"
grep -q -- '-A CASCADE -m set --match-set ru dst -j RETURN' "$OUT/service.geo" && grep -q -- '-A CASCADE -g CASCADE-MARK' "$OUT/service.geo"; ok $? "geo: российское не помечается, остальное помечается"
[ "$(grep -c '^add ru ' "$s")" = 1025 ] && [ "$(grep -c '^add ru ' "$OUT/service.geo")" = 1025 ]; ok $? "набор ru: 1024 сети из файла и одна из настроек"
grep -q '^/run/cascade/exit1.state up' "$s" && grep -q '^/run/cascade/exit3.state down' "$s"; ok $? "выход 1 отвечает — up, выход 3 молчит — down"
[ "$(grep -c '^add cx_1 ' "$s")" = 5 ] && [ "$(grep -c '^add cx_3 ' "$s")" = 0 ]; ok $? "все пять клиентов на живом выходе, включая закреплённого за лежащим"
grep -A1 '^table 299' "$s" | grep -q 'default dev awgk1 .*metric 10' && [ "$(grep -c 'blackhole default metric 1000' "$s")" = 3 ]; ok $? "запасная таблица ведёт в живой выход, под каждой таблицей blackhole"
grep -q '^add sp_ab12 172.16.17.2' "$s" && grep -q '^add sp_ab12 172.16.17.3' "$s" && ! grep -q '^add sp_ab12 172.16.17.9' "$s"; ok $? "пространство: только адреса, на которых в туннеле тот же ключ"
a=$(grep -n -- '-A FORWARD -i awg0 -o awg0 -j CASCADE-PEERS' "$s" | cut -d: -f1); b=$(grep -n -- '-A FORWARD -i awg0 -o awg0 -j DROP' "$s" | cut -d: -f1)
[ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]; ok $? "переход в пространства стоит выше правила изоляции"
grep -q '^add force_exit 1.2.3.4' "$s" && grep -q '^add force_exit_net 149.154.160.0/20' "$s" && grep -q '^add force_exit_net 91.108.56.0/22' "$s"; ok $? "выученные адреса восстановлены, сети списка и автоматические загружены"

[ "$(inside service-twice | tail -1)" = SAME ]; ok $? "перезапуск сервиса ничего не меняет в ядре"

# the boot: the gate runs before any tunnel exists, with nothing in /run
inside boot-service > "$OUT/boot.service"
same "$OUT/boot.service" boot "загрузка: gate.pyz apply на пустом ядре без туннелей ставит то, что записано"
[ "$(grep -c '^add ru ' "$OUT/boot.service")" = 1025 ]; ok $? "загрузка: набор ru полон уже при загрузке"
grep -q -- '-A CASCADE' "$OUT/boot.service" && [ "$(grep -c 'blackhole default metric 1000' "$OUT/boot.service")" = 3 ] && ! grep -q 'default dev awgk' "$OUT/boot.service"; ok $? "загрузка: правила стоят, под каждой таблицей blackhole, маршрутов в выходы ещё нет — закрыто"
grep -q '^from 172.30.99.0/24 lookup 298\|from 172.30.99.0/24 lookup 298' "$OUT/boot.service"; ok $? "загрузка: сеть служебных контейнеров уже направлена в выход"
inside boot-then-service > "$OUT/boot.full"
if diff "$OUT/service.list" "$OUT/boot.full" > "$OUT/diff.full"; then ok 0 "загрузка целиком (правила → туннели → сервис) приводит к тому же ядру, что обычный порядок"
else ok 1 "загрузка целиком: ядро отличается от обычного порядка"; head -30 "$OUT/diff.full"; fi
# the boot after the users' tunnel became the container's: the watch service is up before awg0 exists
inside boot-late-tunnel > "$OUT/boot.late"
grep -q '^WATCH-BEFORE-TUNNEL ok' "$OUT/boot.late" && grep -q '^TUNNEL-MADE ok' "$OUT/boot.late" && grep -q '^WATCH-AFTER-TUNNEL ok' "$OUT/boot.late"
ok $? "туннель в контейнере: сторож отрабатывает проходы без awg0, сервис туннеля создаёт его, сторож продолжает — без сбоев"
a=$(grep -n -- '-A FORWARD -i awg0 -o awg0 -j CASCADE-PEERS' "$OUT/boot.late" | cut -d: -f1); b=$(grep -n -- '-A FORWARD -i awg0 -o awg0 -j DROP' "$OUT/boot.late" | cut -d: -f1); c=$(grep -n -- '-A FORWARD -i awg0 -j ACCEPT' "$OUT/boot.late" | cut -d: -f1)
[ -n "$a" ] && [ -n "$b" ] && [ -n "$c" ] && [ "$a" -lt "$b" ] && [ "$b" -lt "$c" ]; ok $? "туннель в контейнере: настоящий сторож держит переход в пространства выше изоляции, изоляция выше разрешения"
# apart from the tunnel's own four rules (the stand-in tunnel of the usual order has only the isolation one) the kernel is the same
own='^-A FORWARD -i awg0 -j ACCEPT$|^-A POSTROUTING -o eth0 -j MASQUERADE$|^-A FORWARD -[io] awg0 .*TCPMSS --set-mss 1240$'
counts='^/run/cascade/exit[0-9]+\.(ok|fail) '        # how many passes each exit has answered: this order makes twice as many
if diff <(grep -v '^WATCH-\|^TUNNEL-MADE' "$OUT/boot.late" | grep -vE -- "$own" | grep -vE "$counts") <(grep -vE "$counts" "$OUT/boot.full") > "$OUT/diff.late"; then ok 0 "туннель в контейнере: остальное ядро то же, что при обычном порядке загрузки"
else ok 1 "туннель в контейнере: ядро отличается от обычного порядка"; head -30 "$OUT/diff.late"; fi
# the boot after the exit links became the container's as well: the watch service is up before any link exists
inside boot-late-links > "$OUT/boot.links"
grep -q '^WATCH-BEFORE-LINKS ok' "$OUT/boot.links" && grep -q '^LINKS-MADE ok' "$OUT/boot.links" && grep -q '^WATCH-AFTER-LINKS ok' "$OUT/boot.links"
ok $? "связи в контейнере: сторож отрабатывает проходы без единой связи, сервис создаёт их, сторож продолжает — без сбоев"
grep -q '^NO-VERDICT-WITHOUT-LINK ok' "$OUT/boot.links" && grep -q '^CLOSED-WITHOUT-LINK ok' "$OUT/boot.links"; ok $? "связи в контейнере: пока связи нет, выход не объявляется «упавшим», а трафик закрыт"
grep -q '^UP-AT-FIRST-PROBE ok' "$OUT/boot.links"; ok $? "связи в контейнере: связь появилась — выход работает с первой же удачной пробы, без минуты ожидания"
if diff <(grep -v '^WATCH-\|^LINKS-MADE\|^NO-VERDICT\|^CLOSED-\|^UP-AT' "$OUT/boot.links" | grep -vE "$counts") <(grep -vE "$counts" "$OUT/boot.full") > "$OUT/diff.links"; then ok 0 "связи в контейнере: ядро в итоге то же, что при обычном порядке загрузки"
else ok 1 "связи в контейнере: ядро отличается от обычного порядка"; head -30 "$OUT/diff.links"; fi
[ "$(inside boot-broken | tail -1)" = "EXIT nonzero" ]; ok $? "загрузка без настроек: код выхода не ноль — туннель не поднимется"

# a single server (SINGLE_SERVER=1): no exits, no country list
inside boot-single single > "$OUT/single"
m="$OUT/single"
grep -q '^GATE ok' "$m"; ok $? "один сервер: загрузочный шлюз ставит правила без выходов и без списка стран"
grep -q -- '-A CASCADE -d 172.16.17.0/24 -j RETURN' "$m" && ! grep -q -- '-j CASCADE-MARK\|-j MARK\|CONNMARK' "$m"; ok $? "один сервер: цепочка есть, ничего не помечается — интернет уходит с самого сервера"
! grep -q '^ru$\|^cx_' <(sed -n '/^== ipset names/,/^== rules/p' "$m") && ! grep -q 'lookup 2[0-9][0-9]' "$m" && ! grep -q 'blackhole' "$m"; ok $? "один сервер: ни набора стран, ни таблиц выходов, ни blackhole"
grep -q '^add sp_ab12 172.16.17.2' "$m" && grep -q '^add sp_ab12 172.16.17.3' "$m" && ! grep -q '^add sp_ab12 172.16.17.9' "$m"; ok $? "один сервер: пространства загружены, только адреса с тем же ключом"
a=$(grep -n -- '-A FORWARD -i awg0 -o awg0 -j CASCADE-PEERS' "$m" | cut -d: -f1); b=$(grep -n -- '-A FORWARD -i awg0 -o awg0 -j DROP' "$m" | cut -d: -f1)
[ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]; ok $? "один сервер: переход в пространства выше правила изоляции — устройства пространства видят друг друга"
inside single-to-cascade single > "$OUT/grown"
g="$OUT/grown"
grep -q '^SERVICE ok' "$g" && grep -q '^/run/cascade/exit1.state up' "$g" && grep -q -- '-A CASCADE -m set --match-set force_exit dst -g CASCADE-MARK' "$g"
ok $? "один сервер получил первый выход: правила каскада стоят, выход отвечает и работает"
[ "$(grep -c '^add cx_1 ' "$g")" = 5 ] && grep -A1 '^table 201' "$g" | grep -q 'default dev awgk1'; ok $? "…и все пользователи закреплены за ним, его таблица ведёт в него"
[ "$(inside gate-only single-with-exit | tail -1)" = "EXIT nonzero" ]; ok $? "один сервер, но выход назван: шлюз отказывает — ошибка настройки не проходит молча"
echo
echo "$pass of $((pass + fail)) checks passed"
[ "$fail" = 0 ]
