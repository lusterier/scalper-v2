"""§N4 unit tests for :mod:`services.execution.app.placement` (T-216a).

Mock-based: adapter (`ExchangeClient`) + bus (`NatsClient`) +
`MessageEnvelope` constructed inline. Validates handler closure
isolation, OrderRequest validation, subject-payload mismatch WARN,
H-003 UnknownState catch (no DLQ), OrderRejected qty/precision
differentiation, fill_price retry + DLQ + raise, NotImplementedError
T-216b boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.bus import MessageEnvelope
from packages.core import BotId
from packages.exchange.errors import (
    AuthError,
    NetworkTimeout,
    OrderRejected,
    UnknownState,
)
from packages.exchange.types import OrderPlaceResult
from services.execution.app.placement import (
    FillPriceUnresolvedError,
    make_per_bot_handler,
)

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
            placed_at=datetime(2026, 4, 30, tzinfo=UTC),
        )
    )
    adapter.get_fill_price = AsyncMock(return_value=Decimal("45000.50"))
    return adapter


def _ok_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _build(
    adapter: MagicMock | None = None,
    bus: MagicMock | None = None,
    bot_id: str = "alpha",
    attempts: int = 3,
    backoff: float = 0.0,
) -> tuple[Any, MagicMock, MagicMock, MagicMock]:
    used_adapter = adapter or _ok_adapter()
    used_bus = bus or _ok_bus()
    logger = MagicMock()
    handler = make_per_bot_handler(
        bot_id=BotId(bot_id),
        adapter=used_adapter,
        bus=used_bus,
        logger=logger,
        fill_price_retry_attempts=attempts,
        fill_price_retry_backoff_s=backoff,
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
    with pytest.raises(NotImplementedError, match="T-216b"):
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
        fill_price_retry_attempts=3,
        fill_price_retry_backoff_s=0.0,
    )
    handler_b = make_per_bot_handler(
        bot_id=BotId("beta"),
        adapter=adapter_b,
        bus=bus,
        logger=logger,
        fill_price_retry_attempts=3,
        fill_price_retry_backoff_s=0.0,
    )
    with pytest.raises(NotImplementedError):
        await handler_a(_envelope(_request_payload(bot_id="alpha")))
    adapter_a.set_leverage.assert_awaited_once()
    adapter_b.set_leverage.assert_not_called()
    with pytest.raises(NotImplementedError):
        await handler_b(_envelope(_request_payload(bot_id="beta")))
    adapter_b.set_leverage.assert_awaited_once()
    # adapter_a should still have only the original 1 call from earlier.
    assert adapter_a.set_leverage.await_count == 1


async def test_handler_warn_on_botid_mismatch_uses_subject_as_authoritative() -> None:
    """CONCERN #6 pin — payload bot_id != subject bot_id → WARN + continue."""
    handler, adapter, _, logger = _build(bot_id="alpha")
    payload = _request_payload(bot_id="impostor")
    with pytest.raises(NotImplementedError):
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
    with pytest.raises(NotImplementedError, match="T-216b"):
        await handler(_envelope())
    adapter.set_leverage.assert_awaited_once_with("BTCUSDT", 10)
    adapter.place_market_order.assert_awaited_once_with("BTCUSDT", "buy", Decimal("0.001"))
    adapter.get_fill_price.assert_awaited_once_with("BTCUSDT", "ord-1")


async def test_handler_raises_NotImplementedError_with_T_216b_substring_after_fill_price() -> None:
    handler, _, _, _ = _build()
    with pytest.raises(NotImplementedError) as info:
        await handler(_envelope())
    assert "T-216b" in str(info.value)
    assert "post-fill_price" in str(info.value)


async def test_handler_logs_qty_rounding_pending_warning_before_place() -> None:
    """BLOCKER #3 pin — operator visibility hook until T-F2+ step-size cache lands."""
    handler, _, _, logger = _build()
    with pytest.raises(NotImplementedError):
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
    with pytest.raises(NotImplementedError):
        await handler(_envelope())
    assert adapter.get_fill_price.await_count == 1


async def test_handler_retries_up_to_3_times_when_fill_price_returns_none() -> None:
    adapter = _ok_adapter()
    adapter.get_fill_price = AsyncMock(side_effect=[None, None, Decimal("100.0")])
    handler, _, _, _ = _build(adapter=adapter, attempts=3)
    with pytest.raises(NotImplementedError):
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
