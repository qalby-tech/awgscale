#!/usr/bin/env bash
# =============================================================================
# app/qalby/build.sh — the computer's VPN service and command line, built in a
# Go container into app/target/qalby/ (qalby, testserver; static, Linux amd64).
# Formatting, vet and the unit tests come first; then that Windows still compiles.
# =============================================================================
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$HERE/../target/qalby
mkdir -p "$OUT" "$HERE/../target/go"
docker run --rm --network host -u "$(id -u):$(id -g)" -v "$HERE":/src -v "$HERE/../target/go":/go -v "$OUT":/out -w /src \
    -e HOME=/tmp -e GOPATH=/go -e GOCACHE=/go/cache -e GOFLAGS=-modcacherw -e CGO_ENABLED=0 golang:1.25-bookworm sh -ec '
unformatted=$(gofmt -l .); [ -z "$unformatted" ] || { echo "not formatted: $unformatted"; exit 1; }
go vet ./...
go test -count=1 ./... | grep -vE "^(ok|\?) " || true
go test -count=1 ./... >/dev/null
go build -trimpath -o /out/ ./cmd/...
GOOS=windows go build -o /dev/null ./...
echo "qalby: built"'
