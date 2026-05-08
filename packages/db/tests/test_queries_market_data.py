"""§N4 unit tests for :mod:`packages.db.queries.market_data` (T-513a).

Mock-based: ``conn.fetchrow`` returns canned rows. Pin contract:

* :func:`select_latest_close` issues SQL with ``source = $2`` filter (PK is
  ``(symbol, bucket_start, source)``; multiple sources may co-exist).
* Returns ``None`` cold-start (no rows for given ``(symbol, source)``).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.market_data import select_latest_close


@pytest.mark.asyncio
async def test_select_latest_close_filters_by_symbol_and_source() -> None:
    """SQL contract: WHERE symbol = $1 AND source = $2 — both filters present.

    L-002 pin: SQL string must contain explicit ``source = $2`` (PK column).
    Tests guard against regression where caller-side source filter is dropped.
    """
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"close": Decimal("65000")})
    result = await select_latest_close(conn, symbol="BTCUSDT", source="binance")
    assert result == Decimal("65000")
    conn.fetchrow.assert_awaited_once()
    sql = conn.fetchrow.await_args.args[0]
    assert "WHERE symbol = $1 AND source = $2" in sql
    assert "ORDER BY bucket_start DESC LIMIT 1" in sql
    assert conn.fetchrow.await_args.args[1] == "BTCUSDT"
    assert conn.fetchrow.await_args.args[2] == "binance"


@pytest.mark.asyncio
async def test_select_latest_close_cold_start_returns_none() -> None:
    """No rows for ``(symbol, source)`` → ``None`` (caller falls back to Decimal('0'))."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    result = await select_latest_close(conn, symbol="BTCUSDT", source="binance")
    assert result is None
