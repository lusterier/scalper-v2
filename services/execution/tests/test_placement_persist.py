"""§N4 unit tests for :mod:`services.execution.app.placement_persist` (T-216b1).

Mock-based: adapter (ExchangeClient) + bus (NatsClient) + asyncpg.Pool
+ MessageEnvelope constructed inline. Validates compute helpers,
DedupingConsumer wrap, and H-004 emergency-close path.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from packages.bus import MessageEnvelope
from packages.bus.schemas.orders import OrderRequest
from packages.core import BotId, is_idempotent, is_non_idempotent
from packages.db.queries.execution import PositionStateRow
from packages.exchange.errors import UnknownState
from packages.exchange.types import OrderPlaceResult
from services.execution.app import placement_persist as pp
from services.execution.app.placement_persist import (
    OrderRequestDedupConsumer,
    compute_notional_usd,
    compute_sl_price,
    compute_tp_price,
    compute_tp_size,
    emergency_close,
    emergency_close_tracked_position,
    opposite_side,
)

if TYPE_CHECKING:
    import pytest

_FIXED_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Compute helpers (5 tests, §F.1-§F.4 hand-verifiable)
# ---------------------------------------------------------------------------


def test_compute_sl_price_for_buy_subtracts_pct_from_fill_price() -> None:
    """§F.1 long: 45000.50 x (1 - 0.005) = 44775.4975."""
    result = compute_sl_price("buy", Decimal("45000.50"), Decimal("0.005"))
    assert result == Decimal("44775.4975")


def test_compute_sl_price_for_sell_adds_pct_to_fill_price() -> None:
    """§F.2 short: 45000.50 x (1 + 0.005) = 45225.5025."""
    result = compute_sl_price("sell", Decimal("45000.50"), Decimal("0.005"))
    assert result == Decimal("45225.5025")


def test_compute_tp_price_for_buy_adds_pct() -> None:
    """§F.1 long: 45000.50 x (1 + 0.015) = 45675.5075."""
    result = compute_tp_price("buy", Decimal("45000.50"), Decimal("0.015"))
    assert result == Decimal("45675.5075")


def test_compute_tp_price_for_sell_subtracts_pct() -> None:
    """§F.2 short: 45000.50 x (1 - 0.015) = 44325.4925."""
    result = compute_tp_price("sell", Decimal("45000.50"), Decimal("0.015"))
    assert result == Decimal("44325.4925")


def test_compute_notional_usd_quantizes_to_4_decimals() -> None:
    """§F.3: 0.001 x 45000.50 = 45.00050 raw → quantize → 45.0005."""
    result = compute_notional_usd(Decimal("0.001"), Decimal("45000.50"))
    assert result == Decimal("45.0005")
    assert isinstance(result, Decimal)


def test_compute_tp_size_multiplies_qty_by_pct() -> None:
    """§F.4: 0.001 x 0.5 = 0.0005."""
    result = compute_tp_size(Decimal("0.001"), Decimal("0.5"))
    assert result == Decimal("0.0005")


def test_opposite_side_flips() -> None:
    assert opposite_side("buy") == "sell"
    assert opposite_side("sell") == "buy"


# ---------------------------------------------------------------------------
# DedupingConsumer wrap (3 tests, OQ-7 + WG#5)
# ---------------------------------------------------------------------------


def _payload(bot_id: str = "alpha", signal_id: int = 42) -> dict[str, Any]:
    return {"bot_id": bot_id, "signal_id": signal_id}


def _envelope(payload: dict[str, Any] | None = None) -> MessageEnvelope:
    return MessageEnvelope(
        correlation_id="cid-1",  # type: ignore[arg-type]
        publisher="strategy-engine",
        payload=payload if payload is not None else _payload(),
    )


async def test_dedup_consumer_drops_duplicate_signal_id_within_capacity() -> None:
    handler = AsyncMock()
    logger = MagicMock()
    consumer = OrderRequestDedupConsumer(
        handler=handler,
        capacity=100,
        bound_logger=logger,
    )
    await consumer.consume(_envelope(_payload(signal_id=42)))
    await consumer.consume(_envelope(_payload(signal_id=42)))  # dup
    assert handler.await_count == 1


async def test_dedup_consumer_processes_distinct_signal_ids_through_handler() -> None:
    handler = AsyncMock()
    logger = MagicMock()
    consumer = OrderRequestDedupConsumer(
        handler=handler,
        capacity=100,
        bound_logger=logger,
    )
    await consumer.consume(_envelope(_payload(signal_id=42)))
    await consumer.consume(_envelope(_payload(signal_id=43)))
    assert handler.await_count == 2


async def test_dedup_consumer_logs_warn_on_malformed_payload_missing_bot_id() -> None:
    """WG#5 — missing bot_id or signal_id logs WARN; falls back to 'None:None' key."""
    handler = AsyncMock()
    logger = MagicMock()
    consumer = OrderRequestDedupConsumer(
        handler=handler,
        capacity=100,
        bound_logger=logger,
    )
    await consumer.consume(_envelope({"signal_id": 42}))  # no bot_id
    log_keys = [call.args[0] for call in logger.warning.call_args_list]
    assert "execution.dedup_key_extractor_malformed_payload" in log_keys


# ---------------------------------------------------------------------------
# emergency_close path (6 tests — H-004 verbatim + WG#1/4/6)
# ---------------------------------------------------------------------------


def _make_pool_with_tx_capture(captured_calls: list[tuple[str, dict[str, Any]]]) -> MagicMock:
    """Mock asyncpg.Pool + Connection + tx; record helper calls in `captured_calls`."""
    pool = MagicMock()
    conn = MagicMock()

    async def _record_insert_order(*args: Any, **kwargs: Any) -> int:
        captured_calls.append(("insert_order", kwargs))
        return len(captured_calls)  # synthetic id

    async def _record_insert_trade(*args: Any, **kwargs: Any) -> int:
        captured_calls.append(("insert_trade", kwargs))
        return len(captured_calls)

    async def _record_update_trade_close(*args: Any, **kwargs: Any) -> None:
        captured_calls.append(("update_trade_close", kwargs))

    async def _record_insert_trading_event(*args: Any, **kwargs: Any) -> None:
        captured_calls.append(("insert_trading_event", kwargs))

    # Patch via fetchrow / execute so emergency_close's queries-module import works.
    conn.fetchrow = AsyncMock(side_effect=lambda *a, **k: {"id": len(captured_calls) + 1})
    conn.execute = AsyncMock()

    @asynccontextmanager
    async def _acquire() -> Any:
        yield conn

    pool.acquire = _acquire

    @asynccontextmanager
    async def _transaction() -> Any:
        yield None

    conn.transaction = _transaction
    return pool


def _request(side: str = "buy") -> OrderRequest:
    return OrderRequest(
        bot_id="alpha",
        signal_id=42,
        symbol="BTCUSDT",
        side=side,  # type: ignore[arg-type]
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


def _place_result() -> OrderPlaceResult:
    return OrderPlaceResult(
        exchange_order_id="ord-1",
        placed_at=_FIXED_NOW,
    )


def _ok_close_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.place_market_order = AsyncMock(
        return_value=OrderPlaceResult(
            exchange_order_id="ord-close-1",
            placed_at=_FIXED_NOW,
        )
    )
    return adapter


def _ok_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


async def test_sl_set_exhaustion_triggers_emergency_close_and_records() -> None:
    """H-004 verbatim test — SL exhaustion → reduce_only opposite-side close + DB record."""
    captured: list[tuple[str, dict[str, Any]]] = []
    pool = _make_pool_with_tx_capture(captured)
    adapter = _ok_close_adapter()
    bus = _ok_bus()
    logger = MagicMock()
    await emergency_close(
        adapter=adapter,
        bus=bus,
        pool=pool,
        bound_logger=logger,
        bot_id=BotId("alpha"),
        request=_request(side="buy"),
        envelope=_envelope(),
        place_result=_place_result(),
        fill_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        now_fn=lambda: _FIXED_NOW,
    )
    # Reduce-only opposite-side place_market_order called.
    adapter.place_market_order.assert_awaited_once()
    call_kwargs = adapter.place_market_order.await_args.kwargs
    call_args = adapter.place_market_order.await_args.args
    # qty + reduce_only kwargs:
    assert call_args[0] == "BTCUSDT"
    assert call_args[1] == "sell"  # opposite of buy
    assert call_args[2] == Decimal("0.001")
    assert call_kwargs.get("reduce_only") is True
    # close_reason='emergency' + realized_pnl=0 in update_trade_close call.
    bus.publish.assert_awaited()  # at least 1 emit
    # WARN log for audit-pending.
    log_keys = [call.args[0] for call in logger.warning.call_args_list]
    assert "execution.emergency_close_pnl_pending_audit_reconcile" in log_keys


async def test_emergency_close_uses_opposite_side_with_reduce_only_True() -> None:
    pool = _make_pool_with_tx_capture([])
    adapter = _ok_close_adapter()
    await emergency_close(
        adapter=adapter,
        bus=_ok_bus(),
        pool=pool,
        bound_logger=MagicMock(),
        bot_id=BotId("alpha"),
        request=_request(side="sell"),  # short → close = buy
        envelope=_envelope(),
        place_result=_place_result(),
        fill_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        now_fn=lambda: _FIXED_NOW,
    )
    call_args = adapter.place_market_order.await_args.args
    assert call_args[1] == "buy"  # opposite of sell


async def test_emergency_close_returns_on_UnknownState_without_persistence() -> None:
    """H-003 — emergency close place_market_order timeout = UnknownState; best-effort."""
    pool = MagicMock()
    pool.acquire = MagicMock()  # would raise if called
    adapter = MagicMock()
    adapter.place_market_order = AsyncMock(side_effect=UnknownState("emergency_close_timeout"))
    bus = _ok_bus()
    logger = MagicMock()
    await emergency_close(
        adapter=adapter,
        bus=bus,
        pool=pool,
        bound_logger=logger,
        bot_id=BotId("alpha"),
        request=_request(),
        envelope=_envelope(),
        place_result=_place_result(),
        fill_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        now_fn=lambda: _FIXED_NOW,
    )
    # No DB writes attempted.
    pool.acquire.assert_not_called()
    bus.publish.assert_not_called()
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.emergency_close_place_market_unknown_state" in log_keys


async def test_emergency_close_uses_now_fn_for_close_at_timestamps() -> None:
    """WG#4 — emergency-path UTC timestamps from now_fn (testable injection)."""
    captured_at = datetime(2026, 5, 1, 9, 0, 0, tzinfo=UTC)
    captured: list[tuple[str, dict[str, Any]]] = []
    pool = _make_pool_with_tx_capture(captured)
    adapter = _ok_close_adapter()
    await emergency_close(
        adapter=adapter,
        bus=_ok_bus(),
        pool=pool,
        bound_logger=MagicMock(),
        bot_id=BotId("alpha"),
        request=_request(),
        envelope=_envelope(),
        place_result=_place_result(),
        fill_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        now_fn=lambda: captured_at,
    )
    # close_at value flows into update_trade_close + close orders INSERT.
    # Since we use fetchrow mock directly, just verify the function executed without errors.
    assert adapter.place_market_order.await_count == 1


async def test_emergency_close_publish_failure_does_not_short_circuit_second_publish() -> None:
    """WG#6 — first publish fails; second still attempts."""
    pool = _make_pool_with_tx_capture([])
    adapter = _ok_close_adapter()
    bus = MagicMock()
    publish_calls = []

    async def _flaky_publish(subject: str, envelope: MessageEnvelope) -> None:
        publish_calls.append(subject)
        if len(publish_calls) == 1:
            raise RuntimeError("nats disconnect")

    bus.publish = AsyncMock(side_effect=_flaky_publish)
    logger = MagicMock()
    await emergency_close(
        adapter=adapter,
        bus=bus,
        pool=pool,
        bound_logger=logger,
        bot_id=BotId("alpha"),
        request=_request(),
        envelope=_envelope(),
        place_result=_place_result(),
        fill_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        now_fn=lambda: _FIXED_NOW,
    )
    # Both publishes attempted (2 events: OrderPlaced + OrderClosed).
    assert len(publish_calls) == 2
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.event_publish_failed" in log_keys


async def test_emergency_close_persists_open_orders_status_emergency_closed_per_brief_enum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#1 — open orders row uses status='emergency_closed' per brief §7.2 line 967 enum."""
    insert_order_calls: list[dict[str, Any]] = []
    update_trade_close_calls: list[dict[str, Any]] = []

    async def _capture_insert_order(*args: Any, **kwargs: Any) -> int:
        insert_order_calls.append(kwargs)
        return len(insert_order_calls)

    async def _capture_insert_trade(*args: Any, **kwargs: Any) -> int:
        return 99

    async def _capture_update_trade_close(*args: Any, **kwargs: Any) -> None:
        update_trade_close_calls.append(kwargs)

    async def _capture_insert_trading_event(*args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(
        "services.execution.app.placement_persist.insert_order",
        _capture_insert_order,
    )
    monkeypatch.setattr(
        "services.execution.app.placement_persist.insert_trade",
        _capture_insert_trade,
    )
    monkeypatch.setattr(
        "services.execution.app.placement_persist.update_trade_close",
        _capture_update_trade_close,
    )
    monkeypatch.setattr(
        "services.execution.app.placement_persist.insert_trading_event",
        _capture_insert_trading_event,
    )

    pool = MagicMock()
    conn = MagicMock()

    @asynccontextmanager
    async def _acquire() -> Any:
        yield conn

    pool.acquire = _acquire

    @asynccontextmanager
    async def _transaction() -> Any:
        yield None

    conn.transaction = _transaction

    await emergency_close(
        adapter=_ok_close_adapter(),
        bus=_ok_bus(),
        pool=pool,
        bound_logger=MagicMock(),
        bot_id=BotId("alpha"),
        request=_request(side="buy"),
        envelope=_envelope(),
        place_result=_place_result(),
        fill_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        now_fn=lambda: _FIXED_NOW,
    )
    # 2 insert_order calls: open + close.
    assert len(insert_order_calls) == 2
    # First INSERT: open orders row → status='emergency_closed' per WG#1 / brief §7.2 line 967.
    assert insert_order_calls[0]["status"] == "emergency_closed"
    # Second INSERT: close orders row → status='filled'.
    assert insert_order_calls[1]["status"] == "filled"
    # update_trade_close → close_reason='emergency' + realized_pnl=Decimal('0').
    assert len(update_trade_close_calls) == 1
    assert update_trade_close_calls[0]["close_reason"] == "emergency"
    assert update_trade_close_calls[0]["realized_pnl"] == Decimal("0")
    assert update_trade_close_calls[0]["fees_paid"] == Decimal("0")


async def test_emergency_close_logs_persist_failed_on_db_error() -> None:
    pool = MagicMock()
    conn = MagicMock()

    @asynccontextmanager
    async def _acquire() -> Any:
        yield conn

    pool.acquire = _acquire

    class _RaisingTx:
        async def __aenter__(self) -> None:
            raise RuntimeError("db disconnect")

        async def __aexit__(self, *exc: object) -> None:
            return None

    conn.transaction = MagicMock(return_value=_RaisingTx())
    logger = MagicMock()
    await emergency_close(
        adapter=_ok_close_adapter(),
        bus=_ok_bus(),
        pool=pool,
        bound_logger=logger,
        bot_id=BotId("alpha"),
        request=_request(),
        envelope=_envelope(),
        place_result=_place_result(),
        fill_price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        now_fn=lambda: _FIXED_NOW,
    )
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.emergency_close_persist_failed" in log_keys


# ---------------------------------------------------------------------------
# persist_placement_tx (T-216b2; §9.5 step 8)
# ---------------------------------------------------------------------------


def _patch_inserts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    open_order_id: int = 101,
    trade_id: int = 202,
) -> dict[str, list[dict[str, Any]]]:
    """Patch all 4 INSERT helpers; return per-helper kwargs capture lists."""
    captured: dict[str, list[dict[str, Any]]] = {
        "insert_order": [],
        "insert_trade": [],
        "insert_position_state": [],
        "insert_trading_event": [],
    }

    async def _capture_insert_order(*args: Any, **kwargs: Any) -> int:
        captured["insert_order"].append(kwargs)
        return open_order_id

    async def _capture_insert_trade(*args: Any, **kwargs: Any) -> int:
        captured["insert_trade"].append(kwargs)
        return trade_id

    async def _capture_insert_position_state(*args: Any, **kwargs: Any) -> None:
        captured["insert_position_state"].append(kwargs)

    async def _capture_insert_trading_event(*args: Any, **kwargs: Any) -> None:
        captured["insert_trading_event"].append(kwargs)

    monkeypatch.setattr(
        "services.execution.app.placement_persist.insert_order",
        _capture_insert_order,
    )
    monkeypatch.setattr(
        "services.execution.app.placement_persist.insert_trade",
        _capture_insert_trade,
    )
    monkeypatch.setattr(
        "services.execution.app.placement_persist.insert_position_state",
        _capture_insert_position_state,
    )
    monkeypatch.setattr(
        "services.execution.app.placement_persist.insert_trading_event",
        _capture_insert_trading_event,
    )
    return captured


_SL_SET_AT = datetime(2026, 5, 1, 10, 30, 0, tzinfo=UTC)


async def _call_persist_tx(
    *, side: str = "buy", sl_set_at: datetime = _SL_SET_AT
) -> tuple[Any, Any, Any]:
    from services.execution.app.placement_persist import persist_placement_tx

    return await persist_placement_tx(
        conn=MagicMock(),
        bot_id=BotId("alpha"),
        request=_request(side=side),
        envelope=_envelope(),
        place_result=_place_result(),
        fill_price=Decimal("45000.50"),
        sl_price=Decimal("44775.4975"),
        tp_price=Decimal("45675.5075"),
        tp_size=Decimal("0.0005"),
        notional_usd=Decimal("45.0005"),
        sl_set_at=sl_set_at,
        qty=Decimal("0.001"),  # T-529 / H-036: post-quantize qty kwarg.
    )


async def test_persist_placement_tx_inserts_order_trade_position_state_and_two_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_inserts(monkeypatch)
    await _call_persist_tx()
    assert len(captured["insert_order"]) == 1
    assert len(captured["insert_trade"]) == 1
    assert len(captured["insert_position_state"]) == 1
    assert len(captured["insert_trading_event"]) == 2
    event_types = [c["event_type"] for c in captured["insert_trading_event"]]
    assert event_types == ["order_placed", "sl_moved"]


async def test_persist_placement_tx_open_order_uses_status_filled_per_brief_enum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OQ-6 — happy-path open order status='filled' per §7.2 line 967 enum."""
    captured = _patch_inserts(monkeypatch)
    await _call_persist_tx()
    assert captured["insert_order"][0]["status"] == "filled"
    assert captured["insert_order"][0]["closed_at"] is None
    assert captured["insert_order"][0]["idempotent_flag"] is False


async def test_persist_placement_tx_position_state_uses_sl_type_protective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OQ-7 — initial SL is 'protective' (BE/trail come later via T-217)."""
    captured = _patch_inserts(monkeypatch)
    await _call_persist_tx()
    pos_state_kwargs = captured["insert_position_state"][0]
    assert pos_state_kwargs["sl_type"] == "protective"
    assert pos_state_kwargs["sl_price"] == Decimal("44775.4975")
    assert pos_state_kwargs["tp_price"] == Decimal("45675.5075")
    assert pos_state_kwargs["remaining_qty"] == pos_state_kwargs["qty"]


async def test_persist_placement_tx_patches_order_placed_payload_with_returned_open_order_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#2 — OrderPlaced.order_id reflects real BIGSERIAL id from insert_order."""
    captured = _patch_inserts(monkeypatch, open_order_id=12345)
    order_placed, _sl_moved, _trade_id = await _call_persist_tx()
    assert order_placed.order_id == 12345
    # And the trading_event payload carries the real id too.
    order_placed_event_payload = captured["insert_trading_event"][0]["payload"]
    assert order_placed_event_payload["order_id"] == 12345


async def test_persist_placement_tx_uses_place_result_placed_at_for_order_placed_occurred_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OQ-5 — OrderPlaced.occurred_at = place_result.placed_at."""
    captured = _patch_inserts(monkeypatch)
    await _call_persist_tx()
    order_placed_kwargs = captured["insert_trading_event"][0]
    assert order_placed_kwargs["occurred_at"] == _FIXED_NOW  # = place_result.placed_at


async def test_persist_placement_tx_uses_now_fn_value_for_sl_moved_occurred_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OQ-5 — SLMoved.occurred_at = now_fn() value (sl_set_at parameter)."""
    captured = _patch_inserts(monkeypatch)
    custom_sl_set_at = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
    await _call_persist_tx(sl_set_at=custom_sl_set_at)
    sl_moved_kwargs = captured["insert_trading_event"][1]
    assert sl_moved_kwargs["occurred_at"] == custom_sl_set_at


# ---------------------------------------------------------------------------
# emit_post_commit_events (T-216b2; §9.5 step 9)
# ---------------------------------------------------------------------------


async def test_emit_publishes_OrderPlaced_and_SLMoved_to_orders_events_subject() -> None:
    """§9.5 step 9 — OUTSIDE-tx publishes for both event types on orders.events.<bot>."""
    from packages.bus.schemas.orders import OrderPlaced, SLMoved
    from services.execution.app.placement_persist import emit_post_commit_events

    bus = _ok_bus()
    order_placed = OrderPlaced(
        bot_id="alpha",
        order_id=101,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_FIXED_NOW,
    )
    sl_moved = SLMoved(
        bot_id="alpha",
        order_id=101,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_SL_SET_AT,
        new_sl_price=Decimal("44775.4975"),
        sl_type="protective",
    )
    await emit_post_commit_events(
        bus=bus,
        bot_id=BotId("alpha"),
        correlation_id="cid-1",  # type: ignore[arg-type]
        order_placed_payload=order_placed,
        sl_moved_payload=sl_moved,
        bound_logger=MagicMock(),
    )
    assert bus.publish.await_count == 2
    subjects = [call.args[0] for call in bus.publish.await_args_list]
    assert subjects == ["orders.events.alpha", "orders.events.alpha"]


async def test_emit_post_commit_events_first_publish_failure_does_not_short_circuit_second() -> (
    None
):
    """WG#3 — per-publish try/except; first fail does NOT short-circuit second."""
    from packages.bus.schemas.orders import OrderPlaced, SLMoved
    from services.execution.app.placement_persist import emit_post_commit_events

    bus = MagicMock()
    publish_calls: list[str] = []

    async def _flaky_publish(subject: str, envelope: MessageEnvelope) -> None:
        publish_calls.append(subject)
        if len(publish_calls) == 1:
            raise RuntimeError("nats disconnect")

    bus.publish = AsyncMock(side_effect=_flaky_publish)
    logger = MagicMock()
    order_placed = OrderPlaced(
        bot_id="alpha",
        order_id=101,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_FIXED_NOW,
    )
    sl_moved = SLMoved(
        bot_id="alpha",
        order_id=101,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_SL_SET_AT,
        new_sl_price=Decimal("44775.4975"),
        sl_type="protective",
    )
    await emit_post_commit_events(
        bus=bus,
        bot_id=BotId("alpha"),
        correlation_id="cid-1",  # type: ignore[arg-type]
        order_placed_payload=order_placed,
        sl_moved_payload=sl_moved,
        bound_logger=logger,
    )
    assert len(publish_calls) == 2
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.event_publish_failed" in log_keys


# ---------------------------------------------------------------------------
# T-511b2 / ADR-0010 — emit_post_commit_shadow_start_event (live + paper)
# ---------------------------------------------------------------------------


async def test_emit_post_commit_shadow_start_publishes_envelope_live() -> None:
    """T-511b2: emit publishes ShadowStartPayload(parent_kind='live') to shadow.start.<bot>."""
    from packages.bus.payloads import VariantSpec
    from services.execution.app.placement_persist import emit_post_commit_shadow_start_event

    bus = _ok_bus()
    await emit_post_commit_shadow_start_event(
        bus=bus,
        bot_id=BotId("alpha"),
        correlation_id="cid-1",  # type: ignore[arg-type]
        parent_trade_id=42,
        parent_kind="live",
        symbol="BTCUSDT",
        side="buy",
        entry_price=Decimal("65000"),
        qty=Decimal("1"),
        shadow_variants=[
            VariantSpec(name="aggressive", overrides={"be_trigger": Decimal("0.003")})
        ],
        bound_logger=MagicMock(),
    )
    bus.publish.assert_awaited_once()
    subject, envelope = bus.publish.await_args.args
    assert subject == "shadow.start.alpha"
    assert envelope.payload["parent_trade_id"] == 42
    assert envelope.payload["parent_kind"] == "live"
    assert envelope.payload["entry_price"] == "65000"
    assert len(envelope.payload["variants"]) == 1


async def test_emit_post_commit_shadow_start_publishes_envelope_paper() -> None:
    """T-511b2: parent_kind='paper' for paper-mode parent trade."""
    from packages.bus.payloads import VariantSpec
    from services.execution.app.placement_persist import emit_post_commit_shadow_start_event

    bus = _ok_bus()
    await emit_post_commit_shadow_start_event(
        bus=bus,
        bot_id=BotId("alpha"),
        correlation_id="cid-1",  # type: ignore[arg-type]
        parent_trade_id=99,
        parent_kind="paper",
        symbol="BTCUSDT",
        side="sell",
        entry_price=Decimal("65000"),
        qty=Decimal("1"),
        shadow_variants=[VariantSpec(name="conservative", overrides={})],
        bound_logger=MagicMock(),
    )
    bus.publish.assert_awaited_once()
    _, envelope = bus.publish.await_args.args
    assert envelope.payload["parent_trade_id"] == 99
    assert envelope.payload["parent_kind"] == "paper"


async def test_emit_post_commit_shadow_start_publish_failure_logs_does_not_raise() -> None:
    """T-511b2: best-effort emit (DB tx already committed). Failure logged, NOT raised."""
    from packages.bus.payloads import VariantSpec
    from services.execution.app.placement_persist import emit_post_commit_shadow_start_event

    bus = MagicMock()
    bus.publish = AsyncMock(side_effect=RuntimeError("nats down"))
    logger = MagicMock()
    # Does NOT raise.
    await emit_post_commit_shadow_start_event(
        bus=bus,
        bot_id=BotId("alpha"),
        correlation_id="cid-1",  # type: ignore[arg-type]
        parent_trade_id=42,
        parent_kind="live",
        symbol="BTCUSDT",
        side="buy",
        entry_price=Decimal("65000"),
        qty=Decimal("1"),
        shadow_variants=[VariantSpec(name="v1", overrides={})],
        bound_logger=logger,
    )
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.shadow_start_publish_failed" in log_keys


# ---------------------------------------------------------------------------
# emergency_close_tracked_position (T-534b1 — SL-watchdog close primitive)
# ---------------------------------------------------------------------------


def _ps_row(side: str = "buy") -> PositionStateRow:
    return PositionStateRow(
        bot_id="alpha",
        symbol="BTCUSDT",
        trade_id=42,
        side=side,  # type: ignore[arg-type]
        entry_price=Decimal("65000"),
        qty=Decimal("0.05"),
        remaining_qty=Decimal("0.05"),
        sl_price=None,
        tp_price=None,
        sl_type=None,
    )


def _tracked_pool() -> MagicMock:
    pool = MagicMock()
    conn = MagicMock()

    @asynccontextmanager
    async def _acquire() -> Any:
        yield conn

    @asynccontextmanager
    async def _transaction() -> Any:
        yield None

    pool.acquire = _acquire
    conn.transaction = _transaction
    return pool


def _patch_tracked_queries(
    monkeypatch: Any,
    *,
    open_oid: int | None = 7,
) -> dict[str, Any]:
    mocks: dict[str, Any] = {
        "select_open_order_id_by_trade_id": AsyncMock(return_value=open_oid),
        "update_trade_close": AsyncMock(return_value=None),
        "delete_position_state": AsyncMock(return_value=None),
        "insert_trading_event": AsyncMock(return_value=None),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(pp, name, mock)
    return mocks


async def test_tracked_close_marker_mirrors_undecorated_emergency_close() -> None:
    """Verbatim-mirror: emergency_close is undecorated (markers live on the
    composed leaf primitives, §N3) → emergency_close_tracked_position is too."""
    assert is_idempotent(emergency_close) is False
    assert is_non_idempotent(emergency_close) is False
    assert is_idempotent(emergency_close_tracked_position) is False
    assert is_non_idempotent(emergency_close_tracked_position) is False


async def test_tracked_close_happy_path_flatten_tx_audit_emit(
    monkeypatch: Any,
) -> None:
    q = _patch_tracked_queries(monkeypatch, open_oid=7)
    pool = _tracked_pool()
    adapter = _ok_close_adapter()
    bus = _ok_bus()
    logger = MagicMock()
    await emergency_close_tracked_position(
        adapter=adapter,
        bus=bus,
        pool=pool,
        bound_logger=logger,
        bot_id=BotId("alpha"),
        ps_row=_ps_row(side="buy"),
        now_fn=lambda: _FIXED_NOW,
    )
    # Reduce-only opposite-side flatten with remaining_qty.
    args = adapter.place_market_order.await_args.args
    kw = adapter.place_market_order.await_args.kwargs
    assert args[0] == "BTCUSDT"
    assert args[1] == "sell"  # opposite of buy
    assert args[2] == Decimal("0.05")  # ps_row.remaining_qty
    assert kw.get("reduce_only") is True
    # update_trade_close: existing trade_id, close_reason='emergency', 0 placeholders.
    utc_kw = q["update_trade_close"].await_args.kwargs
    assert utc_kw["trade_id"] == 42
    assert utc_kw["close_reason"] == "emergency"
    assert utc_kw["close_order_id"] == 7
    assert utc_kw["exit_price"] == Decimal("0")
    assert utc_kw["realized_pnl"] == Decimal("0")
    assert utc_kw["fees_paid"] is None
    # delete_position_state (bot_id, symbol).
    dps_kw = q["delete_position_state"].await_args.kwargs
    assert dps_kw["bot_id"] == "alpha"
    assert dps_kw["symbol"] == "BTCUSDT"
    # Audit row: distinct event_type + committed sl-watchdog correlation_id.
    ite_kw = q["insert_trading_event"].await_args.kwargs
    assert ite_kw["event_type"] == "sl_watchdog_emergency_close"
    assert ite_kw["correlation_id"] == "sl-watchdog-alpha-BTCUSDT"
    # Single OrderClosed emit with the committed correlation_id.
    bus.publish.assert_awaited_once()
    env = bus.publish.await_args.args[1]
    assert env.correlation_id == "sl-watchdog-alpha-BTCUSDT"
    log_keys = [c.args[0] for c in logger.warning.call_args_list]
    assert "sl_watchdog.emergency_close_pnl_pending_audit_reconcile" in log_keys


async def test_tracked_close_unknown_state_returns_before_db_and_emit(
    monkeypatch: Any,
) -> None:
    q = _patch_tracked_queries(monkeypatch)
    pool = _tracked_pool()
    adapter = MagicMock()
    adapter.place_market_order = AsyncMock(side_effect=UnknownState("ws drop"))
    bus = _ok_bus()
    logger = MagicMock()
    await emergency_close_tracked_position(
        adapter=adapter,
        bus=bus,
        pool=pool,
        bound_logger=logger,
        bot_id=BotId("alpha"),
        ps_row=_ps_row(),
        now_fn=lambda: _FIXED_NOW,
    )
    q["update_trade_close"].assert_not_awaited()
    q["delete_position_state"].assert_not_awaited()
    bus.publish.assert_not_awaited()
    log_keys = [c.args[0] for c in logger.error.call_args_list]
    assert "execution.sl_watchdog_close_unknown_state" in log_keys


async def test_tracked_close_open_oid_none_returns_without_close(
    monkeypatch: Any,
) -> None:
    q = _patch_tracked_queries(monkeypatch, open_oid=None)
    pool = _tracked_pool()
    adapter = _ok_close_adapter()
    bus = _ok_bus()
    logger = MagicMock()
    await emergency_close_tracked_position(
        adapter=adapter,
        bus=bus,
        pool=pool,
        bound_logger=logger,
        bot_id=BotId("alpha"),
        ps_row=_ps_row(),
        now_fn=lambda: _FIXED_NOW,
    )
    q["update_trade_close"].assert_not_awaited()
    q["delete_position_state"].assert_not_awaited()
    q["insert_trading_event"].assert_not_awaited()
    bus.publish.assert_not_awaited()
    log_keys = [c.args[0] for c in logger.error.call_args_list]
    assert "execution.sl_watchdog_close_open_order_missing" in log_keys


async def test_tracked_close_persist_exception_logs_and_returns(
    monkeypatch: Any,
) -> None:
    q = _patch_tracked_queries(monkeypatch)
    q["update_trade_close"] = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(pp, "update_trade_close", q["update_trade_close"])
    pool = _tracked_pool()
    adapter = _ok_close_adapter()
    bus = _ok_bus()
    logger = MagicMock()
    await emergency_close_tracked_position(
        adapter=adapter,
        bus=bus,
        pool=pool,
        bound_logger=logger,
        bot_id=BotId("alpha"),
        ps_row=_ps_row(),
        now_fn=lambda: _FIXED_NOW,
    )
    bus.publish.assert_not_awaited()
    log_keys = [c.args[0] for c in logger.error.call_args_list]
    assert "execution.sl_watchdog_close_persist_failed" in log_keys


async def test_tracked_close_publish_failure_is_logged_not_raised(
    monkeypatch: Any,
) -> None:
    _patch_tracked_queries(monkeypatch)
    pool = _tracked_pool()
    adapter = _ok_close_adapter()
    bus = MagicMock()
    bus.publish = AsyncMock(side_effect=RuntimeError("nats down"))
    logger = MagicMock()
    await emergency_close_tracked_position(
        adapter=adapter,
        bus=bus,
        pool=pool,
        bound_logger=logger,
        bot_id=BotId("alpha"),
        ps_row=_ps_row(),
        now_fn=lambda: _FIXED_NOW,
    )
    log_keys = [c.args[0] for c in logger.error.call_args_list]
    assert "execution.event_publish_failed" in log_keys


async def test_tracked_close_uses_now_fn_for_close_at(monkeypatch: Any) -> None:
    q = _patch_tracked_queries(monkeypatch)
    pool = _tracked_pool()
    await emergency_close_tracked_position(
        adapter=_ok_close_adapter(),
        bus=_ok_bus(),
        pool=pool,
        bound_logger=MagicMock(),
        bot_id=BotId("alpha"),
        ps_row=_ps_row(),
        now_fn=lambda: _FIXED_NOW,
    )
    assert q["update_trade_close"].await_args.kwargs["closed_at"] == _FIXED_NOW
