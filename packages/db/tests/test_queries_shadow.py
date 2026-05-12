"""§N4 unit tests for :mod:`packages.db.queries.shadow` (T-510b).

Mock-based: ``conn.fetch`` / ``conn.fetchrow`` return canned rows. Pin
the public contract:

* ``ShadowVariantRow`` (14 fields) + ``ShadowRejectedRow`` (11 fields)
  dataclass shapes (frozen + slots).
* ``ShadowVariantTerminal`` (5 values) + ``ShadowRejectedTerminal``
  (4 values) StrEnums per BRIEF §13.3 + §13.5.
* ``_row_to_*`` decoders narrow ``terminal_outcome`` via StrEnum
  (operator-decision A; mirror T-407 BacktestStatus precedent).
* L-011 pre-emptive write convention: ``meta`` serialized via
  ``json.dumps(_to_jsonable(meta))`` text-mode; ``meta=None`` →
  ``None`` bind (SQL COALESCE applies DEFAULT).
* ``update_*_terminal`` returns ``None`` on miss (race-defensive).
* All 4 write helpers ``@non_idempotent`` per §N3.
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.core import is_non_idempotent
from packages.core.types import ShadowRejectedTerminal, ShadowVariantTerminal
from packages.db.queries.shadow import (
    ShadowRejectedRow,
    ShadowVariantRow,
    count_shadow_rejected,
    insert_shadow_rejected,
    insert_shadow_variant,
    select_active_shadow_rejected,
    select_active_shadow_variants,
    select_all_active_shadow_rejected,
    select_shadow_rejected_by_id,
    select_shadow_rejected_paginated,
    select_shadow_variant_by_id,
    select_shadow_variants_by_parent,
    update_shadow_rejected_terminal,
    update_shadow_variant_terminal,
)

_FIXED_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
_TERMINATED_AT = datetime(2026, 5, 7, 12, 30, 0, tzinfo=UTC)


def _variant_row(
    *,
    row_id: int = 1,
    parent_trade_id: int = 100,
    parent_kind: str = "live",
    terminal_outcome: str | None = None,
    terminated_at: datetime | None = None,
    realized_pnl: Decimal | None = None,
    mfe_pct: float | None = None,
    mae_pct: float | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "parent_trade_id": parent_trade_id,
        "bot_id": "alpha",
        "variant_name": "no_be",
        "side": "buy",
        "entry_price": Decimal("65000"),
        "qty": Decimal("0.001"),
        "created_at": _FIXED_NOW,
        "terminated_at": terminated_at,
        "terminal_outcome": terminal_outcome,
        "realized_pnl": realized_pnl,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "meta": meta if meta is not None else {},
        "parent_kind": parent_kind,
    }


def _rejected_row(
    *,
    row_id: int = 1,
    terminal_outcome: str | None = None,
    terminated_at: datetime | None = None,
    mfe_pct: float | None = None,
    mae_pct: float | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "signal_id": 42,
        "bot_id": "alpha",
        "symbol": "BTCUSDT",
        "would_side": "buy",
        "created_at": _FIXED_NOW,
        "terminated_at": terminated_at,
        "terminal_outcome": terminal_outcome,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "meta": meta if meta is not None else {},
    }


# ---------------------------------------------------------------------------
# Dataclass + StrEnum shape pins
# ---------------------------------------------------------------------------


def test_shadow_variant_row_dataclass_shape() -> None:
    """ShadowVariantRow has 15 fields per T-511b2a migration 0015 (added parent_kind)."""
    field_names = tuple(f.name for f in fields(ShadowVariantRow))
    assert field_names == (
        "id",
        "parent_trade_id",
        "bot_id",
        "variant_name",
        "side",
        "entry_price",
        "qty",
        "created_at",
        "terminated_at",
        "terminal_outcome",
        "realized_pnl",
        "mfe_pct",
        "mae_pct",
        "meta",
        "parent_kind",
    )
    assert ShadowVariantRow.__dataclass_params__.frozen is True  # type: ignore[attr-defined]


def test_shadow_rejected_row_dataclass_shape() -> None:
    """ShadowRejectedRow has 11 fields per T-510a migration column order."""
    field_names = tuple(f.name for f in fields(ShadowRejectedRow))
    assert field_names == (
        "id",
        "signal_id",
        "bot_id",
        "symbol",
        "would_side",
        "created_at",
        "terminated_at",
        "terminal_outcome",
        "mfe_pct",
        "mae_pct",
        "meta",
    )
    assert ShadowRejectedRow.__dataclass_params__.frozen is True  # type: ignore[attr-defined]


def test_shadow_variant_terminal_strenum_values() -> None:
    """5 values per BRIEF §13.3 + 1 T-512a addition (SHUTDOWN_MID_REPLAY per OQ-4=A)."""
    assert {member.value for member in ShadowVariantTerminal} == {
        "sl_hit",
        "be_hit",
        "tp_trail",
        "tp_full",
        "timeout",
        "shutdown_mid_replay",
    }


def test_shadow_rejected_terminal_strenum_values() -> None:
    """4 values per BRIEF §13.5 + 1 T-513b1 addition (SHUTDOWN_MID_REPLAY mirror T-512a)."""
    assert {member.value for member in ShadowRejectedTerminal} == {
        "would_tp",
        "would_sl",
        "would_be",
        "no_trigger",
        "shutdown_mid_replay",
    }


# ---------------------------------------------------------------------------
# Decoders
# ---------------------------------------------------------------------------


async def test_row_to_shadow_variant_decimal_passthrough_and_terminal_strenum() -> None:
    """Decimal pass-through + StrEnum narrowing on terminal_outcome (operator-decision A)."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _variant_row(
                terminal_outcome="tp_full",
                terminated_at=_TERMINATED_AT,
                realized_pnl=Decimal("12.34"),
                mfe_pct=0.0123,
                mae_pct=-0.0045,
            )
        ]
    )
    rows = await select_active_shadow_variants(conn, bot_id="alpha")
    assert len(rows) == 1
    r = rows[0]
    assert r.entry_price == Decimal("65000")
    assert isinstance(r.entry_price, Decimal)
    assert r.terminal_outcome is ShadowVariantTerminal.TP_FULL
    assert r.realized_pnl == Decimal("12.34")
    assert r.mfe_pct == pytest.approx(0.0123)


async def test_row_to_shadow_rejected_null_optional_fields() -> None:
    """Active rejected row (still observing) — terminal_outcome/timestamps/MFE all None."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[_rejected_row()])
    rows = await select_active_shadow_rejected(conn, bot_id="alpha")
    r = rows[0]
    assert r.terminal_outcome is None
    assert r.terminated_at is None
    assert r.mfe_pct is None
    assert r.mae_pct is None


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def test_select_active_shadow_variants_sql_filter_and_order() -> None:
    """SQL contains WHERE bot_id = $1 AND terminated_at IS NULL ORDER BY created_at ASC."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_active_shadow_variants(conn, bot_id="alpha")
    sql, *bind_args = captured[0]
    assert "WHERE bot_id = $1 AND terminated_at IS NULL" in sql
    assert "ORDER BY created_at ASC" in sql
    assert bind_args == ["alpha"]


async def test_select_active_shadow_rejected_sql_filter_and_order() -> None:
    """SQL mirror — WHERE bot_id = $1 AND terminated_at IS NULL ORDER BY created_at ASC."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_active_shadow_rejected(conn, bot_id="alpha")
    sql, *bind_args = captured[0]
    assert "WHERE bot_id = $1 AND terminated_at IS NULL" in sql
    assert "ORDER BY created_at ASC" in sql
    assert bind_args == ["alpha"]


async def test_select_all_active_shadow_rejected_sql_cross_bot_filter_and_order() -> None:
    """T-513b1 cross-bot helper — terminated_at IS NULL (no bot_id) + ORDER BY created_at ASC."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_all_active_shadow_rejected(conn)
    sql, *bind_args = captured[0]
    assert "WHERE terminated_at IS NULL" in sql
    assert "bot_id =" not in sql
    assert "ORDER BY created_at ASC" in sql
    assert bind_args == []


async def test_select_shadow_variant_by_id_returns_none_on_miss() -> None:
    """fetchrow returns None → helper returns None."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    result = await select_shadow_variant_by_id(conn, variant_id=999)
    assert result is None


async def test_select_shadow_rejected_by_id_happy_path() -> None:
    """fetchrow returns row → typed ShadowRejectedRow."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_rejected_row(row_id=42))
    result = await select_shadow_rejected_by_id(conn, rejected_id=42)
    assert isinstance(result, ShadowRejectedRow)
    assert result.id == 42
    assert result.bot_id == "alpha"


# ---------------------------------------------------------------------------
# Write helpers — L-011 B-mode + non_idempotent
# ---------------------------------------------------------------------------


async def test_insert_shadow_variant_serialises_meta_via_to_jsonable_then_json_dumps() -> None:
    """L-011 B-mode: meta with Decimal serialized as text via json.dumps(_to_jsonable(meta))."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(_sql: str, *args: Any) -> dict[str, Any]:
        captured.append(args)
        return _variant_row()

    conn.fetchrow = _capture
    meta_with_decimal = {"qty_orig": Decimal("0.001"), "tag": "smoke"}
    await insert_shadow_variant(
        conn,
        parent_trade_id=100,
        bot_id="alpha",
        variant_name="no_be",
        side="buy",
        entry_price=Decimal("65000"),
        qty=Decimal("0.001"),
        created_at=_FIXED_NOW,
        parent_kind="live",
        meta=meta_with_decimal,
    )
    bind_args = captured[0]
    meta_arg = bind_args[7]  # 8th positional ($8) = meta
    assert isinstance(meta_arg, str), "L-011 B-mode: bind arg is str (text-mode)"
    parsed = json.loads(meta_arg)
    assert parsed["qty_orig"] == "0.001", "_to_jsonable pre-stringifies Decimal"
    assert parsed["tag"] == "smoke"


async def test_insert_shadow_variant_meta_none_binds_none_for_default() -> None:
    """meta=None → bind None → SQL COALESCE($8::jsonb, '{}'::jsonb) applies DEFAULT."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(_sql: str, *args: Any) -> dict[str, Any]:
        captured.append(args)
        return _variant_row()

    conn.fetchrow = _capture
    await insert_shadow_variant(
        conn,
        parent_trade_id=100,
        bot_id="alpha",
        variant_name="baseline",
        side="buy",
        entry_price=Decimal("65000"),
        qty=Decimal("0.001"),
        created_at=_FIXED_NOW,
        parent_kind="live",
    )
    assert captured[0][7] is None
    # T-511b2a: parent_kind binds at $9 (positional index 8) post-meta.
    assert captured[0][8] == "live"


async def test_insert_shadow_rejected_happy_path() -> None:
    """Basic insert + RETURNING; would_side stored as 'buy'/'sell' literal."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_rejected_row(row_id=7))
    result = await insert_shadow_rejected(
        conn,
        signal_id=42,
        bot_id="alpha",
        symbol="BTCUSDT",
        would_side="buy",
        created_at=_FIXED_NOW,
    )
    assert isinstance(result, ShadowRejectedRow)
    assert result.id == 7


async def test_update_shadow_variant_terminal_returns_row_on_hit_with_strenum_value() -> None:
    """StrEnum input → SQL bind uses .value str literal."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(_sql: str, *args: Any) -> dict[str, Any]:
        captured.append(args)
        return _variant_row(
            row_id=1,
            terminal_outcome="tp_full",
            terminated_at=_TERMINATED_AT,
            realized_pnl=Decimal("12.34"),
        )

    conn.fetchrow = _capture
    result = await update_shadow_variant_terminal(
        conn,
        variant_id=1,
        terminated_at=_TERMINATED_AT,
        terminal_outcome=ShadowVariantTerminal.TP_FULL,
        realized_pnl=Decimal("12.34"),
        mfe_pct=0.0123,
        mae_pct=-0.0045,
    )
    assert isinstance(result, ShadowVariantRow)
    assert result.terminal_outcome is ShadowVariantTerminal.TP_FULL
    # SQL bind uses StrEnum .value (string), not the enum object directly.
    assert captured[0][2] == "tp_full"
    assert isinstance(captured[0][2], str)


async def test_update_shadow_variant_terminal_returns_none_on_miss() -> None:
    """Race-condition defensive: cascade-delete during update → fetchrow None → helper None."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    result = await update_shadow_variant_terminal(
        conn,
        variant_id=999,
        terminated_at=_TERMINATED_AT,
        terminal_outcome=ShadowVariantTerminal.TIMEOUT,
    )
    assert result is None


async def test_update_shadow_rejected_terminal_no_realized_pnl_arg() -> None:
    """update_shadow_rejected_terminal signature has NO realized_pnl kwarg (rejected = no trade)."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value=_rejected_row(
            terminal_outcome="would_tp",
            terminated_at=_TERMINATED_AT,
        )
    )
    # Compile-time check: signature doesn't accept realized_pnl.
    result = await update_shadow_rejected_terminal(
        conn,
        rejected_id=1,
        terminated_at=_TERMINATED_AT,
        terminal_outcome=ShadowRejectedTerminal.WOULD_TP,
    )
    assert isinstance(result, ShadowRejectedRow)
    assert result.terminal_outcome is ShadowRejectedTerminal.WOULD_TP


# ---------------------------------------------------------------------------
# T-511b2a / ADR-0010 — parent_kind discriminator
# ---------------------------------------------------------------------------


async def test_insert_shadow_variant_persists_parent_kind_live() -> None:
    """parent_kind='live' binds at $9 + decoded into ShadowVariantRow.parent_kind."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(_sql: str, *args: Any) -> dict[str, Any]:
        captured.append(args)
        return _variant_row(parent_kind="live")

    conn.fetchrow = _capture
    result = await insert_shadow_variant(
        conn,
        parent_trade_id=100,
        bot_id="alpha",
        variant_name="baseline",
        side="buy",
        entry_price=Decimal("65000"),
        qty=Decimal("0.001"),
        created_at=_FIXED_NOW,
        parent_kind="live",
    )
    assert captured[0][8] == "live"
    assert isinstance(result, ShadowVariantRow)
    assert result.parent_kind == "live"


async def test_insert_shadow_variant_persists_parent_kind_paper() -> None:
    """parent_kind='paper' binds at $9 + decoded into ShadowVariantRow.parent_kind."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(_sql: str, *args: Any) -> dict[str, Any]:
        captured.append(args)
        return _variant_row(parent_kind="paper")

    conn.fetchrow = _capture
    result = await insert_shadow_variant(
        conn,
        parent_trade_id=100,
        bot_id="alpha",
        variant_name="baseline",
        side="buy",
        entry_price=Decimal("65000"),
        qty=Decimal("0.001"),
        created_at=_FIXED_NOW,
        parent_kind="paper",
    )
    assert captured[0][8] == "paper"
    assert isinstance(result, ShadowVariantRow)
    assert result.parent_kind == "paper"


async def test_insert_shadow_variant_requires_parent_kind_kwarg() -> None:
    """parent_kind keyword-only with no default — caller MUST specify (TypeError if omitted)."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_variant_row())
    with pytest.raises(TypeError, match="parent_kind"):
        await insert_shadow_variant(  # type: ignore[call-arg]
            conn,
            parent_trade_id=100,
            bot_id="alpha",
            variant_name="baseline",
            side="buy",
            entry_price=Decimal("65000"),
            qty=Decimal("0.001"),
            created_at=_FIXED_NOW,
        )


@pytest.mark.parametrize(
    "fn",
    [
        insert_shadow_variant,
        insert_shadow_rejected,
        update_shadow_variant_terminal,
        update_shadow_rejected_terminal,
    ],
)
def test_write_helpers_marked_non_idempotent(fn: Any) -> None:
    """All 4 write helpers @non_idempotent per §N3."""
    assert is_non_idempotent(fn)
    assert fn.__non_idempotent__ is True


# ---------------------------------------------------------------------------
# T-516b — select_shadow_variants_by_parent
# ---------------------------------------------------------------------------


async def test_select_shadow_variants_by_parent_returns_variants_for_live_parent() -> None:
    """Live parent: rows with parent_kind='live'; decoder + parent_kind preservation."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _variant_row(row_id=1, parent_trade_id=42, parent_kind="live"),
            _variant_row(row_id=2, parent_trade_id=42, parent_kind="live"),
        ]
    )
    rows = await select_shadow_variants_by_parent(conn, parent_trade_id=42, parent_kind="live")
    assert len(rows) == 2
    assert all(r.parent_kind == "live" for r in rows)
    assert all(r.parent_trade_id == 42 for r in rows)


async def test_select_shadow_variants_by_parent_returns_variants_for_paper_parent() -> None:
    """Paper parent symmetric: parent_kind='paper' rows returned."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _variant_row(row_id=10, parent_trade_id=42, parent_kind="paper"),
            _variant_row(row_id=11, parent_trade_id=42, parent_kind="paper"),
        ]
    )
    rows = await select_shadow_variants_by_parent(conn, parent_trade_id=42, parent_kind="paper")
    assert len(rows) == 2
    assert all(r.parent_kind == "paper" for r in rows)


async def test_select_shadow_variants_by_parent_returns_empty_when_no_variants() -> None:
    """Empty fetch result → empty list; no exception."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    rows = await select_shadow_variants_by_parent(conn, parent_trade_id=999, parent_kind="live")
    assert rows == []


async def test_select_shadow_variants_by_parent_sql_pins_where_and_order() -> None:
    """SQL: parameterized WHERE parent_trade_id=$1 AND parent_kind=$2 + ORDER BY variant_name."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_shadow_variants_by_parent(conn, parent_trade_id=7, parent_kind="live")
    sql, *bind_args = captured[0]
    assert "WHERE parent_trade_id = $1 AND parent_kind = $2" in sql
    assert "ORDER BY variant_name ASC" in sql
    assert bind_args == [7, "live"]


async def test_select_shadow_variants_by_parent_returns_terminated_and_active_mix() -> None:
    """Both terminated (terminated_at non-null) and active (terminated_at None) rows returned."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            _variant_row(
                row_id=1,
                parent_trade_id=5,
                parent_kind="live",
                terminated_at=_TERMINATED_AT,
                terminal_outcome="tp_full",
                realized_pnl=Decimal("10.00"),
            ),
            _variant_row(
                row_id=2,
                parent_trade_id=5,
                parent_kind="live",
                terminated_at=None,
            ),
        ]
    )
    rows = await select_shadow_variants_by_parent(conn, parent_trade_id=5, parent_kind="live")
    assert len(rows) == 2
    # Active variant has None terminal_outcome/realized_pnl/terminated_at.
    active = [r for r in rows if r.terminated_at is None]
    terminated = [r for r in rows if r.terminated_at is not None]
    assert len(active) == 1
    assert len(terminated) == 1
    assert active[0].terminal_outcome is None
    assert active[0].realized_pnl is None
    assert terminated[0].terminal_outcome is ShadowVariantTerminal.TP_FULL
    assert terminated[0].realized_pnl == Decimal("10.00")


# ---------------------------------------------------------------------------
# T-517b1 — select_shadow_rejected_paginated + count_shadow_rejected
# ---------------------------------------------------------------------------


async def test_select_shadow_rejected_paginated_no_filters_emits_no_where_clause() -> None:
    """All-None filter set: SQL has no WHERE; only $1=limit, $2=offset bound."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    rows = await select_shadow_rejected_paginated(
        conn,
        bot_id=None,
        symbol=None,
        status=None,
        terminal_outcome=None,
        from_at=None,
        to_at=None,
        limit=50,
        offset=0,
    )
    assert rows == []
    sql, *bind_args = captured[0]
    assert "WHERE" not in sql
    assert "LIMIT $1 OFFSET $2" in sql
    assert bind_args == [50, 0]


async def test_select_shadow_rejected_paginated_all_filters_predicates_and_bind_order() -> None:
    """Full filter set: 5 predicates AND-joined; status='terminated' constant; bind order pin."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    from_dt = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    to_dt = datetime(2026, 5, 12, 0, 0, 0, tzinfo=UTC)
    await select_shadow_rejected_paginated(
        conn,
        bot_id="alpha",
        symbol="BTCUSDT",
        status="terminated",
        terminal_outcome=ShadowRejectedTerminal.WOULD_TP,
        from_at=from_dt,
        to_at=to_dt,
        limit=25,
        offset=50,
    )
    sql, *bind_args = captured[0]
    assert "WHERE bot_id = $1 AND symbol = $2 AND terminated_at IS NOT NULL" in sql
    assert "AND terminal_outcome = $3 AND created_at >= $4 AND created_at < $5" in sql
    assert "LIMIT $6 OFFSET $7" in sql
    assert bind_args == ["alpha", "BTCUSDT", "would_tp", from_dt, to_dt, 25, 50]


async def test_select_shadow_rejected_paginated_status_active_constant_predicate() -> None:
    """status='active' encodes terminated_at IS NULL (constant; not a $N parameter)."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_shadow_rejected_paginated(
        conn,
        bot_id=None,
        symbol=None,
        status="active",
        terminal_outcome=None,
        from_at=None,
        to_at=None,
        limit=50,
        offset=0,
    )
    sql, *bind_args = captured[0]
    assert "WHERE terminated_at IS NULL" in sql
    assert "LIMIT $1 OFFSET $2" in sql
    assert bind_args == [50, 0]


async def test_select_shadow_rejected_paginated_l008_no_string_interpolation() -> None:
    """L-008: filter values appear ONLY as $N placeholders, NEVER interpolated into SQL."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    sentinel_bot = "alpha-injection-attempt'); DROP TABLE bots; --"
    await select_shadow_rejected_paginated(
        conn,
        bot_id=sentinel_bot,
        symbol=None,
        status=None,
        terminal_outcome=None,
        from_at=None,
        to_at=None,
        limit=10,
        offset=0,
    )
    sql, *bind_args = captured[0]
    assert sentinel_bot not in sql, "filter value must be a $N bind, never interpolated"
    assert "$1" in sql
    assert sentinel_bot in bind_args


async def test_select_shadow_rejected_paginated_order_by_pin_and_no_cast_sites() -> None:
    """ORDER BY created_at DESC, id DESC verbatim; no '::' cast sites (L-021 preventive guard)."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_shadow_rejected_paginated(
        conn,
        bot_id=None,
        symbol=None,
        status=None,
        terminal_outcome=None,
        from_at=None,
        to_at=None,
        limit=50,
        offset=0,
    )
    sql, *_ = captured[0]
    assert "ORDER BY created_at DESC, id DESC" in sql
    # WG#2 — preventive guard: no ::type cast sites today; future edits introducing
    # arithmetic / CASE branches must add explicit casts per L-021.
    assert "::" not in sql


async def test_count_shadow_rejected_uses_same_builder_filter_semantics() -> None:
    """count_shadow_rejected feeds same _build_shadow_rejected_where_clause; bind args identical."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> Any:
        captured.append((sql, *args))
        return {"n": 7}

    conn.fetchrow = _capture
    total = await count_shadow_rejected(
        conn,
        bot_id="alpha",
        symbol="ETHUSDT",
        status="terminated",
        terminal_outcome=ShadowRejectedTerminal.WOULD_SL,
        from_at=None,
        to_at=None,
    )
    assert total == 7
    sql, *bind_args = captured[0]
    assert sql.startswith("SELECT COUNT(*) AS n FROM shadow_rejected ")
    assert "WHERE bot_id = $1 AND symbol = $2 AND terminated_at IS NOT NULL" in sql
    assert "AND terminal_outcome = $3" in sql
    assert bind_args == ["alpha", "ETHUSDT", "would_sl"]


async def test_select_shadow_rejected_paginated_terminal_outcome_bind_uses_dot_value() -> None:
    """WG#1 — terminal_outcome bind is the literal string 'would_sl', not str(enum)/repr."""
    conn = MagicMock()
    captured: list[tuple[Any, ...]] = []

    async def _capture(sql: str, *args: Any) -> list[Any]:
        captured.append((sql, *args))
        return []

    conn.fetch = _capture
    await select_shadow_rejected_paginated(
        conn,
        bot_id=None,
        symbol=None,
        status=None,
        terminal_outcome=ShadowRejectedTerminal.WOULD_SL,
        from_at=None,
        to_at=None,
        limit=50,
        offset=0,
    )
    sql, *bind_args = captured[0]
    assert "WHERE terminal_outcome = $1" in sql
    # WG#1 — must be plain string from `.value`, not StrEnum repr or any subclass instance.
    assert bind_args == ["would_sl", 50, 0]
    assert type(bind_args[0]) is str
