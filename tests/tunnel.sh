#!/usr/bin/env bash
# The tunnel service (src/awgscale/tunnel) in a real kernel: a throwaway container
# with its own network namespace and exactly the privileges of the service in
# compose.yml (NET_ADMIN alone, a read-only root).
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${TUNNEL_TEST_IMAGE:-vpn-tunnel:test}
docker build -q -f "$HERE/docker/Dockerfile" --build-arg APT="iptables iproute2" -t "$IMAGE" "$HERE" >/dev/null
out=$(docker run --rm --network none --read-only --cap-drop ALL --cap-add NET_ADMIN --security-opt no-new-privileges:true \
    --tmpfs /tmp --tmpfs /usr/local/bin:exec -v "$HERE:/src:ro" "$IMAGE" bash /src/tests/tunnel_inside.sh 2>&1) || true
echo "$out"
pass=$(grep -c '^ok ' <<<"$out" || true); fail=$(grep -vc '^ok ' <<<"$out" || true)
echo
echo "$pass of $((pass + fail)) checks passed"
[ "$fail" = 0 ] && [ "$pass" -gt 0 ]
