# Claude Code Implementation Brief — Trading Bot Platform v2

**Document version:** 1.0
**Target audience:** Claude Code (implementation agent)
**Status:** Specification, approved for implementation
**Project codename:** `scalper-v2`

---

## How to use this document

You are Claude Code. You will implement the system specified in this document. Read this section first, then read Section 0 (Operating Rules). Do not start coding until you have read both.

This brief is the **source of truth** for architecture, coding standards, and delivery process. When this document and prior conversation disagree, this document wins. When this document and your intuition disagree, this document wins — unless this document is silent, in which case write an ADR (see §6.3) and ask the operator.

The operator is a single developer working alongside you. They will review your work at phase boundaries and answer questions when you ask. They expect you to behave as a professional engineer: rigorous, test-first on critical code, disciplined in scope, honest when you are uncertain.

This project has a predecessor (v1) which is documented separately in `docs/v1/BOT_DOCUMENTATION.md` in the repo. Read it once for context about what worked and what hurt. **Do not copy code from v1 mechanically.** The new system is a rewrite, not a port; we keep the lessons, not the line-by-line implementation.

---

## Table of contents

0. Operating Rules
1. Mission and Non-Negotiables
2. Architecture Overview
3. Technology Stack
4. Repository Layout
5. Coding Standards
6. Development Workflow and Discipline
7. Data Model (PostgreSQL + TimescaleDB)
8. Message Contracts (NATS JetStream)
9. Service Specifications
10. Feature Store and Scoring Engine
11. Execution Layer and Exchange Adapters
12. Paper Exchange and Backtest Harness
13. Shadow Variants
14. Dashboard UI Specification
15. Observability
16. Security Baseline
17. Testing Strategy
18. Deployment and Operations
19. Phased Delivery Plan
20. Known Hazards Catalog
21. Glossary
22. Appendix A — Server Configuration
23. Appendix B — YAML Configuration Examples
24. Appendix C — References

---

## 0. Operating Rules

These rules apply at all times. Violating them is a regression; call it out in your next message and fix it.

### 0.1 Read before you write

Before starting any new session or any new task, read **in this order**:

1. `TASKS.md` — the current state of the project (what is done, what is in progress, what is next).
2. The three most recent ADRs in `docs/adr/`.
3. `docs/status.md` — any notes the operator has left you.

Your first message in a new session, if one is ongoing, is a short summary: "Last session ended at T-NNN. Current phase: FX. Open questions: ...". Do not start coding until the operator confirms.

### 0.2 One task at a time

You work on exactly one task from `TASKS.md` at a time. You do not start a second task until the first is either (a) merged or (b) explicitly parked by the operator with a note in `TASKS.md`.

If a task reveals sub-work that does not belong in it, do not absorb the sub-work. Add new tasks to `TASKS.md` under "Backlog" or "Next", finish the current task, then ask the operator which to pick up next.

### 0.3 Small diffs

A pull request is at most ~400 lines of diff excluding generated code and tests. If your diff is growing past this, stop and split. Open a separate task for the remainder.

Exception: migrations, generated artifacts, third-party vendoring. Call these out in the PR description.

### 0.4 Green CI is mandatory

No task is complete until CI is green. This is non-negotiable. If CI fails on infrastructure (flaky test, Docker pull timeout), retry; do not work around it.

### 0.5 Ask when uncertain

If a task specification in `TASKS.md` or this brief is ambiguous, do not guess. Post a short question to the operator ("T-042: the brief says X but the data model implies Y. Which is correct?") and wait. Guessing costs more time than asking.

### 0.6 Write ADRs for architectural choices

If you make a decision that affects more than one module, or that deviates from this brief in any way, write an ADR in `docs/adr/` **before** implementing. The ADR must be reviewed by the operator before you proceed. See §6.3 for the format.

### 0.7 Update TASKS.md at the end of every session

Before ending a session, update `TASKS.md`: mark finished tasks done, add any new tasks you discovered, note any blockers. This is the contract that keeps future sessions coherent.

### 0.8 No silent refactors

Do not refactor code outside the scope of your current task "while you are there". Refactoring is a separate task with its own review. If you see cruft, add a task to the backlog.

### 0.9 No dependency additions without justification

Adding a new library to `pyproject.toml` or `package.json` requires a one-paragraph justification in the PR. We prefer small, well-maintained, well-understood dependencies. For anything security-critical (auth, crypto, payment), the operator must approve.

### 0.10 Respect the phase gate

Phases are numbered F0 through F5 (§19). You do not work on F3 tasks while F2 is incomplete. The operator explicitly opens the next phase with a "Phase N unlocked" note in `TASKS.md`.

---

## 1. Mission and Non-Negotiables

### 1.1 Mission

Build `scalper-v2`: a multi-bot crypto-derivatives trading platform that

- receives entry signals from TradingView webhooks,
- evaluates them through a per-bot configurable scoring engine backed by a dynamic feature store,
- executes trades on Bybit (with swappable exchange adapters and a paper exchange for simulation),
- supports parallel bots sharing market data and infrastructure but with isolated state and credentials,
- produces audit-grade JSON logs from day zero,
- is backtestable, replay-able, and introspectable down to per-rule decision provenance.

The system replaces an existing single-bot SQLite-based implementation (v1). v1 works and has earned lessons; v2 is the architectural level-up.

### 1.2 Non-negotiables

These are not opinions. They are invariants.

**N1. UTC everywhere internally.** The server runs UTC. All database timestamps are ISO-8601 UTC with explicit `+00:00` offset. All NATS messages carry UTC timestamps. Display conversion to CEST happens only in the UI layer, the Telegram alert formatter, and optional CLI log viewer scripts. Never use `CURRENT_TIMESTAMP` or `NOW()` in SQL; always pass a parameter computed in Python.

**N2. Structured JSON logs from day zero.** There is no "we will add JSON later" phase. Every service emits JSON Lines to three streams: `trading.log`, `audit.log`, `system.log`. See §15.

**N3. Every external write is classified as idempotent or non-idempotent.** Non-idempotent writes (e.g., `place_market_order`) do not retry. Idempotent writes (e.g., `set_trading_stop`) retry with bounded backoff. This classification is explicit in code (annotation or docstring marker) and enforced in review.

**N4. TDD for financial math and execution lifecycle.** Code that computes P&L, sizes positions, places orders, or reconciles state is written test-first. Other code is not required to be test-first but must have tests before merge.

**N5. 80% line coverage on critical modules.** Enforced in CI. Critical modules are: `execution/`, `scoring/`, `pnl/`, `feature_engine/`, `db/`, `exchange_adapters/`. See §17.

**N6. No globals, no singletons. Dependency injection via constructors.** Every service composes its dependencies at the edge (`main.py`) and passes them down. No hidden state.

**N7. Hexagonal architecture.** Business logic (strategy, P&L math, scoring) is pure and testable without I/O. Adapters (DB, exchange, message bus) are thin wrappers around the domain.

**N8. Forward-only, tested database migrations.** Alembic is mandatory. Every migration has a `test_migration.py` that runs up, then a round-trip integration test where possible.

**N9. Configurable anything that is not an invariant.** Fee rates, SL/TP percentages, polling intervals, retention periods, rule weights — all in YAML or env, never hardcoded.

**N10. Known hazards of v1 must not recur.** Section 20 lists 25+ hazards from the v1 system. New code must address each applicable hazard by design, not by defensive patch after the fact.

---

## 2. Architecture Overview

### 2.1 High-level diagram

```
                                    TradingView alert (HTTPS)
                                             │
                                             ▼
                                   Cloudflare Tunnel
                                             │
                                             ▼
                              ┌──────────────────────────────┐
                              │  signal-gateway              │
                              │  (FastAPI, HMAC validation)  │
                              └──────────────┬───────────────┘
                                             │ publish
                                             ▼
               ┌─────────────────── NATS JetStream ───────────────────┐
               │                                                       │
               │  Streams (persistent):                                │
               │    SIGNALS        : signals.raw, signals.validated   │
               │    ORDERS         : orders.requests, orders.events   │
               │    MARKET         : market.ticks.*, market.ohlc.*    │
               │    FEATURES       : features.updated.*               │
               │    AUDIT          : audit.events                     │
               │    TRADING        : trading.events                   │
               │    SYSTEM         : system.alerts                    │
               │                                                       │
               │  KV buckets:                                          │
               │    config.runtime   (hot config, per bot)             │
               │    rate_limits      (shared across Bybit adapters)    │
               │    feature_latest   (per-symbol latest feature vals)  │
               └───┬──────┬──────┬──────┬──────┬──────────────────────┘
                   │      │      │      │      │
                   ▼      ▼      ▼      ▼      ▼
        ┌──────────────┐┌──────────────┐┌───────────────┐┌──────────────┐
        │ market-data  ││ feature-     ││ strategy-     ││ execution-   │
        │ -svc         ││ engine       ││ engine (botN) ││ service      │
        │              ││              ││ (N processes) ││              │
        │ Binance WS   ││ Computes     ││ Consumes      ││ BybitV5 /    │
        │ ticks → PG   ││ indicators   ││ signals,      ││ PaperExchange│
        │ OHLC, feeds  ││ on candle    ││ scores them,  ││ adapters;    │
        │ to feature-  ││ close;       ││ publishes     ││ lifecycle    │
        │ engine       ││ publishes    ││ orders.       ││ FSM, SL/TP/  │
        │              ││ features     ││ requests      ││ BE/trail     │
        └──────┬───────┘└──────┬───────┘└───────┬───────┘└──────┬───────┘
               │               │                │               │
               └───────────────┴────────────────┴───────────────┘
                                       │
                                       ▼
                          ┌────────────────────────┐
                          │ PostgreSQL 16           │
                          │ + TimescaleDB 2.x       │
                          │ (on /mnt/data)          │
                          └────────────┬────────────┘
                                       │
                                       ▼ (read)
                          ┌────────────────────────┐        ┌──────────────┐
                          │ analytics-api          │◀──SSE──│ Dashboard UI │
                          │ (FastAPI, read-only    │  REST  │ (React/Vite) │
                          │  with write endpoints  │───────▶│              │
                          │  for admin ops)        │        └──────────────┘
                          └────────────────────────┘

                          ┌────────────────────────┐
                          │ alerting-svc           │───────▶ Telegram Bot API
                          │ (consumes system.alerts│
                          │  and trading.events)   │
                          └────────────────────────┘

Cross-cutting:
  - Prometheus scrapes every service's /metrics
  - Grafana dashboards for ops/infra only
  - JSON log files per service, rotated daily
```

### 2.2 Service inventory

| Service | Language | Responsibility | Runtime |
|---|---|---|---|
| `signal-gateway` | Python (FastAPI) | Webhook ingress, HMAC, schema validation, publish to NATS | 1 instance |
| `market-data-svc` | Python | Binance WS → ticks/OHLC → DB + NATS | 1 instance |
| `feature-engine` | Python | Consume OHLC, compute indicators on candle close, publish features | 1 instance |
| `strategy-engine` | Python | Per-bot decision process: signal + features → scored decision → order request | N instances (one per bot) |
| `execution-service` | Python | Exchange adapter pool, order placement, lifecycle FSM, reconciliation | 1 instance, multiplexes bots |
| `analytics-api` | Python (FastAPI) | Read-only PG queries, SSE streaming, admin write endpoints | 1 instance |
| `alerting-svc` | Python | Consume NATS events, format, send to Telegram | 1 instance |
| `dashboard-ui` | TypeScript (React + Vite) | SPA, served by nginx | static |

Infrastructure: PostgreSQL + TimescaleDB, NATS JetStream, Prometheus, Grafana, nginx (reverse proxy, UI static), Cloudflare Tunnel, Loki (optional log aggregation, deferred).

### 2.3 Why these service boundaries

- **signal-gateway is tiny and stable.** It rarely changes, must be highly available (webhooks are real-time), and never blocks on downstream. Isolating it means a bug in strategy code cannot drop a signal.
- **market-data-svc is one process, one WS connection** to Binance. Fan-out happens via NATS. This avoids duplicate subscriptions.
- **feature-engine is separated from strategy-engine** because features are shared across bots. Computing `ind.btcusdt.15m.ema_20` once is correct; computing it N times (per bot) is waste and a source of drift.
- **strategy-engine is N processes**, one per bot. Each bot has its own config, consumer group, and state. A crash in one bot does not affect others.
- **execution-service is one process** that holds the exchange adapter pool and performs the cross-bot rate-limiting. Bots request orders via NATS; execution-service dispatches.
- **analytics-api is read-heavy**; separating it keeps dashboard load off the write path.

### 2.4 Data flow: happy path

A TradingView alert arrives for BTCUSDT LONG:

1. `signal-gateway` receives POST `/webhook`, validates HMAC, parses payload, publishes `signals.raw`.
2. `signal-gateway` also runs canonical normalization (symbol mapping, schema validation), publishes `signals.validated`.
3. Every active bot's `strategy-engine` consumes `signals.validated` via its own consumer group.
4. Each `strategy-engine`:
   - Looks up current feature values for the symbol (from NATS KV `feature_latest`, fallback to PG).
   - Evaluates its scoring config (YAML) against the signal and features.
   - If the score passes the threshold, publishes `orders.requests` with bot identity, symbol, side, sizing, desired SL/TP.
   - If not, writes a `scoring_evaluations` row (rejection with full per-rule audit) and publishes `signals.rejected` for shadow tracking.
5. `execution-service` consumes `orders.requests`, uses the bot's exchange adapter to place the order, then manages the lifecycle. It emits `orders.events` for every fill, SL move, close, etc.
6. `strategy-engine` consumes `orders.events` to update its own view of its positions (it never calls exchange APIs directly).
7. `analytics-api` subscribes to all event streams and streams updates to the dashboard via SSE.
8. `alerting-svc` subscribes to `system.alerts` and critical `trading.events`, forwards to Telegram.

### 2.5 Data flow: paper mode

If the bot's config has `exchange.mode: paper`, `execution-service` routes requests to the `PaperExchange` adapter instead of Bybit. The `PaperExchange` adapter reads the same `market.ticks.*` stream, simulates fills with a configurable slippage model, and emits `orders.events` on the same subject. Downstream services (strategy-engine, analytics-api) cannot distinguish paper from live.

### 2.6 Data flow: backtest

Backtest is "paper mode replaying historical OHLC instead of live ticks":

1. A `backtest-runner` CLI command takes a bot config, a date range, and optional config overrides.
2. It spawns a dedicated `strategy-engine` and `execution-service` pair wired to a `ReplayBus` (NATS-compatible in-memory stream) and a `PaperExchange` fed by historical OHLC from TimescaleDB.
3. Signals from the historical `signals` table are replayed chronologically. Feature values are either snapshotted from the `scoring_evaluations` table (for fast replay with the same features as live) or recomputed from OHLC history (for new features).
4. Results are written to `backtest_runs` and `backtest_trades` tables, tagged with the run UUID.

---

## 3. Technology Stack

### 3.1 Summary

| Layer | Choice |
|---|---|
| Language (services) | Python 3.12 |
| Language (UI) | TypeScript 5.x |
| Package manager (Python) | `uv` |
| Package manager (JS) | `pnpm` |
| Event loop | `uvloop` |
| HTTP server | FastAPI + Uvicorn |
| DB driver | `asyncpg` |
| DB migrations | Alembic |
| ORM | SQLAlchemy Core for dynamic SQL; raw SQL for hot paths; no ORM on write path |
| Validation | Pydantic v2 |
| HTTP client | httpx |
| WebSocket client | `websockets` (async) |
| Message bus | NATS JetStream (via `nats-py`) |
| Database | PostgreSQL 16 + TimescaleDB 2.x |
| Backup | pgBackRest (local, no off-server in v1) |
| Scheduler | APScheduler (in-process), no system cron |
| Logging | `structlog` with JSONRenderer, `stdlib` integration |
| Metrics | `prometheus-client` |
| Tracing | Deferred (correlation IDs in logs sufficient for v1) |
| Testing | `pytest`, `pytest-asyncio`, `hypothesis`, `testcontainers` |
| Lint/format | `ruff` (format + check) |
| Type check | `mypy --strict` |
| Security lint | `bandit` |
| Dep audit | `pip-audit` |
| Pre-commit | `pre-commit` |
| UI framework | React 18 + Vite + TypeScript |
| UI styling | Tailwind CSS + `shadcn/ui` |
| UI state (server) | TanStack Query |
| UI state (client) | Zustand |
| UI router | TanStack Router |
| UI charts | Recharts (primary), ECharts (for financial candles if needed) |
| UI real-time | Server-Sent Events (SSE) |
| Containerization | Docker, docker-compose |
| Ingress | Cloudflare Tunnel |
| Observability backend | Prometheus + Grafana |
| Alert delivery | Telegram Bot API |

### 3.2 Version pinning

- Python: `python-version = "3.12"` in `pyproject.toml`.
- Node: `"engines": { "node": ">=20 <21" }` in `package.json`.
- All runtime dependencies pinned to exact version in `pyproject.toml` / `pnpm-lock.yaml`.
- Dev dependencies can use compatible-release (`~=`).
- Dependabot enabled, PRs auto-opened weekly for security updates.

### 3.3 Rationale for key choices (brief)

- **NATS JetStream over Redpanda/Kafka:** Smaller operational surface, KV store included, sufficient throughput for our scale (sub-10 bots, <1000 signals/day).
- **TimescaleDB over plain PG:** Hypertables for time-series data (features, ticks, OHLC) give automatic partitioning, compression, and continuous aggregates. Regular PG tables coexist.
- **asyncpg over psycopg:** ~10× throughput for async workloads; we are async-first.
- **Pydantic v2 over v1:** 5-50× faster validation; we validate a lot of messages.
- **structlog over stdlib:** Native structured logging; trivial JSON output.
- **`uv` over poetry/pip:** Faster, reproducible installs; designed for the modern Python ecosystem.

---

## 4. Repository Layout

```
scalper-v2/
├── README.md
├── TASKS.md                         # Current state of work (updated every session)
├── pyproject.toml                   # Root config: ruff, mypy, pytest
├── uv.lock
├── .pre-commit-config.yaml
├── .github/workflows/
│   ├── ci-fast.yml                  # unit + lint + type, runs on every push
│   ├── ci-full.yml                  # + integration (testcontainers), runs on PR
│   └── release.yml                  # builds Docker images on tag
├── compose.yaml                     # Production docker-compose (uses /mnt/data)
├── compose.dev.yaml                 # Dev override (testcontainers-style)
├── .env.example                     # Annotated env template
│
├── docs/
│   ├── adr/                         # Architecture Decision Records (NNNN-title.md)
│   ├── modules/                     # Per-module design docs
│   ├── runbook/                     # Operational runbooks
│   ├── v1/                          # Predecessor documentation for context
│   └── status.md                    # Operator notes to Claude Code
│
├── services/
│   ├── signal_gateway/
│   │   ├── app/                     # Business logic
│   │   ├── tests/
│   │   ├── pyproject.toml
│   │   └── Dockerfile
│   ├── market_data/
│   ├── feature_engine/
│   ├── strategy_engine/
│   ├── execution/
│   ├── analytics_api/
│   └── alerting/
│
├── packages/                        # Shared Python packages (installable)
│   ├── core/                        # Domain types, protocols, constants
│   ├── bus/                         # NATS client wrappers, schemas
│   ├── db/                          # SQLAlchemy models, Alembic setup, query helpers
│   ├── features/                    # Feature protocol + built-in implementations
│   ├── exchange/                    # ExchangeClient protocol + adapters
│   ├── scoring/                     # Rule language, evaluator
│   ├── observability/               # structlog setup, metrics, trace IDs
│   └── config/                      # Pydantic settings models
│
├── plugins/
│   ├── features/                    # User-written feature plugins
│   │   ├── oi_squeeze/
│   │   └── README.md
│   └── rules/                       # User-written scoring rule plugins
│
├── configs/
│   ├── bots/                        # Per-bot YAML configs
│   │   ├── alpha.yaml
│   │   └── beta.yaml
│   ├── features/                    # Feature registration
│   │   └── indicators.yaml
│   ├── symbol_map.yaml              # Binance ↔ Bybit symbol aliases
│   └── plugin_registry.yaml         # Registered plugins with versions
│
├── migrations/                      # Alembic
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│
├── infra/
│   ├── grafana/
│   │   ├── provisioning/
│   │   ├── dashboards/              # JSON dashboards, tested in CI
│   │   └── alerts/
│   ├── prometheus/
│   │   └── prometheus.yml
│   ├── nats/
│   │   └── server.conf
│   └── nginx/
│       └── nginx.conf
│
├── ui/                              # React SPA
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── routes/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── api/
│   │   └── lib/
│   └── tests/
│
├── scripts/
│   ├── backfill_features.py         # Compute historical feature values
│   ├── backup_db.sh                 # pgBackRest wrapper
│   ├── tail_log.py                  # CEST-rendered log viewer
│   ├── backtest.py                  # CLI entry to backtest-runner
│   └── rotate_api_keys.py
│
└── tests/
    ├── e2e/                         # End-to-end against testnet (manual trigger)
    ├── integration/                 # Cross-service, testcontainers
    ├── fixtures/
    └── grafana/                     # Grafana dashboard query tests
```

**Key conventions:**

- Each service under `services/` has its own `pyproject.toml` and `Dockerfile` but shares `packages/` as installable workspace deps (managed by `uv workspaces`).
- `packages/core/` has no dependencies on other internal packages; it defines base types.
- `plugins/` are discovered at startup via entry points registered in `plugin_registry.yaml`.
- Nothing in `plugins/` may import from `services/`; plugins depend on `packages/` only.
- `configs/` is checked in (except secrets); live configs on the server symlink to a `/etc/scalper-v2/configs` deployment path.

---

## 5. Coding Standards

### 5.1 Python formatting and linting

- **Formatter:** `ruff format` (drop-in for black). Line length 100.
- **Linter:** `ruff check` with this config:
  ```toml
  [tool.ruff.lint]
  select = [
      "E", "F", "W",     # pycodestyle + pyflakes
      "I",               # isort
      "B",               # bugbear
      "UP",              # pyupgrade
      "SIM",             # simplify
      "TCH",             # type-checking imports
      "RUF",             # ruff-specific
      "ASYNC",           # async patterns
      "S",               # security (bandit subset)
      "A",               # builtins shadowing
      "RET",             # return patterns
      "PT",              # pytest patterns
  ]
  ignore = ["S101"]  # assert in tests is fine
  ```
- **Type checker:** `mypy --strict` on everything under `services/` and `packages/`. Plugins must also type-check; the exception is where a plugin genuinely needs dynamic typing, in which case a targeted `# type: ignore[...]` is acceptable.
- Exactly one class per file for domain entities; helpers may live alongside.

### 5.2 Imports

- Absolute imports only. No relative imports within a package.
- Imports grouped: stdlib, third-party, first-party (ruff/isort handles this).
- `from __future__ import annotations` at the top of every Python file.

### 5.3 Typing

- Every public function and method has complete type annotations for parameters and return.
- Prefer `collections.abc.Iterable`, `Sequence`, `Mapping` over `list`, `tuple`, `dict` in signatures.
- Use `TypeAlias` for complex types that appear more than twice.
- Pydantic models for everything crossing a service boundary (API input, message payload, config file).
- `dataclasses` for internal domain types (immutable via `frozen=True` when possible).
- `attrs` is acceptable if there is a specific reason (we don't mix; choose per file).
- `Protocol` for ports (e.g., `ExchangeClient`, `Feature`, `ScoringRule`).

### 5.4 Errors

- **Define custom exception classes per module.** Do not raise bare `Exception` or `ValueError` for domain errors.
- Exception hierarchy rooted at `core.errors.ScalperError`.
- Catch exceptions narrowly. Never `except Exception` without a comment explaining why and a re-raise at the end unless the error is truly recoverable.
- Log exceptions with `logger.exception()` (includes traceback) unless the exception is being re-raised.
- Public APIs return `Result`-like objects or raise; they do not mix both for the same error.

### 5.5 Async

- Everything I/O is `async`.
- Never use `asyncio.sleep(0)` as a yield hint; use structured concurrency (`asyncio.TaskGroup`).
- Cancellation: every task must be cancellable. `finally` blocks clean up resources; they do not swallow `CancelledError`.
- Timeouts: every external call has a timeout. No unbounded `await`.
- Blocking calls go through `asyncio.to_thread` with explicit reasoning in a comment.

### 5.6 Dependency injection

- Constructors take their dependencies as typed parameters.
- `main.py` in each service composes the dependency graph and starts the service.
- No module-level state except constants and configured loggers.
- `functools.lru_cache` is acceptable only for pure computations.

### 5.7 Logging

- Use the project logger from `packages.observability`, never `logging.getLogger` directly.
- Log messages are **structured facts**, not prose:
  ```python
  # BAD
  logger.info(f"Placed order for {symbol} with size {qty}")

  # GOOD
  logger.info("order_placed", symbol=symbol, qty=qty, bot_id=bot_id, correlation_id=cid)
  ```
- Never interpolate into the message field. Use keyword arguments.
- Every trading-relevant log line includes `bot_id`, `correlation_id`, and `trace_id`.
- Do not log secrets. `packages.observability` includes a redactor for known secret fields.

### 5.8 Idempotency and retry labels

Every function that performs an external side effect is labeled:

```python
from packages.core.markers import idempotent, non_idempotent

@idempotent
async def set_trading_stop(self, ...) -> None: ...

@non_idempotent
async def place_market_order(self, ...) -> OrderResult: ...
```

These are simple marker decorators that also register the function into a module-level registry. CI includes a check that every method on an `ExchangeClient` implementation is labeled. See §20 hazard H-003.

### 5.9 Comments

- Comments explain **why**, not what. If the code is unclear, rewrite it.
- `# TODO:` comments must have an associated task number in `TASKS.md`: `# TODO(T-042): ...`.
- Docstrings for public APIs only. Internal functions get a brief docstring when the purpose is non-obvious.

### 5.10 Database access

- All queries in hot paths are raw SQL executed via `asyncpg` directly, parameterized.
- Management/admin queries can use SQLAlchemy Core for query composition.
- No ORM (no SQLAlchemy ORM, no Tortoise, no Peewee).
- Every query has an identified owner service; queries are not shared across services.
- Common queries go into `packages/db/queries/{service}.py` with the service as the owner.

### 5.11 Configuration

- All configuration is loaded at startup into a typed Pydantic settings model.
- There is no `os.environ` access in business logic.
- Secrets come from environment variables; they are not in config files.
- The settings model validates at startup; failure to validate prevents the service from starting.

### 5.12 Timestamps

- Internal: `datetime.datetime` with `tzinfo=datetime.UTC`. Never naive.
- Serialization to JSON / SQL: ISO-8601 with explicit `+00:00`.
- Helper `packages.core.time.now_utc()` is the ONLY approved way to get current time. Test overrides inject a fake.
- Do not use `time.time()`, `datetime.datetime.now()`, `datetime.datetime.utcnow()`. CI lint catches these.

### 5.13 Money and quantities

- Prices, quantities, and notional values use `decimal.Decimal`, not `float`, for any operation that is user-visible or persisted.
- Exchange-returned quantity strings are preserved verbatim wherever possible (see hazard H-015).
- Floats are acceptable only for internal numeric features where the loss of precision is irrelevant (e.g., RSI value displayed to user).

### 5.14 Sample module structure

Every service follows this structure:

```
services/execution/
├── app/
│   ├── __init__.py
│   ├── main.py                # Entry point, composes and starts
│   ├── config.py              # Pydantic settings model
│   ├── service.py             # Main class: ExecutionService
│   ├── lifecycle.py           # Position FSM
│   ├── reconciler.py          # P&L audit loop
│   ├── handlers/              # NATS message handlers
│   │   ├── order_requests.py
│   │   └── order_events.py
│   └── adapters/              # Local adapter wiring (not the adapters themselves)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── pyproject.toml
├── Dockerfile
└── README.md                  # Brief: purpose, how to run, how to test
```

---

## 6. Development Workflow and Discipline

This section is the contract between the operator and Claude Code on how work proceeds. Deviation from these rules is treated as a regression.

### 6.1 TASKS.md — the single source of truth

`TASKS.md` at the repo root tracks the state of every task. Its format:

```markdown
# Tasks

## Current Phase: F1 — Data & Signals
Unlocked: 2026-04-20

## In progress

### T-042: Implement NATS JetStream consumer base class
- Spec: §9.3 of CLAUDE_CODE_BRIEF.md; docs/modules/bus-consumer.md
- Started: 2026-04-22
- Branch: feat/T-042-consumer-base
- Blockers: none
- Notes: parked on retry-backoff tuning — see ADR-draft-005

## Done (last 10)
- [x] T-041: Alembic migration for features hypertable (2026-04-21)
- [x] T-040: PG + TimescaleDB docker-compose setup (2026-04-20)
- ...

## Next (do not start without operator approval)
- [ ] T-043: Implement EMA built-in feature
- [ ] T-044: Feature engine orchestrator

## Backlog
- [ ] T-F2-001: ExchangeClient protocol definition
- [ ] T-F2-002: BybitV5Adapter initial skeleton
...

## Parked
- [ ] T-035: Migrate v1 symbol_map — parked 2026-04-19, operator said "defer to F5"
```

Rules:

- Every task has a unique ID. IDs are monotonic: `T-001`, `T-002`, ...
- A task description lists its spec reference (this brief or a module doc), any related ADRs, the branch name once work starts, and any blockers.
- The "Next" list is curated by the operator. Claude Code does not move tasks into "In progress" unilaterally; it proposes and waits for approval.
- "Done" keeps the last ~10 items visible for context; older items go to `TASKS.archive.md`.

### 6.2 Module design documents

Before implementing a new module (anything with a public API), write `docs/modules/{name}.md` with:

```markdown
# Module: feature-engine

## Purpose
One-paragraph description of what this module does and why it exists.

## Public interface
- Class `FeatureEngine` with methods: ...
- Consumed NATS subjects: ...
- Published NATS subjects: ...
- DB tables read: ...
- DB tables written: ...

## Dependencies
- packages/core
- packages/bus
- packages/db
- packages/features (plugin registry)

## Lifecycle
Startup: ...
Shutdown: ...
Restart recovery: ...

## Edge cases
- What if NATS is disconnected?
- What if DB is slow?
- What if a plugin raises?
- ...

## Testing strategy
- Unit tests cover: ...
- Integration tests cover: ...
- Fixtures / fakes needed: ...

## Open questions
(None / Listed with owner)
```

The operator reviews this before implementation begins.

### 6.3 ADRs — Architecture Decision Records

Use ADRs for any decision that crosses module boundaries or deviates from this brief. Format (in `docs/adr/NNNN-title.md`):

```markdown
# ADR-0012: Use NATS KV for cross-bot rate limiting state

Status: accepted
Date: 2026-04-25
Deciders: operator, Claude Code

## Context
The brief specifies shared rate-limit coordination across bots (§11). Options considered:
- Redis
- NATS KV
- PostgreSQL row-level locks

## Decision
Use NATS KV bucket `rate_limits`.

## Rationale
- NATS is already in the stack; no new dependency.
- KV operations are sufficiently fast (<1ms local).
- Avoids adding Redis solely for this purpose.

## Consequences
Positive:
- Single infrastructure component for coordination.
- Simpler deployment.

Negative / trade-offs:
- NATS KV eventual consistency characteristics differ from Redis atomic ops; we must design the limiter to be idempotent under retry.
- If NATS fails, rate limiting fails open (ok, acceptable).

## Alternatives considered
- Redis: would add a dependency for one feature. Rejected.
- PG row locks: contention under concurrent bot bursts. Rejected.

## Follow-up tasks
- T-103: implement limiter
- T-104: chaos test: kill NATS, verify failover behavior
```

Numbering is monotonic. Status evolves: `proposed → accepted | rejected | superseded-by-NNNN`.

### 6.4 Phased delivery

The project is delivered in phases F0 through F5 (§19). Each phase has:

- A set of tasks in `TASKS.md` under "Backlog".
- **Exit criteria**: a list of capabilities that must be demonstrated before the phase closes.
- A phase-exit review, during which the operator verifies exit criteria and explicitly unlocks the next phase.

Claude Code does not work on tasks from a future phase before the current phase is closed. If a future-phase task is genuinely blocking progress in the current phase, raise it to the operator with a proposed resolution (defer, bring forward, or split).

### 6.5 Pull requests

Every change is a PR. PR description template:

```markdown
## Task
T-NNN: brief description

## Summary
One or two sentences on what this PR does.

## Changes
- Added X
- Modified Y
- Removed Z

## Testing
- Unit tests: ... (coverage delta if relevant)
- Integration tests: ...
- Manual validation: ...

## ADRs
- Implements ADR-NNNN (if applicable)

## Checklist
- [ ] CI green (fast + full)
- [ ] Coverage on touched code ≥ 80% (or justified if critical module)
- [ ] Docstrings on public APIs
- [ ] TASKS.md updated
- [ ] No new dependencies, OR: dependency justified in description
- [ ] No hardcoded secrets
- [ ] No skipped tests
```

### 6.6 Session protocol

At the start of each Claude Code session, post this short message:

```
Session start.
Last session ended at: T-NNN (mmm-dd).
Current phase: FX
Open questions for me:
  1. ...
  2. ...
Proposed next task: T-NNN — <brief>.
Proceed?
```

Wait for the operator's "proceed" before starting work.

At the end of each session, post:

```
Session end.
Completed: T-NNN, T-NNN.
In progress: T-NNN (status).
TASKS.md updated. ADRs created: NNNN (if any).
Next session priority: ...
```

### 6.7 Deviations

If you must deviate from this brief (e.g., discovering that a specified approach is infeasible), do not just deviate. Write an ADR proposing the deviation, explain why, wait for operator review. Proceeding with a silent deviation and discovering inconsistencies three tasks later is the pattern this section exists to prevent.

---

## 7. Data Model (PostgreSQL + TimescaleDB)

### 7.1 Principles

- **`bot_id` is a first-class column everywhere that represents per-bot data.** Retrofitting this later is painful.
- **Timestamps are `TIMESTAMPTZ`**, set via `now_utc()` in Python (see §5.12).
- **JSONB for flexible payloads** (signal payloads, score snapshots, event contexts). Indexed with GIN where queryable fields exist.
- **TimescaleDB hypertables** for anything with high insert rate keyed on time: `signals`, `features`, `ticks`, `ohlc_*`, `executions`, `trading_events`, `audit_events`, `pnl_snapshots`.
- **No cascading deletes on business data.** Retention is explicit via `drop_chunks` (TimescaleDB) or scheduled DELETE, logged to `audit_events`.
- **Forward-only migrations.** Dropping a column is fine; changing a column's type requires an ADR.

### 7.2 Core tables (DDL summary)

Full DDL lives in Alembic migrations. This section lists the essential shape of each table. Types are PostgreSQL types.

#### `bots` — bot registry (regular table)

```sql
CREATE TABLE bots (
    bot_id              TEXT PRIMARY KEY,             -- e.g., 'alpha', 'beta'
    display_name        TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    status              TEXT NOT NULL,                -- 'active' | 'paused' | 'archived'
    exchange_mode       TEXT NOT NULL,                -- 'live' | 'testnet' | 'paper'
    config_hash         TEXT NOT NULL,                -- SHA256 of the YAML at last apply
    config_applied_at   TIMESTAMPTZ NOT NULL,
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb
);
```

#### `bot_configs` — versioned config history

```sql
CREATE TABLE bot_configs (
    id                  BIGSERIAL PRIMARY KEY,
    bot_id              TEXT NOT NULL REFERENCES bots(bot_id),
    version             INT NOT NULL,                 -- monotonic per bot
    applied_at          TIMESTAMPTZ NOT NULL,
    applied_by          TEXT NOT NULL,                -- 'operator' | 'api'
    config_yaml         TEXT NOT NULL,                -- raw YAML
    config_hash         TEXT NOT NULL,
    notes               TEXT,
    UNIQUE (bot_id, version)
);
```

#### `signals` — every inbound webhook (TimescaleDB hypertable)

```sql
CREATE TABLE signals (
    id                  BIGSERIAL,
    received_at         TIMESTAMPTZ NOT NULL,
    schema_version      TEXT NOT NULL,
    source              TEXT NOT NULL,                -- e.g., 'tv_rsi_div_v3'
    idempotency_key     TEXT NOT NULL,
    symbol              TEXT NOT NULL,                -- Bybit canonical
    original_symbol     TEXT,                         -- as received, pre-mapping
    action              TEXT NOT NULL,                -- 'LONG' | 'SHORT' | 'CLOSE' | 'CUSTOM'
    payload             JSONB NOT NULL,
    ingestion_status    TEXT NOT NULL,                -- 'validated' | 'duplicate' | 'invalid'
    correlation_id      TEXT NOT NULL,
    PRIMARY KEY (received_at, id)
);
SELECT create_hypertable('signals', 'received_at', chunk_time_interval => interval '7 days');
CREATE UNIQUE INDEX signals_idempotency ON signals (idempotency_key, received_at);
CREATE INDEX signals_symbol_time ON signals (symbol, received_at DESC);
CREATE INDEX signals_payload_gin ON signals USING GIN (payload);
```

#### `features` — named feature values (hypertable)

```sql
CREATE TABLE features (
    feature_name        TEXT NOT NULL,                -- e.g., 'ind.btcusdt.15m.ema_20'
    symbol              TEXT NOT NULL,                -- denormalized for query speed
    computed_at         TIMESTAMPTZ NOT NULL,         -- candle close time or ctx compute time
    value_num           DOUBLE PRECISION,
    value_bool          BOOLEAN,
    value_json          JSONB,
    source_version      TEXT NOT NULL,                -- e.g., 'builtin.ema.v1', 'oi_squeeze.v2'
    PRIMARY KEY (feature_name, symbol, computed_at, source_version)
);
SELECT create_hypertable('features', 'computed_at', chunk_time_interval => interval '7 days');
CREATE INDEX features_latest ON features (feature_name, symbol, computed_at DESC);
```

#### `ohlc_1m` — 1-minute candles (hypertable)

```sql
CREATE TABLE ohlc_1m (
    symbol              TEXT NOT NULL,
    bucket_start        TIMESTAMPTZ NOT NULL,
    open                NUMERIC(30, 12) NOT NULL,
    high                NUMERIC(30, 12) NOT NULL,
    low                 NUMERIC(30, 12) NOT NULL,
    close               NUMERIC(30, 12) NOT NULL,
    volume              NUMERIC(30, 12) NOT NULL,
    source              TEXT NOT NULL,                -- 'binance' | 'bybit'
    PRIMARY KEY (symbol, bucket_start, source)
);
SELECT create_hypertable('ohlc_1m', 'bucket_start', chunk_time_interval => interval '7 days');
```

Higher timeframes (`ohlc_5m`, `ohlc_15m`, `ohlc_1h`, `ohlc_4h`, `ohlc_1d`) are continuous aggregates materialized from `ohlc_1m`:

```sql
CREATE MATERIALIZED VIEW ohlc_15m WITH (timescaledb.continuous) AS
SELECT
    symbol,
    time_bucket('15 minutes', bucket_start) AS bucket_start,
    first(open, bucket_start) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, bucket_start) AS close,
    sum(volume) AS volume,
    source
FROM ohlc_1m
GROUP BY symbol, time_bucket('15 minutes', bucket_start), source;

SELECT add_continuous_aggregate_policy('ohlc_15m',
    start_offset => INTERVAL '1 day',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');
```

#### `orders` — order lifecycle (regular table with TS insert-rate)

```sql
CREATE TABLE orders (
    id                  BIGSERIAL PRIMARY KEY,
    bot_id              TEXT NOT NULL REFERENCES bots(bot_id),
    signal_id           BIGINT,                       -- may be null for manual
    correlation_id      TEXT NOT NULL,
    exchange_order_id   TEXT,                         -- null until placed
    exchange            TEXT NOT NULL,                -- 'bybit' | 'paper'
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,                -- 'buy' | 'sell'
    order_type          TEXT NOT NULL,                -- 'market' | 'limit' | ...
    qty                 NUMERIC(30, 12) NOT NULL,
    price               NUMERIC(30, 12),
    status              TEXT NOT NULL,                -- 'requested' | 'placed' | 'filled' | 'cancelled' | 'rejected' | 'emergency_closed'
    requested_at        TIMESTAMPTZ NOT NULL,
    placed_at           TIMESTAMPTZ,
    filled_at           TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ,
    idempotent          BOOLEAN NOT NULL,
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX orders_bot_status ON orders (bot_id, status);
CREATE INDEX orders_correlation ON orders (correlation_id);
```

#### `trades` — executed trades, one row per open-close cycle

```sql
CREATE TABLE trades (
    id                  BIGSERIAL PRIMARY KEY,
    bot_id              TEXT NOT NULL REFERENCES bots(bot_id),
    signal_id           BIGINT,
    open_order_id       BIGINT NOT NULL REFERENCES orders(id),
    close_order_id      BIGINT,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    entry_price         NUMERIC(30, 12) NOT NULL,
    exit_price          NUMERIC(30, 12),
    qty                 NUMERIC(30, 12) NOT NULL,
    notional_usd        NUMERIC(20, 4) NOT NULL,
    realized_pnl        NUMERIC(20, 4),
    fees_paid           NUMERIC(20, 4),
    close_reason        TEXT,                         -- 'tp' | 'sl' | 'be' | 'trail' | 'manual' | 'emergency' | 'reconcile'
    opened_at           TIMESTAMPTZ NOT NULL,
    closed_at           TIMESTAMPTZ,
    status              TEXT NOT NULL,                -- 'open' | 'closed' | 'error'
    mfe_pct             DOUBLE PRECISION,
    mae_pct             DOUBLE PRECISION,
    confidence_score    DOUBLE PRECISION,
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX trades_bot_status ON trades (bot_id, status);
CREATE INDEX trades_closed_at ON trades (closed_at DESC) WHERE status = 'closed';
```

#### `executions` — per-fill ledger (hypertable)

```sql
CREATE TABLE executions (
    id                  BIGSERIAL,
    exchange_exec_id    TEXT NOT NULL,
    order_id            BIGINT NOT NULL REFERENCES orders(id),
    trade_id            BIGINT REFERENCES trades(id),
    bot_id              TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    price               NUMERIC(30, 12) NOT NULL,
    qty                 NUMERIC(30, 12) NOT NULL,
    fee                 NUMERIC(20, 8) NOT NULL,
    exec_type           TEXT NOT NULL,                -- 'open' | 'partial_tp' | 'sl' | 'trail' | 'close'
    executed_at         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (executed_at, id)
);
SELECT create_hypertable('executions', 'executed_at', chunk_time_interval => interval '7 days');
CREATE UNIQUE INDEX executions_exchange_id ON executions (exchange_exec_id, executed_at);
CREATE INDEX executions_trade ON executions (trade_id);
```

#### `scoring_evaluations` — per-signal, per-rule evaluation audit (hypertable)

```sql
CREATE TABLE scoring_evaluations (
    id                  BIGSERIAL,
    bot_id              TEXT NOT NULL,
    signal_id           BIGINT NOT NULL,
    evaluated_at        TIMESTAMPTZ NOT NULL,
    trigger_threshold   DOUBLE PRECISION NOT NULL,
    total_score         DOUBLE PRECISION NOT NULL,
    decision            TEXT NOT NULL,                -- 'execute' | 'reject' | 'passthrough'
    config_version      INT NOT NULL,
    rule_results        JSONB NOT NULL,               -- [{name, weight, applied_weight, result, error}, ...]
    feature_snapshot    JSONB NOT NULL,               -- name → value at eval time
    correlation_id      TEXT NOT NULL,
    PRIMARY KEY (evaluated_at, id)
);
SELECT create_hypertable('scoring_evaluations', 'evaluated_at', chunk_time_interval => interval '30 days');
CREATE INDEX se_bot_signal ON scoring_evaluations (bot_id, signal_id);
CREATE INDEX se_decision ON scoring_evaluations (decision, evaluated_at DESC);
```

#### `position_state` — live in-flight state per bot-symbol (regular table)

```sql
CREATE TABLE position_state (
    bot_id              TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    trade_id            BIGINT NOT NULL REFERENCES trades(id),
    side                TEXT NOT NULL,
    entry_price         NUMERIC(30, 12) NOT NULL,
    qty                 NUMERIC(30, 12) NOT NULL,
    remaining_qty       NUMERIC(30, 12) NOT NULL,
    sl_price            NUMERIC(30, 12),
    tp_price            NUMERIC(30, 12),
    sl_type             TEXT,                         -- 'protective' | 'be' | 'trail'
    best_price          NUMERIC(30, 12),
    tp_hit              BOOLEAN NOT NULL DEFAULT FALSE,
    trailing_active     BOOLEAN NOT NULL DEFAULT FALSE,
    running_pnl         NUMERIC(20, 4) NOT NULL DEFAULT 0,
    mfe_price           NUMERIC(30, 12),
    mae_price           NUMERIC(30, 12),
    updated_at          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (bot_id, symbol)
);
```

#### `shadow_rejected` — tracking of rejected signals (hypertable)

See §13.

#### `shadow_variants` — parallel rule simulations (hypertable)

See §13.

#### `trading_events` — append-only event stream (hypertable)

```sql
CREATE TABLE trading_events (
    id                  BIGSERIAL,
    occurred_at         TIMESTAMPTZ NOT NULL,
    bot_id              TEXT,
    correlation_id      TEXT,
    event_type          TEXT NOT NULL,                -- 'order_placed' | 'fill' | 'sl_set' | 'sl_move_be' | 'trail_update' | 'close' | 'reconcile_adjust' | ...
    payload             JSONB NOT NULL,
    PRIMARY KEY (occurred_at, id)
);
SELECT create_hypertable('trading_events', 'occurred_at', chunk_time_interval => interval '7 days');
CREATE INDEX te_bot_type ON trading_events (bot_id, event_type, occurred_at DESC);
CREATE INDEX te_correlation ON trading_events (correlation_id);
```

#### `audit_events` — config/admin audit trail (hypertable)

```sql
CREATE TABLE audit_events (
    id                  BIGSERIAL,
    occurred_at         TIMESTAMPTZ NOT NULL,
    actor               TEXT NOT NULL,                -- 'operator' | 'system' | 'bot:alpha' | ...
    action              TEXT NOT NULL,
    entity_type         TEXT NOT NULL,
    entity_id           TEXT NOT NULL,
    before_state        JSONB,
    after_state         JSONB,
    correlation_id      TEXT,
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (occurred_at, id)
);
SELECT create_hypertable('audit_events', 'occurred_at', chunk_time_interval => interval '30 days');
CREATE INDEX ae_entity ON audit_events (entity_type, entity_id, occurred_at DESC);
```

#### `symbol_map` — Binance ↔ Bybit aliases (regular table)

```sql
CREATE TABLE symbol_map (
    input_symbol        TEXT PRIMARY KEY,             -- as received from TV
    canonical_symbol    TEXT NOT NULL,                -- Bybit notation
    exchange_source     TEXT NOT NULL,                -- 'binance' | 'bybit' | 'custom'
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL
);
```

#### `backtest_runs` — backtest harness runs

```sql
CREATE TABLE backtest_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    config_yaml         TEXT NOT NULL,                -- bot config + overrides
    config_hash         TEXT NOT NULL,
    date_range_start    TIMESTAMPTZ NOT NULL,
    date_range_end      TIMESTAMPTZ NOT NULL,
    status              TEXT NOT NULL,                -- 'running' | 'completed' | 'failed'
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ,
    summary             JSONB,                        -- WR, PF, total trades, etc.
    notes               TEXT
);

CREATE TABLE backtest_trades (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    -- same shape as trades, plus run_id
    ...
);
```

### 7.3 Retention and compression

TimescaleDB policies, per the retention decisions (§18.3 of this brief):

```sql
-- Drop policies
SELECT add_retention_policy('ticks', INTERVAL '7 days');
SELECT add_retention_policy('ohlc_1m', INTERVAL '180 days');
SELECT add_retention_policy('features', INTERVAL '180 days');
SELECT add_retention_policy('system_events', INTERVAL '14 days');
SELECT add_retention_policy('trading_events', INTERVAL '365 days');

-- Compression (keep older data compressed)
ALTER TABLE ohlc_1m SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol, source');
SELECT add_compression_policy('ohlc_1m', INTERVAL '30 days');

ALTER TABLE features SET (timescaledb.compress, timescaledb.compress_segmentby = 'feature_name, symbol');
SELECT add_compression_policy('features', INTERVAL '30 days');
```

Trades, signals, executions, audit_events have no retention policy in v1 (kept forever). If disk pressure builds, the operator can add policies later.

### 7.4 Migrations discipline

- `alembic revision --autogenerate` is allowed but the generated diff **must** be reviewed; autogeneration misses things.
- Every migration has an accompanying `test_NNN_migration.py` that runs up on an empty DB and verifies the new schema objects exist.
- Destructive migrations (column drop, type change) require an ADR and a data-migration plan.
- Migrations do not contain data changes except seed data (symbol map defaults, plugin registry bootstrap). Data migrations are separate, idempotent scripts under `scripts/data-migrations/`.

---

## 8. Message Contracts (NATS JetStream)

### 8.1 Stream and subject naming

```
signals.raw                              # as received, before validation
signals.validated                        # after schema + symbol mapping
signals.rejected.<bot_id>                # rejected by a bot's scoring; used for shadow tracking

orders.requests.<bot_id>                 # strategy → execution
orders.events.<bot_id>                   # execution → all listeners (strategy, analytics, alerts)

market.ticks.<exchange>.<symbol>         # high-volume, short retention
market.ohlc.<interval>.<symbol>          # on candle close, per interval
market.status.<exchange>                 # WS up/down, reconnects

features.updated.<feature_name>.<symbol> # new feature value available

audit.events                             # config changes, admin actions
trading.events                           # order-lifecycle events (same data as orders.events but persisted)
system.alerts                            # alert-worthy events for alerting-svc
```

### 8.2 Stream configuration

Defined in `infra/nats/streams.yaml`:

```yaml
streams:
  - name: SIGNALS
    subjects: [signals.raw, signals.validated, "signals.rejected.>"]
    retention: limits
    max_age: 7d
    replicas: 1

  - name: ORDERS
    subjects: ["orders.requests.>", "orders.events.>"]
    retention: limits
    max_age: 30d
    replicas: 1

  - name: MARKET_TICKS
    subjects: ["market.ticks.>"]
    retention: limits
    max_age: 1h
    replicas: 1

  - name: MARKET_OHLC
    subjects: ["market.ohlc.>"]
    retention: limits
    max_age: 7d
    replicas: 1

  - name: FEATURES
    subjects: ["features.updated.>"]
    retention: limits
    max_age: 7d
    replicas: 1

  - name: AUDIT
    subjects: [audit.events]
    retention: limits
    max_age: 365d
    replicas: 1

  - name: TRADING_EVENTS
    subjects: [trading.events]
    retention: limits
    max_age: 365d
    replicas: 1

  - name: ALERTS
    subjects: [system.alerts]
    retention: limits
    max_age: 90d
    replicas: 1

kv_buckets:
  - name: config_runtime
    ttl: 0
  - name: rate_limits
    ttl: 10s
  - name: feature_latest
    ttl: 0
```

### 8.3 Message envelope

Every NATS message is Pydantic-serialized JSON with this top-level envelope:

```python
class MessageEnvelope(BaseModel):
    schema_version: str                  # e.g., "1.0"
    message_id: UUID                     # unique per publish
    correlation_id: str                  # links signal → orders → events
    published_at: datetime               # UTC
    publisher: str                       # service name, e.g., "signal-gateway"
    payload: dict                        # message-type-specific
```

Concrete payload types are Pydantic models under `packages/bus/schemas/`.

### 8.4 Key message schemas

#### `signals.validated`

```python
class SignalValidated(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    source: str                          # 'tv_rsi_div_v3'
    idempotency_key: str
    received_at: datetime
    symbol: str                          # Bybit canonical
    original_symbol: str
    action: Literal["LONG", "SHORT", "CLOSE"]
    expires_at: datetime                 # received_at + signal_ttl (default 120s)
    payload: dict                        # free-form, per-source
```

#### `orders.requests`

```python
class OrderRequest(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    bot_id: str
    signal_id: int
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["market"]        # v1 supports market only
    qty: Decimal
    leverage: int
    sl_pct: Decimal
    tp_pct: Decimal
    tp_qty_pct: Decimal
    be_trigger: Decimal
    be_sl_level: Decimal
    trail_pct: Decimal
    exchange_mode: Literal["live", "testnet", "paper"]
```

#### `orders.events`

```python
class OrderEventBase(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    bot_id: str
    event_type: str                      # discriminator
    order_id: int                        # internal
    exchange_order_id: str
    symbol: str
    timestamp: datetime

class OrderPlaced(OrderEventBase): ...
class OrderFilled(OrderEventBase):
    exec_id: str
    price: Decimal
    qty: Decimal
    fee: Decimal
    exec_type: Literal["open", "partial_tp", "sl", "trail", "close"]
class OrderClosed(OrderEventBase):
    realized_pnl: Decimal
    close_reason: str
class SLMoved(OrderEventBase):
    new_sl_price: Decimal
    sl_type: Literal["protective", "be", "trail"]
...
```

#### `market.ohlc.15m.BTCUSDT`

```python
class OHLCCandle(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    symbol: str
    interval: str                        # '1m', '5m', '15m', ...
    bucket_start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: Literal["binance", "bybit"]
    is_closed: bool                      # only closed candles cause feature recompute
```

#### `features.updated.ind.btcusdt.15m.ema_20`

```python
class FeatureUpdate(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    feature_name: str
    symbol: str
    computed_at: datetime
    value_num: Optional[float] = None
    value_bool: Optional[bool] = None
    value_json: Optional[dict] = None
    source_version: str
```

### 8.5 Consumer group conventions

- Each service or bot subscribes with a named, durable consumer.
- Consumer name convention: `<service>-<purpose>` or `<bot_id>-<purpose>`. E.g., `strategy-alpha-signals`, `execution-orders`, `analytics-all`.
- Explicit ack (`ack_explicit`). Messages are acked only after successful processing.
- `max_deliver` set per consumer based on idempotency of handling.
- Dead-letter handling: after `max_deliver` retries, message is published to `deadletter.<stream>` and an alert is raised.

### 8.6 Schema evolution

- Backward-compatible changes (add optional field) bump the schema version's minor. Old consumers tolerate unknown fields.
- Breaking changes require a new schema version and a migration period where both are published in parallel. ADR required.

---

## 9. Service Specifications

Each subsection describes one service: purpose, interfaces, internal structure, key algorithms, and test requirements. See per-module design docs under `docs/modules/` for additional detail.

### 9.1 signal-gateway

**Purpose.** Receive TradingView webhooks, validate, normalize, publish to NATS.

**HTTP surface:**
- `POST /webhook` — HMAC-authenticated signal ingest
- `GET /health` — liveness
- `GET /ready` — readiness (NATS reachable, DB reachable)
- `GET /metrics` — Prometheus

**Validation pipeline:**
1. Extract HMAC header (constant-time compare).
2. Parse JSON. Reject unparseable with 400.
3. Rate limit: sliding window 20 req/60s per source IP.
4. Deduplicate: if `idempotency_key` seen within 10 seconds, return 202 with `{"status": "duplicate"}`.
5. Validate against `SignalEnvelope` Pydantic model (required fields: `symbol`, `action`, `source`, `idempotency_key`).
6. Resolve symbol via `symbol_map` query (cached in-process for 60s).
7. Write to `signals` with `ingestion_status='validated'` (or `'invalid'`/`'duplicate'`).
8. Publish to `signals.raw` (audit) and `signals.validated` (for consumers).
9. Respond 200 with signal ID.

**Key design details:**
- The service holds no long-lived state except the symbol-map cache and the idempotency ring.
- HMAC secret per bot, identified by `X-Bot-Signal-Source` header. A single shared secret is also supported for legacy; this is an ADR-0001 topic.
- Response is always < 100ms p99; processing happens inline (no background tasks).

**Tests required before merge:**
- Unit: HMAC validation, rate-limiter behavior, symbol-map cache.
- Unit: payload-validation edge cases (missing fields, wrong types).
- Integration: full webhook → NATS publish path, verifying message shape on a testcontainer NATS.
- Property test: idempotency_key dedup is correct under concurrent inputs.

**Known hazards addressed:** H-006 (webhook rate limit), H-010 (fan-out before dedup — we fan out via NATS, so this is structurally solved).

### 9.2 market-data-svc

**Purpose.** Maintain WebSocket connections to Binance (primary) with reconnect + backfill, normalize to canonical symbol, persist OHLC to `ohlc_1m`, publish `market.ticks.*` and `market.ohlc.*` to NATS.

**Subscriptions:**
- On startup, reads the list of active symbols from `bots` joined with all active bot configs (symbols that appear in any bot's trading universe).
- Subscribes to Binance WS `kline_1m` and `bookTicker` streams for those symbols.
- Subscribes to `orders.events.>` to dynamically track which symbols need price feeds for open positions.
- A reference-counted `SubscriptionManager` controls WS subscribe/unsubscribe, mirroring the v1 `PriceManager` pattern. See H-014.

**OHLC pipeline:**
- Incoming kline messages fall into one of: "in progress" (unchanged), "updated" (same bucket, newer data), "closed" (bucket complete).
- Only closed candles are written to `ohlc_1m` and emit `market.ohlc.1m.<symbol>` with `is_closed=true`.
- Updates to in-progress candles are emitted as non-closed messages for UI live views, but are not persisted.

**Backfill:**
- On startup, for each active symbol, query the most recent `ohlc_1m` row, identify the gap to `now`, call Binance REST `/api/v3/klines` to backfill 1m candles in that gap.
- Idempotent via PK `(symbol, bucket_start, source)`.

**Reconnection:**
- WS disconnect → exponential backoff 1s → 60s with jitter.
- On reconnect, run backfill for the gap, then resubscribe.
- Alert on disconnect > 60s.

**Tests required:**
- Unit: OHLC aggregation correctness under various message orderings.
- Unit: reference counting of subscriptions.
- Integration: simulated WS feed → verify DB rows + NATS messages.
- Integration: disconnect simulation → verify backfill runs on reconnect.

**Known hazards addressed:** H-007 (WS exp backoff), H-009 (execId dedup — this service does not do exec dedup; that is execution-service), H-014 (PriceManager refcount).

### 9.3 feature-engine

**Purpose.** Compute registered features on candle close and publish them.

**Feature registry:**
- Read at startup from `configs/features/indicators.yaml` + `plugin_registry.yaml`.
- Each feature has: `name` template (with `{symbol}`, `{interval}` placeholders), `type` (`builtin.*` or `plugin`), `params`, `source_version`.
- Built-in features live in `packages/features/builtins/`: EMA, SMA, RSI, ATR, Bollinger, VWAP, MACD, OI change, funding rate.
- Plugin features live in `plugins/features/` and register via entry points.

**Computation loop:**
1. Subscribe to `market.ohlc.<interval>.*` for every interval that any feature uses.
2. On closed-candle message, for each feature that applies to this (symbol, interval), compute value.
3. Write to `features` table (INSERT ON CONFLICT DO UPDATE on `(feature_name, symbol, computed_at, source_version)`).
4. Update NATS KV `feature_latest` with key `<feature_name>:<symbol>` → latest value.
5. Publish `features.updated.<feature_name>.<symbol>` for consumers that want pub/sub.

**Feature protocol:**

```python
class Feature(Protocol):
    name_template: str                   # "ind.{symbol}.{interval}.ema_{period}"
    source_version: str                  # "builtin.ema.v1"
    interval: str                        # "15m"
    warmup_candles: int                  # how many history candles needed

    def compute(self, candles: Sequence[OHLCCandle]) -> FeatureValue:
        ...
```

**Warmup:**
- On startup, for each active feature, query the last `warmup_candles + k` rows from `ohlc_*` via continuous aggregates and prime an in-memory rolling buffer.
- For features whose most recent history is older than one interval, run an immediate catch-up compute to fill `features` table.

**Backfill:**
- CLI: `python scripts/backfill_features.py --feature <name> --from <date> --to <date>`.
- Iterates OHLC history, computes, upserts. Idempotent.
- Automatically triggered when a new feature is registered (detected by `plugin_registry.yaml` diff on startup; ADR for this auto-trigger is in F1).

**Tests required:**
- Unit: each built-in feature has deterministic tests on known candle fixtures.
- Unit: feature protocol conformance for plugins.
- Integration: OHLC in → feature persistence → KV + pub/sub correctness.
- Property test: backfill determinism (running twice yields identical `features` rows).

### 9.4 strategy-engine

**Purpose.** Per-bot process that consumes signals, evaluates the scoring config against current feature snapshot, emits order requests or rejections.

**Instances.** One Docker container per active bot, parameterized by `BOT_ID` env var. The container reads `configs/bots/<bot_id>.yaml` at startup (via mounted volume).

**Main loop:**
1. Subscribe to `signals.validated` via consumer group `strategy-<bot_id>-signals`.
2. Subscribe to `orders.events.<bot_id>` for own-bot state updates.
3. For each signal:
   a. Check TTL: if `expires_at < now`, log `signal_expired`, ack, skip.
   b. Check symbol is in bot's trading universe; if not, ack, skip.
   c. Collect feature snapshot: for each rule's `feature:` reference, look up via NATS KV (fast) with DB fallback for missing.
   d. Evaluate scoring rules (§10).
   e. If `decision == execute`: build `OrderRequest` and publish `orders.requests.<bot_id>`.
   f. If `decision == reject`: publish `signals.rejected.<bot_id>` for shadow tracking.
   g. If `decision == passthrough`: same as execute (in v1 scoring mode) but `scoring_evaluations` row records `decision='passthrough'`.
   h. Always write `scoring_evaluations` row before ack.
4. For each own-bot order event, update in-memory view of own positions (used for context features like `ctx.bot.concurrent_positions_count`).

**Config hot-reload:**
- Config is read only at startup in v1. Config change = restart container. (Hot-reload is F5 roadmap.)

**Tests required:**
- Unit: scoring evaluator with various rule combinations.
- Unit: signal TTL enforcement, passthrough mode behavior.
- Integration: full signal → scoring → order-request publish loop on testcontainers.
- Integration: concurrent signals don't corrupt own-position view.

**Known hazards addressed:** H-005 (opposite-signal guard implemented in scoring as a rule), H-008 (signal TTL and expiry).

### 9.5 execution-service

**Purpose.** Single process holding exchange adapter pool. Accepts `orders.requests.<bot_id>`, places orders, manages position lifecycle, emits `orders.events.<bot_id>`. Performs reconciliation and cumulative-delta P&L audit.

**Adapter pool.** Initialized from `bots` table:

```python
adapters: dict[BotId, ExchangeClient] = {
    "alpha": BybitV5Adapter(api_key=..., api_secret=..., sub_account=...),
    "beta": PaperExchange(seed_balance=..., slippage_model=...),
    ...
}
```

Shared rate limiter across Bybit adapters: NATS KV `rate_limits` bucket with token-bucket semantics.

**Order placement pipeline:**
1. Receive `orders.requests.<bot_id>`.
2. Look up adapter for `bot_id`. If missing, emit alert, DLQ.
3. Call `adapter.set_leverage()` (idempotent, cached — see H-002).
4. Call `adapter.place_market_order()` (non-idempotent, no retry — see H-003).
5. On response, fetch fill price via `adapter.get_fill_price()` (idempotent, retry).
6. Call `adapter.set_trading_stop()` for SL (idempotent, 3× retry). On exhaustion: emergency close + DB record with `close_reason='emergency'` — see H-004.
7. Call `adapter.set_trading_stop()` for TP with `tpslMode=Partial` (explicit, not default — see H-013).
8. Persist `orders`, `trades`, `position_state` rows in one transaction.
9. Emit `orders.events.<bot_id>` with `OrderPlaced`, `OrderFilled`.
10. Spawn `PositionLifecycle` task for this trade.

**Position lifecycle (per trade):**
- Monitor loop ticks at `POSITION_POLL_INTERVAL` (default 1s).
- Read latest price (from NATS KV, with REST fallback).
- Update MFE/MAE in `position_state`.
- Check BE trigger: if favorable move ≥ `be_trigger`, move SL to `entry ± be_sl_level`.
- If `trailing_active`, update trail SL.
- Execution events (fills) arrive via private WS (or paper simulator); `_on_execution` dispatcher deduplicates by `exec_id` (ring buffer, size 10k), updates running P&L, marks trailing.
- On `size=0` event, invoke `_close_trade` which runs the **cumulative-delta P&L reconciliation** (H-001, H-002, H-011).

**Cumulative-delta reconciliation (replaces v1 3-pass matching):**
- Before any close flow begins, snapshot `closed_pnl_total` from Bybit via API.
- After close, snapshot again.
- Delta = attributable realized P&L. Apportion to trades in close order.
- Works correctly with partial-TP (multiple closed_pnl rows).
- Works correctly with concurrent closes across bots sharing IP (we snapshot per sub-account).

**Periodic P&L audit loop:**
- Runs every 5 minutes.
- Fetches Bybit closed-pnl for last 3 hours per sub-account.
- Compares to DB `trades.realized_pnl` for trades with matching `(entry_price, qty)` that are closed.
- If delta > $0.50, writes correction to `trade_pnl_deltas` and updates `trades.realized_pnl`.

**Tests required:**
- Unit: FSM state transitions (open → BE → trail → close).
- Unit: cumulative-delta math under partial TP.
- Unit: emergency close on SL-set exhaustion.
- Integration: full order lifecycle against Bybit mock adapter.
- Integration: P&L audit loop catches a divergence.
- Property test: lifecycle invariant "qty_closed + remaining_qty == entry_qty" holds at all times.

**Known hazards addressed:** H-001 through H-015 (most of the v1 hazard catalog).

### 9.6 analytics-api

**Purpose.** Read-heavy API powering the dashboard UI. Exposes REST for paginated queries and SSE for real-time updates. Minimal write surface for admin operations (symbol-map edits, bot pause/resume).

**Endpoint categories:**
- `/api/bots/*` — bot registry, per-bot status
- `/api/positions/*` — live positions across bots
- `/api/trades/*` — trade history, filtering, drill-down
- `/api/signals/*` — signal feed, filtering
- `/api/scoring/*` — per-signal rule evaluations (scoring inspector)
- `/api/features/*` — feature inspector (latest values, historical chart)
- `/api/analytics/*` — aggregates (expectancy, WR, hourly heatmap, etc.)
- `/api/backtests/*` — backtest runs, trigger, status, results
- `/api/configs/*` — bot config view, upload, validate, apply
- `/api/audit/*` — audit log viewer
- `/api/symbol-map/*` — CRUD (admin)
- `/events/stream` — SSE for real-time updates

**SSE streams:**
- Multiplexed: one connection receives all event types the client subscribed to.
- Subscribes by query params: `?types=positions,signals,trades`.
- Each event is `data: {"type": "position_update", "payload": {...}}`.

**Caching:**
- Most endpoints are direct PG queries with no cache; PG handles load at this scale.
- Monte-Carlo and other CPU-heavy analytics run via `asyncio.to_thread` with in-memory 5-min cache.

**Tests required:**
- Unit: request validation, response shape.
- Integration: each endpoint on a testcontainer PG with seeded data.
- Load test: dashboard polling with 5 concurrent clients — deferred to phase exit checks.

### 9.7 alerting-svc

**Purpose.** Consume `system.alerts` and critical `trading.events`, format per category, deliver to Telegram.

**Channels (Telegram chat topics):**
- `system` — heartbeats, WS disconnects, DB errors, consumer lag
- `trading` — order placed, SL moved, close (optional; configurable per severity)
- `pnl` — P&L audit corrections, reconciliation adjustments
- `security` — auth failures, unexpected admin actions

**Alert rules:**
- Declarative in `configs/alerts.yaml`: subject → severity → channel → template.
- Rate limiting: same alert type deduplicated within a 5-minute window.
- Escalation: critical alerts retried if Telegram API fails.

**Telegram format:**
- CEST timestamps in display.
- Message body: concise, links to dashboard where relevant (if dashboard is public).
- Message rendering uses Jinja2 templates in `configs/alerts/templates/`.

**Tests required:**
- Unit: template rendering for each alert type.
- Unit: rate-limit dedup.
- Integration: NATS event → Telegram API call (mocked).

---

## 10. Feature Store and Scoring Engine

See §9.3 for feature-engine specification. This section covers the scoring engine in detail.

### 10.1 Rule language

Rules are Pydantic models. Each rule has:
- `name`: unique within bot config
- `weight`: float (positive = adds, negative = subtracts)
- `applies_when` (optional): gate conditions on signal context
- `condition`: the actual test
- `on_error`: `"skip"` (default, fail-open) | `"reject"` (fail-closed)
- `required`: if `true`, missing feature data causes rejection
- `max_staleness_sec` (optional): override for feature staleness check

### 10.2 Condition types

Implemented in `packages/scoring/conditions/`:

- `equals`: `feature == value`
- `not_equals`
- `gt`, `gte`, `lt`, `lte`
- `between`: `min ≤ feature ≤ max`
- `in`: `feature in values`
- `ema_stack`: ordered relationship of 3 feature values (direction-aware)
- `rising`: feature increasing over last N samples
- `falling`: feature decreasing over last N samples
- `when_then_else`: conditional branch (reads like a ternary)
- `and`: all subconditions true
- `or`: any subcondition true
- `not`: negation
- `plugin`: delegate to a registered plugin rule

New condition types are added by:
1. Implementing `packages/scoring/conditions/<name>.py` with the `Condition` protocol.
2. Registering in `packages/scoring/registry.py`.
3. Adding Pydantic schema variant to discriminated union.
4. Unit tests.

### 10.3 Feature reference resolution

A rule's `feature:` field uses templated reference:
```
ind.${signal.symbol}.15m.ema_20
```
Resolver:
1. Substitute `${signal.symbol}` with the current signal's symbol (lowercased).
2. Look up in NATS KV `feature_latest` by key.
3. If missing, query DB `features` table for latest row matching `(feature_name, symbol)`.
4. If DB also missing, report `data_missing` status for the rule.

Staleness check: `(now - computed_at).total_seconds() > max_staleness_sec` → status `data_stale`. `max_staleness_sec` default is `2 × interval_seconds` (15m feature: 1800s).

### 10.4 Evaluation pipeline

```python
def evaluate(
    bot_config: BotConfig,
    signal: SignalValidated,
    feature_snapshot: dict[str, FeatureValue],
) -> ScoringResult:
    results = []
    total_score = 0.0
    for rule in bot_config.scoring.rules:
        # applies_when gate
        if rule.applies_when and not matches(rule.applies_when, signal, feature_snapshot):
            results.append(RuleResult(name=rule.name, result="n/a", applied_weight=0))
            continue

        try:
            outcome, error_info = rule.condition.evaluate(signal, feature_snapshot)
        except FeatureMissingError as e:
            if rule.required:
                return ScoringResult(decision="reject", reason="required_feature_missing", ...)
            outcome = "skipped"
            error_info = {"error": str(e)}
        except Exception as e:
            if rule.on_error == "reject":
                return ScoringResult(decision="reject", reason="rule_error", ...)
            outcome = "error_skipped"
            error_info = {"error": repr(e), "traceback": ...}

        applied_weight = rule.weight if outcome is True else 0.0
        total_score += applied_weight
        results.append(RuleResult(
            name=rule.name,
            result=str(outcome),
            weight=rule.weight,
            applied_weight=applied_weight,
            error=error_info,
        ))

    # v1 mode: passthrough
    if bot_config.scoring.mode == "passthrough":
        decision = "passthrough"  # execute anyway
    elif total_score >= bot_config.scoring.trigger_threshold:
        decision = "execute"
    else:
        decision = "reject"

    return ScoringResult(
        decision=decision,
        total_score=total_score,
        threshold=bot_config.scoring.trigger_threshold,
        rule_results=results,
        feature_snapshot=feature_snapshot_json,
    )
```

### 10.5 Passthrough mode

Configured per bot: `scoring.mode: passthrough | active`.

- In `passthrough` mode, the scoring is evaluated normally, `scoring_evaluations` is written with `decision='passthrough'`, but the actual decision is always `execute`. This gives a clean dataset for classifier training.
- In `active` mode, `total_score < threshold` causes `reject`.

This is a runtime config change; switching modes does not require code changes.

### 10.6 Custom plugin rules

Plugin rule example: `plugins/rules/oi_squeeze/`:

```python
from packages.scoring.protocol import Rule, RuleContext, RuleOutcome

class OISqueezeRule(Rule):
    name = "oi_squeeze"
    version = "2"

    def __init__(self, params: dict):
        self.lookback_candles = params["lookback_candles"]
        self.oi_drop_pct = params["oi_drop_pct"]

    def evaluate(self, ctx: RuleContext) -> RuleOutcome:
        # ... custom logic
        return RuleOutcome(result=True, metadata={...})
```

Registration in `plugin_registry.yaml`:

```yaml
rules:
  - name: oi_squeeze
    version: 2
    entry_point: plugins.rules.oi_squeeze:OISqueezeRule
```

### 10.7 Tests required

- Unit per condition type (strict equivalence with spec).
- Unit for evaluator pipeline under all combinations: success, fail-open, fail-closed, data_missing with and without `required`, data_stale.
- Property test: total score equals sum of applied weights for satisfied rules only.
- Property test: passthrough mode always returns `decision='passthrough'` but score matches active-mode computation.

---

## 11. Execution Layer and Exchange Adapters

### 11.1 ExchangeClient protocol

```python
class ExchangeClient(Protocol):
    @idempotent
    async def set_leverage(self, symbol: str, leverage: int) -> None: ...

    @non_idempotent
    async def place_market_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: Decimal,
        reduce_only: bool = False,
    ) -> OrderPlaceResult: ...

    @idempotent
    async def set_trading_stop(
        self,
        symbol: str,
        sl_price: Optional[Decimal] = None,
        tp_price: Optional[Decimal] = None,
        tp_size: Optional[Decimal] = None,
        tpsl_mode: Literal["Full", "Partial"] = "Full",
    ) -> None: ...

    @idempotent
    async def cancel_order(self, symbol: str, order_id: str) -> None: ...

    @idempotent
    async def get_positions(
        self, symbol: Optional[str] = None
    ) -> list[Position]: ...

    @idempotent
    async def get_fill_price(
        self, symbol: str, order_id: str
    ) -> Optional[Decimal]: ...

    @idempotent
    async def get_closed_pnl_cumulative(
        self, sub_account: str
    ) -> Decimal: ...

    async def stream_executions(self) -> AsyncIterator[ExecutionEvent]: ...
    async def stream_positions(self) -> AsyncIterator[PositionEvent]: ...

    async def close(self) -> None: ...
```

### 11.2 BybitV5Adapter

- Uses Bybit V5 REST + private WS.
- Signing: HMAC-SHA256 per existing v1 implementation (port, do not rewrite from scratch).
- Rate limiting: cross-adapter via NATS KV; per-endpoint budgets.
- Retry matrix:
  - `set_leverage`, `set_trading_stop`, `cancel_order`, `get_positions`, `get_fill_price`, `get_closed_pnl_cumulative`: 3× with backoff `[0.5, 1.0, 2.0]s + jitter`.
  - `place_market_order`: no retry; timeout is `unknown` status, upper layer reconciles.
- WS: exp backoff 1s → 60s, auth re-handshake on reconnect, execution dedup via `execId` ring.

### 11.3 Error taxonomy

```python
class ExchangeError(ScalperError): ...
class RateLimitError(ExchangeError): ...           # retCode 10006/10016
class AuthError(ExchangeError): ...
class OrderRejected(ExchangeError):
    reason: str                                     # 'insufficient_margin', 'price_deviation', ...
class NetworkTimeout(ExchangeError): ...
class UnknownState(ExchangeError):                 # place_market_order timeout
    last_known_action: str
```

Upper layer (`execution-service`) maps these to decisions: retry, abort, reconcile on restart.

### 11.4 Shared rate limiter

- Token bucket in NATS KV `rate_limits`.
- Keys: `bybit:<sub_account>:orders`, `bybit:<sub_account>:positions`, `bybit:ip:global`.
- Each call debits one token; refills per Bybit documented limits.
- Coordinated backoff: on `RateLimitError`, all adapters on the same IP receive a 500ms pause flag published to KV.

### 11.5 PaperExchange

See §12.

### 11.6 Adapter tests

- Unit: signing, retry policy behavior per method, WS event dispatch.
- Integration: against Bybit testnet via E2E suite (not CI; manual).
- Contract: adapter protocol conformance test — every adapter implements every method with correct idempotency marker.

---

## 12. Paper Exchange and Backtest Harness

### 12.1 PaperExchange adapter

Implements `ExchangeClient`. State lives in DB tables `paper_positions`, `paper_orders`, `paper_executions` (mirror the shape of their live counterparts), keyed by `bot_id` and `exchange='paper'`.

**Fill semantics:**
- Market order: fill price = last observed tick price + slippage per `slippage_model`.
- SL/TP: monitored per-tick; when price crosses, fill at trigger price + slippage.
- Slippage models: `fixed_pct`, `proportional_to_qty`, `half_spread`. Configured per bot.

**Fee semantics:**
- Fees charged at the configured `fee_rate` (same config as live bot).
- Fees deducted at fill time, same as live.

**Execution emission:**
- Fills emit execution events on the same WS-like async iterator; the consumer does not distinguish.

**Persistence:**
- State persisted to DB, so paper bot can restart and recover.

### 12.2 Backtest harness

CLI: `python scripts/backtest.py --bot <id> --from <date> --to <date> [--override 'path.to.field=value' ...]`

**Components:**
- `ReplayBus`: in-process NATS-compatible publish/subscribe; messages delivered in timestamp order.
- `HistoricalOHLCSource`: reads from TimescaleDB, replays candles at configurable pace (1x, 10x, max).
- `HistoricalSignalSource`: reads `signals` table for the bot's symbol universe, replays chronologically.
- `PaperExchange`: as in 12.1, but wired to `HistoricalOHLCSource` for tick prices.
- `strategy-engine` and `execution-service`: reused unchanged; wired to `ReplayBus` instead of live NATS.

**Intra-candle tick generation:**
- To simulate SL/TP crossings between 1m candles, each candle generates a deterministic intra-candle path: O → (toward high first if close > open else toward low first) → extreme → other extreme → C.
- This matches TradingView "Replay" behavior and is deterministic for reproducibility.

**Results:**
- Each run creates `backtest_runs` row.
- All generated trades in `backtest_trades`, linked by `run_id`.
- Summary statistics (total trades, WR, P&L, PF, MDD) computed and persisted to `backtest_runs.summary`.

**Comparison mode:**
- `python scripts/backtest.py --compare run_A_uuid run_B_uuid`: outputs a diff of aggregate metrics and a per-trade diff where the same signal produced different outcomes.

**Tests required:**
- Unit: replay determinism (same input → same output).
- Unit: intra-candle path generation.
- Integration: full backtest on a 1-week seeded dataset.

---

## 13. Shadow Variants

### 13.1 Purpose

For every accepted trade, run N parallel simulations with alternative SL/TP/BE/trail parameters. Measure what the alternate rule would have produced. Use `PaperExchange` engine internally; this is not a separate implementation.

### 13.2 Configuration

Per bot:

```yaml
shadow:
  enabled: true
  variants:
    - name: baseline                    # inherits execution config
    - name: no_be
      overrides: { be_trigger: 0 }
    - name: full_tp
      overrides: { tp_qty_pct: 1.0, trail_pct: 0 }
    - name: sl_tight
      overrides: { sl_pct: 0.005 }
    - name: sl_wide
      overrides: { sl_pct: 0.015 }
  max_duration_hours: 4
```

### 13.3 Runtime

- When `execution-service` opens a trade, it also publishes `shadow.start.<bot_id>` with the variant specs.
- A `shadow-worker` (part of execution-service in v1, separated later if needed) spawns per-variant simulations using a lightweight version of the position FSM running against a `PaperExchange` instance seeded with the live entry.
- Variants subscribe to `market.ticks.<symbol>` (no exchange writes).
- Each variant persists results to `shadow_variants` table with terminal outcome: `sl_hit`, `be_hit`, `tp_trail`, `tp_full`, `timeout`.

### 13.4 Restart recovery via OHLC replay

- On restart, for each pending variant, query `ohlc_1m` from `created_at` to `now`.
- Replay using the same `_step` function as live.
- If terminal outcome fires during replay → finalize.
- Otherwise → resume from the resulting state.
- No more `lost_on_restart` (H-023).

### 13.5 Rejected-signal shadow tracking

Separate from variants: when a signal is **rejected** by scoring, a 60-minute observation task records MFE/MAE and a terminal label (`would_tp`, `would_sl`, `would_be`, `no_trigger`). Persisted to `shadow_rejected`.

Restart recovery: **also via OHLC replay** in v2 (unlike v1). No `lost_on_restart` state.

### 13.6 Dashboard integration

- Per-trade drill-down shows all 5 variants alongside the live outcome.
- Per-symbol aggregate: "which variant would have been best over last N trades?"
- Per-rejected-signal explorer: "what would rejected signals have yielded?"

### 13.7 Tests required

- Unit: variant `_step` transitions match live lifecycle FSM.
- Unit: replay determinism.
- Unit: intra-candle path equivalence between live and replay.
- Integration: full variant lifecycle under testcontainers with simulated ticks.

---

## 14. Dashboard UI Specification

### 14.1 Technology

- React 18 + Vite + TypeScript (strict mode).
- Tailwind CSS + shadcn/ui (components committed to repo, not NPM deps).
- TanStack Query for server state, TanStack Router for routing, Zustand for local UI state.
- Recharts for standard charts. ECharts for candlestick where needed.
- SSE for real-time updates via `EventSource` or `fetch`-based SSE polyfill.

### 14.2 Layout

- Left nav with sections, main content area.
- Top bar: bot selector (multi-select with "all bots" default), time range picker, CEST/UTC toggle, connection status indicator.
- Dark mode first, light toggle optional.
- No mobile responsiveness in v1; desktop only.

### 14.3 Sections (all in MVP)

1. **Overview** — cross-bot dashboard. Tiles: total open positions, aggregate virtual balance, 24h P&L, signals received/accepted/rejected, alert count.
2. **Per-bot live view** — for the selected bot: open positions table (symbol, side, entry, current, unrealized P&L, SL, TP, running MFE/MAE), live signals feed (last 50), P&L chart.
3. **Trade explorer** — filterable/paginated trade list. Click a trade → drill-down with full timeline: signal details → scoring breakdown → order events → fills → SL moves → close → shadow variants comparison → post-close price snapshots.
4. **Backtest lab** — list of backtest runs, "new run" form (pick bot config, date range, overrides), status, results, comparison view.
5. **Strategy editor** — YAML editor for bot config with live Pydantic validation, diff-against-live, apply (creates a new `bot_configs` version).
6. **Feature inspector** — feature browser: filter by name prefix, select a feature + symbol → chart of historical values, current stale/fresh status.
7. **Scoring inspector** — per-signal view: select signal → full rule-by-rule evaluation with weights, feature snapshot, final decision.
8. **Audit log viewer** — chronological audit_events table with filters.
9. **Settings** — bot registry, symbol map CRUD, plugin registry read-only view, API key status (present/absent, never values).

### 14.4 Component library

Build a small internal component library on top of shadcn/ui:

- `DataTable` with built-in pagination, sorting, filtering, column visibility.
- `TimeRangePicker` with presets (1h, 24h, 7d, 30d, custom).
- `BotSelector` — single or multi-select.
- `StatusBadge` — bot status, order status, signal status with standard color semantics.
- `PriceDelta` — formatted price change with sign-colored.
- `CorrelationIdChip` — clickable chip that filters to all events with the same correlation ID.

### 14.5 Theming

All colors via CSS variables in `ui/src/styles/theme.css`. Changing the palette is a one-file edit.

### 14.6 Tests required

- Unit: component tests for reusable components.
- Snapshot tests for key pages.
- Playwright E2E: critical user journeys (open app, select bot, view trade drill-down). Run on main branch merges.

---

## 15. Observability

### 15.1 Three log streams

Every service emits to stdout in JSON Lines (captured by Docker's log driver, rotated via `logrotate` on host `/var/log/scalper/`).

Log routing by stream is by a `log_stream` field on every record. A log collector (simple Python `logrotate_and_split.py` or Loki + Promtail in the future) splits by stream into:

- `/var/log/scalper/trading.log` — `log_stream: trading`
- `/var/log/scalper/audit.log` — `log_stream: audit`
- `/var/log/scalper/system.log` — `log_stream: system`

Every log record contains:

```
timestamp          ISO-8601 UTC
level              DEBUG | INFO | WARNING | ERROR | CRITICAL
service            signal-gateway, execution, ...
log_stream         trading | audit | system
bot_id             optional
correlation_id     optional
trace_id           always present (service-generated UUID per request/task)
event              short machine-readable event name (snake_case)
message            human-readable summary
... typed fields specific to the event ...
```

### 15.2 Correlation IDs

- Generated at signal ingest (signal-gateway) per incoming webhook.
- Propagated through NATS message envelopes.
- Logged with every event downstream.
- Used as the primary join key for post-hoc debugging.

### 15.3 Metrics

Prometheus `/metrics` endpoint on every service. Standard metrics:

**Counters:**
- `signals_received_total{source}`
- `signals_validated_total{status}`
- `signals_rejected_total{bot_id, reason}`
- `orders_placed_total{bot_id, exchange}`
- `orders_filled_total{bot_id, exec_type}`
- `errors_total{service, error_class}`
- `rate_limit_hits_total{exchange, endpoint_group}`

**Histograms:**
- `webhook_processing_seconds`
- `signal_to_order_seconds`
- `order_placement_seconds`
- `sl_set_seconds`
- `ws_event_lag_seconds`
- `scoring_evaluation_seconds`
- `feature_compute_seconds{feature}`

**Gauges:**
- `open_positions{bot_id}`
- `virtual_balance{bot_id}` → promoted to mandatory T-531 (T-523 reorg 2026-05-08; per ADR-0011)
- `ws_connected{stream}`
- `nats_consumer_pending{consumer}`
- `db_pool_saturation{service}`

### 15.4 Grafana

Located at `infra/grafana/`. Provisioned with:
- Datasources (fixed UIDs: `ds_prom`, `ds_ts_main`).
- Dashboards, each committed as JSON. Tested in CI via `tests/grafana/test_dashboards.py`.

Dashboards (ops focus):
- Service health overview
- NATS consumer lag and stream sizes
- PG health (connections, slow queries, replication lag if configured)
- Host metrics via node-exporter

Trading dashboards live in the custom UI, not Grafana.

### 15.5 Alerting

Alertmanager + Telegram route, OR Grafana Alerting → Telegram directly. Evaluation: ADR for final choice in F0.

Standard alerts:
- Heartbeat stale (>2 min) — per service
- WS disconnected >60s — market-data-svc or execution-service
- `errors_total` rate > N/min for any service
- `orders_filled_total` stalls for > 15 min while `signals_validated_total` is accruing
- P&L audit correction |Δ| > $10
- Consumer lag > 100 messages

### 15.6 Structured audit log

Every config change, every admin action, every reconciliation adjustment writes to `audit_events` AND `/var/log/scalper/audit.log`.

Example entry:

```json
{
  "timestamp": "2026-04-25T08:14:03.214+00:00",
  "level": "INFO",
  "service": "analytics-api",
  "log_stream": "audit",
  "trace_id": "a1b2c3d4",
  "event": "bot_config_applied",
  "actor": "operator",
  "bot_id": "alpha",
  "config_version": 7,
  "config_hash": "sha256:...",
  "change_summary": ["scoring.rules.strong_trend.weight: 1.0 → 1.5", "sizing.max_notional_per_symbol.BTCUSDT: 5000 → 6000"]
}
```

### 15.7 CLI log viewer

`scripts/tail_log.py`: tails a JSON log file, renders in CEST, filterable by event, bot, correlation_id. Usage:

```
./scripts/tail_log.py /var/log/scalper/trading.log --bot alpha --event order_placed
```

---

## 16. Security Baseline

### 16.1 Secrets

- Never in code, never in YAML committed to repo.
- Stored in `/etc/scalper-v2/secrets.env`, mode 600, owned by service user.
- Loaded at service startup via env vars.
- Never logged (redactor in `packages.observability`).

### 16.2 Dashboard auth

- Dashboard binds to `0.0.0.0` on LAN, no authentication (per operator decision).
- Consequence: any device on the LAN can reach the dashboard and its admin endpoints.
- Write endpoints (symbol-map CRUD, bot pause/resume, config apply) log every action to `audit_events` with actor `lan:<source_ip>`.
- Future upgrade to Cloudflare Access is a one-file toggle (Cloudflare Access application + middleware that reads `Cf-Access-Authenticated-User-Email`).

### 16.3 Webhook security

- HMAC-SHA256 over `(timestamp, body)` with per-bot or shared secret.
- Replay protection: reject if `|now - timestamp| > 30s`.
- Rate limiting: 20 req/60s per source IP (sliding window).
- HTTPS-only (enforced at Cloudflare Tunnel).

### 16.4 API key management

- Per bot, per sub-account. Never shared.
- Stored in `/etc/scalper-v2/secrets.env` as `BOT_<ID>_BYBIT_API_KEY` / `BOT_<ID>_BYBIT_API_SECRET`.
- Rotation: new key alongside old, apply to bot config, verify, remove old. Procedure in `docs/runbook/key_rotation.md`.

### 16.5 Live-mode safeguard

- Starting a bot with `exchange.mode: live` requires env var `BOT_CONFIRM_LIVE=yes` in the service's environment.
- Startup logs a loud warning `LIVE MODE ENGAGED` and sends a Telegram alert.
- Testnet and paper modes do not require this.

### 16.6 Network

- Only signal-gateway and analytics-api are exposed outward (via Cloudflare Tunnel).
- All other services bind to localhost or the Docker internal network.
- NATS, PG, Prometheus, Grafana: internal-only by default.

### 16.7 Dependencies

- `pip-audit` in CI fails the build on known CVEs above severity 7.0.
- Dependabot enabled for weekly security updates.
- Docker base images pinned to specific digests, updated monthly.

### 16.8 Audit trail

- Every admin action logged to `audit_events` with `actor`, `before_state`, `after_state`.
- Audit log retention: 180 days on disk per operator decision, forever in DB.

---

## 17. Testing Strategy

### 17.1 Test pyramid

- **Unit** — pure functions and isolated classes with mocked collaborators. Fast (<5s per service). Aim for breadth.
- **Integration** — real PostgreSQL + TimescaleDB + NATS via testcontainers. Verify cross-module behavior. Medium speed (30-180s).
- **Contract** — adapter protocol conformance (every ExchangeClient adapter passes the same test suite), schema migration round-trip, NATS message schema compatibility.
- **Property (Hypothesis)** — price math, P&L math, dedup, idempotency of reconciliation.
- **E2E** — against Bybit testnet. Manual trigger (tagged `@slow`, `@e2e`). Not in default CI.

### 17.2 Coverage

- 80% line coverage enforced in CI on these modules: `packages/core/`, `packages/scoring/`, `packages/features/`, `packages/exchange/`, `packages/db/queries/`, `services/execution/app/`, `services/strategy_engine/app/`.
- Report-only elsewhere (no threshold but visible).
- Measured via `pytest-cov --cov=packages --cov=services --cov-fail-under=80 --cov-config=.coveragerc`.

### 17.3 Fixtures

- `conftest.py` per service provides standard fixtures: `pg_testcontainer`, `nats_testcontainer`, `seeded_pg` (with minimum bot/symbol/config rows), `fake_exchange`, `clock_override`.
- `packages/testing/` provides shared fakes: `FakeExchangeClient`, `FakeFeatureEngine`, `FakeMessageBus`.

### 17.4 Test naming

- `test_<unit>_<behavior>.py`, e.g., `test_cumulative_delta_reconciler_partial_tp.py`.
- Function names: `test_<scenario>_<expected>`.

### 17.5 Flakiness

- Zero tolerance. A flaky test is quarantined within one session (added to `FLAKY_TESTS.md`, skipped with a linked issue) and fixed within the next.
- No `@pytest.mark.flaky` retry decorator. Either deterministic or removed.

### 17.6 CI stages

```yaml
# .github/workflows/ci-fast.yml — runs on every push
jobs:
  lint:
    - ruff check
    - ruff format --check
  type:
    - mypy --strict services/ packages/
  unit:
    - pytest -q --ignore=tests/integration -m 'not slow' --cov --cov-fail-under=80

# .github/workflows/ci-full.yml — runs on PR
jobs:
  integration:
    - docker network create ci
    - pytest tests/integration -q
    - pytest tests/grafana -q  # dashboard queries run against Prom/TS
  security:
    - pip-audit
    - bandit -r services/ packages/
```

### 17.7 Required tests per module

Each service's module design doc (§6.2) lists its required tests. No merge without them.

---

## 18. Deployment and Operations

### 18.1 docker-compose

Production `compose.yaml` maps service volumes to `/mnt/data` partition:

```yaml
services:
  postgres:
    image: timescale/timescaledb:2.15-pg16
    volumes:
      - /mnt/data/postgres:/var/lib/postgresql/data
      - /mnt/data/backups/postgres:/backups
    environment:
      POSTGRES_USER: scalper
      POSTGRES_PASSWORD_FILE: /run/secrets/pg_password
      POSTGRES_DB: scalper
    secrets: [pg_password]
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U scalper"]
      interval: 10s

  nats:
    image: nats:2.10-alpine
    command: ["-js", "-sd", "/data", "-c", "/etc/nats/server.conf"]
    volumes:
      - /mnt/data/nats:/data
      - ./infra/nats/server.conf:/etc/nats/server.conf:ro
    restart: unless-stopped

  signal-gateway:
    build: ./services/signal_gateway
    depends_on: [nats, postgres]
    env_file: /etc/scalper-v2/secrets.env
    environment:
      SERVICE_NAME: signal-gateway
      LOG_LEVEL: INFO
    volumes:
      - /var/log/scalper:/var/log/scalper
      - ./configs:/app/configs:ro
    restart: unless-stopped

  # ... market-data, feature-engine, execution, analytics-api, alerting

  strategy-engine-alpha:
    build: ./services/strategy_engine
    env_file: /etc/scalper-v2/secrets.env
    environment:
      BOT_ID: alpha
      BOT_CONFIRM_LIVE: ${BOT_ALPHA_CONFIRM_LIVE}
    # ...

  strategy-engine-beta:
    # same, BOT_ID: beta

  prometheus:
    image: prom/prometheus
    volumes:
      - /mnt/data/prometheus:/prometheus
      - ./infra/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    restart: unless-stopped

  grafana:
    image: grafana/grafana
    volumes:
      - /mnt/data/grafana:/var/lib/grafana
      - ./infra/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./infra/grafana/dashboards:/var/lib/grafana/dashboards:ro
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports: ["127.0.0.1:8080:80"]
    volumes:
      - ./infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ui/dist:/usr/share/nginx/html:ro
    depends_on: [analytics-api]

  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel run
    env_file: /etc/scalper-v2/cloudflared.env
    restart: unless-stopped

secrets:
  pg_password:
    file: /etc/scalper-v2/pg_password
```

### 18.2 Backups

- `pgBackRest` configured for local backups in `/mnt/data/backups/postgres`.
- Schedule: daily incremental at 04:00 UTC, weekly full on Sunday at 03:00 UTC, continuous WAL archiving.
- Retention: 4 weekly fulls, 7 daily incrementals.
- Off-server backup deferred per operator decision (noted in §16 risk disclosure).
- Restore runbook: `docs/runbook/restore_from_backup.md`.

### 18.3 Retention summary

| Data | Disk | DB |
|---|---|---|
| `trading.log` files | 30d |  |
| `audit.log` files | 180d |  |
| `system.log` files | 14d |  |
| `ticks` table | | 7d |
| `ohlc_1m` | | 180d + compression after 30d |
| `features` | | 180d + compression after 30d |
| `system_events` | | 14d |
| `trading_events`, `audit_events` | | 365d |
| `trades`, `signals`, `executions`, `scoring_evaluations` | | forever |
| `backtest_*` | | forever |

### 18.4 Healthchecks and liveness

- Every service exposes `/health` (liveness) and `/ready` (readiness).
- Docker healthchecks configured per service.
- Operator-observable via `docker compose ps` and Grafana service-status dashboard.

### 18.5 Deployment procedure

1. Operator merges PR on main.
2. CI builds Docker images tagged with commit SHA and pushes to local registry or rebuilds on host (single-host deploy, registry optional).
3. Operator runs `./scripts/deploy.sh <git-sha>` which:
   - Pulls latest compose file
   - Runs `alembic upgrade head` in a one-shot container
   - Runs `docker compose up -d` with new images
4. Post-deploy: `./scripts/smoke.sh` runs a synthetic webhook → expected NATS events → dashboard visibility check.
5. Grafana deploy panel auto-updates with new build number.

### 18.6 Rollback

- `./scripts/deploy.sh <previous-sha>` redeploys previous images.
- Schema migrations are forward-only; a rollback might leave the schema ahead of code. This is acceptable because migrations add, do not remove. If a migration is destructive (rare), a pre-deploy snapshot is taken and restored from backup if needed.

---

## 19. Phased Delivery Plan

### Phase F0 — Foundation (est. 1-2 weeks)

**Goal:** repository, tooling, and infrastructure stand up end-to-end with a hello-world service proving the stack works.

**Tasks:**
- Monorepo scaffold with `uv` workspaces, pre-commit, GitHub Actions CI (fast + full).
- Docker Compose for PG + TimescaleDB + NATS + Prometheus + Grafana + nginx + cloudflared.
- Alembic setup; initial migration creating core tables (`bots`, `bot_configs`, `symbol_map`, seed data).
- `packages/core` (types, errors, markers, time utils).
- `packages/bus` (NATS client with envelope helpers).
- `packages/db` (asyncpg pool, query helpers skeleton).
- `packages/observability` (structlog, metrics, trace IDs).
- Hello-world `signal-gateway` that accepts a webhook, publishes to NATS, logs JSON.
- `TASKS.md` scaffold, ADR directory, first ADR (ADR-0001: NATS JetStream decision).
- Dashboard test harness stub in CI.
- Docker image build and tag workflow.

**Exit criteria:**
- `docker compose up` on the Ubuntu server brings up all infra.
- A curl webhook to `signal-gateway` results in a visible NATS message, a DB signals row, and a JSON log entry.
- CI fast pipeline is green.
- CI full pipeline is green (even if only hello-world tests run).
- Prometheus scrapes signal-gateway `/metrics`.
- Grafana has one provisioned dashboard showing signal-gateway up/down.
- `TASKS.md` has F1 backlog populated.

### Phase F1 — Data and Signals (est. 1-2 weeks)

**Goal:** production-grade signal ingest, market data, and feature engine.

**Tasks:**
- Full `signal-gateway`: HMAC, validation, dedup, symbol mapping, complete schema.
- `market-data-svc`: Binance WS, OHLC persistence, backfill on startup, reconnect.
- `feature-engine`: plugin model, built-in EMA/RSI/ATR/VWAP/Bollinger/MACD, YAML registration, backfill script.
- Alembic migrations for `signals`, `ohlc_1m`, `features`, continuous aggregates for 5m/15m/1h/4h/1d.
- `packages/features` with all built-ins.
- Tests: signal gateway edge cases, feature determinism, WS reconnect behavior.
- ADRs as needed.

**Exit criteria:**
- A TV-style webhook produces a validated `signals` row with correct symbol mapping.
- 15m OHLC candles accumulate live in `ohlc_1m`.
- All six built-in features compute on candle close and appear in `features` table + NATS KV.
- `python scripts/backfill_features.py --feature ind.btcusdt.15m.ema_20 --from ...` populates history.
- CI green with new tests.

### Phase F2 — Execution (est. 2-3 weeks)

**Goal:** working order placement against Bybit testnet, complete lifecycle, reconciliation, PaperExchange for simulation.

**Tasks:**
- `packages/exchange` with `ExchangeClient` protocol, `BybitV5Adapter`, `PaperExchange`.
- `execution-service` with order placement, lifecycle FSM (BE/trail/partial TP), cumulative-delta reconciliation, P&L audit loop, WS handling, dedup.
- Alembic: `orders`, `trades`, `executions`, `position_state`, `paper_*` tables.
- Full hazard catalog addressed (§20).
- Tests: full lifecycle simulations, cumulative delta math, emergency close on SL fail, paper fill simulation.
- ADRs for shared rate limiter, reconciliation strategy.

**Exit criteria:**
- A test webhook against Bybit testnet opens a position, places SL and TP, manages BE move and trail, closes correctly.
- PaperExchange produces identical event stream shape to live for the same scenario.
- Cumulative-delta reconciliation passes integration test under concurrent-bot simulated closes.
- P&L audit loop catches and corrects an injected drift in a test.
- All hazard-catalog tests pass.

### Phase F3 — Strategy Engine and Multi-bot (est. 1-2 weeks)

**Goal:** per-bot process with YAML scoring config, multi-bot deployment.

**Tasks:**
- `packages/scoring` with rule language, condition types, evaluator, passthrough mode.
- `strategy-engine` service.
- `configs/bots/<id>.yaml` validation, loading, hot-config on restart.
- Per-bot Docker container; compose config for N bots.
- `scoring_evaluations` table and writes.
- Plugin rule support.
- Tests: evaluator property tests, multi-bot concurrent signal processing.

**Exit criteria:**
- Two bots with different scoring configs coexist and react differently to the same signal.
- `scoring_evaluations` shows per-rule audit for every signal.
- Passthrough mode verified: every signal executes while `scoring_evaluations` shows decision.
- Plugin rule example (`oi_squeeze`) runs and contributes to score.

### Phase F4 — Analytics API and Dashboard UI (est. 2-3 weeks)

**Goal:** complete 9-section dashboard backed by analytics-api.

**Tasks:**
- `analytics-api` with all endpoint categories.
- SSE streaming.
- React UI scaffold with TanStack Router, TanStack Query, Zustand, Tailwind, shadcn/ui.
- Each of the 9 sections implemented.
- Component library: DataTable, TimeRangePicker, BotSelector, StatusBadge, CorrelationIdChip.
- Playwright E2E critical journeys.
- Grafana ops dashboards (service health, NATS lag, PG, host).
- Dashboard test harness hardened.

**Exit criteria:**
- Operator can navigate all 9 sections, see live data, drill into a trade end-to-end.
- Scoring inspector shows per-rule breakdown for any signal.
- Feature inspector charts historical feature values.
- Backtest lab can trigger a small backtest and show results.
- Playwright smoke journeys pass in CI.

### Phase F5 — Shadow Variants, Backtest Harness, Finishing, Pre-live Hardening (est. 4-6 weeks)

**Goal:** full shadow + backtest capability; polish; **pre-live operational hardening (T-523 reorg 2026-05-08; see ADR-0011 for scope-extension rationale)**.

**Tasks:**
- Backtest harness: ReplayBus, HistoricalOHLCSource, HistoricalSignalSource, intra-candle path generator, comparison mode.
- Shadow variants runtime with OHLC replay restart recovery.
- Rejected-signal shadow tracking with OHLC replay restart recovery (v2 improvement).
- Strategy editor diff-against-live in UI.
- Feature auto-backfill on registration.
- Final pass on runbooks, docs, glossary, README.
- Hardening tasks from backlog.
- **Pre-live operational hardening cluster (T-524..T-536 — 13 mandatory tasks; per ADR-0011)**: bot-level risk caps (max_open_trades, daily_loss_limit, max_drawdown_stop, cooldowns), balance-driven position sizing (§B.1 reified + risk-per-SL + qty_step rounding + available_balance pre-check), account balance / equity tracking (`get_account_balance()` adapter protocol + equity snapshots + funding fees), named-state trade lifecycle FSM enum, SL/TP verification (periodic watchdog + overwrite protection + trailing audit).

**Exit criteria:**
- Backtest on a 30-day historical window completes and reports aggregates.
- Two backtests with different configs compared side-by-side.
- Shadow variants persist across restart (verified by killing execution-service mid-variant).
- All hazards in §20 have an associated test that passes.
- Operator signs off on the **Live-ready MVP** scope. *(Renamed from "Plný MVP" per ADR-0011 — production-ready semantic includes pre-live operational hardening cluster T-524..T-536.)*
- All hardening tasks (T-524..T-536) shipped + integration tests green + Live-ready deployment runbook executed.

---

## 20. Known Hazards Catalog

These are production-earned lessons from v1. The new system must address each by design. Every hazard has an associated test (or set of tests) that fails if the hazard recurs.

### H-001 — P&L orderId matching is unsafe

**Context.** Bybit `closed-pnl` returns CLOSE orderId; DB stored OPEN orderId. Naive matching corrupted $73 of P&L in v1.

**Policy.** v2 uses **cumulative-delta** approach (snapshot closed-pnl total before/after close, attribute delta). No matching.

**Test.** `test_cumulative_delta_ignores_order_ids` verifies correctness when orderIds are unavailable or swapped.

### H-002 — Similar entries collide in entry+qty matching

**Context.** KAITOUSDT incident: 0.5s sleep was too short, similar entry+qty matched wrong record.

**Policy.** Cumulative-delta approach removes matching entirely. If future matching is ever needed, minimum 2s sleep + mandatory timestamp filter.

**Test.** `test_close_with_identical_prior_trade_same_symbol`.

### H-003 — Market order non-idempotent; no retry

**Context.** `place_market_order` timeout ≠ failure; order may have succeeded. Retry creates duplicate position.

**Policy.** `place_market_order` decorated `@non_idempotent`. Zero retries at the adapter level. Timeout raises `UnknownState`. Reconciliation on next startup cleans up.

**Test.** `test_place_order_on_timeout_never_retries_and_raises_unknown_state`.

### H-004 — SL-set failure demands emergency close

**Context.** Position without SL = unbounded risk.

**Policy.** 3× retry `set_trading_stop`. On exhaustion, immediately `place_market_order` with `reduce_only=true` to flatten. Write both open and close to DB with estimated fee P&L. Alert to system channel.

**Test.** `test_sl_set_exhaustion_triggers_emergency_close_and_records`.

### H-005 — Opposite-side signals

**Context.** Live position LONG BTCUSDT; SHORT signal arrives. v1 blocked.

**Policy.** Implemented as a scoring rule (`block_opposite_position_open`) which can be enabled or disabled per bot. Default: blocked. Rule evaluates current position state from own-bot view.

**Test.** `test_opposite_side_signal_is_rejected_by_guard_rule`.

### H-006 — Webhook rate limit

**Context.** v1 signal-gateway with no rate limit was vulnerable to alert storms.

**Policy.** Sliding-window rate limit 20 req/60s per IP in signal-gateway. Configurable.

**Test.** `test_webhook_rate_limit_rejects_above_threshold`.

### H-007 — WS reconnect with exponential backoff

**Context.** v1 had exp backoff 1→60s; must preserve.

**Policy.** Port. Public and private WS reconnect with jitter.

**Test.** `test_ws_reconnect_uses_exponential_backoff`.

### H-008 — Signal TTL

**Context.** Bot resume after long outage replaying hour-old scalping signals is dangerous.

**Policy.** Every signal has `expires_at = received_at + signal_ttl` (default 120s). Strategy-engine drops expired signals with log `signal_expired_on_resume`.

**Test.** `test_expired_signal_is_dropped_at_strategy_engine`.

### H-009 — Execution WS dedup

**Context.** WS reconnect replay causes double-counted fills without dedup.

**Policy.** `packages.bus` base class `DedupingConsumer` with configurable key extractor and ring size 10k. Execution-service dedups by `execId`.

**Test.** `test_duplicate_exec_event_is_ignored`.

### H-010 — Fan-out before dedup

**Context.** v1 sent signals to live path before dedup to avoid dropping.

**Policy.** In v2, fan-out happens via NATS. `signals.raw` captures everything; `signals.validated` is deduplicated. Consumers choose their subject.

**Test.** `test_signal_fanout_preserves_raw_before_dedup`.

### H-011 — 2s sleep before closed-pnl query

**Context.** Bybit needs a moment to materialize closed-pnl record.

**Policy.** Cumulative-delta approach does two snapshots; the second is taken after the size-0 event but a 2s sleep is kept before the "after" snapshot for margin. Configurable.

**Test.** `test_closed_pnl_snapshot_waits_before_reading`.

### H-012 — Closed-PnL is source of truth

**Context.** WS-accumulated fills can have gaps; closed-pnl is authoritative.

**Policy.** On close, cumulative-delta from closed-pnl is the recorded `realized_pnl`. WS-accumulated is a cross-check only.

**Test.** `test_close_uses_closed_pnl_delta_over_ws_accumulation`.

### H-013 — Partial TP requires `tpslMode=Partial`

**Context.** Default `Full` closes whole position, bypassing trail.

**Policy.** `ExchangeClient.set_trading_stop` has required `tpsl_mode` param. No default. Compile error if omitted.

**Test.** `test_set_trading_stop_requires_explicit_tpsl_mode`.

### H-014 — Price subscription refcount

**Context.** Shadow variants + monitor share WS sub; first-to-finish must not cancel.

**Policy.** `SubscriptionManager` reference-counts. Context-manager API: `async with price_mgr.subscribe(symbol) as feed: ...`.

**Test.** `test_refcount_sub_survives_one_caller_releasing`.

### H-015 — Orphan close uses exact qty string

**Context.** Local float rounding leaves sub-step residue.

**Policy.** Adapters accept and return qty as `Decimal` (string-serialized). Qty strings from exchange are preserved for close.

**Test.** `test_orphan_close_uses_exchange_qty_string_not_float`.

### H-016 — Shadow task cleanup

**Context.** Rejected signal tracking leaked WS subs when exception escaped.

**Policy.** All subscribe calls use `async with` context manager. Finalizer unconditional.

**Test.** `test_shadow_task_unsubscribes_on_exception`.

### H-017 — Audit 1:1 matching

**Context.** v1 audit loop could double-attribute a closed-pnl record.

**Policy.** Audit uses cumulative delta per sub-account and time window; per-record attribution is out, cumulative attribution is in. Double-attribution structurally impossible.

**Test.** `test_audit_never_double_attributes_closed_pnl`.

### H-018 — Close by PK only

**Context.** `UPDATE trades ... WHERE symbol=? AND status='open'` risks multi-update.

**Policy.** All trade updates by `WHERE id = ?`. Enforced by code review; `packages/db/queries/trades.py` expose only PK-keyed updates.

**Test.** `test_close_trade_updates_exactly_one_row_by_pk`.

### H-019 — Score fail-open is logged, never silent

**Context.** Filter bypass went silent in an early v1.

**Policy.** Scoring evaluator emits `scoring_failed_open` event on every rule error; alerting-svc forwards to system channel.

**Test.** `test_rule_error_emits_logged_event_not_silent_pass`.

### H-020 — Post-restart reconciliation

**Context.** On startup, DB and exchange may disagree. v1's `restore_monitors` handled this.

**Policy.** On execution-service startup:
1. Fetch open positions per sub-account from exchange.
2. Fetch `position_state` rows from DB.
3. Orphan DB → close with reason `reconcile_gone`.
4. Orphan exchange → market-close (unmanaged).
5. Matching → resume monitor task.
All with audit log entries.

**Test.** `test_reconciliation_closes_db_orphans_and_markets_exchange_orphans`.

### H-021 — Scheduled jobs run in UTC

**Context.** v1 cron TZ mixup caused daily-report misalignment.

**Policy.** All scheduled jobs run via APScheduler in the service process, UTC only. No system cron. If the operator's daily cutoff preference is not 00:00 UTC, it's configurable per report.

**Test.** `test_daily_report_runs_at_configured_utc_time`.

### H-022 — API keys are per-bot, never shared

**Context.** Shared IP+key caused cross-bot rate-limit collisions.

**Policy.** Each bot's sub-account has its own key. Shared cross-bot limiter handles IP-level coordination. Env vars `BOT_<ID>_BYBIT_API_KEY/SECRET`.

**Test.** `test_adapter_pool_uses_distinct_credentials_per_bot`.

### H-023 — Shadow restart via OHLC replay

**Context.** v1 marked many shadows `lost_on_restart`.

**Policy.** Both shadow variants and rejected-signal shadows use OHLC replay on restart in v2. No `lost_on_restart` outcome.

**Test.** `test_shadow_variant_survives_restart_via_replay`.
`test_rejected_signal_shadow_survives_restart_via_replay`.

### H-024 — Fill label derives from our order ID

**Context.** Partial-TP followed by SL: first SL fill inherited TP orderLinkId, was mislabeled.

**Policy.** `exec_type` is assigned based on matching `execId → order_id` in our DB. Order-link fields from exchange are informational, not authoritative.

**Test.** `test_sl_fill_after_partial_tp_labeled_sl_not_tp`.

### H-025 — Cross-bot IP rate limit coordination

**Context.** Three bots sharing IP → one hits rate limit, others retry blind.

**Policy.** Shared rate limiter in NATS KV. On `RateLimitError`, all adapters observe shared pause flag. Exponential backoff with jitter per endpoint group.

**Test.** `test_one_bot_rate_limit_triggers_shared_pause_flag`.

### H-026 — Bybit qty-step rounding in reconcile

**Context.** `has_matching_trade` qty heuristic failed on non-50/50 partial due to step rounding; duplicate LTCUSDT trade recorded.

**Policy.** Reconciliation does not match on qty approximations. Uses time-window + orderId-covering test: "does this exchange position correspond to any DB trade with its open orderId issued in the last 60s?". If no, market-close.

**Test.** `test_partial_non_5050_reconciliation_does_not_create_duplicate`.

### H-030 — Open-fill must not decrement remaining_qty

**Context.** ExecutionDispatcher subtracts `qty_delta` from `position_state.remaining_qty` on every execution event. Placement tx writes `remaining_qty=request.qty` at trade-open commit (`placement_persist.py:419`). If the dispatcher unconditionally subtracts qty for the open-fill audit event (the WS execution event for the same fill), `remaining_qty` drops to 0 → triggers close-flow → trade marked closed in DB while position is still open on exchange. Operator-discovered shipped-code bug 2026-05-08; fix shipped via `fix(T-218b-open-fill-qty-bug)` precedent.

**Policy.** Dispatcher MUST skip `update_position_state_after_fill` when `exec_type="open"`. Open-fill is already accounted-for at placement-tx time; the WS execution event is audit-side mirror only (`insert_execution` still writes the audit row). `update_trade_fees_incremental` is UNCONDITIONAL — entry fee recorded on every fill including open. Defensive close-trigger guard at the `remaining_qty == 0` check ALSO gates on `exec_type != "open"` to protect against state-inconsistency edge cases.

**Test.** `test_process_open_fill_does_not_decrement_remaining_qty` (regression guard) + `test_process_open_fill_with_zero_remaining_qty_does_NOT_trigger_close` (defensive-guard pin) + restored `test_process_open_fill_orders_lookup_to_open_branch` with realistic placement-time `remaining_qty=event.qty` semantics.

H-numbering note: H-027/H-028/H-029 are reserved for ADR-0011 anticipated hazards (T-525/T-534/T-535 per pre-live operational hardening cluster); H-030 is the first concrete hazard discovered post-ADR-0011. **Companion sibling H-031** (paper adapter must not feed live ExecutionDispatcher) shipped via `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 — together H-030 + H-031 complete dispatcher safety contract for live + paper modes respectively.

### H-031 — Paper adapter must not feed live ExecutionDispatcher

**Context.** ExecutionDispatcher consumes `adapter.stream_executions()` per-bot and processes events via LIVE tables (`orders` / `trades` / `position_state`). PaperExchange writes to `paper_*` tables and emits ExecutionEvent for both open and close fills (`paper/adapter.py:820 _persist_open` + `:930` close-flow + `:1185 _emit_close_events` synthetic SL/TP). Dispatcher's LIVE table lookups return None for paper events → `_derive_exec_type` returns `("unknown", None, None)` → `dispatcher.py:188 RuntimeError("unattributable fill: no order match and no position_state")` → `run_dispatcher_for_bot` re-raises → task dies silently (`main.py:392 gather(return_exceptions=True)` swallows pri shutdown only). Operator-discovered shipped-code bug 2026-05-08; fix shipped via `fix(T-218c-paper-dispatcher-skip)` precedent.

**Policy.** ExecutionDispatcher tasks MUST NOT be created for adapters whose `bot_row.exchange_mode == "paper"`. Paper bots have an internal pipeline via PaperExchange (persist to `paper_*` + emit events via `stream_executions` for event-shape symmetry); the LIVE dispatcher's role is irrelevant for paper. `AdapterPoolResult.paper_bot_ids: frozenset[BotId]` is the canonical source for the skip; `main.py:215-240` consults this set via `if bot_id in adapter_pool.paper_bot_ids: continue` before constructing the dispatcher task. The `orders.requests.<bot_id>` subscriber (line 166) STAYS for paper bots — placement handler routes paper orders via `PaperExchange.place_market_order`; that path is independent of ExecutionDispatcher.

**Test.** `test_lifespan_does_not_create_dispatcher_task_for_paper_bots` + `test_build_adapter_pool_populates_paper_bot_ids`.

H-031 numbering note: T-218b shipped H-030 (open-fill must not decrement remaining_qty) on 2026-05-08 LIVE-mode-protective; T-218c addresses the PAPER-mode separate kill-path. T-218b plan claim "bug dormant in paper mode" was incorrect — paper had its own (different) crash path documented here; see L-018 active control on "dormant in mode" claims requiring code-citation evidence.

### H-032 — Retry loop over external adapter call must catch transient exceptions

**Context.** `services/execution/app/placement.py` step 6 calls `adapter.get_fill_price(symbol, order_id)` inside `for attempt in range(fill_price_retry_attempts)` retry loop. The `await` site originally had no try/except — when adapter raised `NetworkTimeout` / `RateLimitError` / `AuthError` from underlying HTTP call (Bybit `/v5/execution/list` per `bybit_v5/adapter.py:273-296`) or asyncpg errors (paper `select_paper_execution_price_by_order_id` per `paper/adapter.py:1299-1309`), the exception bypassed the retry counter, the `await asyncio.sleep(backoff)` step, AND the post-loop `if fill_price is None: DLQ + FillPriceUnresolvedError` contract. Exception propagated up to `bus.subscribe()` framework-level swallow with minimal operator-facing context. Operator-discovered shipped-code bug 2026-05-08; fix shipped via `fix(T-216c-fill-price-retry-exception)` precedent 2026-05-09.

**Policy.** Any retry loop over an external adapter call MUST wrap the `await` site with try/except matching the same error taxonomy as non-retried sibling calls in the same handler. For `placement.py` get_fill_price block: `(AuthError, NetworkTimeout, RateLimitError)` mirroring step 5 `place_market_order` catch (per `§11.3` error taxonomy). Exception treated as None: warn-log key `execution.get_fill_price_transient_error` + retry counter advances + sleep on remaining attempts + post-loop DLQ + `FillPriceUnresolvedError` contract preserved.

**Test.** `test_handler_retries_when_get_fill_price_raises_NetworkTimeout` + `test_handler_retries_when_get_fill_price_raises_RateLimitError` + `test_handler_retries_when_get_fill_price_raises_AuthError` + `test_fill_price_unresolved_after_all_exception_attempts_publishes_to_dlq_and_raises`.

H-032 numbering note: companion to H-030 (open-fill remaining_qty contract) + H-031 (paper adapter must not feed live ExecutionDispatcher). Together H-030/H-031/H-032 form the execution-service operational hardening cluster surfaced via operator audit 2026-05-08/05-09.

### H-033 — Composite-PK position_state UPDATE must include trade_id in WHERE clause

**Context.** `position_state` table uses composite PK `(bot_id, symbol)` per migration 0004. Under operator's short-cycle scalping (1m–5m horizons; rapid close→reopen pattern), the same `(bot_id, symbol)` row identity can host multiple trades sequentially: T1 opens → T1 closes → row deleted → T2 opens → T2's row written. WS execution events for the closing fill of T1 may arrive LATE after T2's `position_state` row exists. ExecutionDispatcher's `_derive_exec_type` Path A (`order_id_match is not None`) sources `trade_id` from the `trades` table via `select_trade_by_open_order_id` / `select_trade_by_close_order_id` — this trade_id is T1's. The subsequent `update_position_state_after_fill(bot_id, symbol)` (composite PK only) modified T2's row using T1's qty_delta. Wrong target row mutation → `remaining_qty` corruption on T2 → potential phantom close cascade. Operator-discovered shipped-code bug 2026-05-08; fix shipped via `fix(T-217c-position-state-trade-id-guard)` precedent 2026-05-09.

**Policy.** `update_position_state_after_fill` SQL helper MUST include `trade_id` in the WHERE clause: `WHERE bot_id = $X AND symbol = $Y AND trade_id = $Z`. The helper returns `rows_updated: int` (parsed from asyncpg command tag `"UPDATE <n>"`). ExecutionDispatcher caller threads the derived `trade_id` (from `_derive_exec_type` Path A trades-table lookup or Path B position_state.trade_id) and halts on `rows_updated == 0`: ERROR log key `execution.dispatcher_position_state_trade_id_mismatch` + raise `RuntimeError("position_state.trade_id mismatch with derived trade_id=...")`. Transaction rolls back; NATS redelivery + T-221 reconciliation own recovery.

**Test.** `test_update_position_state_after_fill_returns_zero_on_zero_rows_tag` (unit; mock-based) + `test_update_position_state_after_fill_returns_zero_when_trade_id_mismatches` + `test_update_position_state_after_fill_returns_one_when_trade_id_matches` (testcontainer-gated integration round-trip per L-008 active control) + `test_dispatcher_halts_on_position_state_trade_id_mismatch_during_fill_update` (dispatcher integration via mocks).

H-033 numbering note: companion to H-030 (open-fill remaining_qty contract) + H-031 (paper adapter must not feed live ExecutionDispatcher) + H-032 (retry loop over external adapter call must catch transient exceptions). Together H-030/H-031/H-032/H-033 form the execution-service operational hardening cluster surfaced via operator audit 2026-05-08/05-09. **H-018 vs H-033 scope clarification**: H-018 governs `trades` table single-PK updates (`WHERE id = ?`); H-033 governs `position_state` composite-PK updates under identity-reuse. H-033 is not a derogation of H-018 — different tables, different invariants.

---

## 21. Glossary

- **MFE / MAE** — maximum favorable / adverse excursion during a trade's lifetime.
- **BE** — break-even. Once price has moved `BE_TRIGGER` in favor, SL moves to `entry ± BE_SL_LEVEL`.
- **Trail** — after partial TP, SL follows `best_price ∓ TRAIL_PCT`.
- **Partial TP** — 50% (configurable) closes at TP; rest trails.
- **Score** — confidence metric (sum of rule weights).
- **Shadow variant** — parallel simulation of alternative SL/TP/BE/trail rules.
- **Shadow tracking** — passive observation of rejected signals.
- **Passthrough mode** — scoring evaluated but not used to filter (dataset generation).
- **Virtual balance** — software-tracked equity used for sizing; decoupled from exchange balance.
- **Feature** — a named, typed value computed on candle close or context (e.g., `ind.btcusdt.15m.ema_20`).
- **Feature store** — the `features` table + NATS KV `feature_latest`.
- **Rule** — a scoring decision element in a bot's YAML config with a weight and a condition.
- **Passing phase gate** — operator-verified transition from phase N to N+1.
- **Correlation ID** — string tying a signal to all downstream events.
- **Trace ID** — per-service-request identifier for debugging.
- **ADR** — Architecture Decision Record (see §6.3).

---

## 22. Appendix A — Server Configuration

Target host: single Ubuntu 24.04.3 LTS server, AMD Ryzen 5 3600 (6 cores / 12 threads), 16 GB RAM, NVMe SSD. Network: Cloudflare Tunnel only (no public IP).

### A.1 Partition layout

Existing (do not repartition):

```
/                            106 GB — system, Docker engine binaries, code checkouts
/var/lib/containerd           53 GB — container images and overlay2 (configured)
/var/log                      11 GB — systemd logs + /var/log/scalper/*
/mnt/data                    158 GB — application state (USE THIS FOR DATA)
```

Data directory layout (created by deploy scripts):

```
/mnt/data/
├── postgres/                    PostgreSQL PGDATA
├── nats/                        NATS JetStream storage
├── prometheus/                  Prometheus TSDB
├── grafana/                     Grafana state
└── backups/
    └── postgres/                pgBackRest repo
```

### A.2 System-level configuration

- `timedatectl set-timezone UTC` — server runs UTC (non-negotiable N1).
- `systemctl enable --now systemd-timesyncd` — NTP sync.
- Swap: keep existing; no tuning needed at 16GB RAM for this workload.
- Kernel parameters: `vm.swappiness=10`, `vm.dirty_ratio=15` for DB write performance (`/etc/sysctl.d/99-scalper.conf`, justified by ADR in F0).

### A.3 Docker

- Docker Engine from official repository (not `docker.io` Ubuntu package).
- `daemon.json`:
  ```json
  {
    "log-driver": "json-file",
    "log-opts": {"max-size": "50m", "max-file": "3"},
    "data-root": "/var/lib/containerd/docker"
  }
  ```
- User `scalper` in `docker` group; run deployments as `scalper` not root.

### A.4 Log rotation

`/etc/logrotate.d/scalper`:

```
/var/log/scalper/*.log {
  daily
  rotate 180
  compress
  missingok
  notifempty
  create 0640 scalper scalper
  sharedscripts
  postrotate
    docker compose kill -s USR1 $(docker compose ps -q) 2>/dev/null || true
  endscript
}
```

Rotate periods mirror the DB retention (audit 180d kept, trading 30d compressed to retain).

---

## 23. Appendix B — YAML Configuration Examples

### B.1 `configs/bots/alpha.yaml`

```yaml
bot_id: alpha
display_name: "Alpha — RSI div passthrough"
created_at: "2026-04-25T10:00:00+00:00"
status: active

exchange:
  mode: testnet                    # live | testnet | paper
  account: sub_alpha               # Bybit sub-account label
  api_key_env: BOT_ALPHA_BYBIT_API_KEY
  api_secret_env: BOT_ALPHA_BYBIT_API_SECRET

signals:
  source_filter: ["tv_rsi_divergence_v3", "tv_squeeze_v1"]   # optional whitelist
  ttl_seconds: 120

trading:
  universe:                        # symbols this bot trades
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT
  primary_interval: 15m

execution:
  leverage: 20
  sl_pct: 0.01
  tp_pct: 0.01
  tp_qty_pct: 0.5
  be_trigger: 0.005
  be_sl_level: 0.003
  trail_pct: 0.005
  fee_rate: 0.00055
  sl_retry_count: 3
  emergency_close_on_sl_fail: true

scoring:
  mode: passthrough                # passthrough | active
  trigger_threshold: 4.0
  rules:
    - name: day_hours
      weight: +1.0
      condition:
        type: between
        feature: ctx.calendar.hour_utc
        min: 6
        max: 23

    - name: rsi_aligned_long
      weight: +1.0
      applies_when: { signal.action: LONG }
      condition:
        type: between
        feature: ind.${signal.symbol}.15m.rsi_14
        min: 55
        max: 70

    - name: rsi_aligned_short
      weight: +1.0
      applies_when: { signal.action: SHORT }
      condition:
        type: between
        feature: ind.${signal.symbol}.15m.rsi_14
        min: 30
        max: 45

    - name: strong_trend
      weight: +1.5
      condition:
        type: ema_stack
        short: ind.${signal.symbol}.15m.ema_20
        mid: ind.${signal.symbol}.15m.ema_50
        long: ind.${signal.symbol}.15m.ema_200
        direction: from_signal

    - name: oi_squeeze_v2
      weight: +1.0
      condition:
        type: plugin
        name: oi_squeeze
        version: 2
        params: { lookback_candles: 5, oi_drop_pct: 1.0 }

    - name: retry_loss_penalty
      weight: -1.0
      condition:
        type: recent_loss_on_symbol
        window_hours: 4

    - name: block_opposite_position
      weight: -999.0                 # effectively blocks via threshold
      required: true
      condition:
        type: opposite_side_open
        bot: self

sizing:
  tiers:
    - { balance_min: 500, size: 700 }
    - { balance_min: 1000, size: 1400 }
    - { balance_min: 2000, size: 2100 }
    - { balance_min: 4000, size: 2800 }
  score_multipliers:
    "4": 0.75
    "5": 1.0
    "6": 1.25
    "7": 1.5
    "8": 1.5
    "9": 1.5
  max_notional_per_symbol:
    default: 3000
    BTCUSDT: 5000
  tier_promotion:
    min_trades: 10
  tier_demotion:
    drawdown_pct: 5.0

shadow:
  enabled: true
  variants:
    - name: baseline
    - name: no_be
      overrides: { be_trigger: 0 }
    - name: full_tp
      overrides: { tp_qty_pct: 1.0, trail_pct: 0 }
    - name: sl_tight
      overrides: { sl_pct: 0.005 }
    - name: sl_wide
      overrides: { sl_pct: 0.015 }
  max_duration_hours: 4
  rejected_signal_tracking:
    enabled: true
    window_seconds: 3600
```

### B.2 `configs/features/indicators.yaml`

```yaml
features:
  - name_template: ind.{symbol}.15m.ema_20
    type: builtin.ema
    interval: 15m
    params: { period: 20 }
    source_version: builtin.ema.v1

  - name_template: ind.{symbol}.15m.ema_50
    type: builtin.ema
    interval: 15m
    params: { period: 50 }
    source_version: builtin.ema.v1

  - name_template: ind.{symbol}.15m.ema_200
    type: builtin.ema
    interval: 15m
    params: { period: 200 }
    source_version: builtin.ema.v1

  - name_template: ind.{symbol}.15m.rsi_14
    type: builtin.rsi
    interval: 15m
    params: { period: 14 }
    source_version: builtin.rsi.v1

  - name_template: ind.{symbol}.15m.atr_14
    type: builtin.atr
    interval: 15m
    params: { period: 14 }
    source_version: builtin.atr.v1

  - name_template: ind.{symbol}.1m.vwap_session
    type: builtin.vwap
    interval: 1m
    params: { session: daily }
    source_version: builtin.vwap.v1
```

### B.3 `configs/plugin_registry.yaml`

```yaml
features:
  - name: oi_squeeze
    version: 2
    entry_point: plugins.features.oi_squeeze:OISqueezeFeature
    params_schema: plugins.features.oi_squeeze:ParamsSchema

rules:
  - name: opposite_side_open
    entry_point: packages.scoring.rules.opposite_side:OppositeSideOpenRule
  - name: recent_loss_on_symbol
    entry_point: packages.scoring.rules.recent_loss:RecentLossRule
```

### B.4 `configs/symbol_map.yaml` (seed data)

```yaml
mappings:
  - input: BTCUSDT.P
    canonical: BTCUSDT
    source: binance
  - input: ETHUSDT.P
    canonical: ETHUSDT
    source: binance
```

### B.5 `configs/alerts.yaml`

```yaml
channels:
  system:
    telegram_chat_id_env: TELEGRAM_CHAT_SYSTEM
  trading:
    telegram_chat_id_env: TELEGRAM_CHAT_TRADING
  pnl:
    telegram_chat_id_env: TELEGRAM_CHAT_PNL
  security:
    telegram_chat_id_env: TELEGRAM_CHAT_SECURITY

rate_limit:
  dedup_window_seconds: 300

rules:
  - event: heartbeat_stale
    channel: system
    severity: critical
    template: templates/heartbeat_stale.j2

  - event: ws_disconnected_over_60s
    channel: system
    severity: warning
    template: templates/ws_disconnected.j2

  - event: sl_set_exhausted_emergency_close
    channel: trading
    severity: critical
    template: templates/emergency_close.j2

  - event: pnl_audit_correction
    channel: pnl
    severity: info
    template: templates/pnl_correction.j2
    threshold:
      field: abs_delta_usd
      min: 10.0

  - event: lan_admin_write
    channel: security
    severity: info
    template: templates/admin_write.j2
```

---

## 24. Appendix C — References

- v1 documentation: `docs/v1/BOT_DOCUMENTATION.md`
- NATS JetStream documentation: https://docs.nats.io/
- TimescaleDB documentation: https://docs.timescale.com/
- Bybit V5 API: https://bybit-exchange.github.io/docs/v5/intro
- FastAPI: https://fastapi.tiangolo.com/
- Pydantic v2: https://docs.pydantic.dev/
- shadcn/ui: https://ui.shadcn.com/
- Hypothesis: https://hypothesis.readthedocs.io/
- Testcontainers Python: https://testcontainers-python.readthedocs.io/

---

*End of document.*
