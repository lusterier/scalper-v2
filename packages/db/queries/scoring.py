"""scoring-engine query module (§7.2:1036-1055, §9.4:1543).

T-301 ships two helpers consumed by T-310 strategy-engine:

* :func:`insert_scoring_evaluation` — fire-and-forget audit row INSERT;
  ``@non_idempotent`` per :func:`packages.db.queries.execution.insert_trade_pnl_delta`
  precedent.
* :func:`select_scoring_evaluations_by_signal` — multi-bot fan-out read
  for analytics drill-down + T-313 E2 audit verification.

JSONB encoding follows :func:`packages.db.queries.execution.insert_trading_event`
precedent — Python-side ``json.dumps(...)`` + ``$N::jsonb`` cast in SQL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packages.core import idempotent, non_idempotent

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = [
    "ScoringEvaluationRow",
    "insert_scoring_evaluation",
    "select_scoring_evaluations_by_signal",
]


@dataclass(frozen=True, slots=True)
class ScoringEvaluationRow:
    """Read-only projection from :func:`select_scoring_evaluations_by_signal`.

    ``rule_results`` and ``feature_snapshot`` are opaque JSONB blobs —
    their inner shape is the T-300 / T-307 evaluator concern. T-301 only
    guarantees the outer Python types (``list[dict[str, Any]]`` and
    ``dict[str, Any]``).
    """

    id: int
    bot_id: str
    signal_id: int
    evaluated_at: datetime
    trigger_threshold: float
    total_score: float
    decision: str
    config_version: int
    rule_results: list[dict[str, Any]]
    feature_snapshot: dict[str, Any]
    correlation_id: str


_INSERT_SCORING_EVALUATION_SQL = """
    INSERT INTO scoring_evaluations (
        bot_id, signal_id, evaluated_at, trigger_threshold, total_score,
        decision, config_version, rule_results, feature_snapshot, correlation_id
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10)
"""


@non_idempotent
async def insert_scoring_evaluation(
    conn: _DbExecutor,
    *,
    bot_id: str,
    signal_id: int,
    evaluated_at: datetime,
    trigger_threshold: float,
    total_score: float,
    decision: str,
    config_version: int,
    rule_results: list[dict[str, Any]],
    feature_snapshot: dict[str, Any],
    correlation_id: str,
) -> None:
    """INSERT scoring_evaluations; fire-and-forget audit write.

    Returns ``None`` to mirror :func:`packages.db.queries.execution.insert_trade_pnl_delta`
    precedent — brief §9.4:1543 has no consumer needing the surrogate ``id``,
    so YAGNI on ``RETURNING id``. ``@non_idempotent`` per T-213b precedent.
    Caller (T-310 strategy-engine) is responsible for ensuring single-write
    per ``(bot_id, signal_id)`` pair (typically via signal-consumer dedup
    ring per §9.4:1533).
    """
    await conn.execute(
        _INSERT_SCORING_EVALUATION_SQL,
        bot_id,
        signal_id,
        evaluated_at,
        trigger_threshold,
        total_score,
        decision,
        config_version,
        json.dumps(rule_results),
        json.dumps(feature_snapshot),
        correlation_id,
    )


_SELECT_SCORING_EVALUATIONS_BY_SIGNAL_SQL = """
    SELECT id, bot_id, signal_id, evaluated_at, trigger_threshold, total_score,
           decision, config_version, rule_results, feature_snapshot, correlation_id
    FROM scoring_evaluations
    WHERE signal_id = $1
    ORDER BY evaluated_at ASC
"""


def _decode_jsonb(value: Any) -> Any:
    """Normalize JSONB column read across codec configurations.

    Default ``asyncpg.connect()`` returns JSONB columns as ``str``
    (raw JSON text); callers that register a JSONB codec via
    ``conn.set_type_codec(...)`` get auto-decoded Python ``dict``/``list``
    directly. The helper handles both — production pools may or may not
    register the codec, and CI integration tests use bare ``asyncpg.connect``
    without codec registration. CI-full T-301 integration test caught the
    mock-vs-real divergence per L-008 active control.
    """
    if isinstance(value, str):
        return json.loads(value)
    return value


@idempotent
async def select_scoring_evaluations_by_signal(
    conn: _DbExecutor,
    signal_id: int,
) -> list[ScoringEvaluationRow]:
    """Return all ``scoring_evaluations`` rows for a ``signal_id``.

    Multi-bot fan-out per §9.4:1533 — one signal can have N evaluations
    across active bots that subscribed to ``signals.validated``. Sort
    ``ORDER BY evaluated_at ASC`` for chronological drill-down per
    signal_id. JSONB columns are decoded via :func:`_decode_jsonb`
    defensive helper (see docstring for codec-vs-text handling).
    """
    rows = await conn.fetch(_SELECT_SCORING_EVALUATIONS_BY_SIGNAL_SQL, signal_id)
    return [
        ScoringEvaluationRow(
            id=int(row["id"]),
            bot_id=row["bot_id"],
            signal_id=int(row["signal_id"]),
            evaluated_at=row["evaluated_at"],
            trigger_threshold=float(row["trigger_threshold"]),
            total_score=float(row["total_score"]),
            decision=row["decision"],
            config_version=int(row["config_version"]),
            rule_results=_decode_jsonb(row["rule_results"]),
            feature_snapshot=_decode_jsonb(row["feature_snapshot"]),
            correlation_id=row["correlation_id"],
        )
        for row in rows
    ]
