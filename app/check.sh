#!/usr/bin/env bash
# The app's shared core (app/core): formatting, lints and tests. Nothing here reaches a network
# but the stand-ins the tests start on 127.0.0.1. Uses the host's cargo, or a Rust container.
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
