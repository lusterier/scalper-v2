"""§N4 unit tests for :mod:`packages.db.queries.trades` (T-526).

Mock-based: ``conn.fetch`` returns canned rows. Pin:

* Returned rows narrow to :class:`ClosedTradeRow` (2 fields).
* SQL string contains charter-invariant predicates per WG#1:
  ``WHERE bot_id = $1`` + ``status = 'closed'`` + ``realized_pnl IS NOT NULL``.
* SQL string contains ``ORDER BY closed_at DESC, id DESC`` deterministic tie-break.
* SQL string contains ``LIMIT $2`` (L-021-safe LIMIT-direct context).
* ``table_name`` Literal dispatch: ``trades`` → live SQL, ``paper_trades`` →
  paper SQL (inline f-string; Literal-typed dispatcher; no injection surface).
* ``$N`` placeholder positions are the only parameter binds (L-008 mock-level
  pin pattern).
* Empty fetch → empty list (not None).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.trades import (
    ClosedTradeRow,
    count_open_trades,
    select_recent_closed_trades,
    sum_realized_pnl_since,
)

_T1 = datetime(2026, 5, 15, 10, 0, 0, tzinfo=UTC)
_T2 = datetime(2026, 5, 15, 9, 50, 0, tzinfo=UTC)


pytestmark = pytest.mark.asyncio


async def test_returns_typed_closedtraderow_list() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"realized_pnl": Decimal("-5.00"), "closed_at": _T1},
            {"realized_pnl": Decimal("-3.00"), "closed_at": _T2},
        ]
    )
    rows = await select_recent_closed_trades(conn, bot_id="alpha", table_name="trades", limit=2)
    assert len(rows) == 2
    assert all(isinstance(r, ClosedTradeRow) for r in rows)
    assert rows[0].realized_pnl == Decimal("-5.00")
    assert rows[0].closed_at == _T1
    assert rows[1].realized_pnl == Decimal("-3.00")


async def test_empty_fetch_returns_empty_list() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    rows = await select_recent_closed_trades(conn, bot_id="alpha", table_name="trades", limit=5)
    assert rows == []


async def test_sql_string_contains_charter_invariant_predicates_live() -> None:
    """WG#1: SQL MUST contain status='closed' + realized_pnl IS NOT NULL + bot_id=$1."""
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured.append(sql)
        return []

    conn.fetch = _capture
    await select_recent_closed_trades(conn, bot_id="alpha", table_name="trades", limit=3)
    sql = captured[0]
    assert "FROM trades " in sql
    assert "WHERE bot_id = $1" in sql
    assert "status = 'closed'" in sql
    assert "realized_pnl IS NOT NULL" in sql


async def test_sql_string_contains_charter_invariant_predicates_paper() -> None:
    """WG#1: paper variant inlines paper_trades table but same invariants."""
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured.append(sql)
        return []

    conn.fetch = _capture
    await select_recent_closed_trades(conn, bot_id="beta", table_name="paper_trades", limit=3)
    sql = captured[0]
    assert "FROM paper_trades " in sql
    assert "WHERE bot_id = $1" in sql
    assert "status = 'closed'" in sql
    assert "realized_pnl IS NOT NULL" in sql


async def test_sql_string_contains_deterministic_order_by() -> None:
    """ORDER BY closed_at DESC, id DESC for tie-break on same-microsecond closes."""
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured.append(sql)
        return []

    conn.fetch = _capture
    await select_recent_closed_trades(conn, bot_id="alpha", table_name="trades", limit=3)
    assert "ORDER BY closed_at DESC, id DESC" in captured[0]


async def test_sql_string_contains_limit_dollar_two() -> None:
    """LIMIT $2 — L-021-safe LIMIT-direct context; no ::int cast needed."""
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured.append(sql)
        return []

    conn.fetch = _capture
    await select_recent_closed_trades(conn, bot_id="alpha", table_name="trades", limit=5)
    assert "LIMIT $2" in captured[0]


async def test_binds_bot_id_and_limit_in_dollar_one_dollar_two_order() -> None:
    """L-008 pin: $1=bot_id, $2=limit — positional binds match SQL positions."""
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(_sql: str, *args: Any) -> list[Any]:
        captured_args.append(args)
        return []

    conn.fetch = _capture
    await select_recent_closed_trades(conn, bot_id="alpha", table_name="trades", limit=7)
    assert captured_args == [("alpha", 7)]


# ---------------------------------------------------------------------------
# count_open_trades (T-524)
# ---------------------------------------------------------------------------


async def test_count_open_trades_returns_typed_int() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=(7,))
    n = await count_open_trades(conn, table_name="trades", bot_id="alpha")
    assert n == 7
    assert isinstance(n, int)


async def test_count_open_trades_none_row_returns_zero() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    n = await count_open_trades(conn, table_name="trades", bot_id="alpha")
    assert n == 0


async def test_count_open_trades_per_bot_sql_has_status_open_and_bot_id() -> None:
    """WG#1: per-bot SQL contains `status = 'open'` + `bot_id = $1`."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> tuple[int]:
        captured.append((sql, *args))
        return (0,)

    conn.fetchrow = _capture
    await count_open_trades(conn, table_name="trades", bot_id="alpha")
    sql = captured[0][0]
    assert "FROM trades " in sql
    assert "WHERE bot_id = $1 AND status = 'open'" in sql
    # WG#2 L-021: no ::text / ::timestamptz cast literals (column-direct $1 only).
    assert "::text" not in sql
    assert "::timestamptz" not in sql
    assert captured[0][1:] == ("alpha",)


async def test_count_open_trades_global_sql_has_no_bot_id_predicate() -> None:
    """WG#1: global SQL (bot_id=None) has `status = 'open'` and NO bot_id bind."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> tuple[int]:
        captured.append((sql, *args))
        return (0,)

    conn.fetchrow = _capture
    await count_open_trades(conn, table_name="trades", bot_id=None)
    sql = captured[0][0]
    assert "WHERE status = 'open'" in sql
    assert "bot_id" not in sql
    assert captured[0][1:] == ()  # no $1 bind


async def test_count_open_trades_paper_table_dispatch() -> None:
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_a: Any) -> tuple[int]:
        captured.append(sql)
        return (0,)

    conn.fetchrow = _capture
    await count_open_trades(conn, table_name="paper_trades", bot_id="beta")
    assert "FROM paper_trades " in captured[0]


async def test_count_open_trades_live_table_dispatch() -> None:
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_a: Any) -> tuple[int]:
        captured.append(sql)
        return (0,)

    conn.fetchrow = _capture
    await count_open_trades(conn, table_name="trades", bot_id=None)
    assert "FROM trades " in captured[0]


# ---------------------------------------------------------------------------
# sum_realized_pnl_since (T-525a2)
# ---------------------------------------------------------------------------

_SINCE = datetime(2026, 5, 15, 0, 0, 0, tzinfo=UTC)


async def test_sum_realized_pnl_since_returns_decimal() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=(Decimal("-105.0000"),))
    total = await sum_realized_pnl_since(
        conn, table_name="paper_trades", bot_id="alpha", since=_SINCE
    )
    assert total == Decimal("-105.0000")
    assert isinstance(total, Decimal)


async def test_sum_realized_pnl_since_zero_rows_returns_decimal_zero() -> None:
    """COALESCE → Decimal('0') (never None) on a fresh trading day."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=(Decimal("0"),))
    total = await sum_realized_pnl_since(conn, table_name="trades", bot_id="alpha", since=_SINCE)
    assert total == Decimal("0")


async def test_sum_realized_pnl_since_none_row_returns_decimal_zero() -> None:
    """Defensive: fetchrow None → Decimal('0') (gate must not crash)."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    total = await sum_realized_pnl_since(conn, table_name="trades", bot_id="alpha", since=_SINCE)
    assert total == Decimal("0")


async def test_sum_realized_pnl_since_sql_l021_timestamptz_cast_and_charter() -> None:
    """WG#2 / L-021: SQL ships explicit $2::timestamptz; charter predicates;
    $1 column-direct (no cast); no NOW()/CURRENT_TIMESTAMP/current_date."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> tuple[Decimal]:
        captured.append((sql, *args))
        return (Decimal("0"),)

    conn.fetchrow = _capture
    await sum_realized_pnl_since(conn, table_name="paper_trades", bot_id="alpha", since=_SINCE)
    sql = captured[0][0]
    assert "FROM paper_trades " in sql
    assert "WHERE bot_id = $1" in sql
    assert "status = 'closed'" in sql
    assert "realized_pnl IS NOT NULL" in sql
    assert "COALESCE(SUM(realized_pnl), 0)" in sql
    # L-021: explicit timestamptz cast on the comparison parameter.
    assert "closed_at >= $2::timestamptz" in sql
    # $1 stays column-direct TEXT equality — no cast literal on it.
    assert "$1::" not in sql
    low = sql.lower()
    assert "now()" not in low
    assert "current_timestamp" not in low
    assert "current_date" not in low
    # $N binds positional: $1 bot_id, $2 since.
    assert captured[0][1:] == ("alpha", _SINCE)


async def test_sum_realized_pnl_since_live_table_dispatch() -> None:
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_a: Any) -> tuple[Decimal]:
        captured.append(sql)
        return (Decimal("0"),)

    conn.fetchrow = _capture
    await sum_realized_pnl_since(conn, table_name="trades", bot_id="beta", since=_SINCE)
    assert "FROM trades " in captured[0]
