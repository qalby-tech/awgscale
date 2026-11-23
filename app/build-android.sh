#!/usr/bin/env bash
# =============================================================================
# app/build-android.sh — builds the app for Android in a container (this
# machine needs nothing but Docker; app/docker/android pins every tool):
#   1. the tunnel's library, libawgscale.so (app/qalby/cmd/libawgscale), with
#      the NDK, for arm64-v8a (phones) and x86_64 (the emulator, Chromebooks)
#   2. the interface and the app (Rust) for the same two, and the APK (Gradle)
# The APK lands in app/target/android/awgscale-android.apk.
#
# Signing. A release key is handed over in the environment, never kept in the
# repository:
#   AWGSCALE_KEYSTORE_FILE=/path/release.jks  (a file under app/ or anywhere: it is mounted read-only)
#   AWGSCALE_KEYSTORE_PASSWORD=…  AWGSCALE_KEY_ALIAS=…  AWGSCALE_KEY_PASSWORD=…
# Without it a key of this machine is made once in app/target/android/keystore/
# (its password beside it) and used for every build here: phones take an update
# only when it is signed by the same key, so the owner's release key must be
# used from the first APK people install on.
#
#   app/build-android.sh             the APK (release, signed)
#   app/build-android.sh library     only libawgscale.so
#   app/build-android.sh test-panel  an APK for app/tests/android.sh only, never to install anywhere else:
#       it joins a stand-in panel (vpn.example.org at 192.168.237.10, the test's sealed network) whose
#       certificate is of an authority made here for it: app/target/android/test-panel/
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE=awgscale-android-build:local
OUT=$HERE/target/android
docker build -q --target build -t "$IMAGE" "$HERE/docker/android" >/dev/null
mkdir -p "$OUT" "$HERE/target/go" "$HERE/target/home"

keystore=()
if [ -n "${AWGSCALE_KEYSTORE_FILE:-}" ]; then
    [ -n "${AWGSCALE_KEYSTORE_PASSWORD:-}" ] && [ -n "${AWGSCALE_KEY_ALIAS:-}" ] && [ -n "${AWGSCALE_KEY_PASSWORD:-}" ] \
        || { echo "AWGSCALE_KEYSTORE_FILE needs AWGSCALE_KEYSTORE_PASSWORD, AWGSCALE_KEY_ALIAS and AWGSCALE_KEY_PASSWORD" >&2; exit 2; }
    keystore=(-v "$(realpath "$AWGSCALE_KEYSTORE_FILE")":/keystore/release.jks:ro -e AWGSCALE_KEYSTORE_FILE=/keystore/release.jks
        -e AWGSCALE_KEYSTORE_PASSWORD -e AWGSCALE_KEY_ALIAS -e AWGSCALE_KEY_PASSWORD)
fi

run() {
    docker run --rm --network host -u "$(id -u):$(id -g)" -v "$HERE":/app -w /app "${keystore[@]}" \
        -e HOME=/app/target/home -e CARGO_HOME=/app/target/cargo-home -e npm_config_cache=/app/target/npm-cache \
        -e GOPATH=/app/target/go -e GOCACHE=/app/target/go/cache -e GOFLAGS=-modcacherw \
        -e GRADLE_USER_HOME=/app/target/gradle-home -e ANDROID_USER_HOME=/app/target/home/.android \
        -e JAVA_TOOL_OPTIONS=-Duser.home=/app/target/home "$IMAGE" bash -ec "$1"
}

# the tunnel's library: Go with cgo, the NDK's clang of API 26 (the app's minSdk), pages of 16 KB (Android 15+)
LIBRARY='cd qalby
for abi in arm64-v8a:arm64:aarch64 x86_64:amd64:x86_64; do
    IFS=: read -r directory arch triple <<<"$abi"
    CGO_ENABLED=1 GOOS=android GOARCH=$arch CC=$NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/$triple-linux-android26-clang \
        go build -buildmode=c-shared -trimpath -ldflags="-s -w -buildid= -extldflags=-Wl,-z,max-page-size=16384" \
        -o /app/target/android/jni/$directory/libawgscale.so ./cmd/libawgscale
    rm -f /app/target/android/jni/$directory/libawgscale.h
done
echo "libawgscale.so: built"'

# a key of this machine, made once, when none is handed over
KEY='if [ -z "${AWGSCALE_KEYSTORE_FILE:-}" ]; then
    store=/app/target/android/keystore
    if [ ! -s $store/local.jks ]; then
        mkdir -p $store && chmod 700 $store
        head -c 24 /dev/urandom | base64 | tr -d "/+=" > $store/password && chmod 600 $store/password
        keytool -genkeypair -keystore $store/local.jks -storepass "$(cat $store/password)" -alias awgscale \
            -keyalg RSA -keysize 3072 -validity 10000 -dname "CN=awgscale local build" >/dev/null 2>&1
        echo "a signing key of this machine: app/target/android/keystore/local.jks"
    fi
    export AWGSCALE_KEYSTORE_FILE=$store/local.jks AWGSCALE_KEY_ALIAS=awgscale
    AWGSCALE_KEYSTORE_PASSWORD=$(cat $store/password); export AWGSCALE_KEYSTORE_PASSWORD
    export AWGSCALE_KEY_PASSWORD=$AWGSCALE_KEYSTORE_PASSWORD
fi'

APP='set -o pipefail
cd ui && { [ -d node_modules/@tauri-apps/cli ] || npm ci --no-audit --no-fund >/dev/null; } && cd ../shell
../ui/node_modules/.bin/tauri android build --apk --target aarch64 --target x86_64 2>&1 \
    | grep -vE "^\s+(Compiling|Downloaded|Downloading)|^w: |JAVA_TOOL_OPTIONS|deprecated|^\s*$" | sed -E "s/^(.*failed to build APK: [^(]{0,80}).*/\1…/" | tail -15
cp gen/android/app/build/outputs/apk/universal/release/app-universal-release.apk /app/target/android/awgscale-android.apk
echo "APK: app/target/android/awgscale-android.apk"'

# the stand-in panel's authority and certificate, made anew each time, and the APK that trusts it
TEST_PANEL='set -o pipefail
dir=/app/target/android/test-panel; rm -rf $dir; mkdir -p $dir; cd $dir
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=awgscale test authority" -keyout authority.key -out authority.pem 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=vpn.example.org" -keyout panel.key -out panel.csr 2>/dev/null
printf "subjectAltName=DNS:vpn.example.org\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n" > panel.ext
openssl x509 -req -in panel.csr -CA authority.pem -CAkey authority.key -CAcreateserial -days 30 -extfile panel.ext -out panel.pem 2>/dev/null
rm -f authority.key panel.csr authority.srl
chmod 644 panel.key   # a throwaway key of the test: the stand-in reads it as root without the right to read others files
export AWGSCALE_TEST_PANEL_CA=$dir/authority.pem AWGSCALE_TEST_PANEL=192.168.237.10:443
cd /app/shell
../ui/node_modules/.bin/tauri android build --apk --target x86_64 --features test-panel 2>&1 \
    | grep -vE "^\s+(Compiling|Downloaded|Downloading)|^w: |JAVA_TOOL_OPTIONS|deprecated|^\s*$" | sed -E "s/^(.*failed to build APK: [^(]{0,80}).*/\1…/" | tail -5
cp gen/android/app/build/outputs/apk/universal/release/app-universal-release.apk /app/target/android/awgscale-android-test-panel.apk
echo "APK for the tests: app/target/android/awgscale-android-test-panel.apk"'

case "${1:-}" in
    library) run "$LIBRARY" ;;
    test-panel) run "$LIBRARY"; run "$KEY
$TEST_PANEL" ;;
    "") run "$LIBRARY"; run "$KEY
$APP" ;;
    *) echo "usage: $0 [library|test-panel]" >&2; exit 2 ;;
esac
