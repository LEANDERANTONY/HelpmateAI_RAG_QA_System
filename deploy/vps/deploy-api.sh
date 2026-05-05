#!/usr/bin/env sh
set -eu

# Pull the latest backend image, restart the API service, then clean old Docker
# images/build cache. Named volumes are preserved by cleanup-docker.sh.

docker compose pull api
docker compose up -d api
sh ./cleanup-docker.sh
