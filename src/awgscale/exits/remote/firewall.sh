#!/usr/bin/env bash
# Runs ON an exit server the panel manages. Default-deny for connections to the
# server itself (exit-firewall.sh, sent before to /usr/local/sbin), applied
# SAFELY in two calls:
#   firewall.sh arm    writes the settings, applies the rules and arms a timer
#                      that removes them again after two minutes
#   firewall.sh keep   called through a NEW SSH login (which proves the rules let
#                      us in): stops the timer, enables the rules at boot
# Env for arm: SSH_PORT, UDP_PORT, STATS_RULE (one iptables match, may be empty).
set -euo pipefail
case "${1:-}" in
    arm)
        : "${SSH_PORT:?}" "${UDP_PORT:?}"
        chmod 755 /usr/local/sbin/exit-firewall.sh
        printf 'TCP_PORTS="%s"\nUDP_PORTS="%s"\nEXTRA_V4=(%s)\n' "$SSH_PORT" "$UDP_PORT" "${STATS_RULE:+\"$STATS_RULE\"}" > /etc/exit-firewall.conf
        systemctl stop fw-rollback.timer 2>/dev/null || true; systemctl reset-failed fw-rollback.service 2>/dev/null || true
        systemd-run --quiet --unit fw-rollback --on-active=120 /usr/local/sbin/exit-firewall.sh remove
        /usr/local/sbin/exit-firewall.sh apply >/dev/null
        echo "@@OK правила применены, ждут подтверждения" ;;
    keep)
        systemctl stop fw-rollback.timer 2>/dev/null || true
        cat > /etc/systemd/system/exit-firewall.service <<UNIT
[Unit]
Description=Default-deny firewall for connections to this exit server
After=network-pre.target docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/exit-firewall.sh apply

[Install]
WantedBy=multi-user.target
UNIT
        systemctl daemon-reload; systemctl enable exit-firewall.service >/dev/null 2>&1
        echo "@@OK файрвол оставлен и включён при загрузке" ;;
    *) echo "@@ERR arm | keep"; exit 1 ;;
esac
