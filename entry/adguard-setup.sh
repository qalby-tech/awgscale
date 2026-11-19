#!/usr/bin/env bash
# =============================================================================
# adguard-setup.sh — run ON the entry server (AWG0) as root. Idempotent.
#
# Installs AdGuard Home natively (not Docker: Docker bypasses UFW) as the
# ad-blocking resolver for VPN clients:
#   DNS : <tunnel address>:53   — reachable only from inside awg0
#   UI  : 127.0.0.1:3001        — reach it with: ssh -L 3001:127.0.0.1:3001 entry
#
# Upstreams are IP-literal DoH (no bootstrap lookup). cascade-setup.sh routes
# those IPs through the foreign exit, so answers are geo-located abroad.
# Fallback is Yandex DNS (RU address → goes direct), so Russian sites keep
# resolving if the exit tunnel is down.
#
# Usage (from the operator machine):
#   ssh -F ssh_config entry 'bash -s' < entry/adguard-setup.sh
# =============================================================================
set -euo pipefail

readonly AGH_DIR="/opt/AdGuardHome"
readonly AGH_URL="https://static.adtidy.org/adguardhome/release/AdGuardHome_linux_amd64.tar.gz"
readonly UI_ADDR="127.0.0.1"
readonly UI_PORT=3001
readonly WIZARD_PORT=3000   # first-run wizard holds 0.0.0.0:3000, so the final UI needs another port
readonly CRED_FILE="/root/awg/adguard-admin.txt"
readonly CFG_INIT="/root/awg/awgsetup_cfg.init"
readonly UPSTREAMS='["https://1.1.1.1/dns-query","https://1.0.0.1/dns-query","https://9.9.9.9/dns-query"]'
readonly FALLBACK='["77.88.8.8","77.88.8.1"]'

info() { echo "[agh] $*"; }
die()  { echo "[agh] ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"

# Tunnel address of this server, from the live interface (not hardcoded)
TUN_IP=$(ip -4 -o addr show awg0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
[[ -n "$TUN_IP" ]] || die "awg0 has no IPv4 address — is awg-quick@awg0 up?"
info "tunnel address: $TUN_IP"

# ── 1. Binary + service ──────────────────────────────────────────────────────
if [[ ! -x "$AGH_DIR/AdGuardHome" ]]; then
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    curl -fsSL --max-time 180 -o "$tmp/agh.tgz" "$AGH_URL" || die "download failed"
    tar -xzf "$tmp/agh.tgz" -C /opt
    info "installed $("$AGH_DIR/AdGuardHome" --version)"
fi

# Start only after the tunnel interface exists, otherwise the bind to
# $TUN_IP:53 fails at boot and clients see "no internet".
mkdir -p /etc/systemd/system/AdGuardHome.service.d
cat > /etc/systemd/system/AdGuardHome.service.d/after-awg.conf <<'EOF'
[Unit]
After=awg-quick@awg0.service
Wants=awg-quick@awg0.service

[Service]
Restart=always
RestartSec=5
EOF

if [[ ! -f /etc/systemd/system/AdGuardHome.service ]]; then
    "$AGH_DIR/AdGuardHome" -s install >/dev/null
    info "service installed"
fi
systemctl daemon-reload
systemctl enable AdGuardHome >/dev/null 2>&1
systemctl is-active -q AdGuardHome || systemctl start AdGuardHome

wait_http() {   # $1 = url; waits until anything answers
    local i
    for i in $(seq 1 30); do
        curl -s -o /dev/null --max-time 2 "$1" && return 0
        sleep 1
    done
    return 1
}

# ── 2. First-run configuration through the install API ───────────────────────
if [[ ! -f "$AGH_DIR/AdGuardHome.yaml" ]]; then
    wait_http "http://127.0.0.1:${WIZARD_PORT}/" || die "setup wizard did not come up on :$WIZARD_PORT"
    pass=$(openssl rand -base64 18 | tr -d '/+=')
    umask 077
    printf 'url: http://127.0.0.1:%s (via ssh -L)\nuser: admin\npassword: %s\n' "$UI_PORT" "$pass" > "$CRED_FILE"
    curl -fsS --max-time 20 -X POST "http://127.0.0.1:${WIZARD_PORT}/control/install/configure" \
        -H 'Content-Type: application/json' \
        -d "{\"web\":{\"ip\":\"${UI_ADDR}\",\"port\":${UI_PORT}},\"dns\":{\"ip\":\"${TUN_IP}\",\"port\":53},\"username\":\"admin\",\"password\":\"${pass}\"}" \
        >/dev/null || die "install/configure failed"
    info "configured: DNS ${TUN_IP}:53, UI ${UI_ADDR}:${UI_PORT}; credentials in $CRED_FILE"
    sleep 3
fi

[[ -f "$CRED_FILE" ]] || die "$CRED_FILE missing — cannot authenticate to the AdGuard API"
pass=$(awk '/^password:/{print $2}' "$CRED_FILE")
api() {  # $1 = path, $2 = json
    curl -fsS --max-time 20 -u "admin:${pass}" -X POST "http://${UI_ADDR}:${UI_PORT}/control/$1" \
        -H 'Content-Type: application/json' -d "$2"
}
wait_http "http://${UI_ADDR}:${UI_PORT}/" || die "AdGuard UI did not come back on ${UI_ADDR}:${UI_PORT}"

# ── 3. Resolver settings ─────────────────────────────────────────────────────
# ratelimit 0: a router client is ONE tunnel address for a whole household, the
# default 20 req/s would throttle it; the listener is tunnel-only anyway.
api dns_config "{\"upstream_dns\":${UPSTREAMS},\"fallback_dns\":${FALLBACK},\"upstream_mode\":\"parallel\",\"ratelimit\":0,\"cache_size\":8388608,\"cache_optimistic\":true,\"blocking_mode\":\"default\"}" >/dev/null
info "upstreams set (DoH by IP), fallback Yandex DNS"

# ── 4. Firewall: port 53 only from inside the tunnel ─────────────────────────
ufw allow in on awg0 to any port 53 comment 'AdGuard DNS (tunnel only)' >/dev/null
info "ufw: 53 allowed on awg0 only"

# ── 5. Hand this resolver to NEW clients ─────────────────────────────────────
if grep -q '^export CLIENT_DNS=' "$CFG_INIT"; then
    sed -i "s|^export CLIENT_DNS=.*|export CLIENT_DNS='${TUN_IP}'|" "$CFG_INIT"
else
    echo "export CLIENT_DNS='${TUN_IP}'" >> "$CFG_INIT"
fi
info "CLIENT_DNS='${TUN_IP}' in $CFG_INIT"

# ── 6. Verify ────────────────────────────────────────────────────────────────
ok=$(dig +short +time=5 +tries=2 @"$TUN_IP" example.org A | head -1)
blocked=$(dig +short +time=5 +tries=2 @"$TUN_IP" ad.doubleclick.net A | head -1)
info "example.org      → ${ok:-FAILED}"
info "ad.doubleclick.net → ${blocked:-<empty>}  (0.0.0.0 = blocked)"
[[ -n "$ok" ]] || die "resolver does not answer on $TUN_IP"
ss -lnu | grep -q "${TUN_IP}:53" || die "not listening on ${TUN_IP}:53"
ss -lnu | grep -qE '(0\.0\.0\.0|\*):53 ' && die "resolver is listening on ALL addresses — must be tunnel only"
info "done"
