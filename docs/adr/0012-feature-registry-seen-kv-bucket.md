# ADR-0012: feature_registry_seen 4th NATS KV bucket — feature-engine auto-backfill state store

**Status:** Accepted (2026-05-12, T-518 plan-stage Gate 1)
**Context window:** F5 close-out polish — feature-engine auto-backfill on registration (BRIEF §9.3:1525-1528)
**Authors:** Operator + Claude Code (T-518 plan-stage)
**Prerequisite for:** T-518 (Feature auto-backfill on registration)

## Decision

The feature-engine service adds a 4th NATS JetStream KV bucket `feature_registry_seen` to track which feature_names have already been auto-backfilled across deploys. This amends BRIEF §8.2 (closed three-bucket set) by adding the 4th bucket with operator approval.

Bucket contract:

| Property | Value | Rationale |
|---|---|---|
| `name` | `feature_registry_seen` | Mirror existing `feature_*` namespace prefix |
| `ttl` | `0` (forever) | First-seen state is permanent across deploys; never expires |
| `history` | `1` | No multi-revision tracking needed (overwrite OK) |
| `replicas` | `1` | Mirror existing 3 buckets — single-node infra |
| `storage` | `file` | Mirror existing 3 buckets — durable through restarts |
| Key shape | `<feature_name>` | e.g., `ind.btcusdt.15m.ema_20`; one entry per registered feature_name (post-cross-product with symbols) |
| Value shape | ISO-8601 UTC timestamp string (bytes) | First-seen wall-clock; written ONCE on successful backfill completion |

Per `infra/nats/bootstrap.sh:79-82` policy: *"§8.2 defines this closed three-bucket set; if a fourth ever lands, that is an explicit scope change (TASKS task or ADR), which is the right gate to revisit the inline-vs-filesystem decision."* — T-518 IS the TASKS-task gate; this ADR is the parallel scope-change record.

The "revisit the inline-vs-filesystem decision" trigger fires HERE: with N=4 buckets, an inline `apply_kv_bucket` line in `bootstrap.sh` is still acceptable (low maintenance overhead, locality with sibling buckets); migration to a filesystem-driven YAML config (one bucket per file under `infra/nats/buckets/*.json` mirroring the streams pattern) is **deferred** to a future task if/when N>=6 or if maintenance friction surfaces.

## Context

### BRIEF §9.3:1525-1528 — feature backfill auto-trigger

> Backfill:
> - CLI: `python scripts/backfill_features.py --feature <name> --from <date> --to <date>`.
> - Iterates OHLC history, computes, upserts. Idempotent.
> - **Automatically triggered when a new feature is registered (detected by `plugin_registry.yaml` diff on startup; ADR for this auto-trigger is in F1).**

The promised F1 ADR was never shipped. T-518 (F5 close-out polish) is the implementation gate; this ADR retroactively documents the auto-trigger architecture per CLAUDE.md §6.7 BRIEF-amendment protocol.

### BRIEF §8.2 closed-set policy

§8.2:1274-1280 declares 3 KV buckets canonical:

```yaml
kv_buckets:
  - name: config_runtime    # ttl=0
  - name: rate_limits       # ttl=10s
  - name: feature_latest    # ttl=0
```

`infra/nats/bootstrap.sh:79-82` policy mirrors this with the inline-`apply_kv_bucket` calls. Adding a 4th bucket is explicitly out-of-set per BRIEF §8.2 — requires this ADR per CLAUDE.md §6.7 protocol.

### State-store alternatives considered

**Option A (chosen): NATS KV bucket `feature_registry_seen`.**

Pros:
- Mirror existing `feature_latest` pattern (§9.3:1505 + §8.2); architectural consistency.
- Durable across restarts (NATS JetStream KV file storage).
- Operator-inspectable via `nats kv ls feature_registry_seen` / `nats kv get feature_registry_seen <name>`.
- No migration; no PG schema cost; no L-021 testcontainer trigger.
- Existing `kv_get` / `kv_put` API in `packages.bus.client.NatsClient:278-407`.

Cons:
- Triggers BRIEF §8.2 amendment + this ADR.
- Bucket pre-provisioning required in `bootstrap.sh` (1 LOC delta).

**Option B (rejected): NEW DB table `feature_registry_seen` + migration 0017.**

Schema: `(feature_name TEXT PK, first_seen TIMESTAMPTZ NOT NULL)`. NEW migration + queries module + L-021 testcontainer test (~80 LOC overhead). No ADR required.

Rejected: divergence from §9.3 NATS KV state-store pattern; redundant migration for a 1-row-per-feature trivially small table; inconsistent with existing `feature_latest` precedent.

**Option C (rejected): Disk marker file `data/feature_registry_seen.json`.**

No infra delta; no migration; no ADR.

Rejected: fragile (operator-deletable); ephemeral storage in containerized deploys; inconsistent with existing state-store patterns.

## Rationale

- **Architectural consistency** — `feature_*` namespace already lives in NATS KV (`feature_latest`); `feature_registry_seen` is the natural sibling.
- **Durability matches need** — first-seen state must persist across deploys (otherwise auto-backfill re-runs on every restart, violating idempotency intent).
- **Minimal infra delta** — 1 LOC in `bootstrap.sh`; no new infra primitives.
- **Operator-inspectable via standard `nats` CLI** — no custom tooling needed.
- **Defers the inline-vs-filesystem refactor** — N=4 is below the maintenance-friction threshold; explicit "revisit at N>=6" trigger documented.

## Consequences

### Accepted

- BRIEF §8.2 effectively extends to 4 buckets at infra layer; canonical 3 listed in §8.2 remain as the spec; ADR-0012 records the 4th. Future doc passes (T-521) may consolidate §8.2 listing.
- `bootstrap.sh` gains a 4th `apply_kv_bucket` call (inline; mirror existing 3).
- T-518 implementation reads/writes via `kv_get`/`kv_put` (existing API).
- `feature-engine/app/auto_backfill.py` (NEW module per T-518) is the sole writer to this bucket.

### Trade-offs

- 4 buckets is the operator-acknowledged ceiling before re-evaluating the inline-vs-filesystem decision (per `bootstrap.sh:81-82` comment); future bucket additions need explicit refactor consideration.
- ADR-0012 must be referenced from T-521 (final docs pass) when consolidating §8.2 update OR keeping it as ADR-only amendment per §6.7 audit trail.

### Implementation pointers (for T-518 plan-stage + brief-reviewer)

- `infra/nats/bootstrap.sh` +1 LOC: `apply_kv_bucket feature_registry_seen 0 1 "first-seen ts per auto-backfilled feature_name (ADR-0012)"`.
- `services/feature_engine/app/auto_backfill.py` (NEW) is sole writer.
- `kv_get(feature_registry_seen, feature_name)` + `kv_put(feature_registry_seen, feature_name, value=now_iso8601_bytes)`.
- Tests mock NATS KV via `AsyncMock` (mirror `packages/bus/tests/test_client_kv.py`).

## Related

- BRIEF §8.2 (KV buckets canonical set).
- BRIEF §9.3:1525-1528 (auto-trigger spec literal).
- BRIEF §6.7 (BRIEF amendment via ADR protocol).
- `infra/nats/bootstrap.sh:79-82` (closed-set policy).
- ADR-0007 (APScheduler operational pattern) — sibling state-store design ADR; not directly applicable (auto-backfill is one-shot per-feature, not recurring → uses `asyncio.create_task` not APScheduler per T-518 plan).
