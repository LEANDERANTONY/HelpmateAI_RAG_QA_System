#!/usr/bin/env sh
set -eu

# Rebuild the backend image on the VPS, restart the API service, then clean old
# Docker images/build cache. Prefer deploy-api.sh when using GHCR images.

docker compose up -d --build api
sh ./cleanup-docker.sh
