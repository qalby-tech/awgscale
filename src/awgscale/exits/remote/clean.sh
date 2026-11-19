#!/usr/bin/env bash
# Runs ON an exit server that was removed from the cascade: takes away what the
# panel put there for the tunnel. The packages, Docker and the firewall stay.
set -u
systemctl disable --now awg-quick@awgc >/dev/null 2>&1
docker rm -f vpn-exit-stats >/dev/null 2>&1
rm -f /etc/amnezia/amneziawg/awgc.conf /etc/amnezia/amneziawg/awgc.key /etc/amnezia/amneziawg/awgc.psk /etc/amnezia/amneziawg/awgc.params
echo "@@OK туннель и мониторинг с сервера убраны"
