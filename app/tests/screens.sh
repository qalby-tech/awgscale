#!/usr/bin/env bash
# =============================================================================
# app/tests/screens.sh — pictures of the app's screens, taken in a container:
# a build whose tunnel only pretends (never packaged), a made-up network, the
# app API answered by tests/standin.py, clicks by xdotool on a virtual display.
# The pictures land in app/target/screens/*.png; the steps are checked by what
# the stand-in was asked.
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=awgscale-app-build:local
docker build -q -t "$IMAGE" "$HERE/docker" >/dev/null
rm -rf "$HERE/target/screens"; mkdir -p "$HERE/target/screens"
docker run --rm --network none -u "$(id -u):$(id -g)" -v "$HERE":/app -w /app -e HOME=/tmp/home -e CARGO_HOME=/app/target/cargo-home \
    -e CARGO_TARGET_DIR=/app/target/pretend -e CARGO_NET_OFFLINE=true "$IMAGE" bash -c '
set -e
cd ui && npm run -s build >/dev/null && cd ../shell
../ui/node_modules/.bin/tauri build --no-bundle --features pretend-tunnel -c "{\"build\":{\"beforeBuildCommand\":\"\"}}" 2>&1 | grep -E "error|warning: unused" || true
data=$HOME/.local/share/awgscale.qalby.tech; mkdir -p $data
printf "%s" "{\"host\":\"vpn.example.org\",\"name\":\"test_laptop\",\"address\":\"172.16.17.23\",\"entry\":\"127.0.0.1\",\"owner\":true,\"conf\":\"[Interface]\\nPrivateKey = made-up\\n\"}" > $data/network.json
python3 /app/tests/standin.py > /app/target/screens/standin.log 2>&1 &
Xvfb :9 -screen 0 420x820x24 >/dev/null 2>&1 & sleep 1
export DISPLAY=:9 WEBKIT_DISABLE_COMPOSITING_MODE=1
/app/target/pretend/release/awgscale-app > /app/target/screens/app.log 2>&1 &
sleep 6
shot() { import -window root /app/target/screens/$1.png; }
click() { xdotool mousemove $1 $2 click 1; sleep ${3:-1.5}; }
shot 1-home
click 210 200; shot 2-connected
click 210 363; shot 3-location
click 210 603; shot 4-pinned
click 157 790; shot 5-network
click 367 790; shot 6-settings
'
echo "pictures: $HERE/target/screens/"
# what the clicks must have asked: keep me on exit 1
grep -qx 'choose 1' "$HERE/target/screens/standin.log" && echo "app screens: ok" || { echo "app screens: the exit was not chosen"; exit 1; }
