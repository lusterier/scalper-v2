"""Pydantic response models for ``/api/analytics/*`` (T-406).

4 envelope models for 4 endpoints. Decimal fields → JSON string per
§5.3 (preserves NUMERIC precision); float fields → JSON number per
§5.13 (statistical metrics, not money).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from decimal import Decimal  # noqa: TC003 — Pydantic runtime needs the type

from pydantic import BaseModel

__all__ = [
    "ExpectancyResponse",
    "HeatmapCellResponse",
    "HeatmapResponse",
    "MonteCarloResponse",
    "PnlSeriesPointResponse",
    "PnlSeriesResponse",
]


class ExpectancyResponse(BaseModel):
    """``/api/analytics/expectancy`` single-row aggregate."""

    expectancy: float  # statistical, §5.13 float
    win_rate: float
    loss_rate: float
    avg_win: Decimal  # money, §5.3 Decimal → JSON string
    avg_loss: Decimal
    total_trades: int
    win_count: int
    loss_count: int
    bot_id: str | None
    from_at: datetime | None
    to_at: datetime | None


class HeatmapCellResponse(BaseModel):
    """One cell of 24x7 hourxweekday grid."""

    weekday: int  # 0=Monday, 6=Sunday (Python dt.weekday())
    hour: int
    trade_count: int
    avg_pnl: Decimal | None  # None when trade_count=0


class HeatmapResponse(BaseModel):
    """``/api/analytics/heatmap/hourly`` 24x7 grid (168 cells)."""

    cells: list[HeatmapCellResponse]
    bot_id: str | None
    from_at: datetime | None
    to_at: datetime | None


class PnlSeriesPointResponse(BaseModel):
    """One point on cumulative P&L time-series."""

    bucket_at: datetime
    bucket_pnl: Decimal
    cumulative_pnl: Decimal


class PnlSeriesResponse(BaseModel):
    """``/api/analytics/pnl-series`` cumulative time-series."""

    points: list[PnlSeriesPointResponse]
    bot_id: str | None
    from_at: datetime | None
    to_at: datetime | None
    bucket: str  # 'hour' | 'day'


class MonteCarloResponse(BaseModel):
    """``/api/analytics/monte-carlo`` bootstrap MC result envelope."""

    n_simulations: int
    seed: int
    p5: Decimal
    p25: Decimal
    p50: Decimal
    p75: Decimal
    p95: Decimal
    bot_id: str | None
    from_at: datetime | None
    to_at: datetime | None
