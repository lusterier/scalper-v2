"""§N4 unit tests for :mod:`packages.db.queries.scoring` (T-301).

Mock-based: ``conn.execute`` / ``conn.fetch`` return canned values.
DB-level integration coverage (real PG fetch + JSONB cast round-trip)
lives in ``tests/integration/queries/test_scoring.py`` per L-008
active control.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from packages.core.markers import is_idempotent
from packages.db.queries.scoring import (
    ScoringEvaluationRow,
    insert_scoring_evaluation,
    select_scoring_evaluations_by_signal,
)

_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


async def test_insert_scoring_evaluation_returns_none() -> None:
    """Mirror `insert_trade_pnl_delta` precedent — fire-and-forget audit row."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    await insert_scoring_evaluation(
        conn,
        bot_id="alpha",
        signal_id=42,
        evaluated_at=_FIXED_NOW,
        trigger_threshold=1.0,
        total_score=2.5,
        decision="execute",
        config_version=1,
        rule_results=[
            {"name": "r1", "weight": 1.0, "applied_weight": 1.0, "result": "True", "error": None}
        ],
        feature_snapshot={"sym1": {"value_num": "1.5"}},
        correlation_id="cid-1",
    )
    conn.execute.assert_awaited_once()


async def test_insert_scoring_evaluation_passes_jsonb_via_json_dumps() -> None:
    """JSONB params are passed as `json.dumps(...)` strings + bound as `$N::jsonb`."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    rule_results = [
        {"name": "r1", "weight": 1.0, "applied_weight": 1.0, "result": "True", "error": None}
    ]
    feature_snapshot = {"sym1": {"value_num": "1.5"}}
    await insert_scoring_evaluation(
        conn,
        bot_id="alpha",
        signal_id=42,
        evaluated_at=_FIXED_NOW,
        trigger_threshold=1.0,
        total_score=2.5,
        decision="execute",
        config_version=1,
        rule_results=rule_results,
        feature_snapshot=feature_snapshot,
        correlation_id="cid-1",
    )
    args = conn.execute.await_args.args
    sql = args[0]
    assert "INSERT INTO scoring_evaluations" in sql
    assert "$8::jsonb" in sql
    assert "$9::jsonb" in sql
    # Positional params: $1 bot_id, $2 signal_id, $3 evaluated_at, $4 trigger,
    # $5 total_score, $6 decision, $7 config_version, $8 rule_results,
    # $9 feature_snapshot, $10 correlation.
    rule_results_arg = args[8]  # SQL is args[0], so $8 is at args[8].
    feature_snapshot_arg = args[9]
    assert isinstance(rule_results_arg, str)
    assert isinstance(feature_snapshot_arg, str)
    assert json.loads(rule_results_arg) == rule_results
    assert json.loads(feature_snapshot_arg) == feature_snapshot


def test_insert_scoring_evaluation_marker_is_non_idempotent() -> None:
    """§N3 — INSERT is non-idempotent (no replay-safe key)."""
    assert is_idempotent(insert_scoring_evaluation) is False


def test_select_scoring_evaluations_by_signal_marker_is_idempotent() -> None:
    """§N3 — SELECT is idempotent (side-effect-free)."""
    assert is_idempotent(select_scoring_evaluations_by_signal) is True


async def test_select_by_signal_orders_by_evaluated_at_asc() -> None:
    """SQL contains `ORDER BY evaluated_at ASC` — chronological per signal_id."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    result = await select_scoring_evaluations_by_signal(conn, 42)
    assert result == []
    sql = conn.fetch.await_args.args[0]
    assert "WHERE signal_id = $1" in sql
    assert "ORDER BY evaluated_at ASC" in sql
    assert conn.fetch.await_args.args[1] == 42


async def test_select_scoring_evaluations_by_signal_returns_empty_list_when_no_rows() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    result = await select_scoring_evaluations_by_signal(conn, 999)
    assert result == []


async def test_select_scoring_evaluations_by_signal_decodes_jsonb_str_via_json_loads() -> None:
    """Defensive: when asyncpg returns JSONB as str (no codec registered),
    helper json.loads it. CI-full L-008 hotfix pin (T-301).
    """
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 1,
                "bot_id": "alpha",
                "signal_id": 42,
                "evaluated_at": _FIXED_NOW,
                "trigger_threshold": 1.0,
                "total_score": 2.5,
                "decision": "execute",
                "config_version": 1,
                "rule_results": '[{"name": "r1", "result": "True"}]',  # str
                "feature_snapshot": '{"sym1": {"v": 1}}',  # str
                "correlation_id": "cid-1",
            },
        ]
    )
    result = await select_scoring_evaluations_by_signal(conn, 42)
    assert len(result) == 1
    row = result[0]
    assert row.rule_results == [{"name": "r1", "result": "True"}]
    assert row.feature_snapshot == {"sym1": {"v": 1}}


async def test_select_passes_through_dict_when_codec_registered() -> None:
    """asyncpg with JSONB codec registered → already decoded list/dict; helper passes through."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 1,
                "bot_id": "alpha",
                "signal_id": 42,
                "evaluated_at": _FIXED_NOW,
                "trigger_threshold": 1.0,
                "total_score": 2.5,
                "decision": "execute",
                "config_version": 1,
                "rule_results": [{"name": "r1", "result": "True"}],
                "feature_snapshot": {"sym1": {"v": 1}},
                "correlation_id": "cid-1",
            },
        ]
    )
    result = await select_scoring_evaluations_by_signal(conn, 42)
    assert len(result) == 1
    row = result[0]
    assert isinstance(row, ScoringEvaluationRow)
    assert row.bot_id == "alpha"
    assert row.signal_id == 42
    assert row.decision == "execute"
    assert row.rule_results == [{"name": "r1", "result": "True"}]
    assert row.feature_snapshot == {"sym1": {"v": 1}}
