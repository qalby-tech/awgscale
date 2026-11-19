#!/usr/bin/env bash
# Builds the image of every Python service of the stack from docker/Dockerfile,
# on THIS machine. stack/deploy.sh ships them to the entry server with
# `docker save | docker load`: nothing is built or downloaded there.
#   docker/build.sh                  every image, tagged awgscale-<name>:local
#   docker/build.sh watch tunnel     only these
#   TAG=test docker/build.sh         … tagged awgscale-<name>:test
#   UBUNTU=26.04 docker/build.sh     on another Ubuntu release (default: the entry's, 24.04)
#   docker/build.sh --list           the names
# One image per container of stack/compose.yml; `links` runs the tunnel's image.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
TAG=${TAG:-local}
# the Ubuntu release the images are built on: the entry server's (stack/deploy.sh passes it)
UBUNTU=${UBUNTU:-24.04}
NAMES="panel bot dnsgate users agent resolver watch tunnel exits"

# the system tools a service runs (what `--build-arg APT=` is given)
apt_of() {
    case "$1" in
        agent)  echo "iptables ipset iproute2 bind9-dnsutils curl" ;;
        watch)  echo "iptables ipset iproute2 conntrack iputils-ping" ;;
        tunnel) echo "iptables iproute2" ;;
        exits)  echo "openssh-client iproute2 iputils-ping" ;;
        users)  echo "iproute2" ;;
        *)      echo "" ;;
    esac
}
# the stage of the Dockerfile an image is: the plain service, or one that carries something more
target_of() {
    case "$1" in
        resolver|panel) echo "$1" ;;
        *)              echo "service" ;;
    esac
}

if [ "${1:-}" = "--list" ]; then echo "$NAMES"; exit 0; fi
if [ $# -gt 0 ]; then wanted="$*"; else wanted=$NAMES; fi
for name in $wanted; do
    case " $NAMES " in *" $name "*) ;; *) echo "no such image: $name (there are: $NAMES)" >&2; exit 1 ;; esac
done
for name in $wanted; do
    built=
    # the registries are sometimes out of reach for a moment: a build is tried three times
    for attempt in 1 2 3; do
        if docker build -q -f "$HERE/docker/Dockerfile" --target "$(target_of "$name")" --build-arg UBUNTU="$UBUNTU" --build-arg APT="$(apt_of "$name")" -t "awgscale-$name:$TAG" "$HERE" >/dev/null; then built=1; break; fi
        sleep 5
    done
    [ -n "$built" ] || { echo "awgscale-$name:$TAG does not build" >&2; exit 1; }
    echo "built awgscale-$name:$TAG"
done
