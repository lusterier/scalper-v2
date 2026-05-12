"""§N4 unit tests for :mod:`packages.db.queries.feature_engine` (T-306).

Mock-based: ``conn.fetchrow`` returns canned values. Integration coverage
(real PG round-trip) lives in ``tests/integration/queries/test_feature_engine.py``
per L-008 active control.

Sibling-flat convention per T-301 ``test_queries_scoring.py`` — first
mock-test file for this query module (``insert_feature``,
``fetch_warmup_window``, ``fetch_ohlc_range`` previously had only
integration coverage).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from packages.db.queries.feature_engine import (
    LatestFeatureRow,
    select_feature_history,
    select_latest_feature,
)

_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


async def test_select_latest_feature_returns_row_on_hit() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "value_num": 50000.5,
            "value_bool": None,
            "value_json": None,
            "computed_at": _FIXED_NOW,
        }
    )
    result = await select_latest_feature(
        conn, feature_name="ind.btcusdt.15m.ema_20", symbol="btcusdt"
    )
    assert result == LatestFeatureRow(
        value_num=50000.5, value_bool=None, value_json=None, computed_at=_FIXED_NOW
    )


async def test_select_latest_feature_returns_none_when_no_row() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    result = await select_latest_feature(conn, feature_name="ind.x.1m.foo", symbol="x")
    assert result is None


async def test_select_latest_feature_sql_pin_order_by_computed_at_desc_limit_1() -> None:
    """SQL must use ``features_latest`` index — ORDER BY computed_at DESC LIMIT 1."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    await select_latest_feature(conn, feature_name="x", symbol="y")
    sql = conn.fetchrow.await_args.args[0]
    assert "WHERE feature_name = $1 AND symbol = $2" in sql
    assert "ORDER BY computed_at DESC" in sql
    assert "LIMIT 1" in sql


async def test_select_latest_feature_value_bool_variant_passthrough() -> None:
    """value_bool variant → primitives stored as-is (no FeatureValue construction here per §N7)."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "value_num": None,
            "value_bool": True,
            "value_json": None,
            "computed_at": _FIXED_NOW,
        }
    )
    result = await select_latest_feature(conn, feature_name="ind.x.1m.bool_flag", symbol="x")
    assert result is not None
    assert result.value_num is None
    assert result.value_bool is True
    assert result.value_json is None


async def test_select_latest_feature_value_json_variant_passthrough() -> None:
    """value_json variant → primitives stored as-is."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "value_num": None,
            "value_bool": None,
            "value_json": {"upper": 100.5, "lower": 99.5},
            "computed_at": _FIXED_NOW,
        }
    )
    result = await select_latest_feature(conn, feature_name="ind.x.1m.bb", symbol="x")
    assert result is not None
    assert result.value_json == {"upper": 100.5, "lower": 99.5}


# ---------------------------------------------------------------------------
# T-520 sub-commit #2 — select_feature_history
# ---------------------------------------------------------------------------


async def test_select_feature_history_returns_chronological_list_oldest_to_newest() -> None:
    """ORDER BY computed_at ASC after inner LIMIT N descending — oldest → newest."""
    conn = MagicMock()
    older = _FIXED_NOW.replace(minute=0)
    middle = _FIXED_NOW.replace(minute=15)
    newer = _FIXED_NOW.replace(minute=30)
    conn.fetch = AsyncMock(
        return_value=[
            {"value_num": 100.0, "value_bool": None, "value_json": None, "computed_at": older},
            {"value_num": 101.0, "value_bool": None, "value_json": None, "computed_at": middle},
            {"value_num": 102.0, "value_bool": None, "value_json": None, "computed_at": newer},
        ]
    )
    result = await select_feature_history(
        conn,
        feature_name="ind.btcusdt.15m.ema_20",
        symbol="btcusdt",
        n_samples=3,
    )
    assert len(result) == 3
    assert [r.value_num for r in result] == [100.0, 101.0, 102.0]
    assert [r.computed_at for r in result] == [older, middle, newer]


async def test_select_feature_history_empty_on_miss() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    result = await select_feature_history(
        conn, feature_name="ind.x.1m.foo", symbol="x", n_samples=10
    )
    assert result == []


async def test_select_feature_history_caps_n_at_max_history_window() -> None:
    """WG#3 + CONCERN#3 plan-stage: n_samples > 200 silently capped at 200."""
    conn = MagicMock()
    captured: list[tuple[object, ...]] = []

    async def _capture(sql: str, *args: object) -> list[object]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_feature_history(conn, feature_name="ind.x.1m.foo", symbol="x", n_samples=10000)
    _sql, *bind_args = captured[0]
    # Cap enforced — bind arg #3 is 200, not 10000.
    assert bind_args[2] == 200


async def test_select_feature_history_sql_pin_l021_casts() -> None:
    """WG#4 + L-021: $1::text + $2::text + $3::int casts must be present."""
    conn = MagicMock()
    captured: list[tuple[object, ...]] = []

    async def _capture(sql: str, *args: object) -> list[object]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_feature_history(conn, feature_name="x", symbol="y", n_samples=5)
    sql, *_ = captured[0]
    assert isinstance(sql, str)
    assert "$1::text" in sql
    assert "$2::text" in sql
    assert "$3::int" in sql
    # Two-step ORDER BY (inner DESC + outer ASC).
    assert "ORDER BY computed_at DESC" in sql
    assert "ORDER BY computed_at ASC" in sql
