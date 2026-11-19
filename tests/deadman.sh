#!/usr/bin/env bash
# The reboot safety net (stack/deadman.sh): when it rolls back and when it must
# not. Runs in throwaway directories with DM_DRY=1 — the rollback's commands are
# printed, never run.
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
PASS=0; FAIL=0
ok() { if [ "$1" = 0 ]; then PASS=$((PASS + 1)); echo "ok   $2"; else FAIL=$((FAIL + 1)); echo "FAIL $2"; fi; }
fresh() { D=$(mktemp -d -p "$T"); mkdir "$D/boot" "$D/run" "$D/units" "$D/stack" "$D/awg"; : > "$D/awg/awgk2.conf"; : > "$D/awg/awgk7.conf"; : > "$D/awg/awgx1.conf"; echo boot-one > "$D/id"; echo proven > "$D/boot/gate.pyz.proven"; echo changed > "$D/boot/gate.pyz"; }
dm() { DM_DRY=1 DM_AWG_DIR="$D/awg" DM_STACK="$D/stack" DM_DIR="$D/boot" DM_RUN="$D/run" DM_UNITS="$D/units" DM_BOOT_ID="$D/id" DM_VALID="${VALID:-600}" bash "$HERE/stack/deadman.sh" "$@" 2>&1; }
reboot() { echo boot-two > "$D/id"; : > "$D/gone"; mv "$D/run" "$D/run.old"; mkdir "$D/run"; }

fresh; out=$(dm fire)
[ -z "$out" ]; ok $? "не взведён — ничего не делает"

fresh; dm arm >/dev/null; out=$(dm fire)
[ -f "$D/boot/deadman.armed" ] && ! grep -q "would run" <<<"$out"; ok $? "взведён, перезагрузки ещё не было — ничего не делает и остаётся взведённым"
dm confirm >/dev/null; [ $? != 0 ] && [ ! -e "$D/run/vpn-deadman.ok" ]; ok $? "подтверждение до перезагрузки не принимается"

fresh; dm arm >/dev/null; reboot; out=$(dm fire)
grep -q "ROLLBACK" <<<"$out" && grep -q "would run: cp -p $D/boot/gate.pyz.proven $D/boot/gate.pyz" <<<"$out" \
    && grep -q "would run: systemctl restart cascade-apply.service" <<<"$out" && grep -q "would run: systemctl restart awg-quick@awg0.service" <<<"$out" \
    && grep -q "would run: timeout 90 docker start vpn-watch" <<<"$out" && grep -q "would run: timeout 120 docker restart -t 40 vpn-resolver" <<<"$out" && grep -q "would run: timeout 90 docker start traefik smartdns" <<<"$out"
ok $? "перезагрузка без подтверждения — откат: проверенный код правил, правила, туннель, сторож маршрутов, DNS, прокси"
a=$(grep -n "cp -p $D/boot/gate.pyz.proven" <<<"$out" | cut -d: -f1); b=$(grep -n "systemctl restart cascade-apply" <<<"$out" | cut -d: -f1)
[ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ] && ! grep -q "cascade.sh\|cascade-watch\|AdGuardHome.service\|vpn-panel-agent" <<<"$out"; ok $? "проверенный код возвращается до применения правил; старые скрипты и службы хоста откату не нужны"
[ ! -e "$D/boot/deadman.armed" ] && [ -f "$D/boot/deadman.last" ]; ok $? "после срабатывания взвод снят"
out=$(dm fire); [ -z "$out" ]; ok $? "повторный запуск — ничего (отката по кругу не бывает)"

fresh; dm arm >/dev/null; reboot; dm confirm >/dev/null; out=$(dm fire)
! grep -q "would run" <<<"$out" && grep -q "confirmed" <<<"$out" && [ ! -e "$D/boot/deadman.armed" ]; ok $? "перезагрузка с подтверждением — отката нет, взвод снят"
[ "$(cat "$D/boot/gate.pyz.proven")" = changed ]; ok $? "подтверждённая загрузка: её код правил запомнен как проверенный"

dm confirm >/dev/null; [ $? = 2 ]; ok $? "подтверждение после срока (взвод уже снят) — отказ с кодом 2, а не «принято»"

fresh; dm arm >/dev/null; : > "$D/run/vpn-deadman.ok"; reboot; out=$(dm fire)
grep -q "ROLLBACK" <<<"$out"; ok $? "подтверждение, оставшееся с прошлой загрузки, не считается"

fresh; VALID=-5 dm arm >/dev/null; reboot; out=$(dm fire)
! grep -q "would run" <<<"$out" && grep -q "too old" <<<"$out" && [ ! -e "$D/boot/deadman.armed" ]; ok $? "взвод просрочен — ничего не трогает и снимается"

fresh; dm arm >/dev/null; dm disarm >/dev/null; reboot; out=$(dm fire)
[ -z "$out" ]; ok $? "снят вручную — после перезагрузки ничего не делает"

fresh; dm arm >/dev/null; reboot; mv "$D/boot/gate.pyz.proven" "$D/boot/x"; out=$(dm fire)
grep -q "no proven copy" <<<"$out" && ! grep -q "would run: cp" <<<"$out" && grep -q "would run: systemctl restart cascade-apply.service" <<<"$out"; ok $? "проверенной копии нет — код не трогает, остальное делает"
fresh; cp "$D/boot/gate.pyz.proven" "$D/boot/gate.pyz"; dm arm >/dev/null; reboot; out=$(dm fire)
! grep -q "would run: cp" <<<"$out" && grep -q "would run: systemctl restart cascade-apply.service" <<<"$out"; ok $? "код правил тот же, что проверенный — не подменяется"

fresh; dm arm >/dev/null; reboot; out=$(dm fire)
! grep -q "vpn-tunnel\|awg-quick down" <<<"$out"; ok $? "сервер не переключён на сервис туннеля — откат туннель в контейнере не ищет"

fresh; : > "$D/stack/tunnel.enabled"; dm arm >/dev/null; reboot; out=$(dm fire)
n() { grep -n -- "$1" <<<"$out" | head -1 | cut -d: -f1; }
a=$(n "docker stop -t 20 vpn-tunnel"); b=$(n "systemctl enable awg-quick@awg0.service"); c=$(n "would run: awg-quick down awg0"); d=$(n "systemctl restart cascade-apply.service"); e=$(n "systemctl restart awg-quick@awg0.service")
[ -n "$a" ] && [ -n "$b" ] && [ -n "$c" ] && [ -n "$d" ] && [ -n "$e" ] && [ "$a" -lt "$b" ] && [ "$b" -lt "$c" ] && [ "$c" -lt "$d" ] && [ "$d" -lt "$e" ]
ok $? "сервер переключён на сервис туннеля — откат: контейнер остановлен, юнит хоста включён, интерфейс снят по-awg-quick, затем правила и туннель средствами хоста"
grep -q "would run: mv -f $D/stack/tunnel.enabled $D/stack/tunnel.enabled.rolled-back" <<<"$out"; ok $? "…и отметка о переключении убрана: следующая выкладка контейнер туннеля не поднимет"

fresh; dm arm >/dev/null; reboot; out=$(dm fire)
! grep -q "vpn-links\|ip link del" <<<"$out" && grep -q "would run: systemctl start awg-quick@awgk2.service" <<<"$out" && grep -q "would run: systemctl start awg-quick@awgk7.service" <<<"$out" && ! grep -q "awgx1\|awgk1" <<<"$out"
ok $? "связи с выходами не в контейнере — откат их не удаляет, только запускает юниты; список связей — по файлам awgk*.conf"

fresh; : > "$D/stack/links.enabled"; dm arm >/dev/null; reboot; out=$(dm fire)
a=$(n "docker stop -t 20 vpn-links"); b=$(n "systemctl enable awg-quick@awgk2.service"); c=$(n "would run: ip link del awgk2"); c7=$(n "would run: ip link del awgk7"); d=$(n "systemctl restart cascade-apply.service"); e=$(n "systemctl start awg-quick@awgk2.service"); e7=$(n "systemctl start awg-quick@awgk7.service")
[ -n "$a" ] && [ -n "$b" ] && [ -n "$c" ] && [ -n "$c7" ] && [ -n "$d" ] && [ -n "$e" ] && [ -n "$e7" ] && [ "$a" -lt "$b" ] && [ "$b" -lt "$c" ] && [ "$c" -lt "$d" ] && [ "$d" -lt "$e" ]
ok $? "связи с выходами в контейнере — откат: контейнер остановлен, юниты включены, сделанные им связи сняты, затем правила и связи средствами хоста"
grep -q "would run: mv -f $D/stack/links.enabled $D/stack/links.enabled.rolled-back" <<<"$out" && ! grep -q "vpn-tunnel" <<<"$out"; ok $? "…отметка убрана; туннель пользователей при этом не трогается, если он не в контейнере"

bash -n "$HERE/stack/deadman.sh" && bash -n "$HERE/ops/reboot-entry.sh" && bash -n "$HERE/stack/switch-tunnel.sh"; ok $? "синтаксис скриптов"
echo
echo "$PASS of $((PASS + FAIL)) checks passed"
[ "$FAIL" = 0 ]
