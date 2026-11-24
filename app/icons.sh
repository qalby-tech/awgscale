#!/usr/bin/env bash
# =============================================================================
# app/icons.sh — the app's icons, every platform's, made anew from its logo (in a
# container, as the builds are): `tauri icon` with icon.json —
#   icon.svg             the logo: the desktop's icons (shell/icons: .ico, .icns,
#                        the .deb's PNGs) and the legacy Android launcher icons
#   icon-foreground.svg  the dots alone, inside the safe zone (the middle 72 of 108
#                        units): the foreground of Android's adaptive icon, and its
#                        monochrome (themed) layer; the background is the logo's orange
#   bg_color             the orange under the iOS icons, which may not be transparent
# Android's go straight into the Android project (shell/gen/android/.../res), iOS's
# into shell/icons/ios (ios/ci.sh copies them into the Xcode project). The iOS icons
# lose their alpha channel afterwards: App Store Connect refuses an icon that has one.
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE=awgscale-app-build:local
docker build -q -t "$IMAGE" "$HERE/docker" >/dev/null
docker run --rm --network host -u "$(id -u):$(id -g)" -v "$HERE":/app -w /app -e HOME=/app/target/home \
    -e npm_config_cache=/app/target/npm-cache "$IMAGE" bash -ec '
{ [ -d ui/node_modules/@tauri-apps/cli ] || (cd ui && npm ci --no-audit --no-fund >/dev/null); }
cd shell
out=$(../ui/node_modules/.bin/tauri icon ../icon.json 2>&1) || { printf "%s\n" "$out"; exit 1; }
for icon in icons/ios/AppIcon-*.png; do
    convert "$icon" -background "#FF8A3D" -alpha remove -alpha off "PNG24:$icon"
done
echo "icons: made"'
