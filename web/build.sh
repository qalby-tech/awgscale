#!/usr/bin/env bash
# Builds the panel page into web/out inside a Node container (the host needs
# only Docker).   web/build.sh           install what is missing, then build
#                 web/build.sh check     types only
#                 web/build.sh lint      the style rules (ESLint, Prettier)
#                 web/build.sh format    rewrite the files to the formatter's shape
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE=${NODE_IMAGE:-node:20-alpine}
run() {
    docker run --rm --user "$(id -u):$(id -g)" -v "$HERE":/app -w /app \
        -e HOME=/tmp -e NEXT_TELEMETRY_DISABLED=1 -e npm_config_update_notifier=false "$IMAGE" "$@"
}
[ -d "$HERE/node_modules/next" ] || run npm ci --no-audit --no-fund
case "${1:-}" in
    check) run npx tsc --noEmit ;;
    lint) run npm run lint && run npm run format:check ;;
    format) run npx eslint src --fix --quiet; run npm run format ;;
    *) run npm run build ;;
esac
