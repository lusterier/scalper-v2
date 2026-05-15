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

from packages.db.queries.trades import ClosedTradeRow, select_recent_closed_trades

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
