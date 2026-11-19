#!/usr/bin/env bash
# Runs ON an exit server the panel manages: all package updates. The tunnel
# module is rebuilt by dkms for a new kernel; that kernel is used after a reboot.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1 || { echo "@@ERR apt-get update не прошёл"; exit 1; }
n=$(apt-get -s dist-upgrade 2>/dev/null | grep -c '^Inst ' || true)
apt-get -y -qq -o Dpkg::Options::=--force-confold dist-upgrade >/dev/null 2>&1 || { echo "@@ERR обновление пакетов не прошло"; exit 1; }
apt-get -y -qq autoremove >/dev/null 2>&1 || true
[ -f /var/run/reboot-required ] && echo "@@REBOOT нужна перезагрузка"
echo "@@OK обновлено пакетов: $n; ядро сейчас $(uname -r)"
