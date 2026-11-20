#!/usr/bin/env bash
# Pushes an image built in GitHub Actions to ghcr.io/<owner>/<name> under the
# tags of this run (.github/workflows/images.yml):
#   sha-<commit>   always
#   latest         a push to main
#   <version>      a tag v1.2.3 → 1.2.3
#   docker/publish.sh awgscale-agent:ci awgscale-agent
set -euo pipefail
local_image=$1
name=$2
owner=$(tr '[:upper:]' '[:lower:]' <<<"${GITHUB_REPOSITORY_OWNER:?run in GitHub Actions}")
repository="ghcr.io/$owner/$name"
tags="sha-${GITHUB_SHA:?}"
case "${GITHUB_REF:-}" in
    refs/heads/main) tags="$tags latest" ;;
    refs/tags/v*)    tags="$tags ${GITHUB_REF#refs/tags/v}" ;;
esac
for tag in $tags; do
    docker tag "$local_image" "$repository:$tag"
    docker push -q "$repository:$tag"
done
