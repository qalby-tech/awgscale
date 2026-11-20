#!/usr/bin/env bash
# =============================================================================
# app/build.sh — builds the app for Linux in a container (this machine needs
# nothing but Docker). The packages land in app/target/release/bundle/.
#
#   app/build.sh          the interface, the service (qalby), the app and a .deb that holds both
#   app/build.sh check    only: the interface's lint and types, clippy on the app
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE=awgscale-app-build:local
docker build -q -t "$IMAGE" "$HERE/docker" >/dev/null
run() {
    docker run --rm --network host -u "$(id -u):$(id -g)" -v "$HERE":/app -w /app \
        -e HOME=/app/target/home -e CARGO_HOME=/app/target/cargo-home -e npm_config_cache=/app/target/npm-cache \
        -e APPIMAGE_EXTRACT_AND_RUN=1 "$IMAGE" bash -c "$1"
}
UI='cd ui && { [ -d node_modules/vite ] || npm ci --no-audit --no-fund >/dev/null; } && npm run -s lint && npm run -s format:check >/dev/null && npm run -s build >/dev/null'
case "${1:-}" in
    check) run "$UI && cd .. && cargo clippy -q -p awgscale-app -- -D warnings && echo 'app: checked'" ;;
    "") "$HERE/qalby/build.sh" >/dev/null
        run "$UI && cd ../shell && ../ui/node_modules/.bin/tauri build --bundles deb 2>&1 | grep -vE '^\s+(Compiling|Downloaded|Downloading)' | tail -15" ;;
    *) echo "usage: $0 [check]" >&2; exit 2 ;;
esac
