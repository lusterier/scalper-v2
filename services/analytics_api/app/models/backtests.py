"""Pydantic request + response models for ``/api/backtests/*`` (T-407).

Mirror :class:`packages.db.queries.analytics.BacktestRunRow` shape — the
12 columns from the ``backtest_runs`` table per migration 0012
(11 brief-spec columns per §7.2:1144-1156 + 12th ``bot_id`` for T-415
per-bot historic-runs UI filter).

``status`` uses the canonical :class:`packages.core.types.BacktestStatus`
StrEnum; serialized as lowercase string via ``use_enum_values=True``.
``id`` is UUID — Pydantic v2 default serializes UUID to string in JSON
(test pin asserts the shape).

POST creates a new run with ``status='queued'``; F4 ships only queue-and-
read surface, F5+ worker ships compute. ``date_range_start`` /
``date_range_end`` validated for ordering at request body level via
``model_validator(mode='after')``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any
from uuid import UUID  # noqa: TC003 — Pydantic runtime needs the type

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.core.types import (
    BacktestStatus,  # noqa: TC001 — Pydantic runtime needs the StrEnum
)

__all__ = [
    "BacktestRunCreateRequest",
    "BacktestRunListResponse",
    "BacktestRunResponse",
]


class BacktestRunResponse(BaseModel):
    """Single ``backtest_runs`` row projected to JSON for the dashboard.

    UUID ``id`` serialized as string (Pydantic v2 default). Status enum
    serialized as lowercase string via ``use_enum_values=True``.
    """

    model_config = ConfigDict(use_enum_values=True)

    id: UUID
    name: str
    bot_id: str
    config_yaml: str
    config_hash: str
    date_range_start: datetime
    date_range_end: datetime
    status: BacktestStatus
    started_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any] | None
    notes: str | None


class BacktestRunListResponse(BaseModel):
    """Envelope for ``GET /api/backtests/`` paginated collection."""

    runs: list[BacktestRunResponse]
    total: int
    limit: int
    offset: int


class BacktestRunCreateRequest(BaseModel):
    """POST /api/backtests/ request body.

    ``config_yaml`` max 200_000 chars (40x headroom per T-405 WG#13
    precedent). ``date_range_start < date_range_end`` enforced by
    model_validator (mode='after').
    """

    name: str = Field(..., min_length=1, max_length=200)
    bot_id: str = Field(..., min_length=1, max_length=64)
    config_yaml: str = Field(..., min_length=1, max_length=200_000)
    date_range_start: datetime
    date_range_end: datetime
    notes: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _date_range_ordered(self) -> BacktestRunCreateRequest:
        if self.date_range_start >= self.date_range_end:
            msg = "date_range_start must be < date_range_end"
            raise ValueError(msg)
        return self
