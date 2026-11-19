#!/usr/bin/env bash
# What stack/deploy.sh does ON the entry server (the script it sends there),
# run in a throwaway container against stand-ins for docker, systemctl, ufw and
# the probes: which containers a deploy starts and which it never touches, what
# it keeps for the way back, when the boot gate is installed and when it is not.
# Nothing is started and no server is reached; the real images and the real
# probes are the business of tests/stack.sh.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${DEPLOY_TEST_IMAGE:-vpn-watch:test}
docker build -q -f "$HERE/docker/Dockerfile" --build-arg APT="iptables ipset iproute2 conntrack iputils-ping" -t "$IMAGE" "$HERE" >/dev/null
out=$(docker run --rm --network none -v "$HERE:/src:ro" "$IMAGE" bash /src/tests/deploy_remote_inside.sh 2>&1) || true
echo "$out"
pass=$(grep -c '^ok ' <<<"$out" || true); fail=$(grep -vc '^ok ' <<<"$out" || true)
echo
echo "$pass of $((pass + fail)) checks passed"
[ "$fail" = 0 ] && [ "$pass" -gt 0 ]
