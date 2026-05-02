"""Integration tests for :mod:`packages.db.queries.scoring` (T-301).

Runs against a throwaway PostgreSQL + TimescaleDB migrated to head
(includes Migration 0010 ``scoring_evaluations`` hypertable).

Per L-008 active control: ``insert_scoring_evaluation`` exercises the
``$N::jsonb`` cast which mock-only tests cannot catch. Round-trip
verifies that ``json.dumps(...)`` Python-side encoding survives the
PG JSONB column boundary.

Skipped at collection when ``POSTGRES_TEST_DSN`` is unset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg

from packages.db.queries.scoring import (
    insert_scoring_evaluation,
    select_scoring_evaluations_by_signal,
)

_T_BASE = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


async def test_insert_scoring_evaluation_real_pg_jsonb_round_trip(
    migrated_db_dsn: str,
) -> None:
    """Verify `::jsonb` cast survives INSERT + SELECT round-trip via real PG."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        rule_results = [
            {"name": "r1", "weight": 1.0, "applied_weight": 1.0, "result": "True", "error": None},
            {"name": "r2", "weight": -0.5, "applied_weight": 0.0, "result": "False", "error": None},
        ]
        feature_snapshot = {"sym1": {"value_num": "1.5"}, "sym2": {"value_bool": True}}
        await insert_scoring_evaluation(
            conn,
            bot_id="alpha",
            signal_id=42,
            evaluated_at=_T_BASE,
            trigger_threshold=1.5,
            total_score=2.5,
            decision="execute",
            config_version=1,
            rule_results=rule_results,
            feature_snapshot=feature_snapshot,
            correlation_id="cid-real-1",
        )
        rows = await select_scoring_evaluations_by_signal(conn, 42)
        assert len(rows) == 1
        row = rows[0]
        assert row.bot_id == "alpha"
        assert row.signal_id == 42
        assert row.trigger_threshold == 1.5
        assert row.total_score == 2.5
        assert row.decision == "execute"
        assert row.config_version == 1
        assert row.rule_results == rule_results
        assert row.feature_snapshot == feature_snapshot
        assert row.correlation_id == "cid-real-1"
    finally:
        await conn.close()


async def test_select_scoring_evaluations_by_signal_returns_chronological_order(
    migrated_db_dsn: str,
) -> None:
    """3 rows distinct evaluated_at; SELECT returns ASC chronological."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # Insert in reverse-chronological order to verify ORDER BY ASC re-sorts.
        for offset_seconds, bot_id in ((2, "gamma"), (0, "alpha"), (1, "beta")):
            await insert_scoring_evaluation(
                conn,
                bot_id=bot_id,
                signal_id=99,
                evaluated_at=_T_BASE + timedelta(seconds=offset_seconds),
                trigger_threshold=1.0,
                total_score=1.0,
                decision="execute",
                config_version=1,
                rule_results=[],
                feature_snapshot={},
                correlation_id=f"cid-{bot_id}",
            )
        rows = await select_scoring_evaluations_by_signal(conn, 99)
        assert [r.bot_id for r in rows] == ["alpha", "beta", "gamma"]
        assert rows[0].evaluated_at == _T_BASE
        assert rows[1].evaluated_at == _T_BASE + timedelta(seconds=1)
        assert rows[2].evaluated_at == _T_BASE + timedelta(seconds=2)
    finally:
        await conn.close()
