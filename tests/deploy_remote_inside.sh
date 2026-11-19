#!/usr/bin/env bash
# Runs INSIDE a throwaway container (see tests/deploy_remote.sh): a pretend
# /opt/vpn-stack, and in PATH stand-ins that write down every call of docker,
# systemctl and ufw instead of making it. The script under test is the part of
# stack/deploy.sh between `<<'REMOTE'` and `REMOTE`, exactly as it is sent.
set -u
[ -f /.dockerenv ] && [ -d /src/stack ] || { echo "FAIL this script runs only inside the container of tests/deploy_remote.sh"; exit 1; }
ok() { if [ "$1" = 0 ]; then echo "ok   $2"; else echo "FAIL $2"; fi; }
awk "/<<'REMOTE'\$/{inside=1; next} /^REMOTE\$/{inside=0} inside" /src/stack/deploy.sh > /remote.sh
mkdir -p /stub /ctl
cat > /stub/docker <<'S'
#!/bin/bash
echo "docker $*" >> /ctl/log
case "$1" in
  inspect)
    case "$*" in
      *Health.Status*)   echo healthy ;;
      *"Config.Image}} {{.State.Running"*) echo "$(cat "/ctl/image.${!#}" 2>/dev/null) true" ;;
      *Config.Image*)    cat "/ctl/image.${!#}" 2>/dev/null || exit 1 ;;
      # the ID of the image a container runs: what it was started from (`runid.<container>`), else its name's first ID
      *"-f {{.Image}}"*) [ -e "/ctl/image.${!#}" ] || exit 1; cat "/ctl/runid.${!#}" 2>/dev/null || echo "id:$(cat "/ctl/image.${!#}")" ;;
      *RestartCount*)    echo "true 0" ;;
      *State.Running*)   echo true ;;
    esac ;;
  image)
    # the ID a name points to: a new build (`newid.<name>`) or the name's first ID
    case "$*" in
      *"image inspect -f {{.Id}} "*) cat "/ctl/newid.${!#}" 2>/dev/null || echo "id:${!#}" ;;
    esac ;;
  run)
    case "$*" in
      *"--entrypoint iptables"*) cat /ctl/iptables.image ;;
      *AdGuardHome*)             echo "AdGuard Home, version v0.107.80" ;;
    esac ;;
  exec)
    case "$*" in
      *awgscale.watch.bundle*)     if [ -e /ctl/broken-bundle ]; then echo "not a bundle"; else /app/.venv/bin/python -m awgscale.watch.bundle -; fi ;;
      *AdGuardHome*)               echo "AdGuard Home, version v0.107.80" ;;
      *"awgscale.tunnel check"*)   [ ! -e /ctl/fail.tunnel ] || exit 1 ;;
    esac ;;
  compose)
    case "$*" in
      # a service that comes up runs the image the compose file names; `stuck.<service>`: the start failed, the old container stays
      "compose up -d --no-deps "*)
        service=${!#}
        case "$service" in traefik|smartdns) container=$service ;; *) container=vpn-$service ;; esac
        case "$service" in smartdns) image=awgscale-dnsgate:local ;; links) image=awgscale-tunnel:local ;; traefik) image=traefik:v3 ;; *) image=awgscale-$service:local ;; esac
        if [ ! -e "/ctl/stuck.$service" ]; then echo "$image" > "/ctl/image.$container"; cp "/ctl/newid.$image" "/ctl/runid.$container" 2>/dev/null || rm -f "/ctl/runid.$container"; fi ;;
      *"compose.yml.next config -q"*) [ ! -e /ctl/compose-refused ] || exit 1 ;;
      *store-init*)                   echo '{"ok": true, "moved": false}' ;;
    esac ;;
esac
exit 0
S
for tool in systemctl systemd-analyze; do printf '#!/bin/bash\necho "%s $*" >> /ctl/log\ncase "$*" in "is-active -q AdGuardHome") exit 1 ;; esac\nexit 0\n' "$tool" > /stub/$tool; done
# the firewall as the server has it: the two ports open, no rule of an older layout
printf '#!/bin/bash\necho "ufw $*" >> /ctl/log\ncase "$1" in status) printf "443/tcp                    ALLOW       Anywhere\\n80/tcp                     ALLOW       Anywhere\\n" ;; esac\nexit 0\n' > /stub/ufw
printf '#!/bin/bash\ncat /ctl/iptables.host\n' > /stub/iptables
printf '#!/bin/bash\nexit 0\n' > /stub/sleep
chmod +x /stub/*
# the probes: each says yes unless the test said it must not
cat > /probes.stub <<'S'
probe() { [ ! -e "/ctl/fail.$1" ]; }
users_answer() { probe users; }
watch_answers() { probe watch; }
exits_answer() { probe exits; }
dns_answers() { probe dns; }
resolver_asks() { probe resolver; }
agent_asks() { probe agent; }
panel_answers() { probe panel; }
dnsgate_answers() { probe smartdns; }
container_holds() { probe "${1#vpn-}"; }
wait_for() { shift; "$@"; }
S
mkdir -p /root/awg/cascade
cat > /root/awg/cascade/cascade.conf <<'C'
CLIENT_IF="awg0"
CLIENT_SUBNET="172.16.17.0/24"
UDP_VIA_EXIT=1
EXITS=(
    "1:awgk1:10.77.1.2:exit1-nl:2"
)
DNS_VIA_EXIT=(1.1.1.1 9.9.9.9)
VIA_EXIT_SUBNETS=(172.30.99.0/24)
PROBE_TARGETS=(1.1.1.1 9.9.9.9)
EXTRA_RU_NETS="203.0.113.0/24"
C
# the entry's side of that exit's link (only its name matters here)
mkdir -p /etc/amnezia/amneziawg && : > /etc/amnezia/amneziawg/awgk1.conf

# a server that runs the stack of an earlier deploy, with the new files shipped beside it (what `stack/deploy.sh` without names leaves)
fresh() {
    rm -rf /opt/vpn-stack /ctl /etc/systemd/system/cascade-apply.service /etc/systemd/system/vpn-deadman.service /etc/systemd/system/vpn-deadman.timer
    mkdir -p /opt/vpn-stack/traefik /opt/vpn-stack/boot /opt/vpn-stack/db-backup /etc/systemd/system /ctl
    cd /opt/vpn-stack
    printf 'services:\n  agent:\n    image: awgscale-agent:local\n    # an earlier deploy\n' > compose.yml
    cp /src/stack/compose.yml compose.yml.next
    cp /probes.stub probes.sh
    cp /src/stack/cascade-apply.service /src/stack/deadman.sh /src/stack/vpn-deadman.service /src/stack/vpn-deadman.timer .
    echo old-unit > /etc/systemd/system/cascade-apply.service
    echo "old gate" > boot/gate.pyz; echo "old proven gate" > boot/gate.pyz.proven; echo "old safety net" > boot/deadman.sh
    echo routing > traefik/dynamic.yml; echo routing-new > traefik/dynamic.yml.next; echo static > traefik/traefik.yml; echo static > traefik/traefik.yml.next
    for name in agent users watch exits resolver tunnel links bot panel; do echo "awgscale-$name:local" > /ctl/image.vpn-$name; done
    echo "awgscale-dnsgate:local" > /ctl/image.smartdns; echo "traefik:v3" > /ctl/image.traefik
    echo "iptables v1.8.11 (nf_tables)" > /ctl/iptables.host; echo "iptables v1.8.11 (nf_tables)" > /ctl/iptables.image
    : > /ctl/log
}
deploy() { RC=0; OUT=$(cd / && SERVICES="$1" OLD_PORTS=8443 EXIT_NET=172.30.99.0/24 PATH=/stub:$PATH bash /remote.sh 2>&1) || RC=$?; }
ups() { grep -E '^docker compose( -f [^ ]+)? up ' /ctl/log | sed -E 's/^docker compose //' | tr '\n' ';'; }
log() { grep -cE -- "$1" /ctl/log; }
R=/opt/vpn-stack

# 1. users and agent: the pair and nothing else
fresh; deploy "agent users"
[ "$RC" = 0 ] && [ "$(ups)" = "up -d --no-deps users;up -d --no-deps agent;" ]; ok $? "users agent: пересозданы ровно эти два контейнера, сначала users, потом agent ($(ups))"
b=$(grep -n 'compose run --rm --no-deps -T agent python -m awgscale.agent store-init' /ctl/log | cut -d: -f1); c=$(grep -n 'up -d --no-deps users' /ctl/log | cut -d: -f1)
[ -n "$b" ] && [ -n "$c" ] && [ "$b" -lt "$c" ]; ok $? "до переключения: store-init новым кодом, затем users"
[ "$(log 'remove-orphans')" = 0 ] && [ "$(grep -E 'compose.* up ' /ctl/log | grep -vc -- '--no-deps')" = 0 ] && [ "$(log 'compose (build|pull|restart|down|stop)')" = 0 ]; ok $? "ни общего up -d, ни --remove-orphans, ни сборки, ни скачивания, ни перезапуска чего-либо ещё"
grep -q 'an earlier deploy' $R/compose.yml.prev && cmp -s $R/compose.yml /src/stack/compose.yml && [ ! -e $R/compose.yml.next ]; ok $? "прежний compose.yml сохранён как compose.yml.prev, новый принят"
[ "$(log 'systemctl (restart|stop|start)')" = 0 ] && [ "$(cat $R/boot/gate.pyz)" = "old gate" ] && [ "$(cat /etc/systemd/system/cascade-apply.service)" = old-unit ]; ok $? "без watch проверка при загрузке и её юнит не тронуты"
[ "$(log 'up -d --no-deps (traefik|smartdns|db|watch|resolver)')" = 0 ] && [ "$(cat $R/traefik/dynamic.yml)" = routing ]; ok $? "traefik, smartdns, db, watch и resolver при этом не тронуты"

# 2. a second deploy of the same compose file keeps the one before it
cp /src/stack/compose.yml $R/compose.yml.next; deploy "agent users"
[ "$RC" = 0 ] && grep -q 'an earlier deploy' $R/compose.yml.prev && [ ! -e $R/compose.yml.next ]; ok $? "повторная выкладка того же compose.yml: compose.yml.prev остаётся от прошлой"

# 3. the pair, when one of the two does not answer
fresh; echo fail > /ctl/fail.users; deploy "agent users"
[ "$RC" != 0 ] && [ "$(ups)" = "up -d --no-deps users;" ] && grep -q "WAY BACK for users.*compose.yml.prev up -d --no-deps users" <<<"$OUT"
ok $? "users не ответил: agent не переключён, напечатан путь назад (прежний compose)"
fresh; echo fail > /ctl/fail.agent; deploy "agent users"
[ "$RC" != 0 ] && grep -q "WAY BACK for agent.*compose.yml.prev up -d --no-deps agent" <<<"$OUT" && grep -q "go back TOGETHER" <<<"$OUT"; ok $? "agent не ответил: код выхода не ноль, путь назад — для пары вместе"
fresh; echo "iptables v1.8.10 (nf_tables)" > /ctl/iptables.image; deploy "agent users"
[ "$RC" != 0 ] && [ -z "$(ups)" ] && [ "$(log 'store-init')" = 0 ] && grep -q "vpn-agent NOT started: iptables in the image" <<<"$OUT"; ok $? "iptables в образе не тот, что на сервере: ничего не запущено и не тронуто"

# 4. watch, and the boot gate after it
fresh; deploy "watch"
[ "$RC" = 0 ] && [ "$(ups)" = "up -d --no-deps watch;" ]; ok $? "watch: пересоздан только он"
/app/.venv/bin/python -m awgscale.watch.bundle - | cmp -s - $R/boot/gate.pyz && [ "$(stat -c %a $R/boot/gate.pyz)" = 600 ]; ok $? "проверка при загрузке — бандл, взятый из контейнера watch (boot/gate.pyz, только для root)"
cmp -s /etc/systemd/system/cascade-apply.service /src/stack/cascade-apply.service && [ "$(cat $R/boot/cascade-apply.service.prev)" = old-unit ] && [ "$(log 'systemctl daemon-reload')" -ge 1 ]; ok $? "юнит заменён на диске, прежний сохранён в boot/, daemon-reload сделан"
[ "$(log 'systemctl (restart|stop|start|try-restart|reload-or-restart)')" = 0 ] && [ "$(log 'systemctl.*(disable|mask).*cascade-apply')" = 0 ]; ok $? "юнит НЕ перезапущен и не остановлен (с ним упал бы туннель)"
[ "$(cat $R/boot/gate.pyz.prev)" = "old gate" ] && [ "$(cat $R/boot/gate.pyz.proven)" = "old proven gate" ]; ok $? "прежний код проверки сохранён (gate.pyz.prev); проверенный код прошлой загрузки не тронут"
cmp -s $R/boot/deadman.sh /src/stack/deadman.sh && cmp -s /etc/systemd/system/vpn-deadman.timer /src/stack/vpn-deadman.timer && grep -q 'GATE=$DIR/gate.pyz' $R/boot/deadman.sh && [ "$(cat $R/boot/deadman.sh.prev)" = "old safety net" ]; ok $? "страховка перезагрузки установлена вместе с проверкой и смотрит на тот же файл; прежняя сохранена (deadman.sh.prev)"
a=$(grep -n 'up -d --no-deps watch' /ctl/log | cut -d: -f1); b=$(grep -n 'awgscale.watch.bundle' /ctl/log | cut -d: -f1); [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]; ok $? "бандл берётся после того, как контейнер запущен и ответил"
cp /src/stack/compose.yml $R/compose.yml.next; deploy "watch"; [ "$RC" = 0 ] && ! grep -q "boot gate:" <<<"$OUT" && [ "$(cat $R/boot/gate.pyz.prev)" = "old gate" ]; ok $? "повторная выкладка того же кода: проверка при загрузке не переустанавливается"

fresh; echo fail > /ctl/fail.watch; deploy "watch"
[ "$RC" != 0 ] && [ "$(cat $R/boot/gate.pyz)" = "old gate" ] && [ "$(cat /etc/systemd/system/cascade-apply.service)" = old-unit ] && [ "$(cat $R/boot/deadman.sh)" = "old safety net" ] && grep -q "WAY BACK for watch" <<<"$OUT"; ok $? "watch не ответил с применёнными правилами: проверка при загрузке, юнит и страховка не тронуты"
fresh; echo awgscale-watch:earlier > /ctl/image.vpn-watch; : > /ctl/stuck.watch; deploy "watch"
[ "$RC" != 0 ] && grep -q "watch was NOT switched: its container runs awgscale-watch:earlier" <<<"$OUT" && [ "$(cat $R/boot/gate.pyz)" = "old gate" ] && [ "$(cat /etc/systemd/system/cascade-apply.service)" = old-unit ] && [ "$(log 'awgscale.watch.bundle')" = 0 ]
ok $? "контейнер watch не пересоздался (старый продолжает отвечать): это замечено, проверка при загрузке не тронута"
fresh; echo id:new-build > /ctl/newid.awgscale-watch:local; : > /ctl/stuck.watch; deploy "watch"
[ "$RC" != 0 ] && grep -q "watch was NOT switched: its container runs awgscale-watch:local (id:awgscale-watch:local), not awgscale-watch:local (id:new-build)" <<<"$OUT" && [ "$(cat $R/boot/gate.pyz)" = "old gate" ]
ok $? "то же имя образа, новая сборка, а контейнер остался прежним: это замечено по ID образа"
fresh; echo id:new-build > /ctl/newid.awgscale-watch:local; deploy "watch"
[ "$RC" = 0 ] && grep -q "watch: running awgscale-watch:local" <<<"$OUT"
ok $? "то же имя образа, новая сборка, контейнер пересоздан: переключение засчитано"
fresh; echo awgscale-agent:earlier > /ctl/image.vpn-agent; : > /ctl/stuck.agent; deploy "agent users"
[ "$RC" != 0 ] && grep -q "agent was NOT switched" <<<"$OUT" && grep -q "WAY BACK for agent" <<<"$OUT"; ok $? "контейнер agent не пересоздался: выкладка не верит ответу старого и говорит путь назад"
fresh; : > /ctl/broken-bundle; deploy "watch"
[ "$RC" != 0 ] && [ "$(cat $R/boot/gate.pyz)" = "old gate" ] && [ "$(cat /etc/systemd/system/cascade-apply.service)" = old-unit ] && grep -q "boot gate NOT installed" <<<"$OUT"; ok $? "бандл не печатает правила на python3 сервера: не установлен, юнит прежний"

fresh; deploy "exits"
[ "$RC" = 0 ] && [ "$(ups)" = "up -d --no-deps exits;" ]; ok $? "exits: пересоздан только он"
fresh; echo fail > /ctl/fail.exits; deploy "exits"
[ "$RC" != 0 ] && grep -q "WAY BACK for exits.*compose.yml.prev up -d --no-deps exits" <<<"$OUT"; ok $? "exits не ответил: код выхода не ноль, путь назад напечатан"

# 5. the resolver: the one before is started again when the new one is silent
fresh; deploy "resolver"
[ "$RC" = 0 ] && [ "$(ups)" = "up -d --no-deps resolver;" ]; ok $? "resolver: пересоздан только он"
# a resolver that ran another image before (say, AdGuard Home's own)
fresh; echo adguard/adguardhome:v0.107.80 > /ctl/image.vpn-resolver; : > /ctl/stuck.resolver; deploy "resolver"
[ "$RC" != 0 ] && grep -q "is started again from compose.yml.prev" <<<"$OUT"; ok $? "контейнер resolver не пересоздался: ответ старого не принимается за новый"
fresh; echo adguard/adguardhome:v0.107.80 > /ctl/image.vpn-resolver; echo fail > /ctl/fail.resolver; deploy "resolver"
[ "$RC" != 0 ] && [ "$(ups)" = "up -d --no-deps resolver;-f compose.yml.prev up -d --no-deps resolver;" ] && grep -q "is started again from compose.yml.prev" <<<"$OUT"; ok $? "resolver не ответил на DNS: тут же запущен прежний (из compose.yml.prev), код выхода не ноль"
fresh; echo fail > /ctl/fail.resolver; deploy "resolver"
[ "$RC" != 0 ] && grep -q "WAY BACK for resolver" <<<"$OUT" && [ "$(ups)" = "up -d --no-deps resolver;" ]; ok $? "resolver того же образа не ответил: путь назад напечатан, другой не запускается"

# 6. the tunnels: only on a server switched to them
fresh; deploy "tunnel links"
[ "$RC" = 0 ] && [ -z "$(ups)" ] && grep -q "nothing to switch" <<<"$OUT"; ok $? "tunnel и links на сервере, где туннели держит awg-quick: ничего не запускается"
fresh; : > $R/tunnel.enabled; : > $R/links.enabled; deploy "tunnel links"
[ "$RC" = 0 ] && [ "$(ups)" = "up -d --no-deps tunnel;up -d --no-deps links;" ] && [ "$(log 'exec vpn-tunnel python -m awgscale.tunnel check')" -ge 1 ] && [ "$(log 'exec vpn-links python -m awgscale.tunnel check')" -ge 1 ]; ok $? "на переключённом сервере: tunnel и links пересозданы и спрошены (python -m awgscale.tunnel check)"
fresh; : > $R/tunnel.enabled; : > /ctl/fail.tunnel; deploy "tunnel"
[ "$RC" != 0 ] && grep -q "WAY BACK for tunnel" <<<"$OUT"; ok $? "tunnel не подтвердил интерфейс: код выхода не ноль, путь назад напечатан"

# 7. the rest, and Traefik only when named
fresh; deploy "panel bot smartdns"
[ "$RC" = 0 ] && [ "$(ups)" = "up -d --no-deps panel;up -d --no-deps bot;up -d --no-deps smartdns;" ] && [ "$(cat $R/traefik/dynamic.yml)" = routing ]; ok $? "panel bot smartdns: только они; маршруты Traefik без его имени не перечитываются"
fresh; echo fail > /ctl/fail.panel; deploy "panel"
[ "$RC" != 0 ] && grep -q "WAY BACK for panel.*compose.yml.prev" <<<"$OUT" && grep -q "panel/web.prev" <<<"$OUT"; ok $? "panel не ответил: путь назад — прежний compose и прежняя страница (web.prev)"
fresh; deploy "traefik"
[ "$RC" = 0 ] && [ "$(ups)" = "up -d --no-deps traefik;" ] && [ "$(cat $R/traefik/dynamic.yml)" = routing-new ] && [ "$(cat $R/traefik/dynamic.yml.prev)" = routing ] && [ "$(log 'compose restart traefik')" = 0 ]; ok $? "traefik: маршруты перечитаны на лету, контейнер не перезапущен (статическая настройка та же)"
fresh; echo static-new > $R/traefik/traefik.yml.next; deploy "traefik"
[ "$RC" = 0 ] && [ "$(log 'compose restart traefik')" = 1 ] && [ "$(cat $R/traefik/traefik.yml)" = static-new ]; ok $? "traefik: статическая настройка изменилась — перезапущен"
fresh; deploy "agent users watch exits resolver panel bot smartdns traefik"
[ "$RC" = 0 ] && [ "$(ups)" = "up -d --no-deps users;up -d --no-deps agent;up -d --no-deps exits;up -d --no-deps watch;up -d --no-deps resolver;up -d --no-deps panel;up -d --no-deps bot;up -d --no-deps smartdns;up -d --no-deps traefik;" ]
ok $? "всё сразу: порядок users → agent → exits → watch → resolver → panel → bot → smartdns → traefik, каким бы ни был порядок имён (агент — раньше сервисов, которые он спрашивает)"
fresh; : > /ctl/compose-refused; deploy "panel"
[ "$RC" != 0 ] && grep -q 'an earlier deploy' $R/compose.yml && [ -z "$(ups)" ] && [ ! -e $R/compose.yml.prev ]; ok $? "compose.yml, который Docker Compose не принимает, не берётся в работу и ничего не запускает"
