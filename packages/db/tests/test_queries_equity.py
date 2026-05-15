"""§N4 unit tests for :mod:`packages.db.queries.equity` (T-531).

Mock-based: ``conn.execute`` is an :class:`AsyncMock`. The SQL-string
assertions are the **L-021 regression tripwire** — they pin the verbatim
``VALUES ($1, $2, $3, $4, $5, $6, $7)`` list and assert NO ``::`` cast
anywhere, so a future edit that introduces a ``$N::type`` cast / CASE /
arithmetic context is caught at mock level before the testcontainer
round-trip (real-PG INSERT covered in
``tests/integration/queries/test_equity.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from packages.core import is_non_idempotent
from packages.db.queries.equity import insert_equity_snapshot

_FIXED_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


def test_insert_equity_snapshot_is_non_idempotent_marked() -> None:
    """§N3 — append-only audit-grade write, explicitly @non_idempotent."""
    assert is_non_idempotent(insert_equity_snapshot) is True


async def test_insert_equity_snapshot_sql_pin_and_bind_order() -> None:
    """L-021 tripwire + $N bind-slot order (mirror test_insert_trading_event)."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    await insert_equity_snapshot(
        conn,
        bot_id="alpha",
        snapshot_at=_FIXED_NOW,
        wallet_balance=Decimal("10250.5000"),
        available_balance=Decimal("10250.5000"),
        total_equity=Decimal("10250.5000"),
        margin_balance=Decimal("10250.5000"),
        unrealized_pnl=Decimal("0"),
    )
    sql_args = conn.execute.await_args.args
    sql = sql_args[0]
    assert "INSERT INTO bot_equity_snapshots" in sql
    # L-021 tripwire — verbatim all-column-direct param list, NO cast.
    assert "VALUES ($1, $2, $3, $4, $5, $6, $7)" in sql
    assert "::" not in sql
    # Append-only — surrogate id auto via Identity, not returned. 7-param
    # VALUES list (not 8) proves the 8th column `id` is omitted from INSERT.
    assert "RETURNING" not in sql
    assert "$8" not in sql
    # $N bind-slot order: SQL, bot_id, snapshot_at, wallet, available,
    # total_equity, margin, unrealized.
    assert sql_args[1] == "alpha"
    assert sql_args[2] == _FIXED_NOW
    assert sql_args[3] == Decimal("10250.5000")
    assert sql_args[4] == Decimal("10250.5000")
    assert sql_args[5] == Decimal("10250.5000")
    assert sql_args[6] == Decimal("10250.5000")
    assert sql_args[7] == Decimal("0")


async def test_insert_equity_snapshot_preserves_negative_unrealized() -> None:
    """Signed Decimal preserved 1:1 (mirror T-530 negative totalPerpUPL golden)."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    await insert_equity_snapshot(
        conn,
        bot_id="beta",
        snapshot_at=_FIXED_NOW,
        wallet_balance=Decimal("9500.0000"),
        available_balance=Decimal("9500.0000"),
        total_equity=Decimal("9500.0000"),
        margin_balance=Decimal("9500.0000"),
        unrealized_pnl=Decimal("-125.2500"),
    )
    sql_args = conn.execute.await_args.args
    assert sql_args[7] == Decimal("-125.2500")
