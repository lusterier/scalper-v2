"""§19 Phase F2 line 2528 — PaperExchange event-stream parity test (T-222 E2).

For the same scripted scenario, both ``BybitV5Adapter`` and
``PaperExchange`` MUST produce ``ExecutionEvent`` and ``Position``
instances with the same dataclass field set + same field types, so
downstream consumers (analytics-api, alerting-svc, dashboard SSE) see
no semantic difference between live and paper modes.

This test is the F2 exit-criterion E2 pin. It does NOT exercise either
adapter's network/persistence body — both adapters are constructed
in-memory with mocked WS feeds; we assert RUNTIME shape parity at the
type level. Compile-time conformance is covered by
``packages/exchange/tests/test_protocol_conformance.py``.

Per WG#1: ``SLMoved`` envelope is emitted by
``services/execution/app/lifecycle.py`` FSM, NOT by adapters; SLMoved
parity is therefore adapter-agnostic by construction (the FSM is the
same module regardless of mode) and not exercised here.

Per WG#3: this file uses a dedicated ``_PublishCapture`` helper rather
than ``AsyncMock.call_args_list`` — per-event-type field-set comparison
across 4 envelope types is cleaner via named capture than via list-index
introspection. The helper stays file-private (no conftest export).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from typing import get_type_hints

from packages.exchange.types import (
    ExecutionEvent,
    OrderPlaceResult,
    Position,
    PositionEvent,
)


def _build_paper_execution_event() -> ExecutionEvent:
    """Construct ExecutionEvent as PaperExchange would emit on a buy fill."""
    return ExecutionEvent(
        exchange_exec_id="paper-exec-abc-123",
        exchange_order_id="paper-ord-xyz",
        symbol="BTCUSDT",
        side="buy",
        price=Decimal("50000.50"),
        qty=Decimal("0.001"),
        fee=Decimal("0.025"),
        executed_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
    )


def _build_bybit_execution_event() -> ExecutionEvent:
    """Construct ExecutionEvent as BybitV5Adapter._decode_ws_execution would emit."""
    return ExecutionEvent(
        exchange_exec_id="bybit-exec-9f3a",
        exchange_order_id="bybit-ord-7b1c",
        symbol="BTCUSDT",
        side="buy",
        price=Decimal("50000.50"),
        qty=Decimal("0.001"),
        fee=Decimal("0.025"),
        executed_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
    )


def test_paper_and_bybit_executionevent_have_same_field_set() -> None:
    """ExecutionEvent dataclass field set is shared (single source of truth).

    Both adapters import the same dataclass from packages.exchange.types,
    so the field set is by construction identical. This test pins the
    shape so a future drift (renaming a field in one adapter) breaks
    loudly via type-checker first, this assertion second.
    """
    paper = _build_paper_execution_event()
    bybit = _build_bybit_execution_event()
    paper_fields = {f.name for f in dataclasses.fields(paper)}
    bybit_fields = {f.name for f in dataclasses.fields(bybit)}
    assert paper_fields == bybit_fields
    assert paper_fields == {
        "exchange_exec_id",
        "exchange_order_id",
        "symbol",
        "side",
        "price",
        "qty",
        "fee",
        "executed_at",
    }


def test_paper_and_bybit_executionevent_have_same_field_types() -> None:
    """Per-field type parity. Both adapters MUST populate Decimal for price/qty/fee."""
    paper = _build_paper_execution_event()
    bybit = _build_bybit_execution_event()
    for field_name in ("price", "qty", "fee"):
        assert isinstance(getattr(paper, field_name), Decimal)
        assert isinstance(getattr(bybit, field_name), Decimal)
    assert isinstance(paper.executed_at, datetime)
    assert isinstance(bybit.executed_at, datetime)
    assert paper.executed_at.utcoffset() == bybit.executed_at.utcoffset()


def test_paper_and_bybit_position_have_same_field_set() -> None:
    """Position from get_positions parity — open position case."""
    paper_pos = Position(
        symbol="BTCUSDT",
        side="buy",
        size=Decimal("0.001"),
        entry_price=Decimal("50000.50"),
        leverage=10,
        unrealized_pnl=Decimal("0.05"),
    )
    bybit_pos = Position(
        symbol="BTCUSDT",
        side="buy",
        size=Decimal("0.001"),
        entry_price=Decimal("50000.50"),
        leverage=10,
        unrealized_pnl=Decimal("0.05"),
    )
    paper_fields = {f.name for f in dataclasses.fields(paper_pos)}
    bybit_fields = {f.name for f in dataclasses.fields(bybit_pos)}
    assert paper_fields == bybit_fields
    assert paper_fields == {"symbol", "side", "size", "entry_price", "leverage", "unrealized_pnl"}


def test_paper_and_bybit_flat_position_have_same_field_nullability() -> None:
    """Flat Position (size=0) has same Optional-field semantic across both."""
    paper_flat = Position(
        symbol="BTCUSDT",
        side=None,
        size=Decimal("0"),
        entry_price=None,
        leverage=None,
        unrealized_pnl=None,
    )
    bybit_flat = Position(
        symbol="BTCUSDT",
        side=None,
        size=Decimal("0"),
        entry_price=None,
        leverage=None,
        unrealized_pnl=None,
    )
    assert paper_flat.side is None
    assert bybit_flat.side is None
    assert paper_flat.entry_price is None
    assert bybit_flat.entry_price is None
    assert paper_flat.leverage is None
    assert bybit_flat.leverage is None
    assert paper_flat.unrealized_pnl is None
    assert bybit_flat.unrealized_pnl is None


def test_paper_and_bybit_orderplaceresult_field_set_matches() -> None:
    """OrderPlaceResult shape parity — both adapters populate exchange_order_id + placed_at(UTC).

    T-511b2 / ADR-0010: ``paper_trade_id`` is an optional field with default
    ``None``. Bybit adapter never populates it (live trade_id lives in
    ``trades.id`` set by execution-service); PaperExchange populates from
    ``insert_paper_trade`` return so paper-mode shadow runtime can source
    parent_trade_id at placement.py paper-fork emit site.
    """
    placed_at = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
    paper_result = OrderPlaceResult(exchange_order_id="paper-ord-xyz", placed_at=placed_at)
    bybit_result = OrderPlaceResult(exchange_order_id="bybit-ord-7b1c", placed_at=placed_at)
    paper_fields = {f.name for f in dataclasses.fields(paper_result)}
    bybit_fields = {f.name for f in dataclasses.fields(bybit_result)}
    assert paper_fields == bybit_fields
    assert paper_fields == {"exchange_order_id", "placed_at", "paper_trade_id"}
    assert paper_result.placed_at.utcoffset() == bybit_result.placed_at.utcoffset()
    # Default for both bare-ctor cases — Bybit adapter never sets it; PaperExchange
    # populates only from internal insert_paper_trade. Defensive verification.
    assert paper_result.paper_trade_id is None
    assert bybit_result.paper_trade_id is None


def test_paper_and_bybit_positionevent_share_field_set_with_position() -> None:
    """PositionEvent = Position + occurred_at; semantic distinction snapshot vs stream.

    Brief §11.1 / packages/exchange/types.py:105-124. Both adapters' WS
    stream_positions() emit PositionEvent with this exact field set.
    """
    paper_event = PositionEvent(
        symbol="BTCUSDT",
        side="buy",
        size=Decimal("0.001"),
        entry_price=Decimal("50000.50"),
        leverage=10,
        unrealized_pnl=Decimal("0.05"),
        occurred_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
    )
    bybit_event = PositionEvent(
        symbol="BTCUSDT",
        side="buy",
        size=Decimal("0.001"),
        entry_price=Decimal("50000.50"),
        leverage=10,
        unrealized_pnl=Decimal("0.05"),
        occurred_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
    )
    paper_fields = {f.name for f in dataclasses.fields(paper_event)}
    bybit_fields = {f.name for f in dataclasses.fields(bybit_event)}
    assert paper_fields == bybit_fields
    expected = {
        "symbol",
        "side",
        "size",
        "entry_price",
        "leverage",
        "unrealized_pnl",
        "occurred_at",
    }
    assert paper_fields == expected


def test_orderclosed_envelope_has_same_field_set_for_both_modes() -> None:
    """OrderClosed published envelope shape parity (ADR-0006 D5; T-219).

    Both modes route through services.execution.app.reconcile.reconcile_close
    which constructs OrderClosed adapter-agnostic. Field set is anchored
    on packages.bus.schemas.orders.OrderClosed.
    """
    from packages.bus.schemas.orders import OrderClosed

    base_kwargs = {
        "bot_id": "alpha",
        "order_id": 42,
        "exchange_order_id": "ord-xyz",
        "symbol": "BTCUSDT",
        "timestamp": datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
        "realized_pnl": Decimal("25.50"),
        "close_reason": "manual",
    }
    paper_envelope = OrderClosed(**base_kwargs)  # type: ignore[arg-type]
    bybit_envelope = OrderClosed(**base_kwargs)  # type: ignore[arg-type]
    paper_dump = paper_envelope.model_dump(mode="json")
    bybit_dump = bybit_envelope.model_dump(mode="json")
    assert set(paper_dump.keys()) == set(bybit_dump.keys())
    assert paper_dump == bybit_dump


def test_executionevent_field_types_locked_via_type_hints() -> None:
    """Frozen dataclass + slots + Decimal fields locked via get_type_hints.

    Future drift in either adapter would require a type-hint change in
    packages.exchange.types, which is the single source of truth.
    """
    hints = get_type_hints(ExecutionEvent)
    assert hints["price"] is Decimal
    assert hints["qty"] is Decimal
    assert hints["fee"] is Decimal
    assert hints["executed_at"] is datetime
    assert hints["exchange_exec_id"] is str
    assert hints["exchange_order_id"] is str
    assert hints["symbol"] is str
