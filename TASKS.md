# Tasks

## Current Phase: F0 — Foundation
Unlocked: 2026-04-19

## In progress
(none)

## Done
- [x] T-019: Populate F1 backlog (2026-04-25) — TASKS.md restructure adding 13 numbered F1 tasks (T-100..T-112) under `### F1 numbered` and 7 opportunistic items under `### F1+ opportunistic` (4 carried from F0 + 3 new: per-bot HMAC, Alembic mako template, prometheus-nats-exporter sidecar). F1 bullet 1 (full signal-gateway) absorbed into F0 (T-015a..T-015b2b) — all §9.1 pipeline + §19 F1 exit criterion "TV webhook → validated `signals` row + correct symbol mapping" met by F0; per-bot HMAC selection per §9.1 ADR-0001 topic moved to T-F1+ opportunistic, not a phase blocker. F0 phase-gate review (per §0.10 "Phase N unlocked" marker) is operator action — T-019 does not flip the phase header.
- [x] T-018: Release workflow (`.github/workflows/release.yml`) (2026-04-25) — Per-service Docker image build + push to GHCR on `v*` tag pushes and via `workflow_dispatch`. Service matrix hardcoded to `[signal_gateway]` (only F0 service with a Dockerfile); F1 service-ship tasks append in the same PR that lands their Dockerfile. Image tags: `<git-tag>` + `sha-<short>`; no `:latest` (deploy script signature is `./scripts/deploy.sh <git-sha>`, prerelease ambiguity deferred). `docker/setup-buildx-action@v3` for the Dockerfile's `--mount=type=cache` + GHA cache backend (`cache-from/to: type=gha,mode=max`). Concurrency `cancel-in-progress: false` (release builds are commit-of-record artifacts). First `workflow_dispatch` on master green: 39s wall-time, build step 19s, cold-cache 0% as expected, no 403 on `packages: write` (default workflow permissions sufficed). Manifest verified at `ghcr.io/lusterier/scalper-v2/signal_gateway:sha-9dae4b5` (HTTP 401 + `WWW-Authenticate: Bearer ... scope="repository:lusterier/scalper-v2/signal_gateway:pull"` confirms exists + private). Package visibility kept private — matches repo privacy, no external pull need at single-dev flow. T-F1+ backlog entry added: bump docker/* actions when upstream publishes Node 24-compatible majors (Node 20 EoL: 2026-06-02 default flip, 2026-09-16 hard cutoff).
- [x] T-017: Dashboard test harness stub — `tests/grafana/` (2026-04-25) — `tests/grafana/{__init__,conftest,test_dashboard_queries}.py` validate every PromQL expression in `infra/grafana/dashboards/*.json` against `/api/v1/query` on a zero-data Prometheus; `status="success"` proves syntax + arity + type-check, no scrape coverage required (metric-name typo detection deferred to F4+ graduation). Module-level skip on `PROMETHEUS_TEST_URL` unset keeps ci-fast collection clean — `parametrize()` never executes when env-gated out. `discover_dashboard_queries()` yields `(dashboard, panel_id, ref_id, expr)` tuples for parametrize; failure messages cite all four for triage. ci-full.yml gains a `prom/prometheus:v3.11.2` service container (matches `compose.yaml:104`, default CMD self-scrape, no `infra/prometheus/prometheus.yml` bind-mount), `PROMETHEUS_TEST_URL=http://localhost:9090` env, and `tests/grafana` appended to the integration-job pytest paths. T-F1+ backlog entry added: align migrations conftest env-gate from fixture-level (no-op `allow_module_level=True` flag) to module-level skip pattern. Both jobs green at `9d5600a`: ci-full 1m 12s (overview.json: 2/2 cases — `sum(up)`, `up`), ci-fast 27s.
- [x] T-016: CI-full workflow (`.github/workflows/ci-full.yml`) (2026-04-25) — integration + security stages on every push/PR. Hybrid container strategy: PG via GH Actions service container (env-driven config, GH-managed health gating); NATS via `docker run` because GH services cannot supply CMD args and JetStream enable lives in `infra/nats/server.conf` (`-c <path>` required). Bootstrap step reuses production `infra/nats/bootstrap.sh` + `infra/nats/streams/*.json` verbatim via nats-box, so SIGNALS topology is identical to prod and dev. Integration job runs `tests/integration` + `services/signal_gateway/tests/integration` with `POSTGRES_TEST_DSN` + `NATS_TEST_URL` set; coverage.xml uploads via pyproject `addopts`. Security job: `pip-audit --skip-editable --ignore-vuln CVE-2026-3219` + `bandit -r services packages`. Pytest stack bumped (`pytest~=9.0`, `pytest-asyncio~=1.0`, `pytest-cov~=7.0`) to clear CVE-2025-71176; CVE-2026-3219 (pip 26.0.1) suppressed pending upstream fix. Both jobs green at `c224f84`: integration 1m 1s, ci-fast 27s.
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

(none — F0 task queue cleared; F1 unlock is operator action per §0.10 "Phase N unlocked" marker)

## Backlog

F1 bullet 1 (full signal-gateway) absorbed into F0 (T-015a..T-015b2b); per-bot HMAC selection per §9.1 ADR-0001 topic moved to `### F1+ opportunistic` below, not a phase blocker.

### F1 numbered

- [ ] T-100: market-data-svc skeleton — FastAPI, /health, /ready, /metrics, Dockerfile, compose. Spec: §9.2, §19 F1 bullet 2. Est: S (~150 LOC).
- [ ] T-101: `packages/market` — Binance REST + WS client wrapper + exp backoff (H-007). Spec: §9.2. Est: M (~250 LOC).
- [ ] T-102: `SubscriptionManager` refcount — dynamic subscribe/unsubscribe (H-014). Spec: §9.2, H-014. Est: M (~200 LOC). Blocked by T-101.
- [ ] T-103: migration 0003 — `ohlc_1m` hypertable + continuous aggregates 5m/15m/1h/4h/1d. Spec: §7.2, §19 F1 bullet 4. Est: M (~200 LOC).
- [ ] T-104: OHLC pipeline — closed-bucket detection, persist to `ohlc_1m`, publish `market.ohlc.*`. Spec: §9.2, §8.2. Est: M (~200 LOC). Blocked by T-101, T-102, T-103.
- [ ] T-105: backfill on startup + reconnect resync via Binance REST `/api/v3/klines`. Spec: §9.2. Est: M (~200 LOC). Blocked by T-104.
- [ ] T-106: `packages/features` skeleton + `Feature` protocol + `FeatureValue` types. Spec: §9.3 lines 1500-1509. Est: S (~100 LOC).
- [ ] T-107: 6 built-in indicators — EMA, RSI, ATR, VWAP, Bollinger, MACD. Spec: §9.3, §19 F1 bullet 3. Est: M (~300 LOC). Blocked by T-106.
- [ ] T-108: migration 0004 — `features` hypertable. Spec: §7.2, §19 F1 bullet 4. Est: S (~100 LOC).
- [ ] T-109: feature-engine service skeleton — FastAPI, Dockerfile, compose. Spec: §9.3, §19 F1 bullet 3. Est: S (~150 LOC).
- [ ] T-110: feature-engine computation loop + warmup — subscribe `market.ohlc.*`, dispatch, persist, KV update, publish. Spec: §9.3 lines 1491-1513. Est: M (~350; close to §0.3 cap, pre-split candidate). Blocked by T-104, T-106, T-107, T-108, T-109.
- [ ] T-111: YAML registration — `configs/features/indicators.yaml` + plugin discovery. Spec: §9.3, §B.2. Est: S (~150 LOC). Blocked by T-110.
- [ ] T-112: `scripts/backfill_features.py` CLI — idempotent historical compute. Spec: §9.3 lines 1515-1518, §19 F1 exit criterion. Est: M (~200 LOC). Blocked by T-107, T-108.

### F1+ opportunistic

- [ ] T-F1+: per-bot HMAC secret selection via `X-Bot-Signal-Source` header with shared-secret fallback. F0 ships single shared `SIGNAL_GATEWAY_HMAC_SECRET`; §9.1 line 1438 calls per-bot secrets an "ADR-0001 topic" — needs ADR for secret store + header parsing + fallback semantics, then implementation. Discretionary enhancement, not phase-blocker. Est: M (~200-300 LOC + ADR).
- [ ] T-F1+: add `migrations/script.py.mako` standard Alembic async template. Currently each migration is hand-written; chore cleanup before F1's first migration task (T-103 or T-108 natural slot). ~20 LOC.
- [ ] T-F1+: `prometheus-nats-exporter` sidecar + nats scrape entry in `prometheus.yml`. Deferred from T-013a because NATS `/varz` returns JSON not Prom text format — direct scrape would produce permanently-DOWN target for healthy service (false-negative). Forward-refs in `infra/nats/README.md` and `infra/prometheus/README.md`. Relevant when F1 dashboards include NATS panels (post-T-017 follow-up or F1 task). ~40-60 LOC (service + image pin + UID probe + scrape job + README touch).
- [ ] T-F1+: install docker-buildx-plugin on lab host. Current compose falls back to legacy builder with Bake warning. Fine for F0 single-arch builds; becomes relevant for any future multi-arch. Out of T-015c scope.
- [ ] T-F1+: bump `docker/*` actions to Node 24-compatible versions when upstream publishes them. Currently pinned at `build-push-action@v6`, `login-action@v3`, `setup-buildx-action@v3` — all run on Node 20, deprecated per GHA runner Node 20 EoL. June 2nd 2026: Node 24 default but Node 20 still available via opt-in env var (`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`). September 16th 2026: Node 20 removed from runners, hard cutoff. Check upstream Docker action repos for new majors before then; bump accordingly. May require workflow syntax updates if maintainers ship breaking changes alongside the Node bump.
- [ ] T-F1+: align `tests/integration/migrations/conftest.py` env-gate with `tests/grafana/conftest.py` module-level skip pattern. Currently uses fixture-level skip with no-op `allow_module_level=True` flag (the flag is only honored when `pytest.skip` is called at module import time). Module-level guard gives true collection skip + non-misleading flag usage. ~10-line edit; behaviour-preserving. Slot: opportunistic.
- [ ] T-F1+: refine `SignalEnvelope._migrate_unknown_keys` docstring to distinguish None coalescing ("payload omitted" shorthand) from other non-dict pass-through. Currently the docstring implies all non-dict values pass through unchanged, but `None` is special-cased to `{}` so extras still merge. ~3-line docstring edit; behaviour locked by `test_payload_as_none_treated_as_missing` in `test_models.py`.

## Parked
(none)
