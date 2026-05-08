# ADR-0008: Backtest summary PF semantics — None for both edge cases

Status: accepted
Date: 2026-05-08
Deciders: operator, Claude Code
Prerequisite for: T-507b (`scripts/backtest.py` CLI orchestrator + summary persistence to `backtest_runs.summary`).

## Context

T-507b ships `_compute_summary` aggregating `backtest_trades` rows for a backtest run into a `dict[str, Any]` persisted to `backtest_runs.summary` JSONB column. 5 metrics: `total_trades`, `wr` (win rate), `pnl` (total realized), `pf` (profit factor), `mdd` (max drawdown).

**Profit factor (PF)** is conventionally defined as `gross_wins / gross_losses` where:
- `gross_wins = sum(realized_pnl for closed trades where pnl > 0)`
- `gross_losses = sum(-realized_pnl for closed trades where pnl < 0)` (positive value)

Two edge cases:
1. **No losses** (`gross_losses == 0`, all trades won) — division by zero
2. **No wins** (`gross_wins == 0`, all trades lost) — `pf = 0/N = 0`

Standard finance convention:
- No-losses → PF = ∞ (positive infinity)
- No-wins → PF = 0

Both are problematic for our use case:
- **JSON serialisation**: Python's `float('inf')` serialises to `Infinity` in some JSON encoders (non-standard) or raises `ValueError` in strict ones (`json.dumps(float('inf'))` raises in default Python). Not portable across UI/API consumers.
- **Operator semantic**: `pf=0.0` for no-wins is ambiguous — does it mean "no profitable trades" (true) or "PF undefined" (also true)? Operator must remember context.

## Decision

T-507b `_compute_summary` returns `pf=None` for **both** edge cases:

```python
if gross_wins == 0 or gross_losses == 0:
    pf: float | None = None
else:
    pf = round(float(gross_wins) / float(gross_losses), 4)
```

Persisted as JSON `null` in `backtest_runs.summary.pf`. UI displays "—" or "N/A".

## Rationale

- **JSON-portable**: `null` is universal across all JSON consumers; no parser-specific behavior for `Infinity`.
- **Single semantic**: `null` unambiguously means "PF undefined for this run". Operator doesn't need to disambiguate `0.0` vs `null`.
- **Defensive against rare edge cases**: backtests with 0 trades, 1-2 trades, or strongly one-sided runs are common during early development.
- **Matches `wr=None` for empty run**: empty-run already returns `wr=None`; PF follows the same "metric undefined" convention.

## Consequences

Positive:
- T-507b summary JSONB always-valid JSON (no `Infinity` strings, no `ValueError` on serialise).
- UI / analytics-api consumers handle `pf=null` uniformly without special-case.

Negative / trade-offs:
- **Deviation from finance convention**: external backtest tools (TradingView, Backtrader, Zipline) typically report PF=∞ on no-losses; PF=0 on no-wins. Operator comparing T-507b summary to external tools must remember T-507b reports both as `null`.
- **Ambiguity hidden**: a glance at `pf=null` doesn't distinguish "all wins" (good) vs "all losses" (bad). Operator must cross-reference `wr` (1.0 vs 0.0) + `pnl` (positive vs negative) to disambiguate.
- **Forward-compat risk**: if F6+ analytics-api adds backtest comparison to external tools, conversion logic must handle both PF=∞ (no losses) → null and PF=0 (no wins) → null. Reverse mapping is lossy.

## Alternatives considered

- **PF=∞ (no losses) + PF=0 (no wins) — finance convention**: rejected. JSON serialisation fragility; UI must handle special floats.
- **PF=Decimal sentinel (e.g., Decimal("999999"))**: rejected. Numeric sentinel is worse-of-both-worlds.
- **PF=string "inf" / "0"**: rejected. Mixed type for `pf` field breaks consumer-side type expectations.
- **Branch on `total_trades` alone**: rejected. Doesn't solve no-losses with N>0 trades.
- **Two fields `pf` + `pf_undefined_reason`**: rejected as over-engineering.

## Cross-references

- `scripts/backtest.py` `_compute_summary` (T-507b) — implementation
- `scripts/tests/test_backtest_cli.py` `test_compute_summary_all_losses_pf_none` + `test_compute_summary_all_wins_pf_none` — regression guards
- BRIEF §12.2:1967-1968 — backtest summary spec (mentions PF as one of 5 stats; doesn't pin semantic)
- T-508 (compare mode) — `--compare run_A run_B` will need to handle both runs having `pf=null`

## Follow-up

- Operator runbook for T-509 worker should include "interpreting pf=null" note for backtest reviewers.
- F6+ external-tool comparison ADR (if/when needed) revisits the lossy null mapping.
