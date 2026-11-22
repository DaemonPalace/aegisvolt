#!/usr/bin/env bash
# build-push.sh <tag> — builds the 4 service images and pushes ghcr.io/<owner>/aegisvolt-<svc>:<tag>.
# Needs `docker login ghcr.io` with write:packages. The source label links each package to the repo.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=$1 REGISTRY=ghcr.io/${IMAGE_OWNER:-daemonpalace}
for svc in gateway reservation billing dispatch; do
  img=$REGISTRY/aegisvolt-$svc:$TAG
  docker build -q --build-arg SERVICE=$svc -f services/Dockerfile \
    --label org.opencontainers.image.source=https://github.com/DaemonPalace/aegisvolt -t "$img" services
  docker push -q "$img"
done
