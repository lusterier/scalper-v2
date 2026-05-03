"""Pure-math tests for `services.analytics_api.app.analytics_compute` (T-406).

15 tests covering 4 compute functions + edge cases (empty, all-wins,
all-losses, sparse pnl-series, deterministic MC seed, single-row MC).
Hand-computed fixtures matching plan §Hand verification section.

WG#3 (empty short-circuit), WG#4 (Decimal precision determinism),
WG#5 (weekday() not isoweekday()), WG#6 (sparse-gap pnl-series test).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from packages.db.queries.analytics import TradeRealizedPnlRow
from services.analytics_api.app.analytics_compute import (
    HeatmapCell,
    compute_expectancy,
    compute_hourly_heatmap,
    compute_monte_carlo,
    compute_pnl_series,
)

_T_BASE = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _row(pnl: str, *, closed_at: datetime = _T_BASE, bot_id: str = "alpha") -> TradeRealizedPnlRow:
    return TradeRealizedPnlRow(
        realized_pnl=Decimal(pnl),
        closed_at=closed_at,
        bot_id=bot_id,
    )


# ---------------------------------------------------------------------------
# compute_expectancy (5 tests)
# ---------------------------------------------------------------------------


def test_compute_expectancy_empty_rows_returns_all_zero() -> None:
    """WG#3 — empty rows short-circuits to all-zero result; no DivisionUndefined."""
    metrics = compute_expectancy([])
    assert metrics.expectancy == 0.0
    assert metrics.win_rate == 0.0
    assert metrics.loss_rate == 0.0
    assert metrics.avg_win == Decimal("0")
    assert metrics.avg_loss == Decimal("0")
    assert metrics.total_trades == 0
    assert metrics.win_count == 0
    assert metrics.loss_count == 0


def test_compute_expectancy_5_trades_hand_computed() -> None:
    """Plan §Hand verification fixture: [+10, +5, -3, +8, -2]."""
    rows = [_row("10"), _row("5"), _row("-3"), _row("8"), _row("-2")]
    metrics = compute_expectancy(rows)
    assert metrics.total_trades == 5
    assert metrics.win_count == 3
    assert metrics.loss_count == 2
    assert metrics.win_rate == 0.6
    assert metrics.loss_rate == 0.4
    assert metrics.avg_win == Decimal("23") / Decimal("3")  # 7.6666...
    assert metrics.avg_loss == Decimal("5") / Decimal("2")  # 2.5
    # expectancy = 0.6 x 7.6667 - 0.4 x 2.5 ≈ 3.6 (within float epsilon)
    assert abs(metrics.expectancy - 3.6) < 1e-4


def test_compute_expectancy_all_wins() -> None:
    rows = [_row("5"), _row("10"), _row("3")]
    metrics = compute_expectancy(rows)
    assert metrics.win_rate == 1.0
    assert metrics.loss_rate == 0.0
    assert metrics.avg_loss == Decimal("0")
    # expectancy = 1.0 x avg_win - 0.0 = avg_win as float
    assert metrics.expectancy == float(metrics.avg_win)


def test_compute_expectancy_all_losses() -> None:
    rows = [_row("-5"), _row("-10"), _row("-3")]
    metrics = compute_expectancy(rows)
    assert metrics.win_rate == 0.0
    assert metrics.loss_rate == 1.0
    assert metrics.avg_win == Decimal("0")
    # expectancy = 0.0 - 1.0 x avg_loss = -avg_loss
    assert metrics.expectancy == -float(metrics.avg_loss)


def test_compute_expectancy_zero_pnl_excluded_from_buckets() -> None:
    """Trades with realized_pnl == 0 (flat) excluded from win/loss but counted in total."""
    rows = [_row("10"), _row("0"), _row("-5")]
    metrics = compute_expectancy(rows)
    assert metrics.total_trades == 3
    assert metrics.win_count == 1
    assert metrics.loss_count == 1
    # WR + LR = 2/3, gap = 1/3 (the zero trade)
    assert abs(metrics.win_rate - 1 / 3) < 1e-9
    assert abs(metrics.loss_rate - 1 / 3) < 1e-9


# ---------------------------------------------------------------------------
# compute_hourly_heatmap (3 tests)
# ---------------------------------------------------------------------------


def test_compute_hourly_heatmap_returns_168_cells() -> None:
    """Empty input → 168 cells in deterministic order: weekday 0..6 x hour 0..23."""
    cells = compute_hourly_heatmap([])
    assert len(cells) == 168
    # First cell: weekday=0 (Monday), hour=0
    assert cells[0] == HeatmapCell(weekday=0, hour=0, trade_count=0, avg_pnl=None)
    # Last cell: weekday=6 (Sunday), hour=23
    assert cells[-1] == HeatmapCell(weekday=6, hour=23, trade_count=0, avg_pnl=None)


def test_compute_hourly_heatmap_uses_weekday_not_isoweekday() -> None:
    """WG#5 — Python dt.weekday() returns 0=Monday..6=Sunday (NOT isoweekday() 1..7).

    Sunday 2026-05-03 12:00 UTC → cell weekday=6, hour=12.
    """
    sunday = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
    rows = [_row("5", closed_at=sunday)]
    cells = compute_hourly_heatmap(rows)
    # Find the (weekday=6, hour=12) cell.
    target = next(c for c in cells if c.weekday == 6 and c.hour == 12)
    assert target.trade_count == 1
    assert target.avg_pnl == Decimal("5")


def test_compute_hourly_heatmap_avg_pnl_uses_decimal_arithmetic() -> None:
    rows = [
        _row("10", closed_at=datetime(2026, 5, 4, 14, 0, tzinfo=UTC)),  # Monday 14h
        _row("20", closed_at=datetime(2026, 5, 4, 14, 30, tzinfo=UTC)),
    ]
    cells = compute_hourly_heatmap(rows)
    target = next(c for c in cells if c.weekday == 0 and c.hour == 14)
    assert target.trade_count == 2
    assert isinstance(target.avg_pnl, Decimal)
    assert target.avg_pnl == Decimal("15")


# ---------------------------------------------------------------------------
# compute_pnl_series (4 tests; WG#6 sparse-gap added)
# ---------------------------------------------------------------------------


def test_compute_pnl_series_empty_rows_returns_empty_list() -> None:
    points = compute_pnl_series([], bucket="day")
    assert points == []


def test_compute_pnl_series_day_bucket_cumulative() -> None:
    """3 trades on 2 consecutive days → 2 points with correct cumulative_pnl."""
    rows = [
        _row("5", closed_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC)),
        _row("3", closed_at=datetime(2026, 5, 1, 15, 0, tzinfo=UTC)),
        _row("-2", closed_at=datetime(2026, 5, 2, 10, 0, tzinfo=UTC)),
    ]
    points = compute_pnl_series(rows, bucket="day")
    assert len(points) == 2
    assert points[0].bucket_at == datetime(2026, 5, 1, tzinfo=UTC)
    assert points[0].bucket_pnl == Decimal("8")
    assert points[0].cumulative_pnl == Decimal("8")
    assert points[1].bucket_at == datetime(2026, 5, 2, tzinfo=UTC)
    assert points[1].bucket_pnl == Decimal("-2")
    assert points[1].cumulative_pnl == Decimal("6")


def test_compute_pnl_series_hour_bucket_truncates_to_hour_boundary() -> None:
    rows = [_row("5", closed_at=datetime(2026, 5, 1, 14, 35, 22, tzinfo=UTC))]
    points = compute_pnl_series(rows, bucket="hour")
    assert points[0].bucket_at == datetime(2026, 5, 1, 14, 0, 0, tzinfo=UTC)


def test_compute_pnl_series_sparse_gap_skips_empty_buckets() -> None:
    """WG#6 — empty buckets between non-empty ones NOT emitted (sparse series).

    3 trades on day 1 + 3 trades on day 5 → 2 points (NOT 5 with empty middles).
    """
    rows = [
        _row("5", closed_at=datetime(2026, 5, 1, 10, 0, tzinfo=UTC)),
        _row("3", closed_at=datetime(2026, 5, 1, 15, 0, tzinfo=UTC)),
        _row("-1", closed_at=datetime(2026, 5, 1, 20, 0, tzinfo=UTC)),
        _row("4", closed_at=datetime(2026, 5, 5, 9, 0, tzinfo=UTC)),
        _row("2", closed_at=datetime(2026, 5, 5, 13, 0, tzinfo=UTC)),
        _row("6", closed_at=datetime(2026, 5, 5, 18, 0, tzinfo=UTC)),
    ]
    points = compute_pnl_series(rows, bucket="day")
    assert len(points) == 2  # NOT 5
    assert points[0].bucket_at == datetime(2026, 5, 1, tzinfo=UTC)
    assert points[1].bucket_at == datetime(2026, 5, 5, tzinfo=UTC)
    assert points[0].cumulative_pnl == Decimal("7")  # 5+3-1
    assert points[1].cumulative_pnl == Decimal("19")  # 7 + (4+2+6)


# ---------------------------------------------------------------------------
# compute_monte_carlo (3 tests)
# ---------------------------------------------------------------------------


def test_compute_monte_carlo_deterministic_with_seed() -> None:
    """Same seed + same input → identical percentiles (deterministic regression pin)."""
    rows = [_row(str(v)) for v in [1, 2, 3, 4, 5, -1, -2, 10, -3, 7]]
    r1 = compute_monte_carlo(rows, n_simulations=500, seed=42)
    r2 = compute_monte_carlo(rows, n_simulations=500, seed=42)
    assert r1.p5 == r2.p5
    assert r1.p25 == r2.p25
    assert r1.p50 == r2.p50
    assert r1.p75 == r2.p75
    assert r1.p95 == r2.p95


def test_compute_monte_carlo_empty_rows_returns_zero_percentiles() -> None:
    result = compute_monte_carlo([], n_simulations=100, seed=42)
    assert result.p5 == Decimal("0")
    assert result.p25 == Decimal("0")
    assert result.p50 == Decimal("0")
    assert result.p75 == Decimal("0")
    assert result.p95 == Decimal("0")


def test_compute_monte_carlo_single_row_input() -> None:
    """Single trade → all simulations equal trade pnl (k=1 sample x 1 trade = trade)."""
    rows = [_row("5.00")]
    result = compute_monte_carlo(rows, n_simulations=100, seed=42)
    # All simulations: 1 sample, all draws are the single trade → equity = 5.00
    assert result.p5 == Decimal("5.00")
    assert result.p50 == Decimal("5.00")
    assert result.p95 == Decimal("5.00")
