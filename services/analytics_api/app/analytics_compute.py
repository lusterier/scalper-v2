"""Pure-Python financial-math aggregates for ``/api/analytics/*`` (T-406).

All functions in this module are **pure** — no DB, no async, no I/O,
deterministic over inputs. Safe to call from `asyncio.to_thread` (used
by Monte-Carlo endpoint per BRIEF §9.6:1641 CPU-heavy offload).

**Decimal context discipline (WG#4)**: compute functions operate under
default :func:`decimal.getcontext` (prec=28). NO ``Decimal.quantize`` on
intermediate values, NO ``getcontext().prec`` mutation. Tests assert
exact Decimal equality; flaky-test risk if precision context shifts.

**Domain split per §5.13**:

* Statistical metrics (expectancy, win_rate, loss_rate) → ``float``
* Money sums/averages/percentiles (avg_win, avg_loss, MC percentiles) →
  ``Decimal`` (preserves NUMERIC(20,4) precision per §N1 / §5.3)

Math-validator OUT OF SCOPE per CLAUDE.md Gate 4 letter
(``services/analytics_api/`` not in math-binding list line 121); plan's
``## Hand verification`` section + brief-reviewer second-look gate
math correctness.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

    from packages.db.queries.analytics import TradeRealizedPnlRow
    from packages.db.queries.shadow import ShadowVariantAggregateRow

__all__ = [
    "ExpectancyMetrics",
    "HeatmapCell",
    "MonteCarloResult",
    "PnlBucket",
    "PnlSeriesPoint",
    "VariantAggregateMetrics",
    "compute_expectancy",
    "compute_hourly_heatmap",
    "compute_monte_carlo",
    "compute_pnl_series",
    "compute_variant_aggregate",
]


type PnlBucket = Literal["hour", "day"]


@dataclass(frozen=True, slots=True)
class ExpectancyMetrics:
    """Statistical expectancy + WR + avg_win + avg_loss + counts."""

    expectancy: float  # E = (WR x avg_win) - (LR x avg_loss); float per §5.13
    win_rate: float
    loss_rate: float
    avg_win: Decimal  # mean of positive realized_pnl; Decimal preserves precision
    avg_loss: Decimal  # mean of |negative realized_pnl|; positive Decimal
    total_trades: int
    win_count: int
    loss_count: int


@dataclass(frozen=True, slots=True)
class HeatmapCell:
    """One cell of 24x7 hourxweekday grid."""

    weekday: int  # 0=Monday, 6=Sunday (Python `dt.weekday()`; NOT isoweekday())
    hour: int  # 0..23 UTC
    trade_count: int
    avg_pnl: Decimal | None  # None when trade_count=0


@dataclass(frozen=True, slots=True)
class PnlSeriesPoint:
    """One point on cumulative P&L time-series chart."""

    bucket_at: datetime  # bucket start (truncated to hour or day boundary, UTC)
    bucket_pnl: Decimal  # sum of realized_pnl in this bucket
    cumulative_pnl: Decimal  # running sum since first bucket


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    """Bootstrap MC result; percentile distribution at end-of-period equity."""

    n_simulations: int
    seed: int
    p5: Decimal
    p25: Decimal
    p50: Decimal
    p75: Decimal
    p95: Decimal


def compute_expectancy(rows: list[TradeRealizedPnlRow]) -> ExpectancyMetrics:
    """Compute statistical expectancy + WR + avg_win + avg_loss.

    Formula (per OQ-6 default A — standard expectancy):
        E = (WR x avg_win) - (LR x avg_loss)
    where:
        WR = wins / total                (win = realized_pnl > 0)
        LR = losses / total              (loss = realized_pnl < 0)
        avg_win = sum(positive realized_pnl) / win_count
        avg_loss = sum(|negative realized_pnl|) / loss_count

    Trades with realized_pnl == 0 (flat) are EXCLUDED from win/loss
    buckets but counted in total. This means WR + LR ≤ 1 (gap = zero
    count / total).

    WG#3: empty input short-circuits to all-zero result BEFORE any
    division to avoid `Decimal('0') / Decimal('0')` raising
    `decimal.DivisionUndefined`.
    """
    total = len(rows)
    if total == 0:
        return ExpectancyMetrics(
            expectancy=0.0,
            win_rate=0.0,
            loss_rate=0.0,
            avg_win=Decimal("0"),
            avg_loss=Decimal("0"),
            total_trades=0,
            win_count=0,
            loss_count=0,
        )

    wins: list[Decimal] = []
    losses: list[Decimal] = []
    for r in rows:
        if r.realized_pnl > 0:
            wins.append(r.realized_pnl)
        elif r.realized_pnl < 0:
            losses.append(-r.realized_pnl)  # absolute value

    win_count = len(wins)
    loss_count = len(losses)
    avg_win = sum(wins, Decimal("0")) / Decimal(win_count) if win_count else Decimal("0")
    avg_loss = sum(losses, Decimal("0")) / Decimal(loss_count) if loss_count else Decimal("0")
    win_rate = win_count / total
    loss_rate = loss_count / total
    expectancy = win_rate * float(avg_win) - loss_rate * float(avg_loss)

    return ExpectancyMetrics(
        expectancy=expectancy,
        win_rate=win_rate,
        loss_rate=loss_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        total_trades=total,
        win_count=win_count,
        loss_count=loss_count,
    )


def compute_hourly_heatmap(rows: list[TradeRealizedPnlRow]) -> list[HeatmapCell]:
    """Bucket trades into 24x7 grid; empty cells emitted with count=0, avg_pnl=None.

    Returns 168 cells in deterministic order: weekday 0..6 x hour 0..23
    (Python ``dt.weekday()``: Monday=0, Sunday=6 per WG#5; NOT
    ``dt.isoweekday()`` 1..7). UTC for closed_at extraction (assumes
    asyncpg returns tz-aware datetime per §N1).
    """
    bucket_sums: dict[tuple[int, int], list[Decimal]] = {}
    for r in rows:
        wd = r.closed_at.weekday()  # WG#5: weekday() not isoweekday()
        hr = r.closed_at.hour
        bucket_sums.setdefault((wd, hr), []).append(r.realized_pnl)

    cells: list[HeatmapCell] = []
    for wd in range(7):
        for hr in range(24):
            pnl_list = bucket_sums.get((wd, hr), [])
            count = len(pnl_list)
            if count == 0:
                cells.append(HeatmapCell(weekday=wd, hour=hr, trade_count=0, avg_pnl=None))
            else:
                avg = sum(pnl_list, Decimal("0")) / Decimal(count)
                cells.append(HeatmapCell(weekday=wd, hour=hr, trade_count=count, avg_pnl=avg))
    return cells


def compute_pnl_series(
    rows: list[TradeRealizedPnlRow],
    *,
    bucket: PnlBucket,
) -> list[PnlSeriesPoint]:
    """Bucket trades into hour or day buckets; emit cumulative P&L series.

    Empty buckets between non-empty ones are NOT emitted (sparse series;
    UI handles gaps client-side via timestamp deltas). Truncation to
    hour/day boundary uses ``datetime.replace(...)`` in UTC.
    """
    if not rows:
        return []

    bucket_pnls: dict[datetime, Decimal] = {}
    for r in rows:
        if bucket == "hour":
            key = r.closed_at.replace(minute=0, second=0, microsecond=0)
        else:  # "day"
            key = r.closed_at.replace(hour=0, minute=0, second=0, microsecond=0)
        bucket_pnls[key] = bucket_pnls.get(key, Decimal("0")) + r.realized_pnl

    sorted_keys = sorted(bucket_pnls.keys())
    cumulative = Decimal("0")
    points: list[PnlSeriesPoint] = []
    for key in sorted_keys:
        b_pnl = bucket_pnls[key]
        cumulative += b_pnl
        points.append(
            PnlSeriesPoint(
                bucket_at=key,
                bucket_pnl=b_pnl,
                cumulative_pnl=cumulative,
            ),
        )
    return points


def compute_monte_carlo(
    rows: list[TradeRealizedPnlRow],
    *,
    n_simulations: int,
    seed: int,
) -> MonteCarloResult:
    """Bootstrap-with-replacement Monte Carlo; deterministic with seed.

    Algorithm:

    1. Extract ``sample = [r.realized_pnl for r in rows]``.
    2. For each simulation, draw ``len(sample)`` samples with replacement
       from ``sample`` and sum → end-of-period equity.
    3. Sort end-equities; compute p5/p25/p50/p75/p95 via
       :func:`_percentile_linear` (matches NumPy ``method='linear'``).

    Empty rows → all percentiles = ``Decimal('0')`` (no exception).
    Single-row input → all simulations sum to that value x len(sample);
    all percentiles equal that value (1 trade x 1 = trade pnl).

    Pure-Python (no numpy dep). Sub-100ms for 1000 sims x 100 trades in
    CPython per OQ-3 default A.
    """
    if not rows:
        zero = Decimal("0")
        return MonteCarloResult(
            n_simulations=n_simulations,
            seed=seed,
            p5=zero,
            p25=zero,
            p50=zero,
            p75=zero,
            p95=zero,
        )

    sample = [r.realized_pnl for r in rows]
    rng = random.Random(seed)  # noqa: S311 # nosec B311 — bootstrap MC is statistical, not cryptographic
    end_equities: list[Decimal] = []
    n_trades = len(sample)
    for _ in range(n_simulations):
        draw = rng.choices(sample, k=n_trades)
        end_equities.append(sum(draw, Decimal("0")))
    end_equities.sort()

    return MonteCarloResult(
        n_simulations=n_simulations,
        seed=seed,
        p5=_percentile_linear(end_equities, 5),
        p25=_percentile_linear(end_equities, 25),
        p50=_percentile_linear(end_equities, 50),
        p75=_percentile_linear(end_equities, 75),
        p95=_percentile_linear(end_equities, 95),
    )


def _percentile_linear(sorted_values: list[Decimal], pct: int) -> Decimal:
    """Linear interpolation between sorted values; matches NumPy method='linear'.

    Algorithm:
        rank = (pct/100) x (n-1)
        lower_idx = floor(rank); upper_idx = ceil(rank)
        frac = rank - lower_idx
        result = sorted[lower_idx] + frac x (sorted[upper_idx] - sorted[lower_idx])

    Empty list → ``Decimal('0')``; single-element → that element.
    """
    n = len(sorted_values)
    if n == 0:
        return Decimal("0")
    if n == 1:
        return sorted_values[0]
    rank_f = (Decimal(pct) / Decimal(100)) * Decimal(n - 1)
    lower_idx = int(rank_f)
    upper_idx = lower_idx + 1 if lower_idx < n - 1 else lower_idx
    frac = rank_f - Decimal(lower_idx)
    lower_val = sorted_values[lower_idx]
    upper_val = sorted_values[upper_idx]
    return lower_val + frac * (upper_val - lower_val)


# ---------------------------------------------------------------------------
# T-517a1 — per-symbol best-variant aggregate (BRIEF §13.6 second bullet)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VariantAggregateMetrics:
    """Per-variant computed metrics for ``/api/shadow/aggregate/{symbol}`` (T-517a1).

    8 metrics per variant_name. Decimal preserves NUMERIC(20,4) precision per
    §5.3 (total_pnl/avg_pnl/best/worst — money sums); float for win_rate +
    avg_mfe/mae_pct (statistical ratios per §5.13).
    """

    variant_name: str
    n_trades: int
    win_count: int
    win_rate: float
    total_pnl: Decimal
    avg_pnl: Decimal
    best_pnl: Decimal
    worst_pnl: Decimal
    avg_mfe_pct: float | None  # None when ALL rows have mfe_pct=None
    avg_mae_pct: float | None


def compute_variant_aggregate(
    rows: list[ShadowVariantAggregateRow],
) -> list[VariantAggregateMetrics]:
    """Group rows by variant_name + compute 8 metrics per variant (T-517a1).

    Returns variants sorted by ``(-total_pnl, variant_name)`` for deterministic
    order: primary descending by total_pnl (operator-visible "best variant"
    highlight); secondary tie-break ascending by variant_name (alphabetical) —
    guarantees stable output independent of SQL row iteration order or
    dict-insertion order. Empty input → empty list.

    Metrics:
        n_trades = count of rows for variant
        win_count = count where realized_pnl > 0
        win_rate = win_count / n_trades (float in [0, 1])
        total_pnl = sum(realized_pnl) (Decimal, precision-preserved)
        avg_pnl = total_pnl / n_trades (Decimal)
        best_pnl = max(realized_pnl) (Decimal)
        worst_pnl = min(realized_pnl) (Decimal)
        avg_mfe_pct = mean of non-None mfe_pct (float; None if all None)
        avg_mae_pct = mean of non-None mae_pct (float; None if all None)

    Empty-bucket guard: division-by-zero precluded by ``n_trades > 0``
    (variant in dict only if at least one row).
    """
    if not rows:
        return []
    buckets: dict[str, list[ShadowVariantAggregateRow]] = {}
    for r in rows:
        buckets.setdefault(r.variant_name, []).append(r)
    metrics: list[VariantAggregateMetrics] = []
    for variant_name, bucket_rows in buckets.items():
        n_trades = len(bucket_rows)
        pnls = [r.realized_pnl for r in bucket_rows]
        win_count = sum(1 for p in pnls if p > 0)
        total_pnl = sum(pnls, Decimal("0"))
        avg_pnl = total_pnl / Decimal(n_trades)
        best_pnl = max(pnls)
        worst_pnl = min(pnls)
        mfe_non_none = [r.mfe_pct for r in bucket_rows if r.mfe_pct is not None]
        mae_non_none = [r.mae_pct for r in bucket_rows if r.mae_pct is not None]
        avg_mfe_pct = sum(mfe_non_none) / len(mfe_non_none) if mfe_non_none else None
        avg_mae_pct = sum(mae_non_none) / len(mae_non_none) if mae_non_none else None
        metrics.append(
            VariantAggregateMetrics(
                variant_name=variant_name,
                n_trades=n_trades,
                win_count=win_count,
                win_rate=win_count / n_trades,
                total_pnl=total_pnl,
                avg_pnl=avg_pnl,
                best_pnl=best_pnl,
                worst_pnl=worst_pnl,
                avg_mfe_pct=avg_mfe_pct,
                avg_mae_pct=avg_mae_pct,
            )
        )
    return sorted(metrics, key=lambda m: (-m.total_pnl, m.variant_name))
