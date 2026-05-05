#!/usr/bin/env sh
set -eu

# Clean Docker disk usage after backend image rebuilds or pulls.
# This intentionally avoids `--volumes` so Helpmate uploads, indexes, cache,
# Caddy data, and other named volumes are not removed.

echo "Docker disk usage before cleanup:"
docker system df

echo
echo "Pruning images not used by any container and older than 72 hours..."
docker image prune -a -f --filter "until=72h"

echo
echo "Pruning unused BuildKit build cache older than 72 hours..."
docker builder prune -a -f --filter "until=72h"

echo
echo "Docker disk usage after cleanup:"
docker system df
