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
) -> tuple[Any, MagicMock, MagicMock, MagicMock]:
    used_adapter = adapter or _ok_adapter()
    used_bus = bus or _ok_bus()
    used_pool = pool if pool is not None else _mock_pool()
    used_now_fn = now_fn if now_fn is not None else (lambda: _FIXED_NOW)
    used_lifecycle_tasks = position_lifecycle_tasks if position_lifecycle_tasks is not None else {}
    logger = MagicMock()
    handler = make_per_bot_handler(
        bot_id=BotId(bot_id),
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


async def test_handler_logs_qty_rounding_pending_warning_before_place() -> None:
    """BLOCKER #3 pin — operator visibility hook until T-F2+ step-size cache lands."""
    handler, _, _, logger = _build()
    await handler(_envelope())
    log_keys = [call.args[0] for call in logger.warning.call_args_list]
    assert "execution.qty_step_rounding_pending_t_f2_plus" in log_keys


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
