#!/usr/bin/env sh
# Clean Docker disk usage after backend image rebuilds or pulls.
# Intentionally avoids `--volumes` so Helpmate uploads, indexes, cache,
# Caddy data, and other named volumes are not removed.
#
# Race-tolerance: this VPS hosts BOTH jobagent and HelpmateAI, and
# their GHA deploys can land within seconds of each other. Docker's
# daemon serializes prune operations and surfaces concurrent attempts
# as ``Error response from daemon: a prune operation is already
# running`` with exit 1. With ``set -e`` that bubbled to the GHA step
# and marked the WHOLE deploy job red — even though the actual code
# deploy + health check had already passed in earlier steps. The
# false-red was misleading and made every race look like a real
# failure.
#
# New behavior:
#   * Drop ``set -e`` (keep ``set -u``) so individual command failures
#     don't halt housekeeping mid-stream.
#   * Wrap each prune in a small retry loop that backs off briefly
#     if Docker reports the "already running" lock — usually the
#     other deploy's prune finishes within a few seconds, and we
#     get our turn.
#   * After retries, soft-fail: log the unrecoverable error and
#     exit 0 anyway. Cleanup is non-critical housekeeping; a stale
#     image hangs around for one more deploy cycle, no big deal.
#     Actual deploy success is gated by the Health check step
#     earlier in the workflow.
set -u

run_with_prune_retry() {
  # $1: human label, $2..: command
  label="$1"
  shift
  attempt=1
  max_attempts=3
  while [ "$attempt" -le "$max_attempts" ]; do
    output=$("$@" 2>&1) && rc=0 || rc=$?
    printf '%s\n' "$output"
    if [ "$rc" -eq 0 ]; then
      return 0
    fi
    # Concurrent-prune lock — back off and retry.
    if printf '%s' "$output" | grep -q "prune operation is already running"; then
      echo "  ($label) another prune is in flight; sleeping 10s then retrying ($attempt/$max_attempts)..."
      sleep 10
      attempt=$((attempt + 1))
      continue
    fi
    # Any other failure: warn + soft-fail. We don't want housekeeping
    # to red-flag a deploy whose actual code rollout already succeeded.
    echo "  ($label) failed with exit $rc; treating as soft-fail."
    return 0
  done
  echo "  ($label) exhausted $max_attempts attempts; soft-failing so the deploy step stays green."
  return 0
}

echo "Docker disk usage before cleanup:"
docker system df || true

echo
echo "Pruning images not used by any container and older than 72 hours..."
run_with_prune_retry "image prune" \
  docker image prune -a -f --filter "until=72h"

echo
echo "Pruning unused BuildKit build cache older than 72 hours..."
run_with_prune_retry "builder prune" \
  docker builder prune -a -f --filter "until=72h"

echo
echo "Docker disk usage after cleanup:"
docker system df || true
