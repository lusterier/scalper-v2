"""Pydantic response models for ``/api/scoring/*`` (T-403).

Mirror :class:`packages.db.queries.analytics.ScoringEvaluationRow`
shape — the 11 fields from the ``scoring_evaluations`` table per
BRIEF §7.2:1039-1051.

DOUBLE PRECISION fields (``trigger_threshold`` / ``total_score``)
serialize as JSON numbers (floats) per §5.13 — statistical metrics,
not money. ``decision`` uses canonical
:class:`packages.core.types.ScoringDecision` StrEnum (T-403 WG#1).
``rule_results`` is a JSONB array; ``feature_snapshot`` is a JSONB
object — both round-trip via T-401a's ``_register_jsonb_codec``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.core.types import ScoringDecision  # noqa: TC001 — Pydantic runtime needs the StrEnum

__all__ = ["ScoringEvaluationListResponse", "ScoringEvaluationResponse"]


class ScoringEvaluationResponse(BaseModel):
    """Single ``scoring_evaluations`` row projected to JSON."""

    model_config = ConfigDict(use_enum_values=True)

    id: int
    bot_id: str
    signal_id: int
    evaluated_at: datetime
    trigger_threshold: float
    total_score: float
    decision: ScoringDecision
    config_version: int
    rule_results: list[dict[str, Any]]
    feature_snapshot: dict[str, Any]
    correlation_id: str


class ScoringEvaluationListResponse(BaseModel):
    """Envelope for ``GET /api/scoring/by-signal/{signal_id}`` collection."""

    evaluations: list[ScoringEvaluationResponse]
