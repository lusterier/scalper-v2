"""§N4 unit tests for :mod:`packages.db.queries.execution` (T-215, T-216b).

Mock-based: ``conn.fetch`` / ``conn.fetchrow`` / ``conn.execute`` return
canned values. Integration coverage (real PG fetch + tx) deferred to
T-222 E1 testnet smoke per §11.6.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.execution import (
    BotRow,
    _validate_exchange_mode,
    delete_position_state,
    insert_order,
    insert_position_state,
    insert_trade,
    insert_trading_event,
    select_active_bots,
    update_trade_close,
)

_FIXED_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


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


# ---------------------------------------------------------------------------
# T-216b — placement-tx persistence helpers
# ---------------------------------------------------------------------------


async def test_insert_order_returns_BIGSERIAL_id_from_RETURNING_clause() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 42})
    result = await insert_order(
        conn,
        bot_id="alpha",
        signal_id=7,
        correlation_id="cid-1",
        exchange_order_id="ord-1",
        exchange="bybit",
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        qty=Decimal("0.001"),
        price=Decimal("45000.50"),
        status="filled",
        requested_at=_FIXED_NOW,
        filled_at=_FIXED_NOW,
        closed_at=None,
        idempotent_flag=False,
    )
    assert result == 42
    assert isinstance(result, int)
    sql_args = conn.fetchrow.await_args.args
    assert "INSERT INTO orders" in sql_args[0]
    assert "RETURNING id" in sql_args[0]


async def test_insert_order_idempotent_flag_propagates_to_param() -> None:
    """H-003 market = False; sl/tp synthetic = True per Decision #3 mapping."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    await insert_order(
        conn,
        bot_id="alpha",
        signal_id=1,
        correlation_id="cid-1",
        exchange_order_id="ord-1",
        exchange="bybit",
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        qty=Decimal("0.001"),
        price=Decimal("45000.50"),
        status="filled",
        requested_at=_FIXED_NOW,
        filled_at=None,
        closed_at=None,
        idempotent_flag=False,
    )
    sql_args = conn.fetchrow.await_args.args
    # The 15th positional param (1-indexed: 14 in zero-indexed args[1:]) is idempotent_flag.
    assert sql_args[15] is False


async def test_insert_order_preserves_qty_NUMERIC_30_12_precision() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    qty = Decimal("0.500000000001")
    await insert_order(
        conn,
        bot_id="alpha",
        signal_id=1,
        correlation_id="cid-1",
        exchange_order_id="ord-1",
        exchange="bybit",
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        qty=qty,
        price=Decimal("45000.50"),
        status="filled",
        requested_at=_FIXED_NOW,
        filled_at=None,
        closed_at=None,
        idempotent_flag=False,
    )
    sql_args = conn.fetchrow.await_args.args
    # qty is positional param index 9 (sql at args[0], 1..15 = 15 params).
    passed_qty = sql_args[9]
    assert passed_qty == qty
    assert isinstance(passed_qty, Decimal)
    assert str(passed_qty) == "0.500000000001"


async def test_insert_order_raises_runtime_error_when_returning_yields_no_row() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="INSERT orders"):
        await insert_order(
            conn,
            bot_id="alpha",
            signal_id=1,
            correlation_id="cid-1",
            exchange_order_id="ord-1",
            exchange="bybit",
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            qty=Decimal("0.001"),
            price=Decimal("45000.50"),
            status="filled",
            requested_at=_FIXED_NOW,
            filled_at=None,
            closed_at=None,
            idempotent_flag=False,
        )


async def test_insert_trade_returns_BIGSERIAL_id_with_NULL_realized_pnl_and_fees_paid() -> None:
    """OQ-5 — realized_pnl + fees_paid NULL at T-216b time; T-218/T-219 backfill."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 13})
    result = await insert_trade(
        conn,
        bot_id="alpha",
        signal_id=1,
        open_order_id=42,
        symbol="BTCUSDT",
        side="buy",
        entry_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        notional_usd=Decimal("45.0005"),
        opened_at=_FIXED_NOW,
    )
    assert result == 13
    sql = conn.fetchrow.await_args.args[0]
    # SQL must NOT mention realized_pnl/fees_paid columns (NULL by default).
    assert "realized_pnl" not in sql
    assert "fees_paid" not in sql
    assert "status, meta" in sql
    assert "'open'" in sql


async def test_insert_trade_quantizes_notional_usd_to_NUMERIC_20_4() -> None:
    """Caller must quantize before passing; helper preserves what's passed."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    notional = Decimal("45.0005")
    await insert_trade(
        conn,
        bot_id="alpha",
        signal_id=1,
        open_order_id=42,
        symbol="BTCUSDT",
        side="buy",
        entry_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        notional_usd=notional,
        opened_at=_FIXED_NOW,
    )
    sql_args = conn.fetchrow.await_args.args
    # notional_usd is positional index 8 in args (sql=0, then 1..9 = 9 params).
    assert sql_args[8] == notional
    assert isinstance(sql_args[8], Decimal)


async def test_insert_position_state_uses_composite_pk_no_id_returned() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    await insert_position_state(
        conn,
        bot_id="alpha",
        symbol="BTCUSDT",
        trade_id=13,
        side="buy",
        entry_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        remaining_qty=Decimal("0.001"),
        sl_price=Decimal("44775.4975"),
        tp_price=Decimal("45675.5075"),
        sl_type="protective",
        updated_at=_FIXED_NOW,
    )
    sql = conn.execute.await_args.args[0]
    assert "INSERT INTO position_state" in sql
    assert "RETURNING" not in sql
    # Composite PK on (bot_id, symbol).
    assert "bot_id" in sql
    assert "symbol" in sql


async def test_insert_trading_event_writes_jsonb_payload() -> None:
    """WG#2 — event_type='sl_moved' (schema-aligned per OrderEventBase)."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    payload = {"order_id": 42, "exchange_order_id": "ord-1", "sl_type": "protective"}
    await insert_trading_event(
        conn,
        occurred_at=_FIXED_NOW,
        bot_id="alpha",
        correlation_id="cid-1",
        event_type="sl_moved",
        payload=payload,
    )
    sql_args = conn.execute.await_args.args
    assert "INSERT INTO trading_events" in sql_args[0]
    assert "::jsonb" in sql_args[0]
    assert sql_args[1] == _FIXED_NOW  # occurred_at
    assert sql_args[2] == "alpha"  # bot_id
    assert sql_args[3] == "cid-1"  # correlation_id
    assert sql_args[4] == "sl_moved"  # event_type WG#2 schema-aligned
    assert json.loads(sql_args[5]) == payload


async def test_update_trade_close_uses_where_id_pk_only_per_H_018() -> None:
    """H-018 invariant — UPDATE keyed by trades.id only (BIGSERIAL surrogate)."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    await update_trade_close(
        conn,
        trade_id=13,
        exit_price=Decimal("45100.00"),
        realized_pnl=Decimal("0.10"),
        fees_paid=Decimal("0.0225"),
        closed_at=_FIXED_NOW,
        close_reason="emergency",
        close_order_id=43,
    )
    sql = conn.execute.await_args.args[0]
    assert "UPDATE trades" in sql
    assert "WHERE id = $7" in sql
    # H-018: WHERE clause has ONLY id (no symbol, status, bot_id).
    assert " symbol" not in sql.split("WHERE")[1]
    assert " status =" not in sql.split("WHERE")[1]
    assert " bot_id" not in sql.split("WHERE")[1]


async def test_update_trade_close_sets_status_closed_and_close_reason() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    await update_trade_close(
        conn,
        trade_id=13,
        exit_price=Decimal("45100.00"),
        realized_pnl=Decimal("0"),
        fees_paid=Decimal("0"),
        closed_at=_FIXED_NOW,
        close_reason="emergency",
        close_order_id=43,
    )
    sql_args = conn.execute.await_args.args
    sql = sql_args[0]
    assert "status = 'closed'" in sql
    # close_reason is positional param index 5 (1-indexed in $).
    assert sql_args[5] == "emergency"


async def test_delete_position_state_uses_composite_pk_bot_id_and_symbol() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    await delete_position_state(conn, bot_id="alpha", symbol="BTCUSDT")
    sql_args = conn.execute.await_args.args
    sql = sql_args[0]
    assert "DELETE FROM position_state" in sql
    assert "bot_id = $1" in sql
    assert "symbol = $2" in sql
    assert sql_args[1] == "alpha"
    assert sql_args[2] == "BTCUSDT"
