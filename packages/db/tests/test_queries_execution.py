"""§N4 unit tests for :mod:`packages.db.queries.execution` (T-215).

Mock-based: ``conn.fetch`` returns canned ``asyncpg.Record``-shaped
dicts. Integration coverage (real PG fetch) deferred to T-222 E1
testnet smoke per §11.6.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.execution import (
    BotRow,
    _validate_exchange_mode,
    select_active_bots,
)


def _row(bot_id: str, display_name: str, exchange_mode: str) -> dict[str, Any]:
    return {"bot_id": bot_id, "display_name": display_name, "exchange_mode": exchange_mode}


async def test_select_active_bots_returns_list_of_BotRow() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _row("alpha", "Alpha Bot", "live"),
            _row("beta", "Beta Bot", "paper"),
        ]
    )
    rows = await select_active_bots(conn)
    assert rows == [
        BotRow(bot_id="alpha", display_name="Alpha Bot", exchange_mode="live"),
        BotRow(bot_id="beta", display_name="Beta Bot", exchange_mode="paper"),
    ]


async def test_select_active_bots_filter_status_applied_via_sql_where_clause() -> None:
    """SQL string carries ``WHERE status = 'active'`` so DB filters; mapping doesn't filter."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    await select_active_bots(conn)
    sql_call = conn.fetch.await_args.args[0]
    assert "WHERE status = 'active'" in sql_call
    assert "ORDER BY bot_id" in sql_call


async def test_select_active_bots_validates_exchange_mode_literal() -> None:
    """Unknown exchange_mode in row → ValueError (defends against operator typos)."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[_row("alpha", "Alpha", "demo")])
    with pytest.raises(ValueError, match="unknown exchange_mode"):
        await select_active_bots(conn)


def test_validate_exchange_mode_accepts_live_testnet_paper() -> None:
    assert _validate_exchange_mode("live") == "live"
    assert _validate_exchange_mode("testnet") == "testnet"
    assert _validate_exchange_mode("paper") == "paper"


def test_validate_exchange_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="unknown exchange_mode"):
        _validate_exchange_mode("garbage")
