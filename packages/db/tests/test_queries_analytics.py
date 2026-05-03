"""§N4 unit tests for :mod:`packages.db.queries.analytics` (T-401a).

Mock-based: ``conn.fetch`` / ``conn.fetchrow`` return canned rows. Pin
the public contract:

* ``select_all_bots`` returns ordered list of :class:`BotDetailRow`;
  empty input → empty list.
* ``select_bot_by_id`` returns :class:`BotDetailRow` on hit, ``None``
  on miss.
* Row narrowing through :class:`packages.core.types.BotStatus` +
  :class:`packages.core.types.ExchangeMode` StrEnum constructors —
  unknown enum value raises :class:`ValueError` (defensive).
* Read functions are NOT decorated (no @idempotent / @non_idempotent
  required for plain SELECTs per existing query module convention).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.core import is_non_idempotent
from packages.core.types import (
    Action,
    BotStatus,
    ExchangeMode,
    ExchangeSource,
    IngestionStatus,
    ScoringDecision,
    TradeStatus,
)
from packages.db.queries.analytics import (
    BotDetailRow,
    OpenPositionRow,
    ScoringEvaluationRow,
    SignalRow,
    SymbolMapRow,
    TradeRow,
    _build_signals_where_clause,
    _build_trades_where_clause,
    count_signals,
    count_trades,
    delete_symbol_map_entry,
    insert_symbol_map_entry,
    select_all_bots,
    select_all_symbol_map_entries,
    select_bot_by_id,
    select_open_positions,
    select_scoring_evaluations_by_signal_id,
    select_signal_by_id,
    select_signals_paginated,
    select_symbol_map_entry,
    select_trade_by_id,
    select_trades_paginated,
    update_symbol_map_entry,
)

_T_CREATED = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
_T_APPLIED = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _make_row(
    *,
    bot_id: str = "alpha",
    status: str = "active",
    exchange_mode: str = "paper",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "bot_id": bot_id,
        "display_name": f"Bot {bot_id}",
        "created_at": _T_CREATED,
        "status": status,
        "exchange_mode": exchange_mode,
        "config_hash": "deadbeef" * 8,
        "config_applied_at": _T_APPLIED,
        "meta": meta if meta is not None else {},
    }


async def test_select_all_bots_returns_typed_dataclass_list() -> None:
    """Two rows → list of 2 BotDetailRow with status/exchange_mode narrowed."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _make_row(bot_id="alpha", status="active", exchange_mode="paper"),
            _make_row(bot_id="beta", status="paused", exchange_mode="live"),
        ]
    )
    rows = await select_all_bots(conn)
    assert len(rows) == 2
    assert all(isinstance(r, BotDetailRow) for r in rows)
    assert rows[0].bot_id == "alpha"
    assert rows[0].status is BotStatus.ACTIVE
    assert rows[0].exchange_mode is ExchangeMode.PAPER
    assert rows[1].bot_id == "beta"
    assert rows[1].status is BotStatus.PAUSED
    assert rows[1].exchange_mode is ExchangeMode.LIVE


async def test_select_all_bots_returns_empty_list_when_no_rows() -> None:
    """Empty fetch → empty list (not None)."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    rows = await select_all_bots(conn)
    assert rows == []


async def test_select_all_bots_query_orders_by_bot_id() -> None:
    """SQL must contain ``ORDER BY bot_id`` per analytics.py contract."""
    conn = MagicMock()
    captured_sql: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured_sql.append(sql)
        return []

    conn.fetch = _capture
    await select_all_bots(conn)
    assert "ORDER BY bot_id" in captured_sql[0]


async def test_select_all_bots_passes_meta_jsonb_through() -> None:
    """``meta`` dict round-trips when JSONB codec returned dict already."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[_make_row(meta={"foo": "bar", "n": 1})],
    )
    rows = await select_all_bots(conn)
    assert rows[0].meta == {"foo": "bar", "n": 1}


async def test_select_all_bots_falls_back_to_empty_dict_when_meta_not_dict() -> None:
    """Defensive: if codec absent and meta is str/None, fall back to ``{}``."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[_make_row(meta=None)],
    )
    rows = await select_all_bots(conn)
    assert rows[0].meta == {}


async def test_select_bot_by_id_returns_row_on_hit() -> None:
    """Existing bot_id → BotDetailRow."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_make_row(bot_id="alpha"))
    row = await select_bot_by_id(conn, "alpha")
    assert row is not None
    assert row.bot_id == "alpha"
    assert isinstance(row, BotDetailRow)


async def test_select_bot_by_id_returns_none_on_miss() -> None:
    """Missing bot_id → None (not exception)."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    row = await select_bot_by_id(conn, "nonexistent")
    assert row is None


async def test_select_bot_by_id_passes_bot_id_as_bind_param() -> None:
    """SQL uses ``WHERE bot_id = $1`` with caller's bot_id."""
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> dict[str, Any] | None:
        captured_args.append((sql, *args))
        return None

    conn.fetchrow = _capture
    await select_bot_by_id(conn, "gamma")
    sql, bind_arg = captured_args[0]
    assert "WHERE bot_id = $1" in sql
    assert bind_arg == "gamma"


async def test_select_all_bots_raises_on_unknown_status_enum() -> None:
    """Unknown ``status`` value → BotStatus(...) raises ValueError (defensive)."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[_make_row(status="garbage_status")],
    )
    with pytest.raises(ValueError, match="garbage_status"):
        await select_all_bots(conn)


async def test_select_all_bots_raises_on_unknown_exchange_mode_enum() -> None:
    """Unknown ``exchange_mode`` value → ExchangeMode(...) raises ValueError."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[_make_row(exchange_mode="garbage_mode")],
    )
    with pytest.raises(ValueError, match="garbage_mode"):
        await select_all_bots(conn)


# ---------------------------------------------------------------------------
# T-401b — symbol_map CRUD tests
# ---------------------------------------------------------------------------

_T_SM_CREATED = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
_T_SM_UPDATED = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


def _make_sm_row(
    *,
    input_symbol: str = "BTCUSDT.P",
    canonical_symbol: str = "BTCUSDT",
    exchange_source: str = "binance",
    notes: str | None = None,
) -> dict[str, Any]:
    return {
        "input_symbol": input_symbol,
        "canonical_symbol": canonical_symbol,
        "exchange_source": exchange_source,
        "notes": notes,
        "created_at": _T_SM_CREATED,
        "updated_at": _T_SM_UPDATED,
    }


async def test_select_all_symbol_map_entries_returns_typed_list() -> None:
    """Two rows → list of 2 SymbolMapRow with exchange_source narrowed."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _make_sm_row(input_symbol="BTCUSDT.P", exchange_source="binance"),
            _make_sm_row(input_symbol="ETHUSDT.P", exchange_source="bybit"),
        ]
    )
    rows = await select_all_symbol_map_entries(conn)
    assert len(rows) == 2
    assert all(isinstance(r, SymbolMapRow) for r in rows)
    assert rows[0].exchange_source is ExchangeSource.BINANCE
    assert rows[1].exchange_source is ExchangeSource.BYBIT


async def test_select_all_symbol_map_entries_returns_empty_list() -> None:
    """Empty fetch → empty list."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    rows = await select_all_symbol_map_entries(conn)
    assert rows == []


async def test_select_all_symbol_map_entries_query_orders_by_input_symbol() -> None:
    """SQL contains ``ORDER BY input_symbol`` per analytics.py contract."""
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured.append(sql)
        return []

    conn.fetch = _capture
    await select_all_symbol_map_entries(conn)
    assert "ORDER BY input_symbol" in captured[0]


async def test_select_symbol_map_entry_returns_row_on_hit() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_make_sm_row())
    row = await select_symbol_map_entry(conn, "BTCUSDT.P")
    assert row is not None
    assert isinstance(row, SymbolMapRow)
    assert row.input_symbol == "BTCUSDT.P"


async def test_select_symbol_map_entry_returns_none_on_miss() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    row = await select_symbol_map_entry(conn, "MISSING.P")
    assert row is None


async def test_insert_symbol_map_entry_returns_inserted_row() -> None:
    """RETURNING * roundtrip: inserted row materialised as SymbolMapRow."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value=_make_sm_row(
            input_symbol="LTCUSDT.P",
            canonical_symbol="LTCUSDT",
            notes="example",
        )
    )
    row = await insert_symbol_map_entry(
        conn,
        input_symbol="LTCUSDT.P",
        canonical_symbol="LTCUSDT",
        exchange_source="binance",
        notes="example",
        created_at=_T_SM_CREATED,
        updated_at=_T_SM_CREATED,
    )
    assert isinstance(row, SymbolMapRow)
    assert row.input_symbol == "LTCUSDT.P"
    assert row.notes == "example"


def test_insert_symbol_map_entry_marker_is_non_idempotent() -> None:
    assert is_non_idempotent(insert_symbol_map_entry)
    assert insert_symbol_map_entry.__non_idempotent__ is True  # type: ignore[attr-defined]


async def test_update_symbol_map_entry_returns_updated_row_when_pk_exists() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value=_make_sm_row(
            input_symbol="BTCUSDT.P",
            canonical_symbol="BTCUSDT_NEW",
        )
    )
    row = await update_symbol_map_entry(
        conn,
        input_symbol="BTCUSDT.P",
        canonical_symbol="BTCUSDT_NEW",
        exchange_source="bybit",
        notes=None,
        updated_at=_T_SM_UPDATED,
    )
    assert row is not None
    assert row.canonical_symbol == "BTCUSDT_NEW"


async def test_update_symbol_map_entry_returns_none_when_pk_missing() -> None:
    """0 rows affected → returns None (caller returns 404)."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    row = await update_symbol_map_entry(
        conn,
        input_symbol="MISSING.P",
        canonical_symbol="X",
        exchange_source="custom",
        notes=None,
        updated_at=_T_SM_UPDATED,
    )
    assert row is None


def test_update_symbol_map_entry_marker_is_non_idempotent() -> None:
    assert is_non_idempotent(update_symbol_map_entry)
    assert update_symbol_map_entry.__non_idempotent__ is True  # type: ignore[attr-defined]


async def test_delete_symbol_map_entry_returns_true_when_deleted() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 1")
    deleted = await delete_symbol_map_entry(conn, "BTCUSDT.P")
    assert deleted is True


async def test_delete_symbol_map_entry_returns_false_when_not_found() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 0")
    deleted = await delete_symbol_map_entry(conn, "MISSING.P")
    assert deleted is False


def test_delete_symbol_map_entry_marker_is_non_idempotent() -> None:
    assert is_non_idempotent(delete_symbol_map_entry)
    assert delete_symbol_map_entry.__non_idempotent__ is True  # type: ignore[attr-defined]


async def test_select_symbol_map_raises_on_unknown_exchange_source() -> None:
    """Unknown exchange_source value → ExchangeSource(...) raises ValueError."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[_make_sm_row(exchange_source="garbage_source")],
    )
    with pytest.raises(ValueError, match="garbage_source"):
        await select_all_symbol_map_entries(conn)


# ---------------------------------------------------------------------------
# T-402 — /api/positions/* + /api/trades/* read endpoint queries
# ---------------------------------------------------------------------------

_T_OPENED = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
_T_CLOSED = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
_T_UPDATED_POS = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


def _make_position_row(
    *,
    bot_id: str = "alpha",
    symbol: str = "BTCUSDT",
    sl_price: str | None = "45000.0",
    sl_type: str | None = "protective",
) -> dict[str, Any]:
    return {
        "bot_id": bot_id,
        "symbol": symbol,
        "trade_id": 1,
        "side": "buy",
        "entry_price": Decimal("50000.123456789012"),
        "qty": Decimal("0.5"),
        "remaining_qty": Decimal("0.5"),
        "sl_price": Decimal(sl_price) if sl_price is not None else None,
        "tp_price": Decimal("55000.0"),
        "sl_type": sl_type,
        "best_price": Decimal("50100.0"),
        "tp_hit": False,
        "trailing_active": False,
        "running_pnl": Decimal("0.0"),
        "mfe_price": Decimal("50100.0"),
        "mae_price": Decimal("49900.0"),
        "updated_at": _T_UPDATED_POS,
    }


def _make_trade_row(
    *,
    trade_id: int = 1,
    bot_id: str = "alpha",
    symbol: str = "BTCUSDT",
    status_value: str = "closed",
    realized_pnl: str | None = "12.34",
    mfe_pct: float | None = 0.025,
) -> dict[str, Any]:
    return {
        "id": trade_id,
        "bot_id": bot_id,
        "signal_id": 42,
        "open_order_id": 100,
        "close_order_id": 101 if status_value != "open" else None,
        "symbol": symbol,
        "side": "buy",
        "entry_price": Decimal("50000.0"),
        "exit_price": Decimal("50500.0") if status_value != "open" else None,
        "qty": Decimal("0.5"),
        "notional_usd": Decimal("25000.0000"),
        "realized_pnl": Decimal(realized_pnl) if realized_pnl is not None else None,
        "fees_paid": Decimal("0.5000"),
        "close_reason": "tp" if status_value == "closed" else None,
        "opened_at": _T_OPENED,
        "closed_at": _T_CLOSED if status_value != "open" else None,
        "status": status_value,
        "mfe_pct": mfe_pct,
        "mae_pct": -0.005,
        "confidence_score": 0.75,
        "meta": {},
    }


# ---- select_open_positions ----


async def test_select_open_positions_returns_all_when_bot_id_none() -> None:
    """Unfiltered → SELECT all + ORDER BY bot_id, symbol."""
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured.append(sql)
        return [
            _make_position_row(bot_id="alpha", symbol="BTCUSDT"),
            _make_position_row(bot_id="beta", symbol="ETHUSDT"),
        ]

    conn.fetch = _capture
    rows = await select_open_positions(conn)
    assert len(rows) == 2
    assert all(isinstance(r, OpenPositionRow) for r in rows)
    assert "ORDER BY bot_id, symbol" in captured[0]
    assert "WHERE" not in captured[0]


async def test_select_open_positions_filters_by_bot_id() -> None:
    """`bot_id='alpha'` → SQL contains `WHERE bot_id = $1`."""
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured_args.append((sql, *args))
        return [_make_position_row(bot_id="alpha")]

    conn.fetch = _capture
    rows = await select_open_positions(conn, bot_id="alpha")
    assert len(rows) == 1
    assert rows[0].bot_id == "alpha"
    sql, bind_arg = captured_args[0]
    assert "WHERE bot_id = $1" in sql
    assert bind_arg == "alpha"


async def test_select_open_positions_returns_empty_list_when_no_rows() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    rows = await select_open_positions(conn)
    assert rows == []


async def test_open_position_dataclass_carries_decimal_precision() -> None:
    """NUMERIC columns round-trip as Decimal — no silent float cast per §N1 / §5.3."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[_make_position_row(sl_price="45000.123456789012")],
    )
    rows = await select_open_positions(conn)
    assert isinstance(rows[0].entry_price, Decimal)
    assert isinstance(rows[0].sl_price, Decimal)
    assert rows[0].sl_price == Decimal("45000.123456789012")


# ---- _build_trades_where_clause ----


def test_build_where_returns_empty_string_when_all_filters_none() -> None:
    """No filters → `("", [])` (no WHERE clause appended)."""
    where, args = _build_trades_where_clause(
        bot_id=None,
        symbol=None,
        status=None,
        from_at=None,
        to_at=None,
    )
    assert where == ""
    assert args == []


def test_build_where_combines_all_5_filters_via_AND() -> None:
    """All 5 filters set → AND-combined WHERE clause + bind args in $N order."""
    where, args = _build_trades_where_clause(
        bot_id="alpha",
        symbol="BTCUSDT",
        status=TradeStatus.CLOSED,
        from_at=_T_OPENED,
        to_at=_T_CLOSED,
    )
    assert where == (
        "WHERE bot_id = $1 AND symbol = $2 AND status = $3 AND closed_at >= $4 AND closed_at < $5"
    )
    assert args == ["alpha", "BTCUSDT", "closed", _T_OPENED, _T_CLOSED]


def test_build_where_solo_bot_id() -> None:
    where, args = _build_trades_where_clause(
        bot_id="alpha",
        symbol=None,
        status=None,
        from_at=None,
        to_at=None,
    )
    assert where == "WHERE bot_id = $1"
    assert args == ["alpha"]


def test_build_where_solo_symbol() -> None:
    where, args = _build_trades_where_clause(
        bot_id=None,
        symbol="BTCUSDT",
        status=None,
        from_at=None,
        to_at=None,
    )
    assert where == "WHERE symbol = $1"
    assert args == ["BTCUSDT"]


def test_build_where_solo_status() -> None:
    where, args = _build_trades_where_clause(
        bot_id=None,
        symbol=None,
        status=TradeStatus.OPEN,
        from_at=None,
        to_at=None,
    )
    assert where == "WHERE status = $1"
    assert args == ["open"]


def test_build_where_solo_from_at() -> None:
    where, args = _build_trades_where_clause(
        bot_id=None,
        symbol=None,
        status=None,
        from_at=_T_OPENED,
        to_at=None,
    )
    assert where == "WHERE closed_at >= $1"
    assert args == [_T_OPENED]


def test_build_where_solo_to_at() -> None:
    where, args = _build_trades_where_clause(
        bot_id=None,
        symbol=None,
        status=None,
        from_at=None,
        to_at=_T_CLOSED,
    )
    assert where == "WHERE closed_at < $1"
    assert args == [_T_CLOSED]


# ---- select_trades_paginated ----


async def test_select_trades_paginated_uses_parameterized_placeholders() -> None:
    """WG#2 — SQL must use $N placeholders only, never f-string interpolation of values."""
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured_args.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_trades_paginated(
        conn,
        bot_id="alpha",
        symbol="BTCUSDT",
        status=TradeStatus.CLOSED,
        from_at=_T_OPENED,
        to_at=_T_CLOSED,
        limit=10,
        offset=20,
    )
    sql, *bind_args = captured_args[0]
    # No interpolated values present in SQL string (those go through $N).
    assert "alpha" not in sql
    assert "BTCUSDT" not in sql
    assert "$1" in sql
    assert "$5" in sql
    # limit + offset come last as $6, $7.
    assert "$6" in sql
    assert "$7" in sql
    assert bind_args == ["alpha", "BTCUSDT", "closed", _T_OPENED, _T_CLOSED, 10, 20]


async def test_select_trades_paginated_orders_by_closed_at_desc_nulls_first() -> None:
    """ORDER BY closed_at DESC NULLS FIRST per analytics.py contract."""
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured.append(sql)
        return []

    conn.fetch = _capture
    await select_trades_paginated(
        conn,
        bot_id=None,
        symbol=None,
        status=None,
        from_at=None,
        to_at=None,
        limit=50,
        offset=0,
    )
    assert "ORDER BY closed_at DESC NULLS FIRST" in captured[0]


async def test_select_trades_paginated_returns_typed_list() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _make_trade_row(trade_id=1, status_value="closed"),
            _make_trade_row(trade_id=2, status_value="open"),
        ]
    )
    rows = await select_trades_paginated(
        conn,
        bot_id=None,
        symbol=None,
        status=None,
        from_at=None,
        to_at=None,
        limit=50,
        offset=0,
    )
    assert len(rows) == 2
    assert all(isinstance(r, TradeRow) for r in rows)
    assert rows[0].status is TradeStatus.CLOSED
    assert rows[1].status is TradeStatus.OPEN
    assert rows[1].closed_at is None
    assert rows[1].realized_pnl == Decimal("12.34")


async def test_trade_double_precision_fields_stay_as_float() -> None:
    """mfe_pct / mae_pct / confidence_score are float (DOUBLE PRECISION domain), not Decimal."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[_make_trade_row(mfe_pct=0.025)])
    rows = await select_trades_paginated(
        conn,
        bot_id=None,
        symbol=None,
        status=None,
        from_at=None,
        to_at=None,
        limit=50,
        offset=0,
    )
    assert isinstance(rows[0].mfe_pct, float)
    assert isinstance(rows[0].mae_pct, float)
    assert isinstance(rows[0].confidence_score, float)


# ---- count_trades ----


async def test_count_trades_returns_int_matching_filters() -> None:
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> dict[str, int]:
        captured_args.append((sql, *args))
        return {"n": 7}

    conn.fetchrow = _capture
    n = await count_trades(
        conn,
        bot_id="alpha",
        symbol=None,
        status=None,
        from_at=None,
        to_at=None,
    )
    assert n == 7
    sql, bind_arg = captured_args[0]
    assert "SELECT COUNT(*)" in sql
    assert "FROM trades" in sql
    assert "WHERE bot_id = $1" in sql
    assert bind_arg == "alpha"


async def test_count_trades_uses_same_where_clause_as_select_trades_paginated() -> None:
    """WG#5 — both helpers route through `_build_trades_where_clause` (no drift)."""
    conn = MagicMock()
    captured_select: list[str] = []
    captured_count: list[str] = []

    async def _capture_fetch(sql: str, *_args: Any) -> list[Any]:
        captured_select.append(sql)
        return []

    async def _capture_fetchrow(sql: str, *_args: Any) -> dict[str, int]:
        captured_count.append(sql)
        return {"n": 0}

    conn.fetch = _capture_fetch
    conn.fetchrow = _capture_fetchrow
    common: dict[str, Any] = {
        "bot_id": "alpha",
        "symbol": "BTCUSDT",
        "status": TradeStatus.CLOSED,
        "from_at": _T_OPENED,
        "to_at": _T_CLOSED,
    }
    await select_trades_paginated(conn, **common, limit=50, offset=0)
    await count_trades(conn, **common)
    # Both SQLs share the same WHERE clause shape.
    where = (
        "WHERE bot_id = $1 AND symbol = $2 AND status = $3 AND closed_at >= $4 AND closed_at < $5"
    )
    assert where in captured_select[0]
    assert where in captured_count[0]


async def test_count_trades_returns_zero_when_no_match() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"n": 0})
    n = await count_trades(
        conn,
        bot_id="missing",
        symbol=None,
        status=None,
        from_at=None,
        to_at=None,
    )
    assert n == 0


# ---- select_trade_by_id ----


async def test_select_trade_by_id_returns_row_on_hit() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_make_trade_row(trade_id=42))
    row = await select_trade_by_id(conn, 42)
    assert row is not None
    assert isinstance(row, TradeRow)
    assert row.id == 42


async def test_select_trade_by_id_returns_none_on_miss() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    row = await select_trade_by_id(conn, 999)
    assert row is None


async def test_select_trades_unknown_status_raises_value_error() -> None:
    """TradeStatus(...) raises on unknown enum value (defensive at row narrowing)."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_make_trade_row(status_value="garbage"))
    with pytest.raises(ValueError, match="garbage"):
        await select_trade_by_id(conn, 1)


# ---------------------------------------------------------------------------
# T-403 — /api/signals/* + /api/scoring/* read endpoint queries
# ---------------------------------------------------------------------------

_T_RECEIVED = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
_T_EVAL = datetime(2026, 5, 1, 10, 0, 1, tzinfo=UTC)


def _make_signal_row(
    *,
    signal_id: int = 1,
    source: str = "tv_rsi_div_v3",
    symbol: str = "BTCUSDT",
    action_value: str = "LONG",
    ingestion_status_value: str = "validated",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": signal_id,
        "received_at": _T_RECEIVED,
        "schema_version": "1",
        "source": source,
        "idempotency_key": f"key-{signal_id}",
        "symbol": symbol,
        "original_symbol": f"{symbol}.P",
        "action": action_value,
        "payload": payload if payload is not None else {"price": "50000"},
        "ingestion_status": ingestion_status_value,
        "correlation_id": f"cid-{signal_id}",
    }


def _make_scoring_row(
    *,
    eval_id: int = 1,
    bot_id: str = "alpha",
    signal_id: int = 1,
    decision_value: str = "execute",
    rule_results: list[dict[str, Any]] | None = None,
    feature_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": eval_id,
        "bot_id": bot_id,
        "signal_id": signal_id,
        "evaluated_at": _T_EVAL,
        "trigger_threshold": 1.0,
        "total_score": 1.5,
        "decision": decision_value,
        "config_version": 1,
        "rule_results": rule_results
        if rule_results is not None
        else [
            {"name": "r1", "weight": 1.0, "applied_weight": 1.0, "result": "True", "error": None},
        ],
        "feature_snapshot": feature_snapshot
        if feature_snapshot is not None
        else {
            "ind.btcusdt.15m.ema_20": "50000",
        },
        "correlation_id": f"cid-{signal_id}",
    }


# ---- _build_signals_where_clause ----


def test_build_signals_where_returns_empty_when_all_filters_none() -> None:
    where, args = _build_signals_where_clause(
        source=None,
        symbol=None,
        action=None,
        ingestion_status=None,
        from_at=None,
        to_at=None,
    )
    assert where == ""
    assert args == []


def test_build_signals_where_combines_all_6_filters_via_AND() -> None:
    where, args = _build_signals_where_clause(
        source="tv_rsi_div_v3",
        symbol="BTCUSDT",
        action=Action.LONG,
        ingestion_status=IngestionStatus.VALIDATED,
        from_at=_T_RECEIVED,
        to_at=_T_EVAL,
    )
    assert where == (
        "WHERE source = $1 AND symbol = $2 AND action = $3 "
        "AND ingestion_status = $4 AND received_at >= $5 AND received_at < $6"
    )
    assert args == [
        "tv_rsi_div_v3",
        "BTCUSDT",
        "LONG",
        "validated",
        _T_RECEIVED,
        _T_EVAL,
    ]


def test_build_signals_where_filters_received_at_range() -> None:
    """from_at/to_at filter on received_at column (NOT closed_at like trades)."""
    where, _args = _build_signals_where_clause(
        source=None,
        symbol=None,
        action=None,
        ingestion_status=None,
        from_at=_T_RECEIVED,
        to_at=_T_EVAL,
    )
    assert "received_at >= $1" in where
    assert "received_at < $2" in where
    assert "closed_at" not in where


# ---- select_signals_paginated ----


async def test_select_signals_paginated_returns_typed_list() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _make_signal_row(signal_id=1, action_value="LONG"),
            _make_signal_row(signal_id=2, action_value="SHORT"),
        ]
    )
    rows = await select_signals_paginated(
        conn,
        source=None,
        symbol=None,
        action=None,
        ingestion_status=None,
        from_at=None,
        to_at=None,
        limit=50,
        offset=0,
    )
    assert len(rows) == 2
    assert all(isinstance(r, SignalRow) for r in rows)
    assert rows[0].action is Action.LONG
    assert rows[1].action is Action.SHORT
    assert rows[0].ingestion_status is IngestionStatus.VALIDATED


async def test_select_signals_paginated_orders_by_received_at_desc() -> None:
    """ORDER BY received_at DESC, id DESC per analytics.py contract (WG#6)."""
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured.append(sql)
        return []

    conn.fetch = _capture
    await select_signals_paginated(
        conn,
        source=None,
        symbol=None,
        action=None,
        ingestion_status=None,
        from_at=None,
        to_at=None,
        limit=50,
        offset=0,
    )
    assert "ORDER BY received_at DESC, id DESC" in captured[0]


async def test_select_signals_paginated_uses_parameterized_placeholders() -> None:
    """WG#3 — `$N` placeholders only, never f-string interpolation of values."""
    conn = MagicMock()
    captured_args: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured_args.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_signals_paginated(
        conn,
        source="tv_rsi_div_v3",
        symbol="BTCUSDT",
        action=Action.LONG,
        ingestion_status=IngestionStatus.VALIDATED,
        from_at=_T_RECEIVED,
        to_at=_T_EVAL,
        limit=10,
        offset=20,
    )
    sql, *bind_args = captured_args[0]
    assert "tv_rsi_div_v3" not in sql
    assert "BTCUSDT" not in sql
    assert "$1" in sql
    assert "$8" in sql  # 6 filters + limit + offset
    assert bind_args == [
        "tv_rsi_div_v3",
        "BTCUSDT",
        "LONG",
        "validated",
        _T_RECEIVED,
        _T_EVAL,
        10,
        20,
    ]


# ---- count_signals ----


async def test_count_signals_returns_int_matching_filters() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"n": 7})
    n = await count_signals(
        conn,
        source="tv_rsi_div_v3",
        symbol=None,
        action=None,
        ingestion_status=None,
        from_at=None,
        to_at=None,
    )
    assert n == 7


async def test_count_signals_uses_same_where_clause_as_select_signals_paginated() -> None:
    """WG#3 — both helpers route through `_build_signals_where_clause`."""
    conn = MagicMock()
    captured_select: list[str] = []
    captured_count: list[str] = []

    async def _capture_fetch(sql: str, *_args: Any) -> list[Any]:
        captured_select.append(sql)
        return []

    async def _capture_fetchrow(sql: str, *_args: Any) -> dict[str, int]:
        captured_count.append(sql)
        return {"n": 0}

    conn.fetch = _capture_fetch
    conn.fetchrow = _capture_fetchrow
    common: dict[str, Any] = {
        "source": "tv_rsi_div_v3",
        "symbol": "BTCUSDT",
        "action": Action.LONG,
        "ingestion_status": IngestionStatus.VALIDATED,
        "from_at": _T_RECEIVED,
        "to_at": _T_EVAL,
    }
    await select_signals_paginated(conn, **common, limit=50, offset=0)
    await count_signals(conn, **common)
    where = (
        "WHERE source = $1 AND symbol = $2 AND action = $3 "
        "AND ingestion_status = $4 AND received_at >= $5 AND received_at < $6"
    )
    assert where in captured_select[0]
    assert where in captured_count[0]


# ---- select_signal_by_id ----


async def test_select_signal_by_id_returns_row_on_hit() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_make_signal_row(signal_id=42))
    row = await select_signal_by_id(conn, 42)
    assert row is not None
    assert isinstance(row, SignalRow)
    assert row.id == 42


async def test_select_signal_by_id_returns_none_on_miss() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    row = await select_signal_by_id(conn, 999)
    assert row is None


async def test_signal_action_narrowing_raises_on_unknown_enum() -> None:
    """Defensive: unknown action raises ValueError at row narrowing."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_make_signal_row(action_value="GARBAGE"))
    with pytest.raises(ValueError, match="GARBAGE"):
        await select_signal_by_id(conn, 1)


async def test_signal_ingestion_status_narrowing_raises_on_unknown_enum() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value=_make_signal_row(ingestion_status_value="garbage_status")
    )
    with pytest.raises(ValueError, match="garbage_status"):
        await select_signal_by_id(conn, 1)


# ---- select_scoring_evaluations_by_signal_id ----


async def test_select_scoring_evaluations_by_signal_id_returns_typed_list() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _make_scoring_row(eval_id=1, bot_id="alpha", decision_value="execute"),
            _make_scoring_row(eval_id=2, bot_id="beta", decision_value="passthrough"),
        ]
    )
    rows = await select_scoring_evaluations_by_signal_id(conn, 1)
    assert len(rows) == 2
    assert all(isinstance(r, ScoringEvaluationRow) for r in rows)
    assert rows[0].decision is ScoringDecision.EXECUTE
    assert rows[1].decision is ScoringDecision.PASSTHROUGH


async def test_select_scoring_evaluations_by_signal_id_returns_empty_list() -> None:
    """Empty list (NOT None) when no evaluations for signal."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    rows = await select_scoring_evaluations_by_signal_id(conn, 999)
    assert rows == []


async def test_select_scoring_evaluations_orders_by_bot_id_asc() -> None:
    """SQL contains ORDER BY bot_id ASC for deterministic UI ordering."""
    conn = MagicMock()
    captured: list[str] = []

    async def _capture(sql: str, *_args: Any) -> list[Any]:
        captured.append(sql)
        return []

    conn.fetch = _capture
    await select_scoring_evaluations_by_signal_id(conn, 1)
    assert "ORDER BY bot_id ASC" in captured[0]


async def test_scoring_decision_narrowing_raises_on_unknown_enum() -> None:
    """Defensive: unknown decision raises ValueError."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[_make_scoring_row(decision_value="garbage_decision")])
    with pytest.raises(ValueError, match="garbage_decision"):
        await select_scoring_evaluations_by_signal_id(conn, 1)


async def test_scoring_evaluation_double_precision_fields_stay_as_float() -> None:
    """trigger_threshold + total_score are float (DOUBLE PRECISION domain)."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[_make_scoring_row()])
    rows = await select_scoring_evaluations_by_signal_id(conn, 1)
    assert isinstance(rows[0].trigger_threshold, float)
    assert isinstance(rows[0].total_score, float)
