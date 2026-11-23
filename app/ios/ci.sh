#!/usr/bin/env bash
# =============================================================================
# app/ios/ci.sh — the steps of the iOS build (.github/workflows/app-ios.yml) on a
# macOS runner with Xcode; nothing here runs on Linux. Everything downloaded is
# pinned by version and checksum (or commit) below.
#
#   ci.sh tools                  XcodeGen and Go into app/target/ios/tools (PATH: ci.sh path)
#   ci.sh path                   prints the tools' directories, for $GITHUB_PATH
#   ci.sh amneziawg              amneziawg-apple at its pinned commit, app/target/ios/amneziawg-apple
#   ci.sh wg-go <platform>       its Go bridge (amneziawg-go) for iphoneos or iphonesimulator:
#                                app/target/ios/wg-go/<platform>/libwg-go.a
#   ci.sh init <YES|NO>          `tauri ios init`: app/shell/gen/apple from app/ios/project.yml,
#                                the tunnel's extension in it or not (AWGSCALE_IOS_TUNNEL)
#   ci.sh screens <app> <steps>  the app on a simulator against app/tests/standin.py, pictures in
#                                app/target/ios/screens; steps: pretend (start, home, location,
#                                connected) or tunnel (home, and Connect refused without signing)
#   ci.sh check-bundle <path>    a built .app or .ipa holds the extension, and it says what it is
# =============================================================================
set -euo pipefail
APP=$(cd "$(dirname "$0")/.." && pwd)
WORK=$APP/target/ios
TOOLS=$WORK/tools

XCODEGEN_VERSION=2.46.0
XCODEGEN_SHA256=4d9e34b62172d645eed6457cac13fc222569974098ef4ee9c3368bedf0196806
# amneziawg-apple's Go patches (goruntime-*.diff) apply to this Go (checked against its source)
GO_VERSION=1.27.4
GO_SHA256=91a44a2b8a716600ad94a4f143e156116620db8828086a6e3602291be9bd0bb4
AMNEZIAWG_APPLE=https://github.com/amnezia-vpn/amneziawg-apple.git
AMNEZIAWG_APPLE_COMMIT=3bca528aca844bbe6b665d8ab5c6724cd261fd54   # tag v3.1.5
IOS_DEPLOYMENT_TARGET=15.0
BUNDLE_ID=tech.qalby.awgscale
# a simulator of the runner image (macos-15, Xcode 16.4); any available iPhone when it is gone
SIMULATOR_DEVICE="iPhone 16"
SIMULATOR_RUNTIME=com.apple.CoreSimulator.SimRuntime.iOS-18-5

tools() {
    mkdir -p "$TOOLS"
    if [ ! -x "$TOOLS/xcodegen/bin/xcodegen" ]; then
        curl -sfL -o "$TOOLS/xcodegen.zip" "https://github.com/yonaskolb/XcodeGen/releases/download/$XCODEGEN_VERSION/xcodegen.zip"
        echo "$XCODEGEN_SHA256  $TOOLS/xcodegen.zip" | shasum -a 256 -c - || { shasum -a 256 "$TOOLS/xcodegen.zip"; exit 1; }
        (cd "$TOOLS" && unzip -q -o xcodegen.zip)
    fi
    if [ ! -x "$TOOLS/go/bin/go" ]; then
        curl -sfL -o "$TOOLS/go.tar.gz" "https://go.dev/dl/go$GO_VERSION.darwin-arm64.tar.gz"
        echo "$GO_SHA256  $TOOLS/go.tar.gz" | shasum -a 256 -c - || { shasum -a 256 "$TOOLS/go.tar.gz"; exit 1; }
        tar -xzf "$TOOLS/go.tar.gz" -C "$TOOLS"
    fi
    "$TOOLS/xcodegen/bin/xcodegen" --version
    "$TOOLS/go/bin/go" version
}

path() {
    echo "$TOOLS/xcodegen/bin"
    echo "$TOOLS/go/bin"
}

amneziawg() {
    local checkout=$WORK/amneziawg-apple
    if [ ! -d "$checkout/.git" ]; then
        git init -q "$checkout"
        git -C "$checkout" remote add origin "$AMNEZIAWG_APPLE"
    fi
    git -C "$checkout" fetch -q --depth 1 origin "$AMNEZIAWG_APPLE_COMMIT"
    git -C "$checkout" checkout -q --force FETCH_HEAD
    [ "$(git -C "$checkout" rev-parse HEAD)" = "$AMNEZIAWG_APPLE_COMMIT" ]
}

wg_go() {
    local platform=$1
    local flag
    case "$platform" in
        iphoneos) flag=miphoneos-version-min ;;
        iphonesimulator) flag=mios-simulator-version-min ;;
        *) echo "wg-go: iphoneos or iphonesimulator" >&2; exit 2 ;;
    esac
    local source=$WORK/amneziawg-apple/Sources/WireGuardKitGo
    local out=$WORK/wg-go/$platform
    mkdir -p "$out"
    # The Makefile is the one Xcode would run as an external build target; its GOOS table has no
    # iphonesimulator (the simulator is iOS to Go: the C flags make it a simulator's library).
    make -C "$source" \
        PLATFORM_NAME="$platform" \
        SDKROOT="$(xcrun --sdk "$platform" --show-sdk-path)" \
        ARCHS=arm64 \
        GOOS_iphonesimulator=ios \
        DEPLOYMENT_TARGET_CLANG_FLAG_NAME="$flag" \
        DEPLOYMENT_TARGET_CLANG_ENV_NAME=IPHONEOS_DEPLOYMENT_TARGET \
        IPHONEOS_DEPLOYMENT_TARGET="$IOS_DEPLOYMENT_TARGET" \
        CONFIGURATION_BUILD_DIR="$out" \
        CONFIGURATION_TEMP_DIR="$WORK/wg-go-build/$platform" \
        build
    # where the package's own -L points (relative to the package), besides the target's search path
    mkdir -p "$source/out"
    cp "$out/libwg-go.a" "$source/out/libwg-go.a"
    lipo -info "$out/libwg-go.a"
}

init() {
    local tunnel=$1
    local version
    version=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$APP/shell/tauri.conf.json")
    rm -rf "$APP/shell/gen/apple"
    # read by XcodeGen while it makes the project (app/ios/project.yml, tunnel.yml, signing.yml)
    export AWGSCALE_IOS_TUNNEL=$tunnel
    export AWGSCALE_IOS_VERSION=$version
    (cd "$APP/shell" && ../ui/node_modules/.bin/tauri ios init --ci)
    # the project is named after the Cargo package (awgscale-app.xcodeproj, scheme awgscale-app_iOS)
    local project
    project=$(find "$APP/shell/gen/apple" -maxdepth 1 -name '*.xcodeproj' | head -1)
    if [ "$tunnel" = YES ]; then
        grep -q 'AwgscaleTunnel' "$project/project.pbxproj" || { echo "ios init: the extension is not in the project"; exit 1; }
    else
        if grep -q 'AwgscaleTunnel' "$project/project.pbxproj"; then echo "ios init: the extension is in the project"; exit 1; fi
    fi
    xcodebuild -list -project "$project"
}

# The simulator: a fresh iPhone of the pinned runtime, or any available one
simulator() {
    local created
    if created=$(xcrun simctl create awgscale "$SIMULATOR_DEVICE" "$SIMULATOR_RUNTIME" 2>/dev/null); then
        echo "$created"
        return
    fi
    xcrun simctl list devices available -j | python3 -c '
import json, sys
devices = json.load(sys.stdin)["devices"]
phones = [device["udid"] for runtime, found in sorted(devices.items(), reverse=True) if "iOS" in runtime
          for device in found if device["name"].startswith("iPhone")]
print(phones[0])'
}

# Waits until the stand-in has been asked for `wanted` more often than `before` times
asked() {
    local wanted=$1
    local before=$2
    local log=$3
    for _ in $(seq 1 60); do
        if [ "$(grep -c "^get $wanted " "$log" || true)" -gt "$before" ]; then
            return 0
        fi
        sleep 1
    done
    echo "the app never asked the stand-in for $wanted" >&2
    return 1
}

count() {
    grep -c "^get $1 " "$2" || true
}

# Starts the app (AWGSCALE_PRESS: the buttons it presses, src/pictures.rs) and checks it still runs
launch() {
    local device=$1
    local press=$2
    local launched
    launched=$(SIMCTL_CHILD_AWGSCALE_PRESS="$press" xcrun simctl launch "$device" "$BUNDLE_ID")
    echo "$launched"
    LAUNCHED_PID=${launched##*: }
}

alive() {
    kill -0 "$LAUNCHED_PID" 2>/dev/null || { echo "the app stopped (pid $LAUNCHED_PID)" >&2; return 1; }
}

shot() {
    xcrun simctl io "$1" screenshot "$WORK/screens/$2.png"
    echo "picture: $2"
}

screens() {
    local app=$1
    local steps=$2
    local pictures=$WORK/screens
    local log=$pictures/standin.log
    rm -rf "$pictures"
    mkdir -p "$pictures"
    local device
    device=$(simulator)
    echo "simulator: $device"
    xcrun simctl boot "$device"
    xcrun simctl bootstatus "$device" -b >/dev/null
    # the network's app API, made up (port 53 may be refused here: its DNS is not needed)
    python3 "$APP/tests/standin.py" > "$log" 2>&1 &
    local standin=$!
    trap 'kill $standin 2>/dev/null || true; xcrun simctl shutdown '"$device"' >/dev/null 2>&1 || true' EXIT
    xcrun simctl install "$device" "$app"

    # 1: the start screen (no network joined)
    if [ "$steps" = pretend ]; then
        launch "$device" ""
        sleep 12
        alive
        shot "$device" 1-start
        xcrun simctl terminate "$device" "$BUNDLE_ID"
    fi

    # a joined network, made up, its app API at the stand-in (as app/tests/screens.sh)
    local data
    data=$(xcrun simctl get_app_container "$device" "$BUNDLE_ID" data)
    mkdir -p "$data/Library/Application Support/$BUNDLE_ID"
    printf '%s' '{"host":"vpn.example.org","name":"test_phone","address":"172.16.17.23","entry":"127.0.0.1","owner":true,"conf":"[Interface]\nPrivateKey = made-up\n"}' \
        > "$data/Library/Application Support/$BUNDLE_ID/network.json"

    # 2: home, the device asks the network who it is
    local before
    before=$(count /app/api/me "$log")
    launch "$device" ""
    asked /app/api/me "$before" "$log"
    sleep 4
    alive
    shot "$device" 2-home
    xcrun simctl terminate "$device" "$BUNDLE_ID"

    if [ "$steps" = pretend ]; then
        # 3: the locations, from the stand-in's exits
        before=$(count /app/api/exits "$log")
        launch "$device" "Location"
        asked /app/api/exits "$before" "$log"
        sleep 6
        alive
        shot "$device" 3-location
        xcrun simctl terminate "$device" "$BUNDLE_ID"
        # 4: connected (the tunnel only pretends in this build)
        launch "$device" "Connect"
        sleep 10
        alive
        shot "$device" 4-connected
        xcrun simctl terminate "$device" "$BUNDLE_ID"
    else
        # 4: Connect through the plugin and NETunnelProviderManager; an unsigned build has no Network
        # Extension entitlement, so the system refuses and the window says why
        launch "$device" "Connect"
        sleep 15
        alive
        shot "$device" 4-connect-unsigned
        xcrun simctl terminate "$device" "$BUNDLE_ID"
    fi
    xcrun simctl spawn "$device" log show --last 5m --style compact \
        --predicate 'process CONTAINS "awgscale" OR subsystem CONTAINS "awgscale"' > "$pictures/device.log" 2>&1 || true
    grep -E '^get ' "$log" | sort | uniq -c
}

check_bundle() {
    local bundle=$1
    local app=$bundle
    if [ "${bundle##*.}" = ipa ]; then
        local unpacked=$WORK/ipa
        rm -rf "$unpacked"
        mkdir -p "$unpacked"
        unzip -q "$bundle" -d "$unpacked"
        app=$(find "$unpacked/Payload" -maxdepth 1 -name '*.app' | head -1)
    fi
    local extension=$app/PlugIns/AwgscaleTunnel.appex
    [ -d "$extension" ] || { echo "no extension in $app"; ls "$app"; exit 1; }
    plutil -extract NSExtension.NSExtensionPointIdentifier raw "$extension/Info.plist"
    plutil -extract CFBundleIdentifier raw "$extension/Info.plist"
    plutil -extract CFBundleIdentifier raw "$app/Info.plist"
    lipo -info "$extension/AwgscaleTunnel"
    # the Go bridge is inside (its build information names amneziawg-go)
    grep -aq 'amneziawg-go' "$extension/AwgscaleTunnel" || { echo "the extension has no amneziawg-go in it"; exit 1; }
    echo "bundle: ok"
}

case "${1:-}" in
    tools) tools ;;
    path) path ;;
    amneziawg) amneziawg ;;
    wg-go) wg_go "${2:?platform}" ;;
    init) init "${2:?YES or NO}" ;;
    screens) screens "${2:?app}" "${3:?pretend or tunnel}" ;;
    check-bundle) check_bundle "${2:?app or ipa}" ;;
    *) sed -n '2,20p' "$0" >&2; exit 2 ;;
esac
