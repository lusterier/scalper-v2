# Tasks

## Current Phase: F0 — Foundation
Unlocked: 2026-04-19

## In progress
(none)

## Done
- [x] T-015b2b: `signal-gateway` `/webhook` handler + integration tests (2026-04-25) — `app/webhook.py` orchestrates the §9.1 13-step pipeline (raw body → HMAC → `signals.raw` publish → JSON parse → `idempotency_key` peek → Pydantic validate → `signals_received` increment → `bind_correlation` → dedup → symbol resolve → `insert_signal` → `signals.validated` publish with `message_id_for` → 200), `app/main.py` `include_router(webhook_router)`, `tests/test_models.py` (16 tests for `SignalEnvelope` + response models), `tests/test_webhook.py` (13 per-branch handler tests with `insert_signal` patched at import site), `tests/integration/{conftest, test_webhook_e2e}.py` (env-gated `POSTGRES_TEST_DSN` + `NATS_TEST_URL`; happy path + duplicate edge), `docs/modules/signal_gateway.md` flipped to present tense + new "Pipeline wire order" + "Response codes" + "Log event catalog" sections. Compose smoke green: `200 {"signal_id": 1}` + `signals_received_total{source="smoke"} 1.0` + `signals_validated_total{status="validated"} 1.0`. Hazards addressed: H-010.
- [x] T-015b2a: `signal-gateway` primitive wiring + metrics + middleware (2026-04-25) — `app/metrics.py` four §15.3 counters / histogram via `build_signal_gateway_metrics(registry)` (T-015a `build_registry` untouched per §0.8), `app/models.py` `SignalEnvelope` (with `model_validator(mode="before")` extras migration) + three response models, `app/middleware.py` `RateLimitMiddleware` (path-gated `POST /webhook`; canonical observation point for `webhook_processing_seconds` SLO histogram), `app/deps.py` six new providers, `app/main.py` lifespan / sync state-attach split. `test_app_factory.py` extended with state-attach + middleware-presence + functional middleware-order verification + 404 on `/webhook` pre-T-015b2b. Hazards addressed: H-006.
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

- [ ] T-016: CI-full workflow (.github/workflows/ci-full.yml) — integration stage using testcontainers for PG+NATS. Spec: §6.5, §17.6, §19 F0 bullet 1
- [ ] T-017: Dashboard test harness stub — `tests/grafana/` placeholder (dashboard JSON query tests) wired into CI-full. Spec: §4, §17, §19 F0 bullet 10
- [ ] T-018: Release workflow (.github/workflows/release.yml) — Docker image build + tag on git tag push, per-service images. Spec: §3.1, §6.5, §18, §19 F0 bullet 11
- [ ] T-019: Populate F1 backlog in TASKS.md (exit-criterion task; enumerate F1 tasks under "Backlog"). Spec: §19 F0 exit criterion, §19 F1

## Backlog
(F1-F5 tasks will be added as phases approach; F1 is populated by T-019)

- [ ] T-F1+: install docker-buildx-plugin on lab host. Current compose falls back to legacy builder with Bake warning. Fine for F0 single-arch builds; becomes relevant for T-016 CI-full (testcontainers may need BuildKit features) and any future multi-arch. Out of T-015c scope.
- [ ] T-F1+: refine `SignalEnvelope._migrate_unknown_keys` docstring to distinguish None coalescing ("payload omitted" shorthand) from other non-dict pass-through. Currently the docstring implies all non-dict values pass through unchanged, but `None` is special-cased to `{}` so extras still merge. ~3-line docstring edit; behaviour locked by `test_payload_as_none_treated_as_missing` in `test_models.py`.

## Parked
(none)
