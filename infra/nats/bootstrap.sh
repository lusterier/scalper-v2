#!/bin/sh
# scalper-v2 JetStream topology applier (brief §8.2, §18.1, §19 F0 bullet 2).
#
# Runs once via the `nats-init` compose sidecar after the `nats` service
# reports healthy. Two sources of truth:
#
#   * $STREAMS_DIR/*.json — one file per stream, passed verbatim to the
#     nats CLI via `nats stream add|update --config`. The CLI unmarshals
#     into the server's StreamConfig struct, so the JSON files ARE the
#     stream topology (no separate reference/documentation sibling).
#     Durations (`max_age`, `duplicate_window`) are nanosecond ints, as
#     required by the unmarshaller; the human-readable form lives in the
#     `description` field of each stream and is visible via `nats stream
#     info <name>`.
#
#   * Inline `apply_kv_bucket` calls below — `nats kv add` has no
#     --config flag equivalent (verified against nats-box 0.19.3), so
#     each of the three §8.2 buckets is an explicit CLI invocation here.
#     YAGNI: the §8.2 set is closed; a fourth bucket is a task, not a
#     filesystem-plumbing exercise.
#
# Idempotent. Existing resources are updated via `stream update` /
# `kv edit`, so re-running after a config change converges the server.
# Exits non-zero on any CLI error; check `docker compose logs nats-init`.

set -eu

NATS_URL="${NATS_URL:-nats://nats:4222}"
STREAMS_DIR="${STREAMS_DIR:-/etc/nats/streams}"

# Preflight: CLI tools available. nats-box 0.19.3 ships both, but fail
# fast with a clear error if the image composition ever changes.
command -v nats >/dev/null 2>&1 || { echo "nats-init: nats CLI missing"; exit 1; }
command -v jq   >/dev/null 2>&1 || { echo "nats-init: jq missing";       exit 1; }

# Preflight: JetStream reachable. `depends_on.condition: service_healthy`
# already gates this, but a direct JS probe gives a better error if the
# healthcheck ever drifts from real JS readiness.
nats --server "$NATS_URL" server check jetstream >/dev/null

apply_stream() {
  file=$1
  name=$(jq -r .name "$file")
  if nats --server "$NATS_URL" stream info "$name" >/dev/null 2>&1; then
    nats --server "$NATS_URL" stream update "$name" --config "$file" >/dev/null
    echo "stream $name: updated"
  else
    nats --server "$NATS_URL" stream add "$name" --config "$file" >/dev/null
    echo "stream $name: created"
  fi
}

apply_kv_bucket() {
  # $1=name  $2=ttl (duration string, "0" = forever)  $3=history  $4=description
  name=$1
  ttl=$2
  history=$3
  desc=$4
  if nats --server "$NATS_URL" kv info "$name" >/dev/null 2>&1; then
    # `kv edit` accepts --ttl/--history/--replicas/--description only;
    # --storage is immutable post-create.
    nats --server "$NATS_URL" kv edit "$name" \
      --ttl "$ttl" --history "$history" --replicas 1 \
      --description "$desc" >/dev/null
    echo "kv $name: updated"
  else
    nats --server "$NATS_URL" kv add "$name" \
      --ttl "$ttl" --history "$history" --storage file --replicas 1 \
      --description "$desc" >/dev/null
    echo "kv $name: created"
  fi
}

# Streams — JSON files under $STREAMS_DIR are source of truth (§8.2).
for f in "$STREAMS_DIR"/*.json; do
  apply_stream "$f"
done

# KV buckets — inline because `nats kv add` has no --config flag.
# §8.2 defines the canonical 3-bucket set; the 4th bucket below
# (feature_registry_seen) lands per ADR-0012 (T-518 plan-stage
# amendment of §8.2 per BRIEF §6.7). Inline-vs-filesystem decision
# deferred to N>=6 per ADR-0012 trade-offs.
apply_kv_bucket config_runtime  0    1 "hot config per bot (§2.1, §8.2)"
apply_kv_bucket rate_limits     10s  1 "cross-bot rate limiter state, 10s TTL (§8.2)"
apply_kv_bucket feature_latest  0    1 "latest feature value per symbol (§8.2)"
apply_kv_bucket feature_registry_seen 0 1 "first-seen ts per auto-backfilled feature_name (ADR-0012, T-518)"

echo "nats-init: topology applied"
