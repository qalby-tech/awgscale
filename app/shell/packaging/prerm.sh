#!/bin/sh
# Before the package goes: the tunnel down (the service gives the network back as it stops).
set -e
if [ -d /run/systemd/system ]; then
    systemctl disable --now awgscale.service || true
fi
