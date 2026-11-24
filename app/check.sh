#!/usr/bin/env bash
# The app's shared core (app/core): formatting, lints and tests, and the tests of the interface's own logic
# (app/ui). Nothing here reaches a network but the stand-ins the tests start on 127.0.0.1 (and npm, once,
# when the interface's packages are not installed yet). Uses the host's cargo, or a Rust container; the
# interface's tests run in the app's build image (app/docker), which has the Node they need.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
RUST_IMAGE=rust:1.95-slim
run() {
    if command -v cargo >/dev/null; then (cd "$HERE" && "$@"); return; fi
    docker run --rm -v "$HERE":/app -w /app -e CARGO_HOME=/app/target/cargo-home "$RUST_IMAGE" sh -c 'rustup component add rustfmt clippy >/dev/null 2>&1; "$@"' sh "$@"
}
run cargo fmt --check -p awgscale-client
run cargo clippy -q -p awgscale-client --all-targets -- -D warnings
out=$(run cargo test -q -p awgscale-client 2>&1) || { printf '%s\n' "$out" | grep -E 'FAILED|panicked|error' ; echo "app core: tests FAILED"; exit 1; }
passed=$(printf '%s\n' "$out" | grep -oE '[0-9]+ passed' | awk '{n+=$1} END{print n}')
echo "app core: $passed passed"

docker build -q -t awgscale-app-build:local "$HERE/docker" >/dev/null
out=$(docker run --rm --network host -u "$(id -u):$(id -g)" -v "$HERE":/app -w /app/ui -e HOME=/app/target/home \
    -e npm_config_cache=/app/target/npm-cache awgscale-app-build:local \
    bash -c '{ { [ -d node_modules/vitest ] && [ node_modules/.package-lock.json -nt package-lock.json ]; } || npm ci --no-audit --no-fund >/dev/null; } && npm run -s test 2>&1') \
    || { printf '%s\n' "$out" | grep -E 'FAIL|×|Error' ; echo "app ui: tests FAILED"; exit 1; }
passed=$(printf '%s\n' "$out" | grep -oE 'Tests +[0-9]+ passed' | grep -oE '[0-9]+')
echo "app ui: $passed passed"
