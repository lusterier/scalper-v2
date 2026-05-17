# scalper-v2

Multi-bot crypto-derivatives trading platform. TradingView webhook signals →
per-bot YAML-configured scoring engine → Bybit (or an in-process paper
exchange) → audit-grade structured JSON logs. Successor to the v1
SQLite-based single-bot scalper (rewrite, not a migration).

**Status:** F5 complete — **Live-ready MVP**. The backtest harness, shadow
variants, and the ADR-0011 pre-live operational hardening cluster
(T-524..T-536) are all shipped; §20 hazard test-coverage audited (E4). The
F5 E1/E2 operator smoke runbooks (below) + the T-522 E1..E6 sign-off close
out the phase. The one capital control DEFERRED at F5 sign-off — the H-005
opposite-side guard — was resolved post-MVP in F6/T-542 as the
`risk.block_opposite_side` consumer pre-scoring gate (ADR-0016); see §20 H-005.

> [`docs/CLAUDE_CODE_BRIEF.md`](docs/CLAUDE_CODE_BRIEF.md) is the **source of
> truth** for all architecture, coding, and process rules. This README is an
> orientation map, not authoritative — read the brief before changing
> anything. Current task state: [`TASKS.md`](TASKS.md). Decisions:
> [`docs/adr/`](docs/adr/).

## Architecture

Hexagonal, multi-service (BRIEF §7/§11). Each service is independently
deployable; state is composed at the edge in each service's `main.py`
(N6/N7 — no globals, DI via constructors).

```
TradingView ─▶ signal-gateway ──(NATS signals.validated)──▶ strategy-engine (per-bot scoring)
                     │                                              │
              market-data-svc ──(market.ohlc.1m / ticks)──▶ feature-engine ──(feature store)
                     │                                              │
                     ▼                                       (NATS orders.requests)
                  Postgres + TimescaleDB                            ▼
                  (audit/trades/features/…)            execution-service ──▶ Bybit  | PaperExchange
                                                              │
                                            analytics-api + React/Vite dashboard
```

**Three execution paths, one pipeline:**

- **Live / testnet** — `execution-service` places real orders via the Bybit
  v5 adapter (`ExchangeClient` protocol; §11).
- **Paper** — the in-process `PaperExchange` simulates fills + slippage + fees
  against live OHLC; same FSM, no exchange writes (§12.1).
- **Backtest** — `ReplayBus` (in-process pub/sub, no broker) drives the same
  strategy→execution consumers over historical OHLC + signals; offline,
  deterministic (§12.2).

## Features (F5 MVP)

- **Backtest harness** (T-501..T-509) — 30-day historical replay →
  `backtest_runs.summary` aggregates (total/WR/P&L/PF/MDD); `--compare`
  two runs side-by-side (aggregate-diff + per-trade diff). §12.2.
- **Shadow variants** (T-510..T-517) — per accepted trade, N parallel
  alternative-parameter simulations on `PaperExchange`; rejected-signal
  shadow tracking; **restart recovery via OHLC replay** (H-023 — no
  `lost_on_restart`). §13.
- **Risk management** (ADR-0011; T-524..T-526) — per-bot concurrent-trades
  caps, persistent kill-switch (daily-loss-limit, max-drawdown-stop —
  survives restart, H-027), loss/streak cooldowns.
- **Balance-driven position sizing** (ADR-0013; T-527/T-528) — `sizing.method`
  discriminator: `tier` (balance→tier ladder + score multiplier + notional
  cap) or `risk_per_sl` (size so an SL hit loses exactly `risk_pct` of
  equity). §B.1.
- **Account equity + funding tracking** (T-530/T-531/T-532) —
  `get_account_balance()` adapter port, periodic equity snapshots
  (`bot_equity_snapshots`), perpetual-funding settlement poll
  (`funding_fees`) feeding the cumulative-delta P&L audit (H-017).
- **Trade lifecycle FSM + SL/TP verification** (T-533..T-536) — named-state
  lifecycle enum; periodic SL watchdog, overwrite protection, trailing-SL
  drift audit.
- **Dashboard** (analytics-api + React/Vite) — per-bot live view, trade
  drill-down, backtest lab, strategy editor, feature/scoring inspectors,
  audit-log viewer (BRIEF §13.6 / F4).

## Stack

Python 3.12 (uv workspace), PostgreSQL 16 + TimescaleDB 2, NATS JetStream,
FastAPI, React + Vite, Docker Compose on Ubuntu 24.04 LTS. Full list:
brief §3.

## Quick start

Deployment (compose topology, env, migrations): BRIEF §18 + §16.2.

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d \
  postgres nats nats-init signal-gateway market-data-svc \
  feature-engine execution-service strategy-engine-<bot>
uv run alembic upgrade head        # migrations to head (incl. 0021 funding_fees)
```

**Operator smoke runbooks (F5 E1/E2 — executed + signed off in T-522):**

- Backtest: [`docs/runbooks/F5_E1_backtest_smoke.md`](docs/runbooks/F5_E1_backtest_smoke.md)
- Shadow restart: [`docs/runbooks/F5_E2_shadow_smoke.md`](docs/runbooks/F5_E2_shadow_smoke.md)
- Backtest CLI: `uv run python scripts/backtest.py --bot <id> --from <ISO-8601+00:00> --to <ISO-8601+00:00>` ; compare: `--compare <run_A> <run_B>` (§12.2).

## Local dev

```bash
./scripts/dev-up.sh    # postgres + nats (compose) + analytics-api (uvicorn) + vite
./scripts/dev-down.sh  # tear down (DB volumes preserved)
```

Full lifecycle, troubleshooting, toolchain:
[`docs/runbooks/dev_stack.md`](docs/runbooks/dev_stack.md). v1 reference:
[`docs/v1/BOT_DOCUMENTATION.md`](docs/v1/BOT_DOCUMENTATION.md).

## Hardening + hazards

The ADR-0011 pre-live operational hardening cluster (T-524..T-536) is
shipped. Every §20 hazard has an associated passing test, enforced
permanently by [`tests/test_hazard_catalog_coverage.py`](tests/test_hazard_catalog_coverage.py)
(the F5 E4 audit guard; T-519). E4 status: **36/36** hazards test-pinned —
H-005 (opposite-side guard) resolved post-MVP in F6/T-542 (was 35/36 +
H-005 DEFERRED at F5 sign-off)
([`docs/audit/hazard-test-coverage.md`](docs/audit/hazard-test-coverage.md)).

## License

Private. Not for redistribution.
