# Tasks

## Current Phase: F0 — Foundation
Unlocked: 2026-04-19

## In progress
(none)

## Done
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

- [ ] T-013: Docker Compose — Prometheus + Grafana with provisioning and one dashboard showing signal-gateway up/down. Spec: §15.3-15.4, §18.1, §19 F0 bullet 2 + exit criterion
- [ ] T-014: Docker Compose — nginx reverse proxy + Cloudflare Tunnel (`cloudflared`) with configuration. Spec: §2.1, §18.1, §19 F0 bullet 2
- [ ] T-015: Hello-world `signal-gateway` — FastAPI skeleton with `/webhook` (NATS publish, signals row insert, JSON log), `/health`, `/ready`, `/metrics`. Spec: §9.1 (subset), §5.7, §19 F0 bullet 8. Split note per §0.2+§0.3: if the diff approaches 400 LOC during implementation, split into T-015a (skeleton + /health + /ready + /metrics) and T-015b (/webhook + NATS publish + DB insert + JSON log) rather than absorb overflow.
- [ ] T-016: CI-full workflow (.github/workflows/ci-full.yml) — integration stage using testcontainers for PG+NATS. Spec: §6.5, §17.6, §19 F0 bullet 1
- [ ] T-017: Dashboard test harness stub — `tests/grafana/` placeholder (dashboard JSON query tests) wired into CI-full. Spec: §4, §17, §19 F0 bullet 10
- [ ] T-018: Release workflow (.github/workflows/release.yml) — Docker image build + tag on git tag push, per-service images. Spec: §3.1, §6.5, §18, §19 F0 bullet 11
- [ ] T-019: Populate F1 backlog in TASKS.md (exit-criterion task; enumerate F1 tasks under "Backlog"). Spec: §19 F0 exit criterion, §19 F1

## Backlog
(F1-F5 tasks will be added as phases approach; F1 is populated by T-019)

## Parked
(none)
