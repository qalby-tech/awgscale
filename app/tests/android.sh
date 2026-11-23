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
# The pictures land in app/target/android-screens/. Needs /dev/kvm: without it
# the test says so and is skipped. Run app/build-android.sh first.
# =============================================================================
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
APK=$HERE/target/android/awgscale-android.apk
PICTURES=$HERE/target/android-screens
RUN=awgandroid-$$
SUBNET=192.168.237.0/24

if ! [ -c /dev/kvm ] || ! [ -r /dev/kvm ] || ! [ -w /dev/kvm ]; then
    echo "android: skipped — /dev/kvm is not usable here (the emulator needs KVM)"
    exit 0
fi
[ -s "$APK" ] || { echo "android: no APK built (app/build-android.sh)"; exit 1; }
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

docker run -d --name "$RUN-server" --network "$RUN" --cap-drop ALL --cap-add NET_ADMIN --cap-add NET_RAW --device /dev/net/tun \
    -v "$HERE/target/qalby":/qalby:ro -v "$HERE/tests":/tests:ro -v "$RUN":/shared awgscale-app-build:local sleep infinity >/dev/null
server_ip=$(docker exec "$RUN-server" hostname -i | awk '{print $1}')
docker exec "$RUN-server" /qalby/testserver make-confs /shared "$server_ip:51820"
docker exec "$RUN-server" sh -c '/qalby/testserver serve /shared > /shared/server.log 2>&1 &'
docker exec "$RUN-server" sh -c 'for i in $(seq 1 50); do ip addr show awgsrv0 2>/dev/null | grep -q 172.16.17.1 && break; sleep 0.1; done
    python3 /tests/standin.py 172.16.17.1 > /shared/standin.log 2>&1 &'
# the emulator's container reads the client's config; the volume is root's
docker exec "$RUN-server" chmod 644 /shared/client.conf

docker run -d --name "$RUN-emulator" --network "$RUN" --device /dev/kvm --group-add "$(stat -c %g /dev/kvm)" \
    -u "$(id -u):$(id -g)" -e HOME=/tmp/home -v "$APK":/apk/awgscale.apk:ro -v "$HERE/tests":/tests:ro \
    -v "$PICTURES":/pictures -v "$RUN":/shared:ro awgscale-android-emulator:local sleep infinity >/dev/null
docker exec "$RUN-emulator" sh -c 'python3 /tests/standin.py > /pictures/standin.log 2>&1 &'

fails=0
docker exec "$RUN-emulator" python3 /tests/android.py /apk/awgscale.apk /pictures /shared/client.conf || fails=$((fails + 1))
check() { if eval "$2" >/dev/null 2>&1; then echo "ok $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
check "the pictures' stand-in was asked for the exits" "grep -q 'get /app/api/exits' '$PICTURES/standin.log'"
check "the app asked the network through the tunnel, as the device" "docker exec $RUN-server grep -q 'get /app/api/me 172.16.17.15' /shared/standin.log"

echo "pictures: $PICTURES/"
[ $fails -eq 0 ] && echo "app android: ok" || { echo "app android: FAILED"; docker exec "$RUN-server" sh -c 'tail -n 5 /shared/*.log'; exit 1; }
