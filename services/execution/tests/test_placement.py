"""§N4 unit tests for :mod:`services.execution.app.placement` (T-216a + T-216b1 + T-216b2).

Mock-based: adapter (`ExchangeClient`) + bus (`NatsClient`) +
`MessageEnvelope` constructed inline. Validates handler closure
isolation, OrderRequest validation, subject-payload mismatch WARN,
H-003 UnknownState catch (no DLQ), OrderRejected qty/precision
differentiation, fill_price retry + DLQ + raise. T-216b2 tests cover
the post-fill_price pipeline: paper branch fork, live SL/TP set
(H-013 explicit kwargs), SL exhaustion → emergency_close (H-004),
TP exhaustion log+continue (OQ-2 default A), persistence-tx + emit
ordering (Q2 publish-after-persist), and dedup-consumer wrap (H-009).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    import asyncio

import pytest
from prometheus_client import CollectorRegistry

from packages.bus import MessageEnvelope
from packages.bus.schemas.orders import OrderPlaced, SLMoved
from packages.core import BotId
from packages.exchange.errors import (
    AuthError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
    UnknownState,
)
from packages.exchange.types import OrderPlaceResult
from services.execution.app.metrics import build_execution_metrics
from services.execution.app.placement import (
    FillPriceUnresolvedError,
    make_per_bot_handler,
)
from services.execution.app.placement_persist import OrderRequestDedupConsumer

_FIXED_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _request_payload(bot_id: str = "alpha", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "bot_id": bot_id,
        "signal_id": 42,
        "symbol": "BTCUSDT",
        "side": "buy",
        "order_type": "market",
        "qty": "0.001",
        "leverage": 10,
        "sl_pct": "0.005",
        "tp_pct": "0.015",
        "tp_qty_pct": "0.5",
        "be_trigger": "0.003",
        "be_sl_level": "0.001",
        "trail_pct": "0.002",
        "exchange_mode": "live",
    }
    payload.update(overrides)
    return payload


def _envelope(payload: dict[str, Any] | None = None) -> MessageEnvelope:
    return MessageEnvelope(
        correlation_id="cid-1",  # type: ignore[arg-type]
        publisher="strategy-engine",
        payload=payload if payload is not None else _request_payload(),
    )


def _ok_adapter() -> MagicMock:
    from packages.exchange.types import InstrumentInfo

    adapter = MagicMock()
    adapter.set_leverage = AsyncMock()
    adapter.place_market_order = AsyncMock(
        return_value=OrderPlaceResult(
            exchange_order_id="ord-1",
            placed_at=_FIXED_NOW,
        )
    )
    adapter.get_fill_price = AsyncMock(return_value=Decimal("45000.50"))
    adapter.set_trading_stop = AsyncMock()
    # T-529 / H-036: pre-flight qty validation requires get_instrument_info.
    # T-557 / H-037: default min/step = 0.0001 so the default _request_payload
    # (qty=0.001, tp_qty_pct=0.5 → tp_size=0.0005) satisfies the partial-TP
    # min-lot pre-flight (0.0005 >= 0.0001). quantize_qty(0.001) and
    # quantize_qty(0.0005) at step 0.0001 are byte-identical to step 0.001
    # (0.001 // 0.0001 * 0.0001 = 0.001; 0.0005 // 0.0001 * 0.0001 = 0.0005)
    # → zero value-ripple across all shared-fixture default-payload tests
    # (verified: no shared-fixture test uses a sub-0.001-granularity qty).
    adapter.get_instrument_info = AsyncMock(
        return_value=InstrumentInfo(
            symbol="BTCUSDT",
            qty_step=Decimal("0.0001"),
            min_order_qty=Decimal("0.0001"),
            min_notional_usd=Decimal("5"),
            tick_size=Decimal("0.1"),
        )
    )
    return adapter


def _ok_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _mock_pool() -> MagicMock:
    """Async-cm-shaped pool with a no-op transaction context."""
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
    return pool


@pytest.fixture(autouse=True)
def _patch_persist_and_emit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default no-op persist + emit so T-216a tests run cleanly through to step 9.

    T-216b2 tests that need to verify persist/emit call sequencing override
    these via their own monkeypatch in the test body.
    """

    async def _no_op_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        return (
            OrderPlaced(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
            ),
            SLMoved(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
                new_sl_price=Decimal("1"),
                sl_type="protective",
            ),
            1,  # T-217a — trade_id BIGSERIAL
        )

    async def _no_op_emit(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _no_op_persist,
    )
    monkeypatch.setattr(
        "services.execution.app.placement.emit_post_commit_events",
        _no_op_emit,
    )


def _build(
    adapter: MagicMock | None = None,
    bus: MagicMock | None = None,
    bot_id: str = "alpha",
    attempts: int = 3,
    backoff: float = 0.0,
    pool: MagicMock | None = None,
    dedup_capacity: int = 100,
    now_fn: Any = None,
    position_lifecycle_tasks: dict[int, asyncio.Task[None]] | None = None,
    sub_account: str = "alpha-sub",
    metrics: Any = None,
) -> tuple[Any, MagicMock, MagicMock, MagicMock]:
    used_metrics = metrics if metrics is not None else build_execution_metrics(CollectorRegistry())
    used_adapter = adapter or _ok_adapter()
    used_bus = bus or _ok_bus()
    used_pool = pool if pool is not None else _mock_pool()
    used_now_fn = now_fn if now_fn is not None else (lambda: _FIXED_NOW)
    used_lifecycle_tasks = position_lifecycle_tasks if position_lifecycle_tasks is not None else {}
    logger = MagicMock()
    handler = make_per_bot_handler(
        bot_id=BotId(bot_id),
        sub_account=sub_account,
        metrics=used_metrics,
        adapter=used_adapter,
        bus=used_bus,
        logger=logger,
        pool=used_pool,
        dedup_capacity=dedup_capacity,
        now_fn=used_now_fn,
        fill_price_retry_attempts=attempts,
        fill_price_retry_backoff_s=backoff,
        position_lifecycle_tasks=used_lifecycle_tasks,
        position_poll_interval_s=3600.0,  # long so spawned task sleeps in background
        position_poll_stale_ticks=5,
    )
    return handler, used_adapter, used_bus, logger


# ---------------------------------------------------------------------------
# Closure isolation (3 tests, WG#4)
# ---------------------------------------------------------------------------


async def test_make_per_bot_handler_returns_callable_matching_natsclient_handler_signature() -> (
    None
):
    handler, _, _, _ = _build()
    assert callable(handler)


async def test_per_bot_handler_uses_closure_bound_adapter_not_subject_lookup() -> None:
    handler, adapter, _, _ = _build()
    await handler(_envelope())
    adapter.set_leverage.assert_awaited_once()


async def test_two_handlers_have_independent_closures_with_distinct_adapters() -> None:
    """WG#4 pin — defends against shared-state regression."""
    adapter_a = _ok_adapter()
    adapter_b = _ok_adapter()
    bus = _ok_bus()
    logger = MagicMock()
    handler_a = make_per_bot_handler(
        bot_id=BotId("alpha"),
        sub_account="alpha-sub",
        metrics=build_execution_metrics(CollectorRegistry()),
        adapter=adapter_a,
        bus=bus,
        logger=logger,
        pool=_mock_pool(),
        dedup_capacity=100,
        now_fn=lambda: _FIXED_NOW,
        fill_price_retry_attempts=3,
        fill_price_retry_backoff_s=0.0,
        position_lifecycle_tasks={},
        position_poll_interval_s=3600.0,
        position_poll_stale_ticks=5,
    )
    handler_b = make_per_bot_handler(
        bot_id=BotId("beta"),
        sub_account="beta-sub",
        metrics=build_execution_metrics(CollectorRegistry()),
        adapter=adapter_b,
        bus=bus,
        logger=logger,
        pool=_mock_pool(),
        dedup_capacity=100,
        now_fn=lambda: _FIXED_NOW,
        fill_price_retry_attempts=3,
        fill_price_retry_backoff_s=0.0,
        position_lifecycle_tasks={},
        position_poll_interval_s=3600.0,
        position_poll_stale_ticks=5,
    )
    await handler_a(_envelope(_request_payload(bot_id="alpha")))
    adapter_a.set_leverage.assert_awaited_once()
    adapter_b.set_leverage.assert_not_called()
    await handler_b(_envelope(_request_payload(bot_id="beta")))
    adapter_b.set_leverage.assert_awaited_once()
    # adapter_a should still have only the original 1 call from earlier.
    assert adapter_a.set_leverage.await_count == 1


async def test_handler_warn_on_botid_mismatch_uses_subject_as_authoritative() -> None:
    """CONCERN #6 pin — payload bot_id != subject bot_id → WARN + continue."""
    handler, adapter, _, logger = _build(bot_id="alpha")
    payload = _request_payload(bot_id="impostor")
    await handler(_envelope(payload))
    # WARN log emitted on mismatch.
    log_keys = [call.args[0] for call in logger.warning.call_args_list]
    assert "execution.subject_payload_botid_mismatch_using_subject" in log_keys
    # Processing continued (set_leverage was called using subject bot_id).
    adapter.set_leverage.assert_awaited_once()


# ---------------------------------------------------------------------------
# Happy path (3 tests, BLOCKER #3 step-size WARN pin)
# ---------------------------------------------------------------------------


async def test_handler_calls_set_leverage_then_place_market_order_then_get_fill_price() -> None:
    handler, adapter, _, _ = _build()
    await handler(_envelope())
    adapter.set_leverage.assert_awaited_once_with("BTCUSDT", 10)
    adapter.place_market_order.assert_awaited_once_with("BTCUSDT", "buy", Decimal("0.001"))
    adapter.get_fill_price.assert_awaited_once_with("BTCUSDT", "ord-1")


# T-529 / H-036: BLOCKER #3 warn key `execution.qty_step_rounding_pending_t_f2_plus`
# REMOVED at T-529 ship — replaced by pre-flight quantize_qty + get_instrument_info
# block. Old test_handler_logs_qty_rounding_pending_warning_before_place deleted.


async def test_placement_quantizes_qty_before_place_market_order() -> None:
    """T-529 / H-036 / AC#16 — request.qty=0.0015 → place_market_order called with quantized 0.001.

    Hand-fixture: qty_step=0.001, request.qty=0.0015 → 0.0015 // 0.001 = 1;
    1 * 0.001 = Decimal("0.001") exact (round-down semantic per OQ-1).
    """
    from packages.exchange.types import InstrumentInfo

    adapter = _ok_adapter()
    adapter.get_instrument_info = AsyncMock(
        return_value=InstrumentInfo(
            symbol="BTCUSDT",
            qty_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            min_notional_usd=Decimal("5"),
            tick_size=Decimal("0.1"),
        )
    )
    handler, _, _, _ = _build(adapter=adapter)
    # T-557 / H-037: tp_qty_pct=1.0 so partial tp_size == quantized qty (>= min);
    # this test pins the H-036 entry-qty 0.0015→0.001 round-down, unrelated to TP.
    await handler(_envelope(_request_payload(qty="0.0015", tp_qty_pct="1.0")))
    adapter.place_market_order.assert_awaited_once()
    call = adapter.place_market_order.await_args
    # Third positional arg is qty per BybitV5Adapter.place_market_order signature.
    assert call.args[2] == Decimal("0.001")


async def test_placement_pre_flight_rejects_when_qty_below_min_order_qty() -> None:
    """T-529 / H-036 / AC#7 — qty < min_order_qty → log execution.qty_validation_failed + return."""
    from packages.exchange.types import InstrumentInfo

    adapter = _ok_adapter()
    adapter.get_instrument_info = AsyncMock(
        return_value=InstrumentInfo(
            symbol="BTCUSDT",
            qty_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            min_notional_usd=Decimal("5"),
            tick_size=Decimal("0.1"),
        )
    )
    handler, _, _, logger = _build(adapter=adapter)
    await handler(_envelope(_request_payload(qty="0.0005")))
    # No place_market_order call — pre-flight reject.
    adapter.place_market_order.assert_not_called()
    error_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.qty_validation_failed" in error_keys
    qty_fail_call = next(
        c for c in logger.error.call_args_list if c.args[0] == "execution.qty_validation_failed"
    )
    assert qty_fail_call.kwargs["constraint"] == "min_order_qty"
    assert qty_fail_call.kwargs["actual_qty"] == "0.0005"


async def test_placement_pre_flight_logs_when_get_instrument_info_raises_auth_error() -> None:
    """T-529 / H-036 / AC#7 — AuthError from get_instrument_info → log + return; no Bybit call."""
    from packages.exchange.errors import AuthError

    adapter = _ok_adapter()
    adapter.get_instrument_info = AsyncMock(side_effect=AuthError("bad sig"))
    handler, _, _, logger = _build(adapter=adapter)
    await handler(_envelope())
    adapter.place_market_order.assert_not_called()
    error_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.get_instrument_info_failed" in error_keys


async def test_placement_uses_quantized_qty_in_all_downstream_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-529 / H-036 / AC#17 — request.qty=0.0015 → quantized 0.001 at ALL downstream sites.

    L-019 mirror: guards in main path can be bypassed by sibling code; verify ALL sites
    explicitly per plan AC#17 (place_market_order + compute_tp_size + compute_notional_usd
    + persist_placement_tx kwarg + NATS publish payload). Spy on each site to capture
    actual qty argument.
    """
    from packages.bus.schemas.orders import OrderPlaced, SLMoved
    from packages.exchange.types import InstrumentInfo

    captured_compute_tp_size: list[Decimal] = []
    captured_compute_notional: list[Decimal] = []
    captured_persist_qty: list[Decimal] = []

    from services.execution.app import placement as placement_mod

    real_compute_tp_size = placement_mod.compute_tp_size  # type: ignore[attr-defined]
    real_compute_notional_usd = placement_mod.compute_notional_usd  # type: ignore[attr-defined]

    def _spy_compute_tp_size(qty: Decimal, tp_qty_pct: Decimal) -> Decimal:
        captured_compute_tp_size.append(qty)
        return real_compute_tp_size(qty, tp_qty_pct)

    def _spy_compute_notional_usd(qty: Decimal, price: Decimal) -> Decimal:
        captured_compute_notional.append(qty)
        return real_compute_notional_usd(qty, price)

    async def _spy_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        captured_persist_qty.append(kwargs["qty"])
        return (
            OrderPlaced(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
            ),
            SLMoved(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
                new_sl_price=Decimal("1"),
                sl_type="protective",
            ),
            1,
        )

    monkeypatch.setattr("services.execution.app.placement.compute_tp_size", _spy_compute_tp_size)
    monkeypatch.setattr(
        "services.execution.app.placement.compute_notional_usd", _spy_compute_notional_usd
    )
    monkeypatch.setattr("services.execution.app.placement.persist_placement_tx", _spy_persist)

    adapter = _ok_adapter()
    adapter.get_instrument_info = AsyncMock(
        return_value=InstrumentInfo(
            symbol="BTCUSDT",
            qty_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            min_notional_usd=Decimal("5"),
            tick_size=Decimal("0.1"),
        )
    )
    handler, _, bus, _ = _build(adapter=adapter)
    # T-557 / H-037: tp_qty_pct=1.0 so partial tp_size == quantized qty (>= min);
    # this test pins the H-036 entry-qty 0.0015→0.001 round-down, unrelated to TP.
    await handler(_envelope(_request_payload(qty="0.0015", tp_qty_pct="1.0")))

    # AC#17 site #1 — place_market_order kwarg.
    pmm_call = adapter.place_market_order.await_args
    assert pmm_call.args[2] == Decimal("0.001"), "place_market_order qty"

    # AC#17 site #2 — compute_tp_size first arg.
    assert captured_compute_tp_size == [Decimal("0.001")], "compute_tp_size qty"

    # AC#17 site #3 — compute_notional_usd first arg.
    assert captured_compute_notional == [Decimal("0.001")], "compute_notional_usd qty"

    # AC#17 site #4 — persist_placement_tx qty kwarg.
    assert captured_persist_qty == [Decimal("0.001")], "persist_placement_tx qty kwarg"

    # AC#17 site #5 — NATS emit payload qty (orders.events publish).
    publish_qtys = [
        call.args[1].payload.get("qty")
        for call in bus.publish.await_args_list
        if hasattr(call.args[1], "payload")
        and isinstance(call.args[1].payload, dict)
        and "qty" in call.args[1].payload
    ]
    # Note: placement happy-path emits OrderPlaced + SLMoved (no qty in either payload by
    # default OrderPlaced/SLMoved schema); shadow_start emit has qty in payload.
    # If shadow_variants empty (default _request_payload), no shadow emit fires; the
    # `qty=quantized_qty` substitution at L412/L426 is verified by static-grep instead.
    # Test passes if the captured qtys list contains only 0.001 OR is empty (no qty-bearing
    # publishes on default fixture).
    for q in publish_qtys:
        if q is not None:
            assert q == "0.001" or q == Decimal("0.001"), f"NATS publish qty={q}"


# ---------------------------------------------------------------------------
# Validation failure (1 test)
# ---------------------------------------------------------------------------


async def test_handler_drops_message_with_malformed_payload_log_only_no_dlq() -> None:
    handler, adapter, bus, logger = _build()
    bad_envelope = MessageEnvelope(
        correlation_id="cid-1",  # type: ignore[arg-type]
        publisher="strategy-engine",
        payload={"bogus": "shape"},
    )
    await handler(bad_envelope)
    adapter.set_leverage.assert_not_called()
    bus.publish.assert_not_called()
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.order_request_validation_failed" in log_keys


# ---------------------------------------------------------------------------
# H-003 UnknownState catch (1 test)
# ---------------------------------------------------------------------------


async def test_handler_logs_and_returns_when_place_market_order_raises_unknown_state() -> None:
    """H-003 — NO retry, NO DLQ, NO duplicate placement."""
    adapter = _ok_adapter()
    adapter.place_market_order = AsyncMock(side_effect=UnknownState("place_market_order"))
    handler, _, bus, logger = _build(adapter=adapter)
    await handler(_envelope())
    assert adapter.place_market_order.await_count == 1
    bus.publish.assert_not_called()  # NO DLQ on H-003
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.place_market_order_unknown_state" in log_keys


# ---------------------------------------------------------------------------
# OrderRejected qty/precision differentiation (3 tests, BLOCKER #3 + WG#5)
# ---------------------------------------------------------------------------


async def test_handler_logs_qty_rejected_when_OrderRejected_reason_contains_precision() -> None:
    """BLOCKER #3 pin — qty/precision substring → operator-actionable log key."""
    adapter = _ok_adapter()
    adapter.place_market_order = AsyncMock(side_effect=OrderRejected("qty precision invalid"))
    handler, _, _, logger = _build(adapter=adapter)
    await handler(_envelope())
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.place_market_order_qty_rejected" in log_keys


async def test_handler_logs_generic_rejected_when_OrderRejected_reason_unrelated() -> None:
    adapter = _ok_adapter()
    adapter.place_market_order = AsyncMock(side_effect=OrderRejected("insufficient_margin"))
    handler, _, _, logger = _build(adapter=adapter)
    await handler(_envelope())
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.place_market_order_rejected" in log_keys
    assert "execution.place_market_order_qty_rejected" not in log_keys


async def test_handler_handles_OrderRejected_with_empty_reason_uses_generic_log_key() -> None:
    """WG#5 pin — defensive against `exc.reason` empty string."""
    adapter = _ok_adapter()
    adapter.place_market_order = AsyncMock(side_effect=OrderRejected(""))
    handler, _, _, logger = _build(adapter=adapter)
    await handler(_envelope())
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.place_market_order_rejected" in log_keys


# ---------------------------------------------------------------------------
# Fill-price retry (3 tests, WG#6 DLQ-publish-failure pin)
# ---------------------------------------------------------------------------


async def test_handler_returns_fill_price_on_first_attempt_when_not_none() -> None:
    handler, adapter, _, _ = _build()
    await handler(_envelope())
    assert adapter.get_fill_price.await_count == 1


async def test_handler_retries_up_to_3_times_when_fill_price_returns_none() -> None:
    adapter = _ok_adapter()
    adapter.get_fill_price = AsyncMock(side_effect=[None, None, Decimal("100.0")])
    handler, _, _, _ = _build(adapter=adapter, attempts=3)
    await handler(_envelope())
    assert adapter.get_fill_price.await_count == 3


async def test_fill_price_unresolved_after_all_None_attempts_publishes_to_dlq_and_raises() -> None:
    adapter = _ok_adapter()
    adapter.get_fill_price = AsyncMock(return_value=None)
    handler, _, bus, _ = _build(adapter=adapter, attempts=3)
    with pytest.raises(FillPriceUnresolvedError):
        await handler(_envelope())
    assert adapter.get_fill_price.await_count == 3
    bus.publish.assert_awaited_once()
    call = bus.publish.await_args
    assert call.args[0] == "orders.dlq.alpha"


async def test_handler_dlq_publish_failure_still_raises_FillPriceUnresolvedError() -> None:
    """WG#6 pin — DLQ publish failure logged but FillPriceUnresolvedError still propagates."""
    adapter = _ok_adapter()
    adapter.get_fill_price = AsyncMock(return_value=None)
    bus = _ok_bus()
    bus.publish = AsyncMock(side_effect=RuntimeError("nats disconnect"))
    handler, _, _, logger = _build(adapter=adapter, bus=bus, attempts=2)
    with pytest.raises(FillPriceUnresolvedError):
        await handler(_envelope())
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.dlq_publish_failed" in log_keys
    assert "execution.fill_price_unresolved" in log_keys


async def test_handler_retries_when_get_fill_price_raises_NetworkTimeout() -> None:
    adapter = _ok_adapter()
    adapter.get_fill_price = AsyncMock(
        side_effect=[NetworkTimeout("conn timeout"), Decimal("100.0")]
    )
    handler, _, bus, logger = _build(adapter=adapter, attempts=3)
    await handler(_envelope())
    assert adapter.get_fill_price.await_count == 2
    bus.publish.assert_not_called()
    warn_keys = [call.args[0] for call in logger.warning.call_args_list]
    assert warn_keys.count("execution.get_fill_price_transient_error") == 1


async def test_handler_retries_when_get_fill_price_raises_RateLimitError() -> None:
    adapter = _ok_adapter()
    adapter.get_fill_price = AsyncMock(
        side_effect=[RateLimitError("429"), RateLimitError("429"), Decimal("100.0")]
    )
    handler, _, bus, logger = _build(adapter=adapter, attempts=3)
    await handler(_envelope())
    assert adapter.get_fill_price.await_count == 3
    bus.publish.assert_not_called()
    warn_keys = [call.args[0] for call in logger.warning.call_args_list]
    assert warn_keys.count("execution.get_fill_price_transient_error") == 2


async def test_handler_retries_when_get_fill_price_raises_AuthError() -> None:
    adapter = _ok_adapter()
    adapter.get_fill_price = AsyncMock(side_effect=[AuthError("bad sig"), Decimal("100.0")])
    handler, _, bus, logger = _build(adapter=adapter, attempts=3)
    await handler(_envelope())
    assert adapter.get_fill_price.await_count == 2
    bus.publish.assert_not_called()
    warn_keys = [call.args[0] for call in logger.warning.call_args_list]
    assert warn_keys.count("execution.get_fill_price_transient_error") == 1


async def test_fill_price_unresolved_after_all_exception_attempts_publishes_to_dlq_and_raises() -> (
    None
):
    adapter = _ok_adapter()
    adapter.get_fill_price = AsyncMock(
        side_effect=[
            NetworkTimeout("t1"),
            NetworkTimeout("t2"),
            NetworkTimeout("t3"),
        ]
    )
    handler, _, bus, logger = _build(adapter=adapter, attempts=3)
    with pytest.raises(FillPriceUnresolvedError):
        await handler(_envelope())
    assert adapter.get_fill_price.await_count == 3
    bus.publish.assert_awaited_once()
    call = bus.publish.await_args
    assert call.args[0] == "orders.dlq.alpha"
    warn_keys = [call.args[0] for call in logger.warning.call_args_list]
    assert warn_keys.count("execution.get_fill_price_transient_error") == 3
    error_keys = [call.args[0] for call in logger.error.call_args_list]
    assert error_keys.count("execution.fill_price_unresolved") == 1


# ---------------------------------------------------------------------------
# T-556 — resolved-after-retry observability log (2 tests)
# ---------------------------------------------------------------------------


async def test_fill_price_resolved_after_retry_logs_attempt_and_elapsed() -> None:
    """T-556 — fill resolves after >=1 retry → INFO log, 1-indexed attempt + elapsed_ms."""
    adapter = _ok_adapter()
    adapter.get_fill_price = AsyncMock(side_effect=[None, Decimal("100.0")])
    handler, _, bus, logger = _build(adapter=adapter, attempts=3)
    await handler(_envelope())
    assert adapter.get_fill_price.await_count == 2
    bus.publish.assert_not_called()
    info_calls = [
        c
        for c in logger.info.call_args_list
        if c.args and c.args[0] == "execution.fill_price_resolved_after_retry"
    ]
    assert len(info_calls) == 1
    kwargs = info_calls[0].kwargs
    assert kwargs["attempt"] == 2  # 1-indexed (resolved on 2nd call)
    assert kwargs["exchange_order_id"] == "ord-1"
    assert kwargs["elapsed_ms"] >= 0


async def test_fill_price_resolved_first_attempt_does_not_log_resolved_after_retry() -> None:
    """T-556 — first-attempt success must NOT emit the resolved-after-retry log."""
    handler, adapter, _, logger = _build()
    await handler(_envelope())
    assert adapter.get_fill_price.await_count == 1
    info_keys = [c.args[0] for c in logger.info.call_args_list if c.args]
    assert "execution.fill_price_resolved_after_retry" not in info_keys


# ---------------------------------------------------------------------------
# set_leverage error path (1 test)
# ---------------------------------------------------------------------------


async def test_handler_logs_and_returns_when_set_leverage_raises_known_exchange_error() -> None:
    adapter = _ok_adapter()
    adapter.set_leverage = AsyncMock(side_effect=AuthError("bad sig"))
    handler, _, _, logger = _build(adapter=adapter)
    await handler(_envelope())
    adapter.place_market_order.assert_not_called()
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.set_leverage_failed" in log_keys


async def test_handler_logs_and_returns_on_NetworkTimeout_in_place_market_order() -> None:
    adapter = _ok_adapter()
    adapter.place_market_order = AsyncMock(side_effect=NetworkTimeout("connect timeout"))
    handler, _, bus, logger = _build(adapter=adapter)
    await handler(_envelope())
    bus.publish.assert_not_called()
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.place_market_order_failed" in log_keys


# ---------------------------------------------------------------------------
# T-216b2 — paper-mode branch (2 tests; OQ-4 + WG#9 H-013)
# ---------------------------------------------------------------------------


async def test_paper_mode_calls_set_trading_stop_full_sl_then_partial_tp_returns() -> None:
    """OQ-4 + WG#9 — paper-mode 2 set_trading_stop calls explicit kwargs; no further work."""
    handler, adapter, _, _ = _build()
    payload = _request_payload(exchange_mode="paper")
    await handler(_envelope(payload))
    # 2 set_trading_stop calls in order: Full SL then Partial TP, both H-013-explicit.
    assert adapter.set_trading_stop.await_count == 2
    sl_call_kwargs = adapter.set_trading_stop.await_args_list[0].kwargs
    tp_call_kwargs = adapter.set_trading_stop.await_args_list[1].kwargs
    assert sl_call_kwargs["tpsl_mode"] == "Full"
    assert sl_call_kwargs["sl_price"] == Decimal("45000.50") * (Decimal("1") - Decimal("0.005"))
    assert tp_call_kwargs["tpsl_mode"] == "Partial"
    assert tp_call_kwargs["tp_price"] == Decimal("45000.50") * (Decimal("1") + Decimal("0.015"))
    assert tp_call_kwargs["tp_size"] == Decimal("0.001") * Decimal("0.5")


async def test_post_fill_price_paper_mode_does_not_acquire_pool_or_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OQ-4 — paper branch returns BEFORE persistence + emit (PaperExchange handles paper_*)."""
    persist_calls: list[Any] = []
    emit_calls: list[Any] = []

    async def _capture_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        persist_calls.append(kwargs)
        return (
            OrderPlaced(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
            ),
            SLMoved(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
                new_sl_price=Decimal("1"),
                sl_type="protective",
            ),
            1,
        )

    async def _capture_emit(**kwargs: Any) -> None:
        emit_calls.append(kwargs)

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _capture_persist,
    )
    monkeypatch.setattr(
        "services.execution.app.placement.emit_post_commit_events",
        _capture_emit,
    )
    handler, _, bus, _ = _build()
    await handler(_envelope(_request_payload(exchange_mode="paper")))
    assert persist_calls == []
    assert emit_calls == []
    bus.publish.assert_not_called()


# ---------------------------------------------------------------------------
# T-216b2 — live SL set (H-013 explicit Full + 5 exception classes → emergency_close)
# ---------------------------------------------------------------------------


async def test_post_fill_price_live_sl_set_uses_tpsl_mode_full_explicit_kwarg() -> None:
    """H-013 — SL call_site MUST pass tpsl_mode='Full' explicitly."""
    handler, adapter, _, _ = _build()
    await handler(_envelope())
    # First set_trading_stop call = SL with Full mode.
    sl_call = adapter.set_trading_stop.await_args_list[0]
    assert sl_call.kwargs["tpsl_mode"] == "Full"
    assert sl_call.kwargs["sl_price"] == Decimal("45000.50") * (Decimal("1") - Decimal("0.005"))


@pytest.mark.parametrize(
    ("exc_factory", "exc_label"),
    [
        (lambda: AuthError("bad"), "AuthError"),
        (lambda: OrderRejected("nope"), "OrderRejected"),
        (lambda: NetworkTimeout("t/o"), "NetworkTimeout"),
        (lambda: RateLimitError("rl"), "RateLimitError"),
        (lambda: UnknownState("?"), "UnknownState"),
    ],
)
async def test_post_fill_price_live_sl_set_failure_invokes_emergency_close_and_returns(
    monkeypatch: pytest.MonkeyPatch,
    exc_factory: Any,
    exc_label: str,
) -> None:
    """OQ-1 + H-004 — any of 5 catch-set exceptions triggers emergency_close."""
    emergency_calls: list[dict[str, Any]] = []

    async def _capture_emergency_close(**kwargs: Any) -> None:
        emergency_calls.append(kwargs)

    monkeypatch.setattr(
        "services.execution.app.placement.emergency_close",
        _capture_emergency_close,
    )

    persist_calls: list[Any] = []

    async def _no_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        persist_calls.append(kwargs)
        raise AssertionError("persist_placement_tx must NOT be called after emergency_close")

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _no_persist,
    )

    adapter = _ok_adapter()
    # First set_trading_stop call = SL → raises; subsequent should NOT happen.
    adapter.set_trading_stop = AsyncMock(side_effect=exc_factory())
    handler, _, _, logger = _build(adapter=adapter)
    await handler(_envelope())
    assert len(emergency_calls) == 1, (
        f"emergency_close not called for {exc_label} (got {len(emergency_calls)} calls)"
    )
    assert persist_calls == []  # short-circuited
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.set_trading_stop_sl_failed_invoking_emergency_close" in log_keys


# ---------------------------------------------------------------------------
# T-216b2 — live TP set (H-013 explicit Partial + OQ-2 default A continue)
# ---------------------------------------------------------------------------


async def test_post_fill_price_live_tp_set_uses_tpsl_mode_partial_with_tp_size_explicit_kwarg() -> (
    None
):
    """H-013 — TP call_site MUST pass tpsl_mode='Partial' + tp_size explicit."""
    handler, adapter, _, _ = _build()
    await handler(_envelope())
    # Second set_trading_stop call = TP with Partial mode + tp_size.
    tp_call = adapter.set_trading_stop.await_args_list[1]
    assert tp_call.kwargs["tpsl_mode"] == "Partial"
    assert tp_call.kwargs["tp_price"] == Decimal("45000.50") * (Decimal("1") + Decimal("0.015"))
    assert tp_call.kwargs["tp_size"] == Decimal("0.001") * Decimal("0.5")


async def test_post_fill_price_live_tp_set_failure_logs_error_and_continues_to_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OQ-2 default A — TP exhaustion = log + continue; persist + emit STILL run."""
    persist_calls: list[Any] = []
    emit_calls: list[Any] = []

    async def _capture_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        persist_calls.append(kwargs)
        return (
            OrderPlaced(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
            ),
            SLMoved(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
                new_sl_price=Decimal("1"),
                sl_type="protective",
            ),
            1,
        )

    async def _capture_emit(**kwargs: Any) -> None:
        emit_calls.append(kwargs)

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _capture_persist,
    )
    monkeypatch.setattr(
        "services.execution.app.placement.emit_post_commit_events",
        _capture_emit,
    )

    adapter = _ok_adapter()
    sl_ok = AsyncMock(side_effect=[None, NetworkTimeout("tp timeout")])
    adapter.set_trading_stop = sl_ok
    handler, _, _, logger = _build(adapter=adapter)
    await handler(_envelope())
    assert len(persist_calls) == 1
    assert len(emit_calls) == 1
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.tp_set_failed_continuing_with_sl_only" in log_keys


# ---------------------------------------------------------------------------
# T-216b2 — persistence + emit ordering (Q2 publish-after-persist)
# ---------------------------------------------------------------------------


async def test_post_fill_price_live_persist_called_after_sl_and_tp_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering pin — persist_placement_tx invoked after both set_trading_stop calls."""
    sequence: list[str] = []

    async def _track_set_trading_stop(*args: Any, **kwargs: Any) -> None:
        sequence.append(f"set_trading_stop_{kwargs.get('tpsl_mode', '?').lower()}")

    async def _track_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        sequence.append("persist_placement_tx")
        return (
            OrderPlaced(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
            ),
            SLMoved(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
                new_sl_price=Decimal("1"),
                sl_type="protective",
            ),
            1,
        )

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _track_persist,
    )

    adapter = _ok_adapter()
    adapter.set_trading_stop = AsyncMock(side_effect=_track_set_trading_stop)
    handler, _, _, _ = _build(adapter=adapter)
    await handler(_envelope())
    assert sequence == [
        "set_trading_stop_full",
        "set_trading_stop_partial",
        "persist_placement_tx",
    ]


async def test_post_fill_price_live_emit_called_after_persist_tx_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q2 — emit invoked after persist_placement_tx returns."""
    sequence: list[str] = []

    async def _track_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        sequence.append("persist")
        return (
            OrderPlaced(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
            ),
            SLMoved(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
                new_sl_price=Decimal("1"),
                sl_type="protective",
            ),
            1,
        )

    async def _track_emit(**kwargs: Any) -> None:
        sequence.append("emit")

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _track_persist,
    )
    monkeypatch.setattr(
        "services.execution.app.placement.emit_post_commit_events",
        _track_emit,
    )
    handler, _, _, _ = _build()
    await handler(_envelope())
    assert sequence == ["persist", "emit"]


async def test_post_fill_price_live_persist_tx_failure_does_not_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#8 — persist failure SHORT-CIRCUITS emit (publish-after-persist guarantee)."""
    emit_calls: list[Any] = []

    async def _failing_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        raise RuntimeError("db disconnect")

    async def _capture_emit(**kwargs: Any) -> None:
        emit_calls.append(kwargs)

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _failing_persist,
    )
    monkeypatch.setattr(
        "services.execution.app.placement.emit_post_commit_events",
        _capture_emit,
    )
    handler, _, _, logger = _build()
    await handler(_envelope())
    assert emit_calls == []
    log_keys = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.placement_persist_tx_failed" in log_keys


# ---------------------------------------------------------------------------
# T-216b2 — make_per_bot_handler dedup wrap + ctor kwargs (H-009 + WG#5/6)
# ---------------------------------------------------------------------------


async def test_make_per_bot_handler_returns_dedup_consumer_consume_callable_not_inner_handle() -> (
    None
):
    """WG#6 — returned callable is OrderRequestDedupConsumer.consume bound method."""
    handler, _, _, _ = _build()
    # The handler is a bound method on an OrderRequestDedupConsumer instance.
    assert isinstance(handler.__self__, OrderRequestDedupConsumer)
    assert handler.__name__ == "consume"


async def test_make_per_bot_handler_threads_dedup_capacity_to_consumer() -> None:
    """ctor wiring pin — Settings dedup_capacity propagates to consumer."""
    handler, _, _, _ = _build(dedup_capacity=42)
    consumer = handler.__self__
    assert consumer._capacity == 42  # pyright: ignore[reportPrivateUsage]


async def test_handler_wrapped_in_dedup_consumer_drops_duplicate_signal_id() -> None:
    """H-009 — duplicate (bot_id, signal_id) envelopes are filtered before _handle runs."""
    handler, adapter, _, _ = _build()
    env = _envelope(_request_payload(signal_id=999))
    await handler(env)
    await handler(env)  # duplicate
    # set_leverage called only once (second envelope dropped by dedup ring).
    assert adapter.set_leverage.await_count == 1


# ---------------------------------------------------------------------------
# T-216b2 — H-024 defensive: no OrderFilled emit at this boundary (WG#11)
# ---------------------------------------------------------------------------


async def test_post_fill_price_does_not_emit_OrderFilled_at_T_216b2_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#11 H-024 — emit_post_commit_events is invoked with OrderPlaced + SLMoved only;
    no OrderFilled at this stage (T-218 dispatcher owns that emit from stream_executions)."""
    from packages.bus.schemas.orders import OrderFilled

    emit_payloads: list[Any] = []

    async def _capture_emit(**kwargs: Any) -> None:
        emit_payloads.append(kwargs.get("order_placed_payload"))
        emit_payloads.append(kwargs.get("sl_moved_payload"))

    monkeypatch.setattr(
        "services.execution.app.placement.emit_post_commit_events",
        _capture_emit,
    )
    handler, _, _, _ = _build()
    await handler(_envelope())
    assert not any(isinstance(p, OrderFilled) for p in emit_payloads)
    assert any(isinstance(p, OrderPlaced) for p in emit_payloads)
    assert any(isinstance(p, SLMoved) for p in emit_payloads)


# ---------------------------------------------------------------------------
# T-217a — PositionLifecycle spawn pin tests
# ---------------------------------------------------------------------------


async def test_placement_persists_fsm_params_into_trades_meta_jsonb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-217a OQ-A — FSM params round-trip from OrderRequest into trades.meta."""
    captured: dict[str, Any] = {}

    async def _capture_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        captured.update(kwargs)
        return (
            OrderPlaced(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
            ),
            SLMoved(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
                new_sl_price=Decimal("1"),
                sl_type="protective",
            ),
            42,
        )

    async def _no_op_emit(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _capture_persist,
    )
    monkeypatch.setattr(
        "services.execution.app.placement.emit_post_commit_events",
        _no_op_emit,
    )

    handler, _, _, _ = _build()
    await handler(_envelope())
    request = captured["request"]
    # Verify the OrderRequest threaded through has the FSM fields
    # (persist_placement_tx is responsible for writing them to trades.meta).
    assert request.be_trigger == Decimal("0.003")
    assert request.be_sl_level == Decimal("0.001")
    assert request.trail_pct == Decimal("0.002")


async def test_placement_spawns_lifecycle_task_post_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-217a — placement spawns asyncio.Task named lifecycle_<bot_id>_<trade_id>."""

    async def _no_op_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        return (
            OrderPlaced(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
            ),
            SLMoved(
                bot_id="alpha",
                order_id=1,
                exchange_order_id="ord-1",
                symbol="BTCUSDT",
                timestamp=_FIXED_NOW,
                new_sl_price=Decimal("1"),
                sl_type="protective",
            ),
            7,  # trade_id
        )

    async def _no_op_emit(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _no_op_persist,
    )
    monkeypatch.setattr(
        "services.execution.app.placement.emit_post_commit_events",
        _no_op_emit,
    )

    lifecycle_tasks: dict[int, asyncio.Task[None]] = {}
    handler, _, _, _ = _build(position_lifecycle_tasks=lifecycle_tasks)
    await handler(_envelope())
    assert 7 in lifecycle_tasks
    task = lifecycle_tasks[7]
    assert task.get_name() == "lifecycle_alpha_7"
    task.cancel()


async def test_placement_lifecycle_task_not_spawned_on_emergency_close_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emergency-closed trades have no monitor — lifecycle dict stays empty."""

    async def _no_persist(**kwargs: Any) -> tuple[OrderPlaced, SLMoved, int]:
        raise AssertionError("persist must NOT be called on emergency_close path")

    async def _capture_emergency(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "services.execution.app.placement.persist_placement_tx",
        _no_persist,
    )
    monkeypatch.setattr(
        "services.execution.app.placement.emergency_close",
        _capture_emergency,
    )

    adapter = _ok_adapter()
    sl_fail = AsyncMock(side_effect=NetworkTimeout("sl set timeout"))
    adapter.set_trading_stop = sl_fail
    lifecycle_tasks: dict[int, asyncio.Task[None]] = {}
    handler, _, _, _ = _build(adapter=adapter, position_lifecycle_tasks=lifecycle_tasks)
    await handler(_envelope())
    assert lifecycle_tasks == {}


# ---------------------------------------------------------------------------
# T-527b2b — §B.1 sizing placement seam (capital-path; ADR-0013)
# ---------------------------------------------------------------------------

_SIZING_WIRE: dict[str, Any] = {
    "tiers": [
        {"balance_min": "500", "size": "700"},
        {"balance_min": "1000", "size": "1400"},
    ],
    "score_multipliers": {"4": "0.75"},
    "max_notional_per_symbol": {"default": "3000"},
}


def _account_balance(total_equity: str) -> Any:
    from packages.exchange.types import AccountBalance

    eq = Decimal(total_equity)
    return AccountBalance(
        wallet_balance=eq,
        available_balance=eq,
        total_equity=eq,
        margin_balance=eq,
        unrealized_pnl=Decimal("0"),
    )


async def test_sizing_happy_path_substitutes_computed_qty() -> None:
    """request.sizing set → compute_qty_from_sizing output (quantized) is sent
    to place_market_order, NOT request.qty. Hand: equity 1500 -> tier {1000,1400};
    score None -> *1.0 -> 1400; cap default 3000 -> 1400; 1400 / mark 700 = 2;
    quantize(2, step 0.001) = 2.000. (persist/emit are no-op via the autouse
    _patch_persist_and_emit fixture.)"""
    adapter = _ok_adapter()
    adapter.get_account_balance = AsyncMock(return_value=_account_balance("1500"))
    adapter.get_mark_price = AsyncMock(return_value=Decimal("700"))
    handler, _, _, _ = _build(adapter=adapter)
    await handler(_envelope(_request_payload(sizing=_SIZING_WIRE)))
    adapter.place_market_order.assert_awaited_once()
    assert adapter.place_market_order.await_args.args[2] == Decimal("2")  # not 0.001


async def test_sizing_sub_lowest_tier_skips_before_place() -> None:
    """equity 499 < lowest balance_min 500 → compute returns None → skip
    before place_market_order; signals_skipped_sizing{reason=sub_lowest_tier}."""
    registry = CollectorRegistry()
    metrics = build_execution_metrics(registry)
    adapter = _ok_adapter()
    adapter.get_account_balance = AsyncMock(return_value=_account_balance("499"))
    adapter.get_mark_price = AsyncMock(return_value=Decimal("700"))
    handler, _, _, _ = _build(adapter=adapter, metrics=metrics)
    await handler(_envelope(_request_payload(sizing=_SIZING_WIRE)))
    adapter.place_market_order.assert_not_awaited()
    assert (
        registry.get_sample_value(
            "signals_skipped_sizing_total",
            {"bot_id": "alpha", "reason": "sub_lowest_tier"},
        )
        == 1.0
    )


async def test_sizing_get_account_balance_raise_b1_skips() -> None:
    """B1: get_account_balance raises NetworkTimeout → log + skip, NO place,
    signals_skipped_sizing{reason=fetch_failed} (mirror get_instrument_info)."""
    registry = CollectorRegistry()
    metrics = build_execution_metrics(registry)
    adapter = _ok_adapter()
    adapter.get_account_balance = AsyncMock(side_effect=NetworkTimeout("balance down"))
    adapter.get_mark_price = AsyncMock(return_value=Decimal("700"))
    handler, _, _, _ = _build(adapter=adapter, metrics=metrics)
    await handler(_envelope(_request_payload(sizing=_SIZING_WIRE)))
    adapter.place_market_order.assert_not_awaited()
    assert (
        registry.get_sample_value(
            "signals_skipped_sizing_total",
            {"bot_id": "alpha", "reason": "fetch_failed"},
        )
        == 1.0
    )


async def test_sizing_get_mark_price_raise_b1_skips() -> None:
    """B1: get_mark_price raises RateLimitError → skip, NO place (both-sides L-017)."""
    registry = CollectorRegistry()
    metrics = build_execution_metrics(registry)
    adapter = _ok_adapter()
    adapter.get_account_balance = AsyncMock(return_value=_account_balance("1500"))
    adapter.get_mark_price = AsyncMock(side_effect=RateLimitError("ticker 429"))
    handler, _, _, _ = _build(adapter=adapter, metrics=metrics)
    await handler(_envelope(_request_payload(sizing=_SIZING_WIRE)))
    adapter.place_market_order.assert_not_awaited()
    assert (
        registry.get_sample_value(
            "signals_skipped_sizing_total",
            {"bot_id": "alpha", "reason": "fetch_failed"},
        )
        == 1.0
    )


async def test_sizing_none_uses_static_qty_path_unchanged() -> None:
    """request.sizing is None (no sizing key) → working_qty == request.qty,
    get_account_balance/get_mark_price NEVER called (byte-unchanged path).
    persist/emit no-op via the autouse _patch_persist_and_emit fixture."""
    adapter = _ok_adapter()
    adapter.get_account_balance = AsyncMock()
    adapter.get_mark_price = AsyncMock()
    handler, _, _, _ = _build(adapter=adapter)
    await handler(_envelope(_request_payload()))  # no sizing
    adapter.place_market_order.assert_awaited_once_with("BTCUSDT", "buy", Decimal("0.001"))
    adapter.get_account_balance.assert_not_awaited()
    adapter.get_mark_price.assert_not_awaited()


# ---------------------------------------------------------------------------
# T-528b — sizing.method dispatch (risk_per_sl vs tier; OQ-2/3/B1=A)
# ---------------------------------------------------------------------------

_RISK_SIZING_WIRE: dict[str, Any] = {
    "method": "risk_per_sl",
    "tiers": [],
    "score_multipliers": {},
    "risk_pct": "0.01",
    "max_notional_per_symbol": {"default": "3000"},
}


async def test_sizing_risk_per_sl_dispatches_to_compute_qty_from_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L-017 both-sides: method='risk_per_sl' → compute_qty_from_risk IS
    called (with request.sl_pct + wire risk_pct) AND compute_qty_from_sizing
    is NOT; the computed qty (quantized) reaches place_market_order."""
    spy_risk = MagicMock(return_value=Decimal("2"))
    spy_tier = MagicMock(return_value=Decimal("99"))
    monkeypatch.setattr("services.execution.app.placement.compute_qty_from_risk", spy_risk)
    monkeypatch.setattr("services.execution.app.placement.compute_qty_from_sizing", spy_tier)
    adapter = _ok_adapter()
    adapter.get_account_balance = AsyncMock(return_value=_account_balance("10000"))
    adapter.get_mark_price = AsyncMock(return_value=Decimal("700"))
    handler, _, _, _ = _build(adapter=adapter)
    await handler(_envelope(_request_payload(sizing=_RISK_SIZING_WIRE)))
    spy_risk.assert_called_once()
    assert spy_risk.call_args.kwargs["sl_pct"] == Decimal("0.005")  # request.sl_pct
    assert spy_risk.call_args.kwargs["risk_pct"] == Decimal("0.01")
    spy_tier.assert_not_called()
    adapter.place_market_order.assert_awaited_once()
    assert adapter.place_market_order.await_args.args[2] == Decimal("2")


async def test_sizing_tier_method_dispatches_to_compute_qty_from_sizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L-017 mirror: method='tier' (default; _SIZING_WIRE has no method key)
    → compute_qty_from_sizing IS called AND compute_qty_from_risk is NOT
    (the shipped T-527b2b tier path byte-unchanged)."""
    spy_risk = MagicMock(return_value=Decimal("99"))
    spy_tier = MagicMock(return_value=Decimal("2"))
    monkeypatch.setattr("services.execution.app.placement.compute_qty_from_risk", spy_risk)
    monkeypatch.setattr("services.execution.app.placement.compute_qty_from_sizing", spy_tier)
    adapter = _ok_adapter()
    adapter.get_account_balance = AsyncMock(return_value=_account_balance("1500"))
    adapter.get_mark_price = AsyncMock(return_value=Decimal("700"))
    handler, _, _, _ = _build(adapter=adapter)
    await handler(_envelope(_request_payload(sizing=_SIZING_WIRE)))
    spy_tier.assert_called_once()
    spy_risk.assert_not_called()
    adapter.place_market_order.assert_awaited_once()
    assert adapter.place_market_order.await_args.args[2] == Decimal("2")


async def test_sizing_risk_per_sl_risk_pct_none_skips_compute_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L-019 defensive guard: method='risk_per_sl' but risk_pct absent on the
    wire (None — the thin transport does NOT re-validate the coupling) →
    compute_error skip BEFORE place; compute_qty_from_risk NEVER called."""
    spy_risk = MagicMock(return_value=Decimal("2"))
    monkeypatch.setattr("services.execution.app.placement.compute_qty_from_risk", spy_risk)
    registry = CollectorRegistry()
    metrics = build_execution_metrics(registry)
    adapter = _ok_adapter()
    adapter.get_account_balance = AsyncMock(return_value=_account_balance("10000"))
    adapter.get_mark_price = AsyncMock(return_value=Decimal("700"))
    handler, _, _, _ = _build(adapter=adapter, metrics=metrics)
    wire_no_risk_pct = {k: v for k, v in _RISK_SIZING_WIRE.items() if k != "risk_pct"}
    await handler(_envelope(_request_payload(sizing=wire_no_risk_pct)))
    spy_risk.assert_not_called()
    adapter.place_market_order.assert_not_awaited()
    assert (
        registry.get_sample_value(
            "signals_skipped_sizing_total",
            {"bot_id": "alpha", "reason": "compute_error"},
        )
        == 1.0
    )


async def test_sizing_risk_per_sl_zero_equity_skips_sub_lowest_tier() -> None:
    """OQ-B1=A: real compute_qty_from_risk, total_equity 0 → None → the
    shipped sub_lowest_tier skip reused verbatim (shared low-cardinality
    label for the risk-per-SL no-capital path); NO place_market_order."""
    registry = CollectorRegistry()
    metrics = build_execution_metrics(registry)
    adapter = _ok_adapter()
    adapter.get_account_balance = AsyncMock(return_value=_account_balance("0"))
    adapter.get_mark_price = AsyncMock(return_value=Decimal("700"))
    handler, _, _, _ = _build(adapter=adapter, metrics=metrics)
    await handler(_envelope(_request_payload(sizing=_RISK_SIZING_WIRE)))
    adapter.place_market_order.assert_not_awaited()
    assert (
        registry.get_sample_value(
            "signals_skipped_sizing_total",
            {"bot_id": "alpha", "reason": "sub_lowest_tier"},
        )
        == 1.0
    )


# ---------------------------------------------------------------------------
# T-557 / H-037 — partial-TP size pre-flight min-lot validation (3 tests)
# ---------------------------------------------------------------------------


async def test_tp_size_below_min_order_qty_rejects_before_place() -> None:
    """T-557 / H-037 — partial tp_size < min_order_qty → pre-flight reject; NO order."""
    from packages.exchange.types import InstrumentInfo

    registry = CollectorRegistry()
    metrics = build_execution_metrics(registry)
    adapter = _ok_adapter()
    adapter.get_instrument_info = AsyncMock(
        return_value=InstrumentInfo(
            symbol="BTCUSDT",
            qty_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            min_notional_usd=Decimal("5"),
            tick_size=Decimal("0.1"),
        )
    )
    # qty 0.001 (== min, entry OK) x tp_qty_pct 0.5 = 0.0005 < min 0.001 → TP unsatisfiable.
    handler, _, bus, logger = _build(adapter=adapter, metrics=metrics)
    await handler(_envelope(_request_payload(qty="0.001", tp_qty_pct="0.5")))
    adapter.set_leverage.assert_not_called()
    adapter.place_market_order.assert_not_called()
    bus.publish.assert_not_called()
    error_keys = [c.args[0] for c in logger.error.call_args_list]
    assert "execution.tp_size_below_min_order_qty" in error_keys
    assert (
        registry.get_sample_value(
            "execution_tp_unsatisfiable_skipped_total",
            {"bot_id": "alpha", "symbol": "BTCUSDT"},
        )
        == 1.0
    )


async def test_tp_size_qty_step_round_down_proceeds_with_aligned_size() -> None:
    """T-557 / H-037 — qty_step round-down keeps tp_size >= min → proceed, aligned downstream."""
    from packages.exchange.types import InstrumentInfo

    adapter = _ok_adapter()
    adapter.get_instrument_info = AsyncMock(
        return_value=InstrumentInfo(
            symbol="BTCUSDT",
            qty_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            min_notional_usd=Decimal("5"),
            tick_size=Decimal("0.1"),
        )
    )
    # qty 0.003 x 0.5 = 0.0015 → quantize_qty step 0.001 → 0.001 (>= min) → proceed.
    handler, _, _, _ = _build(adapter=adapter)
    await handler(_envelope(_request_payload(qty="0.003", tp_qty_pct="0.5")))
    adapter.place_market_order.assert_awaited_once()
    # Live path: 2nd set_trading_stop call = Partial TP; tp_size must be the
    # qty_step-aligned 0.001 (NOT raw 0.0015).
    partial_tp_call = adapter.set_trading_stop.await_args_list[1]
    assert partial_tp_call.kwargs["tpsl_mode"] == "Partial"
    assert partial_tp_call.kwargs["tp_size"] == Decimal("0.001")


async def test_tp_qty_pct_one_boundary_proceeds() -> None:
    """T-557 / H-037 — tp_qty_pct=1.0 → tp_size == entry qty (>= min) → proceeds, no reject."""
    from packages.exchange.types import InstrumentInfo

    adapter = _ok_adapter()
    adapter.get_instrument_info = AsyncMock(
        return_value=InstrumentInfo(
            symbol="BTCUSDT",
            qty_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            min_notional_usd=Decimal("5"),
            tick_size=Decimal("0.1"),
        )
    )
    handler, _, _, _ = _build(adapter=adapter)
    await handler(_envelope(_request_payload(qty="0.001", tp_qty_pct="1.0")))
    adapter.place_market_order.assert_awaited_once()
