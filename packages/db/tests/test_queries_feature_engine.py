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

from packages.db.queries.feature_engine import LatestFeatureRow, select_latest_feature

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
