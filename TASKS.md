# Tasks

## Current Phase: F0 — Foundation
Unlocked: 2026-04-19

## In progress
(none)

## Done
- [x] T-015d: `insert_signal` marked `@non_idempotent` (§N3 / §5.8) (2026-04-25) — erratum on T-015b1; query landed without the idempotency classification, T-015b2 handler adds four no-retry call sites so the marker question could no longer wait. Decorator is marker-only, no behaviour change.
- [x] T-015c: .dockerignore to exclude build-cache from docker context (2026-04-24) — hotfix for T-015a residue; `packages/__pycache__/` from local pytest/mypy runs was leaking into docker build context and tripping uv workspace glob.
- [x] T-015b1: `signal-gateway` primitives — `verify_hmac`, `RateLimiter`, `DedupRing`, `SymbolMapCache`, `SignalValidated` schema + `message_id_for` helper (`packages.bus.schemas.signals`), `packages.db.queries.signal_gateway` (`fetch_symbol_mapping` + `insert_signal`), Hypothesis property test for `DedupRing`. (2026-04-22)
- [x] T-015a: Hello-world `signal-gateway` — FastAPI skeleton with `/health`, `/ready`, `/metrics`. Spec: §9.1, §19 F0 bullet 8 (partial). (2026-04-22)
- [x] T-014: Docker Compose — nginx reverse proxy + Cloudflare Tunnel (`cloudflared`) (2026-04-22)
- [x] T-013b: Overview dashboard — stat + table panels over the up metric (2026-04-21)
- [x] T-013a: Prometheus + Grafana services with provisioning (2026-04-21)
- [x] T-012: Docker Compose — NATS JetStream service + stream bootstrap (2026-04-21)
- [x] T-011: Migration 0002 — `signals` hypertable (2026-04-20)
- [x] T-010: Alembic setup + migration 0001 — bots, bot_configs, symbol_map + seed (2026-04-20)
- [x] T-009: Docker Compose — PostgreSQL 16 + TimescaleDB service (2026-04-20)
- [x] T-008b: `packages/bus` — `NatsClient` wrapper (2026-04-20)
- [x] T-008a: `packages/bus` — MessageEnvelope + error taxonomy scaffold (2026-04-20)
- [x] T-007: `packages/db` — asyncpg pool factory + query helper skeleton (2026-04-19)
- [x] T-006: `packages/observability` — structlog JSON, trace/correlation IDs, Prometheus registry factory, secret redactor (2026-04-19)
- [x] T-020: Workspace build-system (hatchling) + ADR-0002 + CI-fast --all-packages (2026-04-19)
- [x] T-005: `packages/core` — domain types, errors, markers, now_utc (2026-04-19)
- [x] T-004: ADR-0001 — NATS JetStream (2026-04-19)
- [x] T-003: CI-fast workflow (2026-04-19)
- [x] T-002: Root tooling config (2026-04-19)
- [x] T-001: Monorepo scaffold (2026-04-19)

## Next (do not start without operator approval)

Proposed Phase F0 breakdown. Order reflects dependency chain: root scaffold/tooling → shared packages → infra compose → alembic + signals table → hello-world service → CI-full/release → F1 backlog. Each task is scoped to ≤~400 LOC diff per §0.3.

- [ ] T-015b2: `signal-gateway` `/webhook` pipeline — wire T-015b1 primitives through §9.1 9 steps in `services/signal_gateway/app/webhook.py`: `verify_hmac` via FastAPI dependency (reads raw body), rate-limit middleware wrapping `RateLimiter` with client-IP from `X-Real-IP` / `X-Forwarded-For`, `signals.raw` NATS publish (pre-parse, H-010 fan-out), JSON parse, `DedupRing.check_and_record`, `SignalEnvelope` + response models in `app/models.py` (`model_validator(mode="before")` migrates unknown top-level keys into `payload`), `SymbolMapCache.resolve`, `insert_signal` DB write, `signals.validated` publish with `message_id_for(idempotency_key)`. §15.3 service counters (`signals_received_total`, `signals_validated_total`, `errors_total`) + `webhook_processing_seconds` histogram in `app/metrics.py`. Module doc `*future (T-015b2)*` markers flipped to present tense. Integration test via testcontainers (skip-on-missing-env, lit up by T-016). Spec: §9.1, §5.7, §8.4, §15.3, §19 F0 bullet 8. Hazards addressed: H-006, H-010.
- [ ] T-016: CI-full workflow (.github/workflows/ci-full.yml) — integration stage using testcontainers for PG+NATS. Spec: §6.5, §17.6, §19 F0 bullet 1
- [ ] T-017: Dashboard test harness stub — `tests/grafana/` placeholder (dashboard JSON query tests) wired into CI-full. Spec: §4, §17, §19 F0 bullet 10
- [ ] T-018: Release workflow (.github/workflows/release.yml) — Docker image build + tag on git tag push, per-service images. Spec: §3.1, §6.5, §18, §19 F0 bullet 11
- [ ] T-019: Populate F1 backlog in TASKS.md (exit-criterion task; enumerate F1 tasks under "Backlog"). Spec: §19 F0 exit criterion, §19 F1

## Backlog
(F1-F5 tasks will be added as phases approach; F1 is populated by T-019)

- [ ] T-F1+: install docker-buildx-plugin on lab host. Current compose falls back to legacy builder with Bake warning. Fine for F0 single-arch builds; becomes relevant for T-016 CI-full (testcontainers may need BuildKit features) and any future multi-arch. Out of T-015c scope.

## Parked
(none)
