#!/bin/sh
# After the package is installed: the group whose members may switch the VPN on, the person who
# installed it in that group, and the service started.
set -e
getent group awgscale >/dev/null || groupadd --system awgscale
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    usermod -aG awgscale "$SUDO_USER" || true
fi
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
    systemctl enable --now awgscale.service || true
fi
