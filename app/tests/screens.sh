#!/usr/bin/env bash
# =============================================================================
# app/tests/screens.sh — pictures of the app's screens, taken in a container:
# a build whose tunnel only pretends (never packaged), a made-up network, the
# app API answered by tests/standin.py, on a virtual display. tests/screens.py
# drives the app through the web view's WebDriver: it presses what it finds by
# its text and fails when a text does not appear. Twice: in English against
# today's exits, then in Russian against exits as an older server sends them,
# then the first run, a connection given by hand (an AmneziaWG config).
# The pictures land in app/target/screens/*.png; the steps are also checked by
# what the stand-in was asked.
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
data=$HOME/.local/share/tech.qalby.awgscale; mkdir -p $data
printf "%s" "{\"host\":\"vpn.example.org\",\"name\":\"test_laptop\",\"address\":\"172.16.17.23\",\"entry\":\"127.0.0.1\",\"owner\":true,\"conf\":\"[Interface]\\nPrivateKey = made-up\\n\"}" > $data/network.json
Xvfb :9 -screen 0 420x820x24 >/dev/null 2>&1 & sleep 1
export DISPLAY=:9 WEBKIT_DISABLE_COMPOSITING_MODE=1 TAURI_WEBVIEW_AUTOMATION=true
WebKitWebDriver --port=4444 > /app/target/screens/webdriver.log 2>&1 & sleep 1
python3 /app/tests/standin.py > /app/target/screens/standin.log 2>&1 & standin=$!
python3 /app/tests/screens.py current
kill $standin; sleep 0.5
printf "%s" "{\"language\":\"ru\"}" > $data/settings.json
STANDIN_PAYLOAD=older python3 /app/tests/standin.py > /app/target/screens/standin-older.log 2>&1 &
python3 /app/tests/screens.py older
rm -f $data/network.json
printf "%s" "{\"language\":\"en\"}" > $data/settings.json
python3 /app/tests/screens.py manual
'
echo "pictures: $HERE/target/screens/"
# what the presses must have asked: the best of nl (5: as few online as 2, fewer people), Авто again, then 2;
# from the older server its best (1: nobody counted anywhere, the largest share), then Авто
chosen=$(grep -E '^choose ' "$HERE/target/screens/standin.log" | tr '\n' ' ')
[ "$chosen" = "choose 5 choose auto choose 2 " ] || { echo "app screens: the exits chosen were: $chosen"; exit 1; }
chosen=$(grep -E '^choose ' "$HERE/target/screens/standin-older.log" | tr '\n' ' ')
[ "$chosen" = "choose 1 choose auto " ] || { echo "app screens: the exits chosen from the older server were: $chosen"; exit 1; }
grep -qx 'route all' "$HERE/target/screens/standin.log" || { echo "app screens: the route was not chosen"; exit 1; }
# the protection: Pro++, Off (late), Ultimate (refused), then of Pro, Ultimate and Kids pressed at once only Pro
# (already under way) and Kids, one after the other, the network ending on Kids
asked=$(grep -E '^filter [a-z]+$' "$HERE/target/screens/standin.log" | tr '\n' ' ')
[ "$asked" = "filter proplus filter off filter ultimate filter pro filter kids " ] || { echo "app screens: the protection asked: $asked"; exit 1; }
taken=$(grep -E '^filter taken ' "$HERE/target/screens/standin.log" | tr '\n' ' ')
[ "$taken" = "filter taken proplus filter taken off filter taken pro filter taken kids " ] || { echo "app screens: the protection taken: $taken"; exit 1; }
# the app never asks for «as my group» again (the server's "auto"): a level only
! grep -qx 'filter auto' "$HERE"/target/screens/standin*.log || { echo "app screens: the app asked for the group's protection"; exit 1; }
# the owner's changes: YouTube abroad → directly and the list by link off, on the version shown; the shares
# 55/17/28 on the weights shown; an invitation for one device for 7 days into Kids, and its QR code
log="$HERE/target/screens/standin.log"
grep -qx 'rule yt0001 direct 0123456789ab' "$log" && grep -qx 'rule ad0001 off 0123456789ab' "$log" \
    || { echo "app screens: the rules sent: $(grep -E '^rule ' "$log" | tr '\n' ' ')"; exit 1; }
grep -qx 'weights {"1": 55, "2": 17, "4": 28} base {"1": 3, "2": 1, "4": 2}' "$log" \
    || { echo "app screens: the shares sent: $(grep -E '^weights ' "$log")"; exit 1; }
grep -qx 'invite 1 7 kid001' "$log" && grep -qx 'invite qr https://vpn.example.org/i/Zx3kZx3kZx3kZx3kZx3kZx' "$log" \
    || { echo "app screens: the invitation asked: $(grep -E '^invite' "$log" | tr '\n' ' ')"; exit 1; }
# an exit switched off (share 0, its share to the largest); the mode, the order and whom Banks is for; a person
# paused and another removed
grep -qx 'weights {"1": 72, "2": 0, "4": 28} base {"1": 55, "2": 17, "4": 28}' "$log" \
    || { echo "app screens: the exit switched off: $(grep -E '^weights ' "$log" | tr '\n' ' ')"; exit 1; }
grep -qx 'mode geo' "$log" && grep -qx 'order ai0001,yt0001,sc0001,tg0001,bk0001,ad0001' "$log" && grep -qx 'rule groups bk0001 fam001' "$log" \
    || { echo "app screens: the mode, order and groups sent: $(grep -E '^(mode|order|rule groups) ' "$log" | tr '\n' ' ')"; exit 1; }
grep -qx 'people group=fam001' "$log" && grep -qx 'pause mom_phone True' "$log" && grep -qx 'remove dad_tv' "$log" \
    || { echo "app screens: the people asked and changed: $(grep -E '^(people|pause|remove) ' "$log" | tr '\n' ' ')"; exit 1; }
! grep -qE '^(pause|remove) test_laptop' "$log" || { echo "app screens: this device was paused or removed"; exit 1; }
! grep -qE '^(rule|weights|invite|mode|order|pause|remove)' "$HERE/target/screens/standin-older.log" || { echo "app screens: the older server was sent an owner's change"; exit 1; }
echo "app screens: ok"
