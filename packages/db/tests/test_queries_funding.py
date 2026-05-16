"""§N4 unit tests for :mod:`packages.db.queries.funding` (T-532a).

Mock-based: ``conn.execute`` is an :class:`AsyncMock`. The SQL-string
assertions are the **L-021 regression tripwire** — they pin the verbatim
``VALUES ($1, $2, $3, $4)`` list and assert NO ``::`` cast anywhere, so a
future edit that introduces a ``$N::type`` cast / CASE / arithmetic context
is caught at mock level before the testcontainer round-trip (real-PG INSERT
covered by ``tests/integration/migrations/test_0021_migration.py`` schema +
T-532b's tick integration). Mirror ``test_queries_equity`` (T-531).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from packages.core import is_non_idempotent
from packages.db.queries.funding import insert_funding_fee

_FIXED_SETTLED = datetime(2026, 5, 16, 8, 0, 0, tzinfo=UTC)


def test_insert_funding_fee_is_non_idempotent_marked() -> None:
    """§N3 — append-only audit-grade write, explicitly @non_idempotent
    (mirror insert_equity_snapshot; pre-empts the T-534b1 §N3-marker class)."""
    assert is_non_idempotent(insert_funding_fee) is True


async def test_insert_funding_fee_sql_pin_and_bind_order() -> None:
    """L-021 tripwire + $N bind-slot order (mirror test_insert_equity_snapshot)."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    await insert_funding_fee(
        conn,
        bot_id="alpha",
        symbol="BTCUSDT",
        settled_at=_FIXED_SETTLED,
        funding=Decimal("-0.1235"),
    )
    sql_args = conn.execute.await_args.args
    sql = sql_args[0]
    assert "INSERT INTO funding_fees" in sql
    # L-021 tripwire — verbatim all-column-direct param list, NO cast.
    assert "VALUES ($1, $2, $3, $4)" in sql
    assert "::" not in sql
    # Append-only — surrogate id auto via Identity, not returned. 4-param
    # VALUES list proves the surrogate `id` column is omitted from INSERT.
    assert "RETURNING" not in sql
    assert "$5" not in sql
    # $N bind-slot order: SQL, bot_id, symbol, settled_at, funding.
    assert sql_args[1] == "alpha"
    assert sql_args[2] == "BTCUSDT"
    assert sql_args[3] == _FIXED_SETTLED
    assert sql_args[4] == Decimal("-0.1235")


async def test_insert_funding_fee_preserves_signed_funding() -> None:
    """Signed Decimal preserved 1:1 - credit (+) and debit (-) both exact
    (mirror the T-530/T-531 negative-money golden)."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    await insert_funding_fee(
        conn,
        bot_id="beta",
        symbol="ETHUSDT",
        settled_at=_FIXED_SETTLED,
        funding=Decimal("0.5000"),
    )
    assert conn.execute.await_args.args[4] == Decimal("0.5000")
