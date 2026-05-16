# F5 E1 backtest smoke runbook

**Phase:** F5 exit-criteria — backtest harness (BRIEF §19, the F5 "Exit criteria" bullets verbatim below)
**Mode:** dev (operator-host; production deploy per BRIEF §16.2 / §18)
**Owner:** operator (manual; T-521 ships this runbook as a required F5 close-out deliverable; executed + signed off as part of T-522 E5/E6)

Verbatim BRIEF §19 F5 exit-criteria bullets this runbook verifies:

> - Backtest on a 30-day historical window completes and reports aggregates.
> - Two backtests with different configs compared side-by-side.

## Purpose

End-to-end operator smoke of the backtest harness (T-501..T-509): a single
30-day replay completes and persists `backtest_runs.summary` aggregates,
and `--compare` of two runs with different configs emits an aggregate-diff
+ a per-trade diff. Unit/integration tests already cover replay determinism
+ intra-candle path + the 1-week seeded full-backtest (BRIEF §12.2 "Tests
required"); this runbook is the operator-facing end-to-end confirmation,
NOT a re-test of those.

## Prerequisites

- [ ] Project-root `.env` populated with `DATABASE_URL` + `POSTGRES_PASSWORD`.
- [ ] `docker compose -f compose.yaml -f compose.dev.yaml up -d postgres nats nats-init` → infra healthy.
- [ ] Alembic migrations at head: `uv run alembic upgrade head` (includes 0021 funding_fees).
- [ ] At least 1 bot config in `configs/bots/` (e.g. `alpha.yaml`) with a `bots` row for its `bot_id`.
- [ ] Historical OHLC seeded for the bot's symbol universe across the chosen 30-day window (binance source; `--source` default). Without seeded OHLC the replay completes with zero trades — still a valid "completes + reports aggregates" tick, but a non-empty window better exercises the harness.

## Step 1 — Single 30-day backtest completes + persists aggregates

Run (substitute a real `bot_id` + a 30-day UTC window with seeded OHLC):

```
uv run python scripts/backtest.py --bot alpha \
  --from 2026-04-01T00:00:00+00:00 --to 2026-05-01T00:00:00+00:00
```

- [ ] Command exits 0 (no traceback); a `run_id` (UUID) is printed/logged.
- [ ] `backtest_runs` has the new row: `SELECT id, bot_id, started_at, finished_at, summary FROM backtest_runs ORDER BY started_at DESC LIMIT 1;` — `finished_at` is non-NULL (run completed).
- [ ] `backtest_runs.summary` (jsonb) contains the 5 aggregates per BRIEF §12.2:1977 — total trades, win rate (WR), P&L, profit factor (PF), max drawdown (MDD).
- [ ] `backtest_trades` rows exist linked by `run_id` (`SELECT count(*) FROM backtest_trades WHERE run_id = '<run_id>';`) — or 0 if the window had no qualifying signals (still a valid completion).

## Step 2 — Two backtests, different configs, compared side-by-side

Run a second backtest of the SAME window with a config override (different
sizing / scoring / SL), capturing a second `run_id`:

```
uv run python scripts/backtest.py --bot alpha \
  --from 2026-04-01T00:00:00+00:00 --to 2026-05-01T00:00:00+00:00 \
  --override 'sizing.method=risk_per_sl' --override 'sizing.risk_pct=0.01'
```

- [ ] Second run exits 0; a second distinct `run_id` is recorded.

Compare the two runs (BRIEF §12.2:1980):

```
uv run python scripts/backtest.py --compare <run_A_uuid> <run_B_uuid>
```

- [ ] Output shows an **aggregate-metrics diff** (the 5 summary stats for run A vs run B + a delta column).
- [ ] Output shows a **per-trade diff** — signals that produced a different outcome (close_reason / realized_pnl) between the two runs.
- [ ] No traceback; exits 0.

## Exit checklist (BRIEF §19 F5 exit-criteria — verbatim)

- [ ] **Backtest on a 30-day historical window completes and reports aggregates.** (Step 1: run exits 0, `backtest_runs.summary` has total trades / WR / P&L / PF / MDD.)
- [ ] **Two backtests with different configs compared side-by-side.** (Step 2: two distinct `run_id`s; `--compare` emits aggregate-diff + per-trade diff.)

_(Note: the F5 §19 bullets are unlabelled; this runbook is the 1st F5 close-out runbook by sequential naming [`F5_E1_…`] per the T-521 task-def — the filename's "E1" is not a §19-bullet index. The two backtest bullets above are the criteria this runbook verifies, quoted verbatim.)_

## Sign-off

_(Filled by the operator when this runbook is executed as part of T-522 E5/E6.)_

```
Run timestamp: `YYYY-MM-DDTHH:MM:SS+00:00`   (§N1 — explicit UTC offset)
Operator: `<name>`
Master HEAD at run: `<git-hash>`
Result: `PASS` / `PASS WITH N PARTIALS` / `FAIL` — <one-line summary>
```

Discoveries during run (any master-fix commits in the same session — list with `fix(T-NNN)` + hash, mirror the F4_E1 precedent). Tech-debt / follow-up candidates (NOT F5 blockers): list or "none".
