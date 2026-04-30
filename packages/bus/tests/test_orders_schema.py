"""§N4 unit tests for :mod:`packages.bus.schemas.orders` (T-216a)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.bus.schemas.orders import (
    OrderClosed,
    OrderFilled,
    OrderPlaced,
    OrderRequest,
    SLMoved,
    TradingEvent,
    subject_for_orders_dlq,
    subject_for_orders_event,
    subject_for_orders_request,
)


def _utc_now() -> datetime:
    return datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# OrderRequest (3 tests)
# ---------------------------------------------------------------------------


def test_order_request_round_trip_preserves_decimal_precision() -> None:
    request = OrderRequest(
        bot_id="alpha",
        signal_id=42,
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("0.001000000000"),
        leverage=10,
        sl_pct=Decimal("0.005"),
        tp_pct=Decimal("0.015"),
        tp_qty_pct=Decimal("0.5"),
        be_trigger=Decimal("0.003"),
        be_sl_level=Decimal("0.001"),
        trail_pct=Decimal("0.002"),
        exchange_mode="live",
    )
    dumped = request.model_dump()
    rebuilt = OrderRequest.model_validate(dumped)
    assert rebuilt.qty == Decimal("0.001000000000")
    assert isinstance(rebuilt.qty, Decimal)
    assert str(rebuilt.qty) == "0.001000000000"


def test_order_request_validates_side_literal() -> None:
    with pytest.raises(ValidationError):
        OrderRequest(
            bot_id="alpha",
            signal_id=1,
            symbol="BTCUSDT",
            side="long",  # type: ignore[arg-type]
            qty=Decimal("0.001"),
            leverage=10,
            sl_pct=Decimal("0.005"),
            tp_pct=Decimal("0.015"),
            tp_qty_pct=Decimal("0.5"),
            be_trigger=Decimal("0.003"),
            be_sl_level=Decimal("0.001"),
            trail_pct=Decimal("0.002"),
            exchange_mode="live",
        )


def test_order_request_validates_exchange_mode_literal() -> None:
    with pytest.raises(ValidationError):
        OrderRequest(
            bot_id="alpha",
            signal_id=1,
            symbol="BTCUSDT",
            side="buy",
            qty=Decimal("0.001"),
            leverage=10,
            sl_pct=Decimal("0.005"),
            tp_pct=Decimal("0.015"),
            tp_qty_pct=Decimal("0.5"),
            be_trigger=Decimal("0.003"),
            be_sl_level=Decimal("0.001"),
            trail_pct=Decimal("0.002"),
            exchange_mode="demo",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# OrderEvent subclasses (4 tests)
# ---------------------------------------------------------------------------


def test_order_placed_serializes_timestamp_with_explicit_utc_offset() -> None:
    event = OrderPlaced(
        bot_id="alpha",
        order_id=1,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_utc_now(),
    )
    dumped = event.model_dump()
    assert dumped["timestamp"].endswith("+00:00")
    assert dumped["event_type"] == "order_placed"


def test_order_filled_carries_exec_id_price_qty_fee_exec_type() -> None:
    event = OrderFilled(
        bot_id="alpha",
        order_id=1,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_utc_now(),
        exec_id="exec-1",
        price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        fee=Decimal("0.0225"),
        exec_type="open",
    )
    assert event.event_type == "order_filled"
    assert event.exec_type == "open"
    assert isinstance(event.price, Decimal)


def test_order_closed_carries_realized_pnl_and_close_reason() -> None:
    event = OrderClosed(
        bot_id="alpha",
        order_id=1,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_utc_now(),
        realized_pnl=Decimal("12.50"),
        close_reason="tp",
    )
    assert event.event_type == "order_closed"
    assert event.close_reason == "tp"


def test_sl_moved_carries_new_sl_price_and_sl_type() -> None:
    event = SLMoved(
        bot_id="alpha",
        order_id=1,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_utc_now(),
        new_sl_price=Decimal("44900.00"),
        sl_type="be",
    )
    assert event.event_type == "sl_moved"
    assert event.sl_type == "be"


def test_order_event_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        OrderPlaced(
            bot_id="alpha",
            order_id=1,
            exchange_order_id="ord-1",
            symbol="BTCUSDT",
            timestamp=datetime(2026, 4, 30, 12, 0, 0),  # noqa: DTZ001 — naive datetime intentionally tested
        )


def test_order_event_rejects_non_utc_offset() -> None:
    with pytest.raises(ValidationError):
        OrderPlaced(
            bot_id="alpha",
            order_id=1,
            exchange_order_id="ord-1",
            symbol="BTCUSDT",
            timestamp=datetime(2026, 4, 30, 14, 0, 0, tzinfo=timezone(timedelta(hours=2))),
        )


# ---------------------------------------------------------------------------
# TradingEvent (1 test)
# ---------------------------------------------------------------------------


def test_trading_event_carries_event_type_and_payload_dict() -> None:
    te = TradingEvent(
        occurred_at=_utc_now(),
        bot_id="alpha",
        correlation_id="cid-1",
        event_type="order_placed",
        payload={"order_id": 1, "exchange_order_id": "ord-1"},
    )
    dumped = te.model_dump()
    assert dumped["occurred_at"].endswith("+00:00")
    assert dumped["event_type"] == "order_placed"
    assert dumped["payload"] == {"order_id": 1, "exchange_order_id": "ord-1"}


# ---------------------------------------------------------------------------
# Subject helpers (1 test)
# ---------------------------------------------------------------------------


def test_subject_helpers_match_brief_section_8_format() -> None:
    assert subject_for_orders_request("alpha") == "orders.requests.alpha"
    assert subject_for_orders_event("alpha") == "orders.events.alpha"
    assert subject_for_orders_dlq("alpha") == "orders.dlq.alpha"
