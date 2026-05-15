"""Surface invariants for :mod:`packages.exchange.types` (T-201).

Each dataclass is verified for: frozen + slots, expected field set, and
hazard-driven field exclusions (H-024 → no ``exec_type`` on
:class:`ExecutionEvent`). Behavioural tests (real Bybit / paper fills)
land in T-207 / T-211 / T-218.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from packages.exchange import (
    ExecutionEvent,
    OrderPlaceResult,
    Position,
    PositionEvent,
)


def _now() -> datetime:
    return datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)


# --- OrderPlaceResult -------------------------------------------------------


def test_order_place_result_constructs_with_required_fields() -> None:
    r = OrderPlaceResult(exchange_order_id="abc-123", placed_at=_now())
    assert r.exchange_order_id == "abc-123"
    assert r.placed_at == _now()


def test_order_place_result_is_frozen() -> None:
    r = OrderPlaceResult(exchange_order_id="abc-123", placed_at=_now())
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.exchange_order_id = "x"  # type: ignore[misc]


def test_order_place_result_uses_slots() -> None:
    r = OrderPlaceResult(exchange_order_id="abc-123", placed_at=_now())
    assert not hasattr(r, "__dict__")


def test_order_place_result_field_set() -> None:
    """T-511b2 / ADR-0010: paper_trade_id added (default None; PaperExchange populates)."""
    fields = {f.name for f in dataclasses.fields(OrderPlaceResult)}
    assert fields == {"exchange_order_id", "placed_at", "paper_trade_id"}


def test_order_place_result_paper_trade_id_default_none() -> None:
    """Bybit-side construction (no paper_trade_id arg) → default None per ADR-0010."""
    r = OrderPlaceResult(exchange_order_id="bybit-ord-1", placed_at=_now())
    assert r.paper_trade_id is None


# --- Position ---------------------------------------------------------------


def test_position_open_long() -> None:
    p = Position(
        symbol="BTCUSDT",
        side="buy",
        size=Decimal("0.05"),
        entry_price=Decimal("65000.00"),
        leverage=10,
        unrealized_pnl=Decimal("12.50"),
        sl_price=Decimal("64000.00"),
    )
    assert p.side == "buy"
    assert p.size == Decimal("0.05")
    assert p.sl_price == Decimal("64000.00")


def test_position_flat_uses_none_fields() -> None:
    p = Position(
        symbol="BTCUSDT",
        side=None,
        size=Decimal("0"),
        entry_price=None,
        leverage=None,
        unrealized_pnl=None,
        sl_price=None,
    )
    assert p.side is None
    assert p.size == Decimal("0")
    assert p.entry_price is None
    # T-534a: flat (size==0) → sl_price is None too (docstring invariant).
    assert p.sl_price is None


def test_position_size_is_decimal_not_float() -> None:
    fields = {f.name: f.type for f in dataclasses.fields(Position)}
    assert "Decimal" in str(fields["size"])


def test_position_field_set_is_minimal_seven_with_sl_price() -> None:
    fields = {f.name for f in dataclasses.fields(Position)}
    assert fields == {
        "symbol",
        "side",
        "size",
        "entry_price",
        "leverage",
        "unrealized_pnl",
        "sl_price",
    }


def test_position_is_frozen_and_slotted() -> None:
    p = Position(
        symbol="BTCUSDT",
        side=None,
        size=Decimal("0"),
        entry_price=None,
        leverage=None,
        unrealized_pnl=None,
        sl_price=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.size = Decimal("1")  # type: ignore[misc]
    assert not hasattr(p, "__dict__")


# --- ExecutionEvent ---------------------------------------------------------


def test_execution_event_constructs_with_required_fields() -> None:
    e = ExecutionEvent(
        exchange_exec_id="exec-1",
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        side="buy",
        price=Decimal("65000.00"),
        qty=Decimal("0.05"),
        fee=Decimal("0.0325"),
        executed_at=_now(),
    )
    assert e.exchange_exec_id == "exec-1"
    assert e.fee == Decimal("0.0325")


def test_execution_event_does_not_carry_exec_type() -> None:
    """H-024: dispatcher (T-218) derives exec_type via DB, not from this event."""
    fields = {f.name for f in dataclasses.fields(ExecutionEvent)}
    assert "exec_type" not in fields
    assert "execType" not in fields


def test_execution_event_field_set() -> None:
    fields = {f.name for f in dataclasses.fields(ExecutionEvent)}
    assert fields == {
        "exchange_exec_id",
        "exchange_order_id",
        "symbol",
        "side",
        "price",
        "qty",
        "fee",
        "executed_at",
    }


def test_execution_event_fee_is_required_decimal() -> None:
    e = ExecutionEvent(
        exchange_exec_id="exec-1",
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        side="sell",
        price=Decimal("65000.00"),
        qty=Decimal("0.05"),
        fee=Decimal("0"),
        executed_at=_now(),
    )
    assert e.fee == Decimal("0")


# --- PositionEvent ----------------------------------------------------------


def test_position_event_excludes_sl_price_carries_occurred_at() -> None:
    """T-534a / OQ-5=b: PositionEvent deliberately does NOT carry the
    REST-snapshot-only ``sl_price`` (no WS-stream SL-existence consumer;
    §0.8). Pinned to an explicit set so the deliberate Position vs
    PositionEvent field-seam divergence cannot silently re-converge.
    """
    pe_fields = {f.name for f in dataclasses.fields(PositionEvent)}
    assert pe_fields == {
        "symbol",
        "side",
        "size",
        "entry_price",
        "leverage",
        "unrealized_pnl",
        "occurred_at",
    }
    p_fields = {f.name for f in dataclasses.fields(Position)}
    assert "sl_price" in p_fields
    assert "sl_price" not in pe_fields


def test_position_event_constructs_open_state() -> None:
    e = PositionEvent(
        symbol="BTCUSDT",
        side="sell",
        size=Decimal("0.10"),
        entry_price=Decimal("65000.00"),
        leverage=20,
        unrealized_pnl=Decimal("-5.00"),
        occurred_at=_now(),
    )
    assert e.side == "sell"
    assert e.occurred_at == _now()


def test_position_event_is_frozen_and_slotted() -> None:
    e = PositionEvent(
        symbol="BTCUSDT",
        side=None,
        size=Decimal("0"),
        entry_price=None,
        leverage=None,
        unrealized_pnl=None,
        occurred_at=_now(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.size = Decimal("1")  # type: ignore[misc]
    assert not hasattr(e, "__dict__")
