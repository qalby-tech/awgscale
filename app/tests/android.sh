#!/usr/bin/env bash
# =============================================================================
# app/tests/android.sh — the app on Android, in an emulator (headless, KVM) in
# a container, sealed off: a Docker network with no way out holds
#   server:    app/target/qalby/testserver with the entry's obfuscation and
#              fresh keys, the app API stand-in on its tunnel address 172.16.17.1
#   emulator:  Android 15 (x86_64), the APK of app/build-android.sh installed and
#              driven by tests/android.py through adb and uiautomator; the stand-in
#              on its container's 127.0.0.1 (10.0.2.2 for the emulator) for the
#              pictures of the screens
#   the panel: a stand-in of the invitation over HTTPS (tests/panel_standin.py)
#              at vpn.example.org = 192.168.237.10, which only the build of
#              `app/build-android.sh test-panel` trusts and finds; the join runs on it
#   the server's name: awg-server.test, which the network's DNS gives the
#              emulator; the always-on test finds the server by it
# The pictures land in app/target/android-screens/. Needs /dev/kvm: without it
# the test says so and is skipped, unless AWGSCALE_REQUIRE_EMULATOR=1 (CI):
# then that is a failure. Run app/build-android.sh and
# app/build-android.sh test-panel first. About 7 minutes (3 of them waiting for
# the handshake to grow old).
# =============================================================================
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
APK=$HERE/target/android/awgscale-android.apk
PICTURES=$HERE/target/android-screens
RUN=awgandroid-$$
SUBNET=192.168.237.0/24
SERVER_IP=192.168.237.10   # the stand-in panel's address the test build knows (build-android.sh)
TEST_PANEL_APK=$HERE/target/android/awgscale-android-test-panel.apk

if ! [ -c /dev/kvm ] || ! [ -r /dev/kvm ] || ! [ -w /dev/kvm ]; then
    if [ "${AWGSCALE_REQUIRE_EMULATOR:-}" = 1 ]; then
        echo "android: FAILED — /dev/kvm is not usable here, and the emulator test is required"
        exit 1
    fi
    echo "android: skipped — /dev/kvm is not usable here (the emulator needs KVM)"
    exit 0
fi
[ -s "$APK" ] || { echo "android: no APK built (app/build-android.sh)"; exit 1; }
[ -s "$TEST_PANEL_APK" ] || { echo "android: no APK for the join built (app/build-android.sh test-panel)"; exit 1; }
docker build -q --target emulator -t awgscale-android-emulator:local "$HERE/docker/android" >/dev/null || { echo "android: the emulator's image does not build"; exit 1; }
docker build -q -t awgscale-app-build:local "$HERE/docker" >/dev/null
"$HERE/qalby/build.sh" >/dev/null || { echo "android: qalby does not build"; exit 1; }
rm -rf "$PICTURES"; mkdir -p "$PICTURES"

cleanup() {
    docker rm -f "$RUN-server" "$RUN-emulator" >/dev/null 2>&1
    docker network rm "$RUN" >/dev/null 2>&1; docker volume rm "$RUN" >/dev/null 2>&1
}
[ -n "${KEEP:-}" ] || trap cleanup EXIT
docker network create --internal --subnet "$SUBNET" "$RUN" >/dev/null
docker volume create "$RUN" >/dev/null

docker run -d --name "$RUN-server" --network "$RUN" --ip "$SERVER_IP" --network-alias awg-server.test --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW \
    --cap-add NET_BIND_SERVICE --device /dev/net/tun -v "$HERE/target/qalby":/qalby:ro -v "$HERE/tests":/tests:ro \
    -v "$HERE/target/android/test-panel":/panel:ro -v "$RUN":/shared awgscale-app-build:local sleep infinity >/dev/null
server_ip=$SERVER_IP
docker exec "$RUN-server" /qalby/testserver make-confs /shared "$server_ip:51820"
docker exec "$RUN-server" sh -c '/qalby/testserver serve /shared > /shared/server.log 2>&1 &'
docker exec "$RUN-server" sh -c 'for i in $(seq 1 50); do ip addr show awgsrv0 2>/dev/null | grep -q 172.16.17.1 && break; sleep 0.1; done
    python3 /tests/standin.py 172.16.17.1 > /shared/standin.log 2>&1 &'
# the emulator's container reads the client's config; the volume is root's
docker exec "$RUN-server" chmod 644 /shared/client.conf
docker exec "$RUN-server" sh -c "python3 /tests/panel_standin.py $SERVER_IP /panel/panel.pem /panel/panel.key /shared/client.conf > /shared/panel.log 2>&1 &"

docker run -d --name "$RUN-emulator" --network "$RUN" --device /dev/kvm --group-add "$(stat -c %g /dev/kvm)" \
    -u "$(id -u):$(id -g)" -e HOME=/tmp/home -v "$APK":/apk/awgscale.apk:ro -v "$TEST_PANEL_APK":/apk/test-panel.apk:ro -v "$HERE/tests":/tests:ro \
    -v "$PICTURES":/pictures -v "$RUN":/shared:ro awgscale-android-emulator:local sleep infinity >/dev/null
docker exec "$RUN-emulator" sh -c 'python3 /tests/standin.py > /pictures/standin.log 2>&1 &'

fails=0
docker exec "$RUN-emulator" python3 /tests/android.py /apk/awgscale.apk /apk/test-panel.apk /pictures /shared/client.conf || fails=$((fails + 1))
check() { if eval "$2" >/dev/null 2>&1; then echo "ok $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
check "the pictures' stand-in was asked for the exits" "grep -q 'get /app/api/exits' '$PICTURES/standin.log'"
check "the app asked the network through the tunnel, as the device" "docker exec $RUN-server grep -q 'get /app/api/me 172.16.17.15' /shared/standin.log"
check "the device joined the stand-in panel by its name" "docker exec $RUN-server grep -qx 'redeem test_join' /shared/panel.log"

echo "pictures: $PICTURES/"
[ $fails -eq 0 ] && echo "app android: ok" || { echo "app android: FAILED"; docker exec "$RUN-server" sh -c 'tail -n 5 /shared/*.log'; exit 1; }
