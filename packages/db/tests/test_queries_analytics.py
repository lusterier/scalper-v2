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
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.core.types import BotStatus, ExchangeMode
from packages.db.queries.analytics import (
    BotDetailRow,
    select_all_bots,
    select_bot_by_id,
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
