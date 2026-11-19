#!/usr/bin/env bash
# =============================================================================
# stack/deploy.sh — run ON THE OPERATOR MACHINE. Deploys the application stack to the
# entry server (/opt/vpn-stack). AmneziaWG and the boot-time rules are native
# on the host and are NOT touched here.
#
#   stack/deploy.sh                    ship the images and the files; NOTHING that
#                                      is running is changed
#   stack/deploy.sh users agent        … and switch these services to what was shipped
#   stack/deploy.sh watch
#   stack/deploy.sh panel bot
#
# A service is recreated only when it is named here, never because another one
# was: every start is `docker compose up -d --no-deps <service>`, and there is
# no blanket `up -d` and no `--remove-orphans`. Named services are switched in
# a fixed order, whatever the order of the arguments:
#   db → users → agent → exits → watch (+ the boot gate) → tunnel → links
#   → resolver → panel → bot → smartdns → prometheus loki alloy grafana → traefik
#   users, agent   one unit: naming either switches both (they speak to each
#                  other in one language; an old one beside a new one does not work)
#   watch          the boot gate and the reboot safety net are installed with it,
#                  after it answered with the rules applied
#   tunnel, links  only on a server switched to them (stack/switch-tunnel.sh)
#   panel          the built page (web/out) is shipped with it
#   prometheus, loki, alloy, grafana   their config (stack/obs) is shipped with
#                  any of them, and a named one is restarted to read it
#   traefik        the routing (dynamic.yml) is hot-reloaded with it; the container
#                  is recreated only if its static config or definition changed
#   traefik, smartdns   the operator's own DNS may go through them: only when named
#
# The Python services are the package awgscale (src/awgscale). Their images are
# built HERE (docker/build.sh, the same Ubuntu as the server) and shipped with
# `docker save | docker load`: the server builds nothing and downloads nothing.
#
# The way back. The compose file before the last deploy that changed it is
# compose.yml.prev:
#   ssh entry 'cd /opt/vpn-stack && docker compose -f compose.yml.prev up -d --no-deps <service>'
# The code before is the commit before this deploy: check it out here and run
# stack/deploy.sh <service> again. When a switch fails the script says which
# command it is for that service. users and agent go back together.
#   the boot gate   boot/gate.pyz.prev → boot/gate.pyz,
#                boot/cascade-apply.service.prev → /etc/systemd/system/
#                cascade-apply.service, boot/deadman.sh.prev → boot/deadman.sh,
#                then `systemctl daemon-reload` — and NEVER a restart of the unit.
#   panel        the page before this deploy is panel/web.prev. The page is
#                shipped before anything is switched: name `panel` alone, so a
#                failure of another service cannot leave the new page on the old panel.
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
# DOMAIN, the panel's allow-list (PANEL_ALLOW) and the addresses that need no
# code for the smart DNS (DNS_STATIC): site.env, see site.env.example
. "$HERE/site.sh"
site_env DOMAIN PANEL_ALLOW
# The optional parts this server runs (site.env FEATURES, space separated): cascade
# (the exits service), bot, smartdns, obs (history and logs), backup. None: the mesh alone.
FEATURES=${FEATURES:-}
DNS_STATIC=${DNS_STATIC:-}
for feature in $FEATURES; do
    case " cascade bot smartdns obs backup " in *" $feature "*) ;; *) echo "FEATURES in site.env: no such feature: $feature (there are: cascade bot smartdns obs backup)" >&2; exit 1 ;; esac
done
has() { [[ " $FEATURES " == *" $1 "* ]]; }
# the compose profile of an optional service; nothing for one that is always there
feature_of() { case "$1" in exits) echo cascade ;; bot) echo bot ;; smartdns) echo smartdns ;; prometheus|loki|alloy|grafana) echo obs ;; db-backup) echo backup ;; esac; }
PROFILES=$(echo $FEATURES | tr ' ' ',')
EXIT_NET="172.30.99.0/24"       # Docker network of the service containers (compose.yml)
OLD_PORTS="8443"                # ports of earlier layouts whose firewall rules must go

SSH=(ssh -F "$HERE/ssh_config" -o BatchMode=yes)
SCP=(scp -q -F "$HERE/ssh_config")
R=/opt/vpn-stack

# The page is built here, in a container (web/build.sh); a page that does not
# build or whose types do not check never replaces the admin's working tool.
"$HERE/web/build.sh" >/dev/null || { echo "the panel page does not build: web/build.sh" >&2; exit 1; }
[[ -s "$HERE/web/out/index.html" ]] || { echo "no built page in web/out" >&2; exit 1; }
if has bot; then
    [[ -s "$HERE/panel/.bot-token" && -s "$HERE/bot/admin-id" ]] || { echo "bot token (panel/.bot-token) or bot/admin-id missing" >&2; exit 1; }
fi

# ── what to switch ───────────────────────────────────────────────────────────
KNOWN="db users agent exits watch tunnel links resolver panel bot smartdns prometheus loki alloy grafana traefik db-backup"
SERVICES=""
for name in "$@"; do
    case " $KNOWN " in *" $name "*) ;; *) echo "no such service: $name (there are: $KNOWN)" >&2; exit 1 ;; esac
    feature=$(feature_of "$name")
    if [ -n "$feature" ] && ! has "$feature"; then echo "$name is part of the feature $feature, which is not in FEATURES (site.env)" >&2; exit 1; fi
    SERVICES="$SERVICES $name"
done
# users and agent are one unit
case " $SERVICES " in *" users "*|*" agent "*) SERVICES="$SERVICES users agent" ;; esac
SERVICES=$(tr ' ' '\n' <<<"$SERVICES" | grep . | sort -u | tr '\n' ' ' || true)
named() { [[ " $SERVICES " == *" $1 "* ]]; }

# ── the images: built here, the same Ubuntu as the server ────────────────────
IMAGES=$("$HERE/docker/build.sh" --list)
# on the server's own Ubuntu release: iptables in the images must be the server's
UBUNTU=$("${SSH[@]}" entry '. /etc/os-release && [ "$ID" = ubuntu ] && echo "$VERSION_ID"') || { echo "the server is not Ubuntu, or did not say which release it runs" >&2; exit 1; }
export UBUNTU
"$HERE/docker/build.sh" >/dev/null || { echo "the images do not build: docker/build.sh" >&2; exit 1; }
docker run --rm --network none awgscale-agent:local python -m compileall -q /app/src >/dev/null || { echo "the package does not compile" >&2; exit 1; }
for f in "$HERE"/src/awgscale/exits/remote/*.sh "$HERE/stack/probes.sh" "$HERE/stack/deadman.sh"; do bash -n "$f"; done
# an image is told from another by its layers and its environment (the same on any Docker, however it stores images)
PRINT='{{json .RootFS.Layers}} {{json .Config.Env}}'
prints_here=$(for name in $IMAGES; do echo "$name $(docker image inspect -f "$PRINT" "awgscale-$name:local" | sha256sum | cut -c1-16)"; done)
PRINTS_THERE="for name in $IMAGES; do echo \"\$name \$(docker image inspect -f '$PRINT' awgscale-\$name:local 2>/dev/null | sha256sum | cut -c1-16)\"; done"

"${SSH[@]}" entry 'command -v docker >/dev/null && docker compose version >/dev/null 2>&1' || "${SSH[@]}" entry 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 >/dev/null && systemctl enable --now docker >/dev/null 2>&1'
there=$("${SSH[@]}" entry 'uname -m')
[[ "$there" == "$(uname -m)" ]] || { echo "the server is $there, this machine is $(uname -m): images built here would not run there" >&2; exit 1; }

# ── directories and secrets on the server: made once, never replaced ─────────
"${SSH[@]}" entry "mkdir -p $R/traefik/acme $R/panel $R/panel-data $R/bot $R/bot-data $R/smartdns-data $R/lists \
    && chmod 700 $R $R/traefik/acme $R/panel-data $R/bot $R/bot-data $R/smartdns-data \
    && { [ -f $R/lists/domains-exit.txt ] || cp /root/awg/cascade/domains-exit.txt $R/lists/ 2>/dev/null || touch $R/lists/domains-exit.txt; } \
    && { [ -f $R/lists/dns-tokens.json ] || { umask 077; echo '{\"tokens\": {}}' > $R/lists/dns-tokens.json; }; }"
# History and logs (stack/obs): each store's data directory belongs to the user
# its image runs as; Grafana's admin password is made once and stays on the server.
has obs && "${SSH[@]}" entry "mkdir -p $R/obs $R/obs-data/prometheus $R/obs-data/loki $R/obs-data/alloy $R/obs-data/grafana && chmod 700 $R/obs-data \
    && chown 65534:65534 $R/obs-data/prometheus && chown 10001:10001 $R/obs-data/loki && chown 473:473 $R/obs-data/alloy && chown 472:0 $R/obs-data/grafana \
    && { [ -s $R/obs-data/grafana-password ] || { umask 077; head -c 18 /dev/urandom | base64 | tr '+/' '-_' > $R/obs-data/grafana-password; }; } \
    && chown 472:0 $R/obs-data/grafana-password && chmod 400 $R/obs-data/grafana-password"
# The settings database: its files belong to the image's postgres user (70); the
# password is made once, stays on the server and is read by the services that use the database.
"${SSH[@]}" entry "mkdir -p $R/db-data $R/db-secret $R/db-backup && chmod 700 $R/db-data $R/db-secret $R/db-backup \
    && { [ -s $R/db-secret/password ] || { umask 077; head -c 24 /dev/urandom | base64 | tr -d '+/=' > $R/db-secret/password; }; } \
    && chown -R 70:0 $R/db-secret && chmod 750 $R/db-secret && chmod 440 $R/db-secret/password && chown 70:70 $R/db-data $R/db-backup"
# The tokens the agent presents to the users, exits, watch services and to the
# resolver's keeper; the directory the exits service keeps its SSH key in
# (made by the service itself, mounted in no other container).
for name in users exits watch resolver; do
    "${SSH[@]}" entry "mkdir -p $R/$name-secret && chmod 700 $R/$name-secret \
        && { [ -s $R/$name-secret/token ] || { umask 077; head -c 32 /dev/urandom | base64 | tr -d '+/=' > $R/$name-secret/token; }; } && chmod 400 $R/$name-secret/token"
done
"${SSH[@]}" entry "mkdir -p $R/exits-key && chmod 700 $R/exits-key"

# ── the images: only those the server does not have yet, in one stream ───────
prints_there=$("${SSH[@]}" entry "$PRINTS_THERE")
to_ship=$(diff <(echo "$prints_there") <(echo "$prints_here") | awk '/^>/{print "awgscale-" $2 ":local"}' | tr '\n' ' ' || true)
if [[ -n "${to_ship// /}" ]]; then
    echo "shipping images: $to_ship"
    # shellcheck disable=SC2086
    docker save $to_ship | gzip -1 | "${SSH[@]}" entry 'gunzip | docker load >/dev/null'
    prints_there=$("${SSH[@]}" entry "$PRINTS_THERE")
    [[ "$prints_there" == "$prints_here" ]] || { echo "the images on the server are not the ones built here — nothing was switched" >&2; exit 1; }
else
    echo "images: the server has them all"
fi

# ── the files: put beside what is in use (.next), taken into use below ───────
"${SCP[@]}" "$HERE/stack/compose.yml" entry:$R/compose.yml.next
"${SCP[@]}" "$HERE/stack/traefik/traefik.yml" entry:$R/traefik/traefik.yml.next
"${SCP[@]}" "$HERE/stack/probes.sh" entry:$R/probes.sh
# the boot gate's unit and the reboot safety net: copies to install from (see `watch` below)
"${SCP[@]}" "$HERE/stack/cascade-apply.service" "$HERE/stack/deadman.sh" "$HERE/stack/vpn-deadman.service" "$HERE/stack/vpn-deadman.timer" entry:$R/
if named prometheus || named loki || named alloy || named grafana; then
    tar -C "$HERE/stack" -cf - obs | "${SSH[@]}" entry "tar -C $R -xf - --no-same-owner"
fi
allow_yaml=$(for ip in $PANEL_ALLOW; do printf '"%s/32", ' "$ip"; done | sed 's/, $//')
sed "s|__DOMAIN__|$DOMAIN|g; s|__ALLOW__|$allow_yaml|" "$HERE/stack/traefik/dynamic.yml" \
    | "${SSH[@]}" entry "cat > $R/traefik/dynamic.yml.next"
# the bot's token and the stack's .env: written only when they differ from what is there
has bot && "${SSH[@]}" entry "umask 077; cat > $R/bot/token.next; if cmp -s $R/bot/token.next $R/bot/token; then rm -f $R/bot/token.next; else cat $R/bot/token.next > $R/bot/token; rm -f $R/bot/token.next; fi" < "$HERE/panel/.bot-token"
admin_id=
if has bot; then admin_id=$(tr -dc 0-9 < "$HERE/bot/admin-id"); fi
"${SSH[@]}" entry "umask 077; printf 'COMPOSE_PROFILES=%s\nBOT_ADMIN_ID=%s\nDOMAIN=%s\nDNS_STATIC=%s\nPROXY_IP=%s\nJOURNAL_GID=%s\n' '$PROFILES' '$admin_id' '$DOMAIN' '$DNS_STATIC' \"\$(ip -4 route get 1.0.0.0 | grep -oP 'src \\K\\S+')\" \"\$(getent group systemd-journal | cut -d: -f3)\" > $R/.env.next; grep -q 'PROXY_IP=[0-9]' $R/.env.next && grep -q 'JOURNAL_GID=[0-9]' $R/.env.next \
    && if cmp -s $R/.env.next $R/.env; then rm -f $R/.env.next; else mv -f $R/.env.next $R/.env; fi"

if [[ -z "${SERVICES// /}" ]]; then
    echo "shipped: the images and the files are on the server; nothing that is running was changed."
    echo "switch a service to them:  stack/deploy.sh <service> …   ($KNOWN)"
    exit 0
fi
echo "switching: $SERVICES"

if named panel; then
# The built page: a directory of files named by their content. Written into the
# directory the container has mounted (not swapped: the mount would keep the
# old one) — the scripts first, the index that names them last, then what the
# new build no longer has is removed. The previous build stays in web.prev.
WEB_LIST=$(mktemp)
( cd "$HERE/web/out" && { find . -type f ! -name index.html | LC_ALL=C sort; echo ./index.html; } ) > "$WEB_LIST"
cp "$WEB_LIST" "$HERE/web/out/.files"
( cd "$HERE/web/out" && { echo ./.files; cat "$WEB_LIST"; } | tar -cf - -T - ) | "${SSH[@]}" entry "set -e; cd $R/panel; mkdir -p web
    rm -rf web.pre; cp -a web web.pre
    tar -C web -xf -
    ( cd web && LC_ALL=C sort .files > .files.sorted \
        && find . -type f ! -name .files ! -name .files.sorted | LC_ALL=C sort | LC_ALL=C comm -23 - .files.sorted | xargs -r rm -f \
        && rm -f .files.sorted && find . -mindepth 1 -type d -empty -delete )
    if [ ! -s web.pre/index.html ] || cmp -s web/index.html web.pre/index.html; then rm -rf web.pre; else rm -rf web.prev; mv web.pre web.prev; fi"
rm -f "$WEB_LIST" "$HERE/web/out/.files"
fi

# The server-side half runs ON the server, detached from this ssh session: a
# link that drops in the middle must not stop a switch half way (say, the new
# users service beside the old agent). It is shipped as a file, started under
# setsid with its output in deploy.log, and followed from here; a dropped
# connection is only a pause in the following. deploy.rc holds its exit code.
REMOTE_SCRIPT=$(mktemp)
cat > "$REMOTE_SCRIPT" <<'REMOTE'
set -euo pipefail
cd /opt/vpn-stack
. ./probes.sh
named() { [[ " $SERVICES " == *" $1 "* ]]; }
# The features of this server (FEATURES in site.env), and a server switched to the
# tunnel service: every compose call below knows those services.
profiles=${PROFILES:-}
if [ -f tunnel.enabled ]; then profiles=${profiles:+$profiles,}tunnel; fi
if [ -f links.enabled ]; then profiles=${profiles:+$profiles,}links; fi
if [ -n "$profiles" ]; then export COMPOSE_PROFILES=$profiles; fi

# ── the compose file is taken into use; what it replaces is kept ─────────────
# compose.yml.prev: what was there before the last deploy that changed it.
docker compose -f compose.yml.next config -q || { echo "the shipped compose file does not pass docker compose config — nothing was switched" >&2; exit 1; }
if ! cmp -s compose.yml.next compose.yml; then
    [ -f compose.yml ] && cp -p compose.yml compose.yml.prev
    mv -f compose.yml.next compose.yml
    echo "compose.yml: updated (the one before is compose.yml.prev)"
else
    rm -f compose.yml.next
fi

# only the named service, never what it depends on, never anything else
up() { docker compose up -d --no-deps "$@" 2>&1 | grep -vE 'Running|Pulled|Pulling|Waiting|Healthy' || true; }
container_of() { case "$1" in traefik|smartdns) echo "$1" ;; *) echo "vpn-$1" ;; esac; }
image_of() { case "$1" in smartdns) echo awgscale-dnsgate:local ;; links) echo awgscale-tunnel:local ;; *) echo "awgscale-$1:local" ;; esac; }
# the image a name points to now, and the image a service's container really runs (both by ID:
# the name stays awgscale-<name>:local from one deploy to the next, the ID does not)
image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || true; }
running_image() { docker inspect -f '{{.Image}}' "$(container_of "$1")" 2>/dev/null || true; }
# switch <service>: recreated from the package's image — and really so. If the
# start failed, the container before it is still there and would answer every
# question below in its place.
switch() {
    local service=$1 now want name
    up "$service"
    want=$(image_id "$(image_of "$service")")
    now=$(running_image "$service")
    if [ -z "$want" ] || [ "$now" != "$want" ]; then
        name=$(docker inspect -f '{{.Config.Image}}' "$(container_of "$service")" 2>/dev/null || true)
        echo "$service was NOT switched: its container runs ${name:-nothing} (${now:-no image}), not $(image_of "$service") (${want:-not loaded})" >&2
        docker compose ps -a --format '{{.Name}}: {{.Status}}' 2>/dev/null | grep "$(container_of "$service")" >&2 || true
        way_back "$service"
        return 1
    fi
}
# what a service ran before this deploy, to say how to go back to it
declare -A WAS
for service in $SERVICES; do WAS[$service]=$(docker inspect -f '{{.Config.Image}}' "$(container_of "$service")" 2>/dev/null || true); done
way_back() {
    local service=$1
    if [ -f compose.yml.prev ]; then
        echo "WAY BACK for $service (it ran ${WAS[$service]:-nothing} before):  ssh entry 'cd /opt/vpn-stack && ${profiles:+COMPOSE_PROFILES=$profiles }docker compose -f compose.yml.prev up -d --no-deps $service'" >&2
    else
        echo "No earlier compose file for $service on the server (compose.yml.prev): the way back is the commit before this deploy and stack/deploy.sh $service." >&2
    fi
    case "$service" in
    users|agent) echo "users and agent go back TOGETHER: they speak to each other in one language." >&2 ;;
    esac
}
same_iptables() {
    local host image
    host=$(iptables --version); image=$(docker run --rm --network none --entrypoint iptables "$1" --version)
    if [ "$host" != "$image" ]; then
        echo "$2 NOT started: iptables in the image ($image) is not the server's ($host)" >&2
        return 1
    fi
}

if named db; then
    up db
fi

# ── users and agent, first half: the database, the store, the users service ──
if named agent; then
    for _ in $(seq 1 40); do [ "$(docker inspect -f '{{.State.Health.Status}}' vpn-db 2>/dev/null)" = healthy ] && break; sleep 1; done
    [ "$(docker inspect -f '{{.State.Health.Status}}' vpn-db 2>/dev/null)" = healthy ] || { echo "the database (vpn-db) is not healthy — users and agent are left as they are (start it: stack/deploy.sh db)" >&2; exit 1; }
    same_iptables awgscale-agent:local vpn-agent || exit 1
    # `store-init` creates the tables and, the first time only, moves the settings
    # from the old files into them (read back and compared before it counts).
    # It runs from the agent's image, as a one-off container beside the database.
    # </dev/null: this script itself arrives on stdin, and the container must not read it away
    # A new server (cascade/deploy.sh seeded its settings and left new-server behind) starts with an empty store.
    fresh=
    if [ -f /root/awg/cascade/new-server ]; then fresh=--fresh; fi
    docker compose run --rm --no-deps -T agent python -m awgscale.agent store-init $fresh </dev/null | cut -c1-200
    rm -f /root/awg/cascade/new-server
    # The users service serves only once the users are in the database. Moving
    # them there from the old installer's files is a deliberate, one-time act
    # (python -m awgscale.users import), not something a deploy does on its own.
    switch users || exit 1
    if ! wait_for 40 users_answer; then
        echo "vpn-users did not answer. The agent was NOT switched; the panel and the bot cannot list or change users until this is fixed (the tunnel and the users in it are not affected):" >&2
        docker logs vpn-users --tail 20 2>&1 | cut -c1-200 >&2 || true
        way_back users
        exit 1
    fi
    echo "users: running awgscale-users:local"
fi

# ── users and agent, second half: the agent, right after the users service ───
if named agent; then
    mkdir -p /run/vpn-panel && chmod 750 /run/vpn-panel
    switch agent || exit 1
    agent_up=0
    for _ in $(seq 1 40); do
        if [ "$(docker inspect -f '{{.State.Running}}' vpn-agent 2>/dev/null)" = true ] && agent_asks; then agent_up=1; break; fi
        sleep 1
    done
    if [ "$agent_up" != 1 ]; then
        echo "vpn-agent did not answer. The panel and the bot have no agent now (the tunnel, the routing and DNS are not affected)." >&2
        docker logs vpn-agent --tail 20 2>&1 | cut -c1-200 >&2 || true
        way_back agent
        exit 1
    fi
    echo "agent: running awgscale-agent:local"
fi

# ── exits service ────────────────────────────────────────────────────────────
# Restarting it disconnects nobody: it only works on a server when asked to.
if named exits; then
    switch exits || exit 1
    if ! wait_for 30 exits_answer; then
        echo "vpn-exits did not come up — exits cannot be added or updated from the panel until this is fixed (the exits that work are not affected):" >&2
        docker logs vpn-exits --tail 10 2>&1 | cut -c1-200 >&2 || true
        way_back exits
        exit 1
    fi
    echo "exits: running awgscale-exits:local"
fi

# ── watch service ────────────────────────────────────────────────────────────
# Its iptables must be the very version the server uses (two versions of
# iptables-nft on one ruleset may not read each other's rules): a different
# one is not started.
if named watch; then
    same_iptables awgscale-watch:local vpn-watch || exit 1
    mkdir -p /run/cascade
    switch watch || { echo "The boot gate was NOT touched." >&2; exit 1; }
    if ! wait_for 60 watch_answers; then
        echo "vpn-watch did not come up with the rules applied. The rules that are in the kernel stay; nobody refreshes them until this is fixed. The boot gate was NOT touched." >&2
        docker logs vpn-watch --tail 20 2>&1 | cut -c1-200 >&2 || true
        way_back watch
        exit 1
    fi
    echo "watch: running awgscale-watch:local, the rules are applied"

    # ── boot gate: the rules at boot come from the watch service's own code ──
    # Installed only here, after the container answered with the rules applied:
    # the gate is one file taken out of that very container
    # (awgscale.watch.bundle), and it must first print the rules on this
    # server's own python3 without touching anything. It lives in ./boot, which
    # no container mounts. The unit is only replaced on disk: restarting or
    # stopping it would take the client tunnel down with it (awg-quick@awg0
    # Requires it); it matters at boot.
    mkdir -p boot && chmod 700 boot
    docker exec vpn-watch python -m awgscale.watch.bundle - > boot/gate.pyz.new </dev/null
    chmod 600 boot/gate.pyz.new
    if ! PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 boot/gate.pyz.new rules 2>/dev/null | grep -q -- '-A CASCADE'; then
        echo "boot gate NOT installed: the bundle does not print the rules on this server's python3 ($(/usr/bin/python3 --version 2>&1)). The gate that was there stays; the watch service is running the new code." >&2
        PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 boot/gate.pyz.new rules 2>&1 | tail -3 | cut -c1-200 >&2 || true
        exit 1
    fi
    if ! cmp -s boot/gate.pyz.new boot/gate.pyz; then
        [ -f boot/gate.pyz ] && cp -p boot/gate.pyz boot/gate.pyz.prev
        mv -f boot/gate.pyz.new boot/gate.pyz
        echo "boot gate: code updated (gate.pyz)"
    else
        rm -f boot/gate.pyz.new
    fi
    if ! cmp -s cascade-apply.service /etc/systemd/system/cascade-apply.service; then
        systemd-analyze verify ./cascade-apply.service 2>&1 | grep -v 'awg-quick@' | cut -c1-200 >&2 || true
        [ -f /etc/systemd/system/cascade-apply.service ] && cp -p /etc/systemd/system/cascade-apply.service boot/cascade-apply.service.prev
        cp cascade-apply.service /etc/systemd/system/cascade-apply.service.new && mv -f /etc/systemd/system/cascade-apply.service.new /etc/systemd/system/cascade-apply.service
        systemctl daemon-reload
        systemctl enable cascade-apply.service >/dev/null 2>&1
        echo "boot gate: unit installed (gate.pyz apply); the one before is boot/cascade-apply.service.prev"
    fi
    # ── reboot safety net (deadman.sh): installed, never armed here ──────────
    # Arming is done by ops/reboot-entry.sh right before a reboot. The timer
    # runs after every boot and does nothing while the net is not armed. It is
    # installed together with the gate: both must name the same file.
    if ! cmp -s deadman.sh boot/deadman.sh || ! cmp -s vpn-deadman.service /etc/systemd/system/vpn-deadman.service || ! cmp -s vpn-deadman.timer /etc/systemd/system/vpn-deadman.timer; then
        bash -n deadman.sh
        if [ -f boot/deadman.sh ] && ! cmp -s deadman.sh boot/deadman.sh; then cp -p boot/deadman.sh boot/deadman.sh.prev; fi
        cp deadman.sh boot/deadman.sh.new && chmod 700 boot/deadman.sh.new && mv -f boot/deadman.sh.new boot/deadman.sh
        cp vpn-deadman.service vpn-deadman.timer /etc/systemd/system/
        systemctl daemon-reload
        systemctl enable -q vpn-deadman.timer
        echo "reboot safety net: installed (not armed); the one before is boot/deadman.sh.prev"
    fi
fi

# ── tunnel service: only where the server was switched to it ─────────────────
# Recreating it disconnects nobody: an interface that is there is taken over
# as it is, and stopping the service leaves it. Its iptables must be the
# server's own version, like the watch service's.
if named tunnel; then
    if [ -f tunnel.enabled ]; then
        same_iptables awgscale-tunnel:local vpn-tunnel || exit 1
        switch tunnel || exit 1
        if ! wait_for 30 docker exec vpn-tunnel python -m awgscale.tunnel check >/dev/null 2>&1; then
            echo "vpn-tunnel does not report the tunnel as it should be (the interface itself is not touched by this):" >&2
            docker exec vpn-tunnel python -m awgscale.tunnel check 2>&1 | cut -c1-300 >&2 || true
            docker logs vpn-tunnel --tail 10 2>&1 | cut -c1-200 >&2 || true
            way_back tunnel
            exit 1
        fi
        echo "tunnel: running awgscale-tunnel:local, interface taken over"
    else
        echo "tunnel: this server keeps awg0 with the host's awg-quick unit — nothing to switch (stack/switch-tunnel.sh on awg0)"
    fi
fi
# The links to the exits, the same way: taken over as they are, never restarted.
if named links; then
    if [ -f links.enabled ]; then
        same_iptables awgscale-tunnel:local vpn-links || exit 1
        switch links || exit 1
        if ! ls /etc/amnezia/amneziawg/awgk*.conf >/dev/null 2>&1; then
            # no exit yet: nothing to check but that it stays up
            sleep 8
            container_holds vpn-links || { echo "vpn-links does not stay up:" >&2; docker logs vpn-links --tail 10 2>&1 | cut -c1-200 >&2 || true; way_back links; exit 1; }
        elif ! wait_for 30 docker exec vpn-links python -m awgscale.tunnel check >/dev/null 2>&1; then
            echo "vpn-links does not report the exit links as they should be (the links themselves are not touched by this):" >&2
            docker exec vpn-links python -m awgscale.tunnel check 2>&1 | cut -c1-400 >&2 || true
            docker logs vpn-links --tail 10 2>&1 | cut -c1-200 >&2 || true
            way_back links
            exit 1
        fi
        echo "links: running awgscale-tunnel:local, exit links taken over"
    else
        echo "links: this server keeps the exit links with the host's awg-quick units — nothing to switch (stack/switch-tunnel.sh on links)"
    fi
fi

# ── resolver: AdGuard Home under its keeper ──────────────────────────────────
# AdGuard Home with the config and data it already has in /opt/AdGuardHome.
# The image's binary must be the very version that wrote them (a newer one
# rewrites the config and there is no way back). Exactly one may hold port 53:
# recreating the container takes DNS away for a few seconds, and if the new
# one does not answer DNS, the one before it is started again right here.
# The agent restarts it through its keeper.
if named resolver; then
    if [ -x /opt/AdGuardHome/AdGuardHome ]; then
        host_agh=$(/opt/AdGuardHome/AdGuardHome --version); image_agh=$(docker run --rm --network none --entrypoint /opt/adguardhome/AdGuardHome awgscale-resolver:local --version)
        if [ "$host_agh" != "$image_agh" ]; then
            echo "vpn-resolver NOT started: AdGuard Home in the image ($image_agh) is not the server's ($host_agh)" >&2
            exit 1
        fi
    fi
    running_agh=$(docker exec vpn-resolver /opt/adguardhome/AdGuardHome --version 2>/dev/null || true)
    if [ -n "$running_agh" ] && [ "$running_agh" != "$(docker run --rm --network none --entrypoint /opt/adguardhome/AdGuardHome awgscale-resolver:local --version)" ]; then
        echo "vpn-resolver NOT started: AdGuard Home in the image is not the version that is running ($running_agh)" >&2
        exit 1
    fi
    if systemctl is-active -q AdGuardHome; then
        echo "resolver: the host unit is stopped, the container takes over (DNS is away for a few seconds)"
        systemctl disable --now AdGuardHome >/dev/null 2>&1 || true
    fi
    up resolver
    resolver_up=0
    for _ in $(seq 1 40); do
        if [ "$(running_image resolver)" = "$(image_id awgscale-resolver:local)" ] && [ "$(docker inspect -f '{{.State.Running}}' vpn-resolver 2>/dev/null)" = true ] && resolver_asks; then resolver_up=1; break; fi
        sleep 1
    done
    if [ "$resolver_up" != 1 ]; then
        echo "vpn-resolver (awgscale-resolver:local) did not come up answering DNS:" >&2
        docker logs vpn-resolver --tail 20 2>&1 | cut -c1-200 >&2 || true
        if [ -n "${WAS[resolver]:-}" ] && [ "${WAS[resolver]}" != awgscale-resolver:local ] && [ -f compose.yml.prev ]; then
            echo "the resolver that ran before (${WAS[resolver]}) is started again from compose.yml.prev" >&2
            docker compose -f compose.yml.prev up -d --no-deps resolver >/dev/null 2>&1 || true
            if wait_for 40 dns_answers; then echo "DNS answers again (the resolver before the switch)" >&2; else echo "DNS STILL DOES NOT ANSWER — look at vpn-resolver now" >&2; fi
        else
            way_back resolver
        fi
        exit 1
    fi
    echo "resolver: running awgscale-resolver:local, DNS answers"
fi

# ── firewall ─────────────────────────────────────────────────────────────────
# :443 is open to everyone: the panel is restricted by Traefik (ipAllowList),
# the smart DNS by its own codes. Remove the per-address rules of older layouts.
ufw allow 443/tcp comment 'Traefik: panel (allow-listed inside) + smart DNS' >/dev/null
ufw allow 80/tcp comment 'ACME HTTP-01 for Traefik' >/dev/null
if [[ " ${profiles//,/ } " == *" bot "* || " ${profiles//,/ } " == *" smartdns "* ]]; then
    ufw allow from "$EXIT_NET" to 172.16.17.1 port 53 comment 'AdGuard DNS for service containers' >/dev/null
fi
ports="443|$(echo $OLD_PORTS | tr ' ' '|')"
while read -r num; do
    [[ -n "$num" ]] && ufw --force delete "$num" >/dev/null
done < <(ufw status numbered | grep -E "^\[ *[0-9]+\] +($ports)/tcp +ALLOW IN +[0-9]" | sed -E 's/^\[ *([0-9]+)\].*/\1/' | sort -rn)

# ── the panel, the bot, the smart DNS ────────────────────────────────────────
if named panel; then
    switch panel || exit 1
    if ! wait_for 30 panel_answers; then
        echo "vpn-panel does not answer on its port:" >&2
        docker logs vpn-panel --tail 20 2>&1 | cut -c1-200 >&2 || true
        way_back panel
        echo "(the page that was there before is /opt/vpn-stack/panel/web.prev)" >&2
        exit 1
    fi
    echo "panel: running $(docker inspect -f '{{.Config.Image}}' vpn-panel)"
fi
if named bot; then
    switch bot || exit 1
    sleep 8
    if ! container_holds vpn-bot; then
        echo "vpn-bot does not stay up:" >&2
        docker logs vpn-bot --tail 20 2>&1 | cut -c1-200 >&2 || true
        way_back bot
        exit 1
    fi
    echo "bot: running $(docker inspect -f '{{.Config.Image}}' vpn-bot)"
fi
if named smartdns; then
    switch smartdns || exit 1
    if ! wait_for 30 dnsgate_answers; then
        echo "smartdns does not answer on its DoH port — devices without the VPN have no DNS until this is fixed:" >&2
        docker logs smartdns --tail 20 2>&1 | cut -c1-200 >&2 || true
        way_back smartdns
        exit 1
    fi
    echo "smartdns: running $(docker inspect -f '{{.Config.Image}}' smartdns)"
fi

# ── history and logs ─────────────────────────────────────────────────────────
# No `docker compose pull`: updating one of these images is a deliberate act
# (docker compose pull <service>, then this script with its name). A named one
# is created if missing and restarted, so that it reads the config shipped above.
for service in prometheus loki alloy grafana db-backup; do
    if named "$service"; then
        up "$service"
        docker compose restart "$service" >/dev/null 2>&1
        echo "restarted $service"
    fi
done

# ── Traefik: routing is hot-reloaded; a restart only for static config ───────
# Written in place (same inode) so Traefik's file watcher sees the change.
if named traefik; then
    if ! cmp -s traefik/dynamic.yml.next traefik/dynamic.yml; then
        cp -p traefik/dynamic.yml traefik/dynamic.yml.prev 2>/dev/null || true
        cat traefik/dynamic.yml.next > traefik/dynamic.yml
        echo "routing reloaded"
    fi
    static_changed=0
    if ! cmp -s traefik/traefik.yml.next traefik/traefik.yml; then
        cp -p traefik/traefik.yml traefik/traefik.yml.prev 2>/dev/null || true
        cat traefik/traefik.yml.next > traefik/traefik.yml
        static_changed=1
    fi
    up traefik
    if [ "$static_changed" = 1 ]; then docker compose restart traefik >/dev/null 2>&1; echo "restarted traefik"; fi
fi
rm -f traefik/dynamic.yml.next traefik/traefik.yml.next

sleep 3
docker compose ps --format '{{.Name}}: {{.Image}}: {{.Status}}'
docker logs traefik --since 20s 2>&1 | grep -E 'ERR' | tail -3 | cut -c1-220 || true
ufw status | grep -E '^(443|80)/tcp' | grep -v v6
REMOTE
"${SCP[@]}" "$REMOTE_SCRIPT" entry:$R/deploy-remote.sh
rm -f -- "$REMOTE_SCRIPT"
"${SSH[@]}" entry "cd $R && if [ -f deploy.running ] && kill -0 \"\$(cat deploy.running)\" 2>/dev/null; then echo 'another deploy is running on the server (deploy.running)' >&2; exit 1; fi
    rm -f deploy.rc deploy.log
    SERVICES='$SERVICES' PROFILES='$PROFILES' OLD_PORTS='$OLD_PORTS' EXIT_NET='$EXIT_NET' setsid nohup bash -c 'echo \$\$ > deploy.running; bash deploy-remote.sh > deploy.log 2>&1; echo \$? > deploy.rc; rm -f deploy.running' </dev/null >/dev/null 2>&1 &"
shown=0
remote_rc=
while [ -z "$remote_rc" ]; do
    sleep 2
    # one look: the exit code once there is one, then the new lines of the log —
    # in this order, so that with the code in hand the log read after it is whole
    if ! look=$("${SSH[@]}" -o ConnectTimeout=10 entry "cd $R && cat deploy.rc 2>/dev/null; echo '@@log'; tail -n +$((shown + 1)) deploy.log 2>/dev/null; echo '@@end'"); then
        echo "(no connection to the server — the deploy goes on there; following again)" >&2
        sleep 5
        continue
    fi
    remote_rc=$(printf '%s' "${look%%@@log*}" | tr -dc 0-9)
    lines=${look#*@@log}
    lines=${lines#$'\n'}
    lines=${lines%@@end*}
    # a line still being written is left for the next look (unless the log is finished)
    if [ -z "$remote_rc" ] && [[ $lines != *$'\n' ]]; then
        case $lines in
            *$'\n'*) lines=${lines%$'\n'*}$'\n' ;;
            *) lines= ;;
        esac
    fi
    if [ -n "$lines" ]; then
        printf '%s' "$lines"
        [[ $lines == *$'\n' ]] || echo
        shown=$((shown + $(printf '%s\n' "${lines%$'\n'}" | grep -c '' || true)))
    fi
done
if [ "$remote_rc" != 0 ]; then
    echo "the server-side part ended with code $remote_rc (the whole log: $R/deploy.log on the server)" >&2
    exit "$remote_rc"
fi
echo "Panel: https://$DOMAIN   (password: ssh -F $HERE/ssh_config entry cat $R/panel-data/.password)"
