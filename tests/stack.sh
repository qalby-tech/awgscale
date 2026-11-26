#!/usr/bin/env bash
# The stack as the deploy ships it, proven on this machine: every image is
# built (docker/build.sh), stack/compose.yml is read by Docker Compose itself,
# and every Python service is started from its image with exactly the
# environment, privileges and mounts compose.yml gives it (tests/stack_args.py
# makes the `docker run` from `docker compose config`). Each one is then asked
# the very question stack/deploy.sh asks before it believes a service
# (stack/probes.sh), and the commands the deploy runs are run: the agent's
# store-init, the tunnel's check and strip-sum, the boot gate from its bundle.
#
# Nothing of this machine is touched and nothing outside is reached: the
# "server" is ONE throwaway network namespace without a network (the
# database's container; no port is published), which stands in for the entry's
# loopback, and a temporary directory which stands in for /opt/vpn-stack and
# for the server's own paths (the awg tool, /etc/amnezia, /run/cascade, …).
# What differs from the server, and only this:
#   * the tunnels are dummy links (TUNNEL_LINK_TYPE=dummy) and `awg` is a stand-in;
#   * the bot is given a stand-in for Telegram (BOT_API_URL);
#   * the resolver has nobody upstream: it must listen, it cannot answer.
#
#   tests/stack.sh            a minute or two once the images are built; part of tests/run.sh
#   KEEP=1 tests/stack.sh     leave the containers and the directories to look at
# Several runs can go at once: every name carries this run's process id, and no port is published.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
ID=stacktest-$$
W=$(mktemp -d)                  # /opt/vpn-stack of the pretend server
H=$W/host                       # the pretend server's own paths
O=$(mktemp -d)                  # what this script itself writes down
DB=$ID-db                       # the database; its network namespace is the "server"
NET=container:$DB
PROJECT=vpn-stack
TELEGRAM_PORT=18081
PASS=0; FAIL=0
ok() { if [ "$1" = 0 ]; then PASS=$((PASS + 1)); echo "ok   $2"; else FAIL=$((FAIL + 1)); echo "FAIL $2"; fi; }
started=$(date +%s)

cleanup() {
    if [ -n "${KEEP:-}" ]; then echo "kept: containers $ID-*, directories $W $O"; return; fi
    docker ps -aq --filter "name=$ID-" | xargs -r docker rm -f >/dev/null 2>&1
    # what the containers wrote there belongs to root
    docker run --rm -v "$W":/w --entrypoint sh awgscale-bot:local -c 'rm -rf /w/* /w/.env' >/dev/null 2>&1
    rmdir "$W" 2>/dev/null
    rm -rf "$O"
}
trap cleanup EXIT

# ── 1. the images ────────────────────────────────────────────────────────────
"$HERE/docker/build.sh" >/dev/null; ok $? "все образы собраны одним рецептом (docker/Dockerfile): $("$HERE/docker/build.sh" --list)"
# what a build is given of this directory: the whitelist of .dockerignore, and never a secret
context=$(printf 'FROM awgscale-bot:local\nCOPY . /context\n' | docker build -q -f - -t "$ID-context" "$HERE" 2>/dev/null) \
    && files=$(docker run --rm --network none --entrypoint sh "$ID-context" -c 'cd /context && find . -type f | sort'); built=$?
docker rmi -f "$ID-context" >/dev/null 2>&1
[ "$built" = 0 ] && grep -qx './pyproject.toml' <<<"$files" && grep -qx './uv.lock' <<<"$files" && grep -qx './panel/invite.html' <<<"$files" && grep -q '^./src/awgscale/watch/gate.py$' <<<"$files" \
    && ! grep -vE '^\./(pyproject\.toml|uv\.lock|src/|panel/invite\.html$|panel/fonts/)' <<<"$files" | grep -q .
ok $? "в сборку попадает только разрешённое: pyproject.toml, uv.lock, src, panel/invite.html, panel/fonts ($(grep -c . <<<"$files") файлов)"
! grep -qE '(^|/)\.env|bot-token|admin-id|\.password|^\./clients/|\.key$|\.pem$|ssh_config' <<<"$files"; ok $? "в сборке нет .env, panel/.bot-token, bot/admin-id, паролей и ключей"
for image in agent watch tunnel; do docker run --rm --network none --entrypoint iptables "awgscale-$image:local" --version; done | sort -u > "$O/iptables.versions"
[ "$(grep -c 'iptables v' "$O/iptables.versions")" = 1 ]; ok $? "iptables в образах agent, watch и tunnel один и тот же: $(head -1 "$O/iptables.versions")"
docker run --rm --network none --entrypoint /opt/adguardhome/AdGuardHome awgscale-resolver:local --version | grep -q 'version v0\.107\.80'; ok $? "в образе resolver — AdGuard Home закреплённой версии v0.107.80"
docker run --rm --network none awgscale-exits:local sh -c 'd=/app/src/awgscale/exits; [ -x $d/askpass.sh ] && [ -s $d/remote/prepare.sh ] && [ -s $d/remote/exit-firewall.sh ] && [ -s $d/remote/stats/server.py ] && [ -s $d/remote/stats/Dockerfile ] && command -v ssh ssh-keygen ip ping >/dev/null'
ok $? "в образе exits — askpass.sh, скрипты для выхода, сервис статистики, ssh, ip, ping"
docker run --rm --network none awgscale-panel:local sh -c '[ -s /app/invite.html ] && ls /app/fonts/*.woff2 >/dev/null'; ok $? "в образе panel — страница приглашения и шрифты"
docker run --rm --network none awgscale-agent:local sh -c 'command -v iptables ipset ip dig curl >/dev/null' && docker run --rm --network none awgscale-watch:local sh -c 'command -v iptables iptables-restore ipset ip conntrack ping >/dev/null' \
    && docker run --rm --network none awgscale-users:local sh -c 'command -v ip >/dev/null'; ok $? "инструменты на месте: agent (iptables, ipset, ip, dig, curl), watch (iptables, ipset, ip, conntrack, ping), users (ip)"

# ── 1a. what the rules for some groups rely on in the pinned AdGuard Home ─────
# (tests/stack_adguard.py: the binary of the resolver's image, in a throwaway namespace with the watch image's tools)
mkdir -p "$O/ag"
agid=$(docker create awgscale-resolver:local) && docker cp "$agid:/opt/adguardhome/AdGuardHome" "$O/ag/" >/dev/null 2>&1; docker rm "$agid" >/dev/null 2>&1
docker run --rm --network none --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW --cap-add NET_BIND_SERVICE -v "$O/ag":/ag:ro \
    -v "$HERE/tests/stack_adguard.py":/stack_adguard.py:ro -v "$HERE/src":/awgsrc:ro --entrypoint python3 awgscale-watch:local /stack_adguard.py /ag/AdGuardHome > "$O/adguard.out" 2>&1
grep -q '^ok ' "$O/adguard.out"; ok $? "AdGuard Home v0.107.80 запущен в отдельном пространстве имён: $(grep -c '^ok ' "$O/adguard.out") проверок"
while IFS= read -r line; do
    case "$line" in
        "ok "*) ok 0 "AdGuard: ${line#ok }" ;;
        "FAIL "*) ok 1 "AdGuard: ${line#FAIL }" ;;
        "note: "*) echo "     AdGuard, для сведения: ${line#note: }" ;;
    esac
done < "$O/adguard.out"

# ── 2. compose.yml, read by Docker Compose ───────────────────────────────────
chmod 755 "$W"
cp "$HERE/stack/compose.yml" "$W/compose.yml"
printf 'BOT_ADMIN_ID=1\nDOMAIN=vpn.example.test\nDNS_STATIC=198.51.100.7 198.51.100.8\nPROXY_IP=203.0.113.10\nJOURNAL_GID=999\n' > "$W/.env"
compose() { COMPOSE_PROFILES=tunnel,links,cascade,bot,smartdns,obs,backup docker compose -p "$PROJECT" --project-directory "$W" --env-file "$W/.env" "$@"; }
compose -f "$W/compose.yml" config --format json > "$O/config.json" 2> "$O/config.err"; ok $? "docker compose config принимает compose.yml с примером .env"
# with no FEATURES the stack is the core alone, and it needs nothing but the server's name
core=$(DOMAIN=vpn.example.test docker compose -p "$PROJECT-core" --project-directory "$W" --env-file /dev/null -f "$W/compose.yml" config --services 2>&1 | sort | tr '\n' ' ')
[ "$core" = "agent db panel resolver traefik users watch " ]; ok $? "без FEATURES — только ядро (db, users, agent, watch, resolver, panel, traefik), и ему нужен лишь DOMAIN: $core"
python3 - "$O/config.json" <<'PY'
import json, sys
services = json.load(open(sys.argv[1]))["services"]
ours = {"panel": "panel", "bot": "bot", "smartdns": "dnsgate", "users": "users", "agent": "agent", "resolver": "resolver", "watch": "watch", "tunnel": "tunnel", "links": "tunnel", "exits": "exits"}
bad = []
for name, package in ours.items():
    service = services[name]
    image = "awgscale-" + package + ":local"
    if service["image"] != image or service.get("pull_policy") != "never" or service["command"] != ["python", "-m", "awgscale." + package] or "build" in service:
        bad.append(name + ": image or command")
    if not service.get("read_only") or service.get("cap_drop") != ["ALL"] or "no-new-privileges:true" not in service.get("security_opt", []) or not service.get("mem_limit"):
        bad.append(name + ": hardening")
if int(services["users"]["mem_limit"]) != 160 * 1024 * 1024:
    bad.append("users: mem_limit is not 160m")
print("; ".join(bad))
sys.exit(1 if bad else 0)
PY
ok $? "каждый сервис на Python: образ awgscale-<имя>:local без сборки и скачивания, python -m awgscale.<имя>, корень только для чтения, без привилегий, с пределом памяти; users — 160m"

# ── the pretend server ───────────────────────────────────────────────────────
mkdir -p "$H/usr/bin" "$H/etc/amnezia/amneziawg" "$H/root/awg/cascade" "$H/run/cascade" "$H/run/vpn-panel" "$H/run/systemd/journal" "$H/opt/AdGuardHome" "$H/etc/apt/keyrings" \
    "$W/panel/web" "$W/panel-data" "$W/bot" "$W/bot-data" "$W/lists" "$W/smartdns-data" "$W/db-secret" "$W/users-secret" "$W/exits-secret" "$W/watch-secret" "$W/resolver-secret" "$W/exits-key" "$W/boot"
for secret in db-secret/password users-secret/token exits-secret/token watch-secret/token resolver-secret/token bot/token; do echo "throwaway-$RANDOM$RANDOM" > "$W/$secret"; done
echo '<!doctype html><title>stand-in page</title>' > "$W/panel/web/index.html"
printf 'chatgpt.com\n149.154.160.0/20\n' > "$W/lists/domains-exit.txt"
echo '{"tokens": {}}' > "$W/lists/dns-tokens.json"
printf 'Username: admin\nPassword: throwaway\n' > "$H/root/awg/adguard-admin.txt"
echo stand-in > "$H/etc/apt/keyrings/amnezia-ppa.gpg"
cat > "$H/usr/bin/awg" <<'A'
#!/bin/sh
# a stand-in for the host's awg tool: one interface with no peers, the key and the port of its file
case "$1" in
  show)
    case "${3:-}" in
      listen-port) [ "$2" = awg0 ] && echo 58728 || echo 0 ;;
      public-key)  echo "PUBofPRIV=" ;;
      dump)        printf 'PRIVpppppppppppppppppppppppppppppppppppppp=\tPUBofPRIV=\t58728\toff\n' ;;
    esac ;;
  pubkey)        cat >/dev/null; echo "PUBofPRIV=" ;;
  genkey|genpsk) echo "GENkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk=" ;;
  *)             cat >/dev/null 2>&1 ;;
esac
exit 0
A
chmod 755 "$H/usr/bin/awg"
cat > "$H/etc/amnezia/amneziawg/awg0.conf" <<'C'
[Interface]
PrivateKey = AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=
Address = 172.16.17.1/24
MTU = 1280
ListenPort = 58728
PostUp = iptables -I FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE; iptables -t mangle -A FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240; iptables -t mangle -A FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240; while iptables -D FORWARD -i %i -o %i -j DROP 2>/dev/null; do :; done; iptables -I FORWARD -i %i -o %i -j DROP
Jc = 5
H1 = 1
C
# what the bash installer left beside the tunnel file: the server's public key (of the throwaway private key above)
mkdir -p "$H/etc/amnezia/amneziawg/installer"
docker run --rm --network none awgscale-users:local python -c "from awgscale.users.keys import public_of; print(public_of('AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE='))" > "$H/etc/amnezia/amneziawg/installer/server_public.key"
printf '[Interface]\nPrivateKey = PRIVpppppppppppppppppppppppppppppppppppppp=\nAddress = 10.77.1.2/32\nMTU = 1280\nTable = off\n\n[Peer]\nPublicKey = AAAAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=\nEndpoint = 198.51.100.1:47211\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n' > "$H/etc/amnezia/amneziawg/awgk1.conf"
cat > "$H/root/awg/cascade/cascade.conf" <<'C'
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
# as many Russian networks as the real list has (about eight thousand): the watch service's memory is measured with them loaded
for i in $(seq 0 127); do for j in $(seq 0 4 252); do echo "5.$i.$j.0/22"; done; done > "$H/root/awg/cascade/ru.zone"
cp "$W/lists/domains-exit.txt" "$H/root/awg/cascade/domains-exit.txt"
printf '# Telegram\n91.108.56.0/22\n' > "$H/root/awg/cascade/nets-auto.txt"
# the resolver's config, as an installed AdGuard Home has it: DNS on the tunnel's address, the control port on loopback
cat > "$H/opt/AdGuardHome/AdGuardHome.yaml" <<'Y'
http:
  address: 127.0.0.1:3001
users:
  - name: admin
    password: $2a$10$abcdefghijklmnopqrstuuJ5kq7yTqvXz1mOqz0mVx0G4yQkGyEFa
dns:
  bind_hosts:
    - 172.16.17.1
  port: 53
  upstream_dns:
    - 127.0.0.1:5353
  bootstrap_dns:
    - 127.0.0.1:5353
schema_version: 29
Y

DB_PASSWORD=$(cat "$W/db-secret/password")
# on the server all of this is root's; a container's root without capabilities writes only what is its own
docker run --rm -v "$W":/w --entrypoint sh awgscale-bot:local -c 'chown -R 0:0 /w/* && chmod -R a+rX /w'

args_of() { python3 "$HERE/tests/stack_args.py" "$O/config.json" "$1" "$W" "$H" "$NET" "${@:2}"; }
# start <service> [docker run options …]: the service's container, as compose.yml describes it
start() {
    local service=$1 args
    shift
    mapfile -t args < <(args_of "$service")
    docker run -d --name "$ID-$service" "$@" "${args[@]}" >/dev/null
}
# once <service> <command …>: a one-off run of another command in the service's container (docker compose run)
once() {
    local service=$1 args
    shift
    mapfile -t args < <(args_of "$service" "$@")
    docker run --rm "${args[@]}" </dev/null
}
# ask '<probe …>': a question of stack/probes.sh, asked on the pretend server with its bare python3
ask() {
    docker run --rm --network "$NET" -w / -v "$W":/opt/vpn-stack:ro -v "$H/run/vpn-panel":/run/vpn-panel -v "$HERE/stack/probes.sh":/probes.sh:ro \
        -e PATH=/usr/sbin:/usr/bin:/sbin:/bin --entrypoint bash awgscale-bot:local -c ". /probes.sh; $1"
}
# in the server's network namespace, with the kernel tools: to look at what a service left there
kernel() { docker run --rm --network "$NET" --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW --entrypoint sh awgscale-watch:local -c "$1"; }
alive() { [ "$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$ID-$1" 2>/dev/null)" = "true 0" ]; }
settings_of() { once "$1" python -c "from awgscale.$2.settings import get_settings; get_settings()"; }
why() { docker logs "$ID-$1" 2>&1 | tail -8 | cut -c1-220; }

docker run -d --name "$DB" --network none -e POSTGRES_USER=vpn -e POSTGRES_DB=vpn -e POSTGRES_PASSWORD="$DB_PASSWORD" postgres:17-alpine >/dev/null
for _ in $(seq 1 60); do docker exec "$DB" psql -U vpn -d vpn -h 127.0.0.1 -c 'select 1' >/dev/null 2>&1 && break; sleep 0.5; done

# ── 3. every settings class takes the environment compose.yml gives it ───────
for pair in panel:panel bot:bot smartdns:dnsgate users:users agent:agent resolver:resolver watch:watch tunnel:tunnel links:tunnel exits:exits; do
    settings_of "${pair%%:*}" "${pair##*:}" > "$O/settings.out" 2>&1; ok $? "$(printf '%-8s' "${pair%%:*}") настройки awgscale.${pair##*:} читаются из окружения compose.yml$(tail -1 "$O/settings.out" | cut -c1-160)"
done

# ── 4. the boot gate: one file, the server's bare python3 ────────────────────
docker run --rm --network none awgscale-watch:local python -m awgscale.watch.bundle - > "$O/gate.pyz" && docker run --rm -i -v "$W/boot":/boot --entrypoint sh awgscale-bot:local -c 'cat > /boot/gate.pyz' < "$O/gate.pyz" && docker run --rm --network none awgscale-watch:local python -m awgscale.watch.bundle - | cmp -s - "$O/gate.pyz"
ok $? "бандл проверки при загрузке собирается из образа watch и побайтно повторяется ($(wc -c < "$O/gate.pyz") байт)"
# an empty kernel, no venv, no package on disk: only the file, as cascade-apply.service runs it
unit_command=$(sed -n 's/^ExecStart=//p' "$HERE/stack/cascade-apply.service")
[ "$unit_command" = "/usr/bin/python3 /opt/vpn-stack/boot/gate.pyz apply" ]; ok $? "cascade-apply.service запускает именно его: $unit_command"
docker run --rm --network none --read-only --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW -w / -e PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$W/boot":/opt/vpn-stack/boot:ro -v "$H/root/awg/cascade":/root/awg/cascade -v "$H/usr/bin/awg":/usr/bin/awg:ro --tmpfs /run --entrypoint sh awgscale-watch:local -c '
    /usr/bin/python3 -c "import pydantic" 2>/dev/null && { echo "this python has the dependencies: not a bare one"; exit 1; }
    '"$unit_command"' || { echo "apply failed"; exit 1; }
    iptables -t mangle -S CASCADE | grep -q -- "-A CASCADE" && ipset list -n | grep -qx ru && ip rule | grep -q "lookup 298" && ip route show table 201 | grep -q "^blackhole default metric 1000" && ! ip route show table 201 | grep -q "default dev"' > "$O/gate.out" 2>&1
ok $? "бандл на голом python3 (без venv и без пакета на диске) в пустом ядре: правила, наборы и маршруты поставлены, выход закрыт blackhole $(tail -1 "$O/gate.out" | cut -c1-160)"
docker run --rm --network none -w / -v "$W/boot":/opt/vpn-stack/boot:ro --tmpfs /root/awg/cascade --entrypoint /usr/bin/python3 awgscale-watch:local /opt/vpn-stack/boot/gate.pyz apply >/dev/null 2>&1; [ $? = 1 ]
ok $? "без настроек бандл выходит с кодом 1: туннель не поднимется"
# the same at the "boot" of the pretend server, before any service
docker run --rm --network "$NET" --read-only --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW -w / -e PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    -v "$W/boot":/opt/vpn-stack/boot:ro -v "$H/root/awg/cascade":/root/awg/cascade -v "$H/run/cascade":/run/cascade -v "$H/usr/bin/awg":/usr/bin/awg:ro --entrypoint sh awgscale-watch:local -c "$unit_command" >/dev/null 2>&1
ok $? "«загрузка» условного сервера: правила поставлены бандлом до сервисов"

# ── 5. the services, in the order the deploy switches them ───────────────────
start watch; ask 'wait_for 60 watch_answers'; ok $? "watch    отвечает: правила применены, проход сделан (вопрос выкладки) $(alive watch || why watch)"
docker exec "$ID-watch" python -m awgscale.watch.bundle - | cmp -s - "$O/gate.pyz"; ok $? "watch    бандл из работающего контейнера — тот же файл, что проверен на голом python3"
docker run --rm --network none -w / -e PATH=/usr/sbin:/usr/bin:/sbin:/bin -v "$W/boot":/opt/vpn-stack/boot:ro -v "$H/root/awg/cascade":/root/awg/cascade:ro --entrypoint /usr/bin/python3 awgscale-bot:local \
    /opt/vpn-stack/boot/gate.pyz rules 2>/dev/null | grep -q -- '-A CASCADE'
ok $? "watch    gate.pyz rules печатает правила, ничего не трогая: этим выкладка проверяет бандл на python3 сервера до установки"

start tunnel -e TUNNEL_LINK_TYPE=dummy; start links -e TUNNEL_LINK_TYPE=dummy
for _ in $(seq 1 30); do docker exec "$ID-tunnel" python -m awgscale.tunnel check >/dev/null 2>&1 && break; sleep 1; done
docker exec "$ID-tunnel" python -m awgscale.tunnel check > "$O/check.tunnel" 2>&1; ok $? "tunnel   check: интерфейс awg0 такой, как в файле $(cut -c1-200 "$O/check.tunnel")"
sum=$(docker exec "$ID-tunnel" python -m awgscale.tunnel strip-sum); want=$(grep -vE '^(Address|MTU|PostUp|PostDown|Table|DNS|SaveConfig) ' "$H/etc/amnezia/amneziawg/awg0.conf" | python3 -c "import sys, hashlib; print(hashlib.sha256(sys.stdin.read().rstrip(chr(10)).encode()).hexdigest())")
[ -n "$sum" ] && [ "$sum" = "$want" ]; ok $? "tunnel   strip-sum — отпечаток файла без строк awg-quick, как его считает switch-tunnel.sh из awg-quick strip"
for _ in $(seq 1 30); do docker exec "$ID-links" python -m awgscale.tunnel check >/dev/null 2>&1 && break; sleep 1; done
docker exec "$ID-links" python -m awgscale.tunnel check > "$O/check.links" 2>&1; ok $? "links    check: связь awgk1 такая, как в файле $(cut -c1-200 "$O/check.links")"
[ -n "$(docker exec "$ID-links" python -m awgscale.tunnel strip-sum awgk1)" ] && ! docker exec "$ID-links" python -m awgscale.tunnel strip-sum >/dev/null 2>&1; ok $? "links    strip-sum <связь> печатает отпечаток; без имени связи — отказ"
kernel 'ip -4 -o addr show dev awg0 | grep -q 172.16.17.1/24 && ip link show awgk1 >/dev/null'; ok $? "tunnel   в ядре условного сервера есть awg0 с адресом и awgk1"

start resolver; ask 'wait_for 40 keeper_answers'; ok $? "resolver отвечает: AdGuard Home из образа запущен и слушает 172.16.17.1:53 (вопрос выкладки, без запроса наружу) $(alive resolver || why resolver)"
docker exec "$ID-resolver" sh -c 'for p in /proc/[0-9]*; do case "$(tr "\0" " " < $p/cmdline 2>/dev/null)" in */AdGuardHome\ *) tr "\0" "\n" < $p/environ | grep -qx GOMEMLIMIT=450MiB && exit 0;; esac; done; exit 1'
ok $? "resolver: AdGuard Home запущен хранителем с GOMEMLIMIT=450MiB из compose.yml (его память не растёт за предел контейнера)"

once agent python -m awgscale.agent store-init > "$O/store-init.out" 2>&1; ok $? "agent    store-init на пустой базе: $(tail -1 "$O/store-init.out" | cut -c1-200)"
once agent python -m awgscale.agent store-init > "$O/store-init.again" 2>&1; ok $? "agent    store-init повторно ничего не ломает: $(tail -1 "$O/store-init.again" | cut -c1-200)"

# the one-time move of the users into the database (here: a tunnel file without users)
once users python -m awgscale.users import /etc/amnezia/amneziawg/installer > "$O/users-import.out" 2>&1; ok $? "users    import: пользователи (здесь — ни одного) перенесены в базу, сервис может отвечать $(tail -1 "$O/users-import.out" | cut -c1-160)"
start users; ask 'wait_for 40 users_answer'; ok $? "users    отвечает: база и ядро на месте (вопрос выкладки) $(alive users || why users)"
start exits; ask 'wait_for 40 exits_answer'; ok $? "exits    отвечает на /v1/nodes (вопрос выкладки) $(alive exits || why exits)"
start agent; ask 'wait_for 40 agent_asks'; ok $? "agent    отвечает на state по сокету (вопрос выкладки) $(alive agent || why agent)"
once agent python -m awgscale.agent cascade-get > "$O/cascade-get.out" 2>&1 && grep -q . "$O/cascade-get.out"; ok $? "agent    cascade-get печатает настройки каскада (подсказка оператору)"

start panel; ask 'wait_for 30 panel_answers'; ok $? "panel    отвечает на своём порту (без входа — отказ, а не тишина) $(alive panel || why panel)"
ask 'python3 - <<PY
import urllib.request
page = urllib.request.urlopen("http://127.0.0.1:8787/", timeout=3).read()
invite = urllib.request.urlopen("http://127.0.0.1:8787/i/abcdefghijklmnopqrstuv", timeout=3)
font = urllib.request.urlopen("http://127.0.0.1:8787/fonts/geist-latin-400.woff2", timeout=3)
raise SystemExit(0 if b"stand-in page" in page and invite.status == 200 and font.status == 200 and len(font.read()) > 1000 else 1)
PY' > "$O/panel.out" 2>&1; ok $? "panel    отдаёт страницу из примонтированного каталога, страницу приглашения и шрифт из образа $(tail -1 "$O/panel.out" | cut -c1-160)"
start smartdns; ask 'wait_for 30 dnsgate_answers http://127.0.0.1:8053'; ok $? "smartdns отвечает на порту DoH (без кода — отказ, а не тишина) $(alive smartdns || why smartdns)"
docker run -d --name "$ID-telegram" --network "$NET" -v "$HERE/tests/stack_telegram.py":/telegram.py:ro --entrypoint /usr/bin/python3 awgscale-bot:local /telegram.py "$TELEGRAM_PORT" >/dev/null
start bot -e BOT_API_URL="http://127.0.0.1:$TELEGRAM_PORT"
for _ in $(seq 1 30); do docker logs "$ID-bot" 2>&1 | grep -q 'started as @stand_in_bot' && break; sleep 1; done
docker logs "$ID-bot" 2>&1 | grep -q 'started as @stand_in_bot' && alive bot; ok $? "bot      запущен, поздоровался с Telegram (заглушка) и работает $(alive bot || why bot)"

# ── 6. all of them together, for a while ─────────────────────────────────────
ask 'for _ in 1 2 3 4 5; do agent_asks && watch_answers && users_answer && exits_answer || exit 1; done'; ok $? "все вместе: агент, сторож, пользователи и выходы отвечают раз за разом"
sleep 20
still=0; for service in watch tunnel links resolver users exits agent panel smartdns bot; do alive "$service" || { still=1; echo "  $service is not running: $(why "$service")"; }; done
ok $still "через 20 секунд все десять контейнеров работают, Docker ни один не перезапускал"

echo
echo "memory (after the questions above; the peak is what the limit must hold):"
printf '  %-9s %10s %10s %10s\n' service now peak limit
for service in panel bot smartdns users agent resolver watch tunnel links exits; do
    now=$(docker exec "$ID-$service" cat /sys/fs/cgroup/memory.current 2>/dev/null || echo 0)
    peak=$(docker exec "$ID-$service" cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo 0)
    limit=$(docker inspect -f '{{.HostConfig.Memory}}' "$ID-$service" 2>/dev/null || echo 0)
    printf '  %-9s %8s M %8s M %8s M\n' "$service" $((now / 1048576)) $((peak / 1048576)) $((limit / 1048576))
    [ "$peak" -gt 0 ] && [ $((peak * 100 / (limit + 1))) -gt 75 ] && { FAIL=$((FAIL + 1)); echo "FAIL $service: the peak is above three quarters of its limit"; }
done

# ── 7. stopping: clean, and within the time compose.yml gives ────────────────
for service in bot smartdns panel agent exits users resolver links tunnel watch; do docker stop -t 20 "$ID-$service" >/dev/null 2>&1; done
codes=$(for service in bot smartdns panel agent exits users resolver links tunnel watch; do printf '%s=%s ' "$service" "$(docker inspect -f '{{.State.ExitCode}}' "$ID-$service")"; done)
! grep -qE '=(137|[1-9][0-9]*) ' <<<"$codes "; ok $? "остановка: каждый сервис завершается сам и с кодом 0 ($codes)"
kernel 'ip link show awg0 >/dev/null && ip link show awgk1 >/dev/null && iptables -t mangle -S CASCADE | grep -q -- "-A CASCADE"'; ok $? "после остановки сервисов туннели и правила в ядре остались"

# ── 8. a users' table with moments as numbers is turned into dates ───────────
column_types() { docker exec "$DB" psql -U vpn -d vpn -At -c "SELECT string_agg(column_name || '=' || data_type, ' ' ORDER BY column_name) FROM information_schema.columns WHERE table_name = 'users' AND column_name IN ('created_at', 'expires_at')"; }
before=$(column_types)
back_sql="ALTER TABLE users ALTER COLUMN expires_at TYPE bigint USING extract(epoch from expires_at)::bigint, ALTER COLUMN created_at TYPE bigint USING extract(epoch from created_at)::bigint"
docker exec "$DB" psql -q -U vpn -d vpn -c "$back_sql" >/dev/null 2>&1; altered=$?
[ "$before" = "created_at=timestamp with time zone expires_at=timestamp with time zone" ] && [ "$altered" = 0 ] && [ "$(column_types)" = "created_at=bigint expires_at=bigint" ]
ok $? "(подготовка) столбцы времени users переведены в числа ($before → $(column_types))"
docker start "$ID-users" >/dev/null && ask 'wait_for 40 users_answer' && [ "$(column_types)" = "created_at=timestamp with time zone expires_at=timestamp with time zone" ]; ok $? "…а сервис пакета при следующем запуске снова переводит их в даты и отвечает"
docker stop -t 20 "$ID-users" >/dev/null 2>&1

for script in stack/deploy.sh stack/probes.sh stack/switch-tunnel.sh stack/deadman.sh docker/build.sh docker/publish.sh tests/stack.sh tests/deadman.sh tests/kernel.sh tests/kernel_inside.sh cascade/deploy.sh cascade/link-exit-kernel.sh; do bash -n "$HERE/$script" || FAIL=$((FAIL + 1)); done
ok 0 "синтаксис скриптов выкладки"
echo
echo "$PASS of $((PASS + FAIL)) checks passed in $(( $(date +%s) - started )) s"
[ "$FAIL" = 0 ]
