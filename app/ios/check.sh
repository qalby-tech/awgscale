#!/usr/bin/env bash
# =============================================================================
# app/ios/check.sh — what of the iOS app can be checked on Linux, in containers:
#   * the Rust of the app and of its tunnel plugin (app/tunnel-ios) type-checked and
#     linted for aarch64-apple-ios (a phone) and aarch64-apple-ios-sim (a simulator),
#     with and without the pictures' features. There is no Apple SDK here: C code of
#     dependencies is NOT compiled (check/stub-cc makes empty objects) and nothing is
#     linked; that, the Swift package and Xcode are for .github/workflows/app-ios.yml.
#   * the Swift (the plugin, the Network Extension): parsed only, by the Swift of
#     Linux (no UIKit, NetworkExtension or WireGuardKit to type-check against), and
#     searched for an error's text or value that could reach a log, a file or the window.
#   * the words of the tunnel's refusals and the kill switch's check (app/tunnel-ios,
#     refusal.rs): tested.
#   * the Xcode project: ios/project.yml rendered roughly as `tauri ios init` does
#     (check/render.js) and given to XcodeGen (2.46.0, built for Linux here, as CI
#     uses), with the extension and without it, and signed; what it makes is
#     looked at, not built.
#   * the shell scripts by shellcheck.
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=awgscale-ios-check:local
# Xcode 16.4 has Swift 6.1.2; the nearest Linux image
SWIFT_IMAGE=swift:6.1.3-bookworm
SHELLCHECK_IMAGE=koalaman/shellcheck:v0.10.0
XCODEGEN_VERSION=2.46.0
XCODEGEN_COMMIT=8445e778451c7e44237b90281bde622d764b0084
HANDLEBARS_VERSION=4.7.8
docker build -q -t "$IMAGE" "$HERE/ios/check" >/dev/null
run() {
    docker run --rm --network host -u "$(id -u):$(id -g)" -v "$HERE":/app -w /app \
        -e HOME=/app/target/home -e CARGO_HOME=/app/target/cargo-home -e CARGO_TARGET_DIR=/app/target/ios-check \
        -e CC_aarch64_apple_ios=stub-cc -e CC_aarch64_apple_ios_sim=stub-cc "$IMAGE" bash -c "$1"
}
# shellcheck disable=SC2016  # expanded in the container
run 'cargo fmt --check -p awgscale-app -p awgscale-tunnel-ios
for build in "--target aarch64-apple-ios" "--target aarch64-apple-ios-sim --features awgscale-app/pictures" \
             "--target aarch64-apple-ios-sim --features awgscale-app/pretend-tunnel"; do
    cargo clippy -q -p awgscale-app -p awgscale-tunnel-ios $build -- -D warnings
    echo "ios rust: $build: checked"
done
out=$(cargo test -q -p awgscale-tunnel-ios 2>&1) || { printf "%s\n" "$out"; exit 1; }
echo "ios rust: refusals: $(printf "%s\n" "$out" | grep -oE "[0-9]+ passed" | head -1)"'
# The Swift is not type-checked here, so what must never be written is looked for: no error's text or
# value goes to a log, a file or a rejection (the codes of Refusal and ParseError do).
leaks=$(grep -nE 'localizedDescription|\\\(error|\\\(reason|ParseError\.[A-Za-z]+\(|invoke\.reject\(' \
    "$HERE"/tunnel-ios/ios/Sources/*.swift "$HERE"/ios/tunnel/*.swift | grep -vE 'manager\.localizedDescription = |invoke\.reject\(rejection\.detail, code: rejection\.refusal\.rawValue\)' || true)
[ -z "$leaks" ] || { printf 'ios swift: an error text or value may leave:\n%s\n' "$leaks"; exit 1; }
echo "ios swift: no error text or value leaves"
docker run --rm -v "$HERE":/app:ro -w /app "$SWIFT_IMAGE" bash -c '
for file in tunnel-ios/ios/Sources/*.swift ios/tunnel/*.swift; do
    swiftc -parse "$file" && echo "ios swift: $file: parsed"
done'
# XcodeGen for Linux, once (target/ios-check/xcodegen), and the renderer's Handlebars
work=$HERE/target/ios-check
mkdir -p "$work"
if [ ! -x "$work/xcodegen/.build/release/xcodegen" ]; then
    rm -rf "$work/xcodegen"
    git clone -q --depth 1 --branch "$XCODEGEN_VERSION" https://github.com/yonaskolb/XcodeGen.git "$work/xcodegen"
    [ "$(git -C "$work/xcodegen" rev-parse HEAD)" = "$XCODEGEN_COMMIT" ] || { echo "XcodeGen $XCODEGEN_VERSION is not $XCODEGEN_COMMIT"; exit 1; }
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$work/xcodegen":/x -w /x "$SWIFT_IMAGE" \
        swift build -q -c release --product xcodegen
fi
docker build -q -t awgscale-app-build:local "$HERE/docker" >/dev/null
docker run --rm -u "$(id -u):$(id -g)" -v "$HERE":/app -w /app/target/ios-check -e HOME=/tmp -e npm_config_cache=/tmp/npm \
    awgscale-app-build:local sh -c "[ -d render/node_modules/handlebars ] || npm install -s --prefix render --no-audit --no-fund handlebars@$HANDLEBARS_VERSION >/dev/null
        NODE_PATH=render/node_modules node /app/ios/check/render.js /app/ios/project.yml project.yml"
for variant in "NO NO" "YES NO" "YES YES"; do
    read -r tunnel signed <<< "$variant"
    tree=$work/tree
    rm -rf "$tree"
    mkdir -p "$tree/app/shell/gen/apple" "$tree/app/target/ios/amneziawg-apple"
    cp -r "$HERE/ios" "$tree/app/ios"
    cp "$work/project.yml" "$tree/app/shell/gen/apple/"
    (cd "$tree/app/shell/gen/apple" && mkdir -p Sources Assets.xcassets Externals awgscale-app_iOS assets && touch LaunchScreen.storyboard)
    # the package's place only (XcodeGen does not read it)
    printf '// swift-tools-version:5.5\n' > "$tree/app/target/ios/amneziawg-apple/Package.swift"
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e USER=check -v "$work":/work -w /work/tree/app/shell/gen/apple \
        -e AWGSCALE_IOS_TUNNEL="$tunnel" -e AWGSCALE_IOS_SIGNED="$signed" -e AWGSCALE_IOS_VERSION=0.0.0 \
        -e APPLE_DEVELOPMENT_TEAM=TEAMID0000 -e AWGSCALE_TUNNEL_PROFILE_UUID=00000000-0000-0000-0000-000000000000 \
        "$SWIFT_IMAGE" /work/xcodegen/.build/release/xcodegen generate --quiet --spec project.yml 2>&1 | { grep -v 'settings found' || true; }
    pbxproj=$tree/app/shell/gen/apple/awgscale-app.xcodeproj/project.pbxproj
    extension=$(grep -c 'com.apple.product-type.app-extension' "$pbxproj" || true)
    embedded=$(grep -c 'AwgscaleTunnel.appex in Embed Foundation Extensions' "$pbxproj" || true)
    manual=$(grep -c 'CODE_SIGN_STYLE = Manual' "$pbxproj" || true)
    grep -q 'com.apple.developer.networking.networkextension' "$tree/app/shell/gen/apple/awgscale-app_iOS/awgscale-app_iOS.entitlements"
    # Xcode builds the Rust with the CLI of app/ui/node_modules, whatever started `tauri ios init`
    grep -q 'ui/node_modules/.bin/tauri\\" ios xcode-script' "$pbxproj" || { echo "ios project: the Rust is not built by app/ui's CLI"; exit 1; }
    if [ "$tunnel" = YES ]; then
        if [ "$extension" != 1 ] || [ "$embedded" -lt 1 ]; then echo "ios project: the extension is missing"; exit 1; fi
        grep -q 'tech.qalby.awgscale.tunnel' "$pbxproj"
        grep -q 'XCLocalSwiftPackageReference "../../../target/ios/amneziawg-apple"' "$pbxproj"
        grep -q 'com.apple.networkextension.packet-tunnel' "$tree/app/shell/gen/apple/AwgscaleTunnel/Info.plist"
    else
        [ "$extension" = 0 ] || { echo "ios project: the extension is there without AWGSCALE_IOS_TUNNEL"; exit 1; }
    fi
    if [ "$signed" = YES ]; then [ "$manual" -ge 1 ]; else [ "$manual" = 0 ]; fi
    echo "ios project: tunnel=$tunnel signed=$signed: generated, extension targets $extension"
done
docker run --rm -v "$HERE":/app:ro "$SHELLCHECK_IMAGE" /app/ios/ci.sh /app/ios/check.sh /app/ios/check/xcrun /app/ios/check/stub-cc
echo "ios: checked (Swift parsed only; nothing linked: the rest is the workflow's)"
