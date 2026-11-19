#!/usr/bin/env bash
# Runs ON a new exit server (piped over SSH by the exits service). Checks that
# the box can be an exit, then installs what an exit needs: the AmneziaWG kernel
# module and tools (Amnezia PPA) and Docker. Safe to run again.
# The PPA's key is expected at /etc/apt/keyrings/amnezia-ppa.gpg (sent before).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
[ "$(id -u)" = 0 ] || { echo "@@ERR нужен вход под root"; exit 1; }
. /etc/os-release
[ "${ID:-}" = ubuntu ] || { echo "@@ERR нужна Ubuntu (здесь ${PRETTY_NAME:-неизвестная система})"; exit 1; }
[ "$(uname -m)" = x86_64 ] || { echo "@@ERR нужен сервер x86_64 (здесь $(uname -m))"; exit 1; }
k=$(uname -r); kmaj=${k%%.*}; krest=${k#*.}; kmin=${krest%%.*}
if [ "$kmaj" -lt 6 ] || { [ "$kmaj" = 6 ] && [ "$kmin" -lt 7 ]; }; then echo "@@ERR ядро $k слишком старое: нужно 6.7 или новее (Ubuntu 24.04+)"; exit 1; fi
[ -s /etc/apt/keyrings/amnezia-ppa.gpg ] || { echo "@@ERR ключ репозитория не доставлен"; exit 1; }
cat > /etc/apt/sources.list.d/amnezia-ppa.sources <<S
Types: deb
URIs: https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu
Suites: ${VERSION_CODENAME}
Components: main
Signed-By: /etc/apt/keyrings/amnezia-ppa.gpg
S
apt-get update -qq >/dev/null 2>&1 || { echo "@@ERR apt-get update не прошёл"; exit 1; }
apt-get install -y -qq linux-headers-generic "linux-headers-$k" amneziawg-dkms amneziawg-tools docker.io conntrack curl >/dev/null 2>&1 \
    || { echo "@@ERR пакеты не установились (apt-get install)"; exit 1; }
modprobe amneziawg 2>/dev/null || { echo "@@ERR модуль amneziawg не загрузился (dkms: $(dkms status 2>/dev/null | tr '\n' ';'))"; exit 1; }
echo amneziawg > /etc/modules-load.d/amneziawg.conf
systemctl enable -q --now docker
echo "@@OK ${PRETTY_NAME}; ядро $k; $(nproc) CPU; $(free -m | awk '/Mem:/{print $2}') МБ"
