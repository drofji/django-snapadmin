#!/usr/bin/env bash
#
# scripts/docker_build.sh
#
# Build the SnapAdmin test/demo image with the day-tag retention policy from
# roadmap task #P:
#
#   1. Build the image.
#   2. Tag it <image>:YYYY-MM-DD (today's build-day) and <image>:latest.
#      Rebuilding the same day re-points the day-tag at the new image, so only
#      the latest same-day build keeps the tag.
#   3. Prune so only the last N build-days survive (N = SNAPADMIN_IMAGE_KEEP_DAYS,
#      default 3), then reclaim dangling layers.
#
# Usage:
#   scripts/docker_build.sh                       # image=snapadmin-test, keep 3 days
#   IMAGE=myimg scripts/docker_build.sh           # custom image name
#   SNAPADMIN_IMAGE_KEEP_DAYS=5 scripts/docker_build.sh
#
set -euo pipefail

IMAGE="${IMAGE:-snapadmin-test}"
KEEP_DAYS="${SNAPADMIN_IMAGE_KEEP_DAYS:-3}"
DAY_TAG="$(date +%Y-%m-%d)"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "🛠   Building ${IMAGE}:${DAY_TAG} (keep last ${KEEP_DAYS} build-days)…"
# Build context is the repo root (contains both snapadmin/ and demo/); the
# Dockerfile lives under demo/ after the demo restructure.
docker build -f demo/Dockerfile -t "${IMAGE}:${DAY_TAG}" -t "${IMAGE}:latest" .

echo "🧹  Pruning old day-tagged images…"
SNAPADMIN_IMAGE_KEEP_DAYS="${KEEP_DAYS}" \
    python -m scripts.docker_retention prune --image "${IMAGE}" --keep-days "${KEEP_DAYS}"

echo "✅  Build complete: ${IMAGE}:${DAY_TAG} (also tagged :latest)"
