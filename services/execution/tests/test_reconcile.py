"""§N4 unit tests for :mod:`services.execution.app.reconcile` (T-219).

Mock-based: adapter (`ExchangeClient.get_closed_pnl_cumulative`), bus
(`NatsClient.publish` + `MessageEnvelope` wrapping), conn ctx for the
`update_trade_close` + `delete_position_state` writes.

Validates ADR-0006 D1-D5 + D6 hazard-test mapping (H-001/H-002/H-011/H-012):

- Cumulative-delta computation reads only ``after - before``; orderId-permutation
  invariance.
- Single sleep BEFORE the AFTER snapshot only (H-011 timing pin).
- Per-sub-account ``asyncio.Lock`` serialization across cross-bot calls.
- Synthetic close ``close_order_id`` resolution via ``select_open_order_id_by_trade_id``
  + OPEN order ``exchange_order_id`` via ``select_order_meta_by_id``.
- Atomic close persistence (``update_trade_close`` PK-only + ``delete_position_state``
  composite-PK).
- ``OrderClosed`` payload returned for caller-side post-commit emit (mirror T-216b2
  ``persist_placement_tx`` + ``emit_post_commit_events`` split).
- ``emit_post_commit_close_event`` wraps in :class:`MessageEnvelope` with
  ``correlation_id`` + ``publisher`` per audit-grade contract.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.bus.schemas.orders import OrderClosed
from packages.core import CorrelationId
from services.execution.app import reconcile as reconcile_mod
from services.execution.app.reconcile import (
    emit_post_commit_close_event,
    reconcile_close,
)

_FIXED_NOW = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def patched_queries(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    mocks: dict[str, Any] = {
        "select_open_order_id_by_trade_id": AsyncMock(return_value=100),
        "select_order_meta_by_id": AsyncMock(return_value=("cid-default", "ord-exch-1")),
        "update_trade_close": AsyncMock(return_value=None),
        "delete_position_state": AsyncMock(return_value=None),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(reconcile_mod, name, mock)
    return mocks


def _build_kwargs(
    *,
    close_order_id: int | None = 100,
    exec_type: str = "close",
    fees_paid_at_close: Decimal | None = None,
    before_total: Decimal = Decimal("0"),
    after_total: Decimal = Decimal("25.50"),
) -> tuple[dict[str, Any], MagicMock]:
    adapter = MagicMock()
    adapter.get_closed_pnl_cumulative = AsyncMock(side_effect=[before_total, after_total])
    return (
        {
            "conn": MagicMock(),
            "adapter": adapter,
            "bound_logger": MagicMock(),
            "bot_id": "alpha",
            "symbol": "BTCUSDT",
            "sub_account": "alpha-sub",
            "closed_pnl_lock": asyncio.Lock(),
            "closed_pnl_post_close_sleep_s": 0.0,
            "trade_id": 1,
            "close_order_id": close_order_id,
            "exec_type": exec_type,
            "fees_paid_at_close": fees_paid_at_close,
            "final_fill_price": Decimal("100"),
            "closed_at": _FIXED_NOW,
        },
        adapter,
    )


# ---------------------------------------------------------------------------
# Hazard test pins (verbatim brief §20 names)
# ---------------------------------------------------------------------------


async def test_cumulative_delta_ignores_order_ids(
    patched_queries: dict[str, Any],
) -> None:
    """H-001 — delta = total - total; per-row orderId never accessed.

    Adapter.get_closed_pnl_cumulative returns scalar Decimal totals;
    reconcile_close reads only the cumulative sum, NEVER iterates per-row
    orderIds. Orderid-permutation invariance is structural.
    """
    kwargs, _ = _build_kwargs(before_total=Decimal("100"), after_total=Decimal("125.50"))
    payload, _, _ = await reconcile_close(**kwargs)
    update_call = patched_queries["update_trade_close"].call_args
    assert update_call.kwargs["realized_pnl"] == Decimal("25.50")
    assert payload.realized_pnl == Decimal("25.50")


async def test_close_with_identical_prior_trade_same_symbol(
    patched_queries: dict[str, Any],
) -> None:
    """H-002 — entry+qty collision impossible; cumulative-delta has no matching."""
    # Pre-existing closed trade (identical symbol/qty/entry_price) already in
    # before_total; new close adds Decimal("8.00").
    kwargs, _ = _build_kwargs(before_total=Decimal("17.00"), after_total=Decimal("25.00"))
    await reconcile_close(**kwargs)
    update_call = patched_queries["update_trade_close"].call_args
    assert update_call.kwargs["realized_pnl"] == Decimal("8.00")


async def test_closed_pnl_snapshot_waits_before_reading(
    monkeypatch: pytest.MonkeyPatch,
    patched_queries: dict[str, Any],
) -> None:
    """H-011 — single 2s sleep between BEFORE and AFTER snapshots."""
    sleep_calls: list[float] = []

    async def _capture_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("services.execution.app.reconcile.asyncio.sleep", _capture_sleep)

    kwargs, adapter = _build_kwargs()
    kwargs["closed_pnl_post_close_sleep_s"] = 2.5
    await reconcile_close(**kwargs)
    assert sleep_calls == [2.5]
    assert adapter.get_closed_pnl_cumulative.await_count == 2


async def test_close_uses_closed_pnl_delta_over_ws_accumulation(
    patched_queries: dict[str, Any],
) -> None:
    """H-012 — realized_pnl = closed-pnl delta, NOT WS-accumulated running_pnl.

    The reconcile_close body has NO knowledge of ``position_state.running_pnl``;
    delta is computed exclusively from snapshot pair. Test asserts realized_pnl
    matches snapshot-derived delta (Decimal('23.75')) regardless of any
    WS-accumulated value the caller may have.
    """
    kwargs, _ = _build_kwargs(before_total=Decimal("0"), after_total=Decimal("23.75"))
    await reconcile_close(**kwargs)
    update_call = patched_queries["update_trade_close"].call_args
    assert update_call.kwargs["realized_pnl"] == Decimal("23.75")


# ---------------------------------------------------------------------------
# D1-D5 + edge cases
# ---------------------------------------------------------------------------


async def test_reconcile_close_writes_realized_pnl_from_cumulative_delta(
    patched_queries: dict[str, Any],
) -> None:
    """D3 — full delta to single trade via update_trade_close call."""
    kwargs, _ = _build_kwargs()
    await reconcile_close(**kwargs)
    update_call = patched_queries["update_trade_close"].call_args
    assert update_call.kwargs["realized_pnl"] == Decimal("25.50")
    assert update_call.kwargs["close_reason"] == "manual"  # exec_type='close'


async def test_reconcile_close_acquires_lock_around_snapshot_pair() -> None:
    """D4 — Lock held during BEFORE→sleep→AFTER triplet."""
    lock = asyncio.Lock()
    held_during_snapshots: list[bool] = []

    adapter = MagicMock()

    async def _snapshot(_sub: str) -> Decimal:
        held_during_snapshots.append(lock.locked())
        return Decimal("0")

    adapter.get_closed_pnl_cumulative = AsyncMock(side_effect=_snapshot)
    kwargs, _ = _build_kwargs()
    kwargs["adapter"] = adapter
    kwargs["closed_pnl_lock"] = lock
    # Patch queries
    # (Test runs without the patched_queries fixture; we monkey-patch directly.)
    from unittest.mock import patch

    with (
        patch.object(
            reconcile_mod, "select_order_meta_by_id", AsyncMock(return_value=("cid", "ord-1"))
        ),
        patch.object(reconcile_mod, "update_trade_close", AsyncMock()),
        patch.object(reconcile_mod, "delete_position_state", AsyncMock()),
    ):
        await reconcile_close(**kwargs)
    assert held_during_snapshots == [True, True]
    assert lock.locked() is False  # released post-AFTER snapshot


async def test_reconcile_close_invokes_update_trade_close_with_close_reason_from_exec_type(
    patched_queries: dict[str, Any],
) -> None:
    """D5 — exec_type → close_reason mapping per ADR-0006."""
    for exec_type, expected_reason in (
        ("close", "manual"),
        ("sl", "sl"),
        ("trail", "trail"),
        ("unknown", "unknown"),
    ):
        kwargs, _ = _build_kwargs(exec_type=exec_type)
        await reconcile_close(**kwargs)
        update_call = patched_queries["update_trade_close"].call_args
        assert update_call.kwargs["close_reason"] == expected_reason


async def test_reconcile_close_resolves_synthetic_close_order_id_via_select_helper(
    patched_queries: dict[str, Any],
) -> None:
    """D5 amendment — synthetic close (None close_order_id) resolves via OPEN order id."""
    patched_queries["select_open_order_id_by_trade_id"].return_value = 77
    kwargs, _ = _build_kwargs(close_order_id=None)
    await reconcile_close(**kwargs)
    patched_queries["select_open_order_id_by_trade_id"].assert_awaited_once_with(kwargs["conn"], 1)
    update_call = patched_queries["update_trade_close"].call_args
    assert update_call.kwargs["close_order_id"] == 77


async def test_reconcile_close_returns_tuple_with_payload_correlation_id_and_exchange_order_id(
    patched_queries: dict[str, Any],
) -> None:
    """BLOCKER #1 fix — return contract for caller-side post-commit emit."""
    patched_queries["select_order_meta_by_id"].return_value = ("cid-xyz", "ord-exch-7")
    kwargs, _ = _build_kwargs()
    payload, corr_id, exch_id = await reconcile_close(**kwargs)
    assert isinstance(payload, OrderClosed)
    assert corr_id == CorrelationId("cid-xyz")
    assert exch_id == "ord-exch-7"
    assert payload.exchange_order_id == "ord-exch-7"


async def test_reconcile_close_orphan_trade_raises_runtime_error_when_open_order_missing(
    patched_queries: dict[str, Any],
) -> None:
    """Defensive halt — synthetic close + missing trade row → RuntimeError."""
    patched_queries["select_open_order_id_by_trade_id"].return_value = None
    kwargs, _ = _build_kwargs(close_order_id=None)
    with pytest.raises(RuntimeError, match="orphan trade"):
        await reconcile_close(**kwargs)


async def test_reconcile_close_order_meta_missing_raises_runtime_error(
    patched_queries: dict[str, Any],
) -> None:
    """CONCERN #4 fix — defensive halt when select_order_meta returns None."""
    patched_queries["select_order_meta_by_id"].return_value = None
    kwargs, _ = _build_kwargs()
    with pytest.raises(RuntimeError, match="orders row id="):
        await reconcile_close(**kwargs)


# ---------------------------------------------------------------------------
# emit_post_commit_close_event
# ---------------------------------------------------------------------------


async def test_emit_post_commit_close_event_wraps_payload_in_message_envelope() -> None:
    """BLOCKER #2 fix — MessageEnvelope wrapping with correlation_id + publisher.

    T-511b2 / ADR-0010: now publishes TWO envelopes — OrderClosed to
    ``orders.events.<bot_id>`` (production wire) + TradeClosedPayload to
    ``trade.closed.<bot_id>`` (internal H-016 cancel hook).
    """
    bus = MagicMock()
    bus.publish = AsyncMock()
    payload = OrderClosed(
        bot_id="alpha",
        order_id=1,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_FIXED_NOW,
        realized_pnl=Decimal("12.50"),
        close_reason="manual",
    )
    await emit_post_commit_close_event(
        bus=bus,
        bot_id="alpha",
        correlation_id=CorrelationId("cid-1"),
        order_closed_payload=payload,
        trade_id=42,
        closed_at=_FIXED_NOW,
        bound_logger=MagicMock(),
    )
    assert bus.publish.await_count == 2
    from packages.bus import MessageEnvelope

    # Publish 1: orders.events.<bot_id> with OrderClosed payload.
    subject_1, envelope_1 = bus.publish.await_args_list[0].args
    assert subject_1 == "orders.events.alpha"
    assert isinstance(envelope_1, MessageEnvelope)
    assert str(envelope_1.correlation_id) == "cid-1"
    assert envelope_1.publisher == "execution-service"
    assert envelope_1.payload["realized_pnl"] == "12.50"
    # Publish 2: trade.closed.<bot_id> with TradeClosedPayload (H-016 cancel hook).
    subject_2, envelope_2 = bus.publish.await_args_list[1].args
    assert subject_2 == "trade.closed.alpha"
    assert isinstance(envelope_2, MessageEnvelope)
    assert envelope_2.payload["parent_trade_id"] == 42
    assert envelope_2.payload["parent_kind"] == "live"


async def test_emit_post_commit_close_event_publish_failure_logs_does_not_raise() -> None:
    """Best-effort emit — publish failure logged but does NOT raise.

    T-511b2: per-publish try/except — first publish failure does NOT short-
    circuit second; both error-event names land in logger.
    """
    bus = MagicMock()
    bus.publish = AsyncMock(side_effect=RuntimeError("nats down"))
    logger = MagicMock()
    payload = OrderClosed(
        bot_id="alpha",
        order_id=1,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_FIXED_NOW,
        realized_pnl=Decimal("0"),
        close_reason="manual",
    )
    # Does NOT raise.
    await emit_post_commit_close_event(
        bus=bus,
        bot_id="alpha",
        correlation_id=CorrelationId("cid-1"),
        order_closed_payload=payload,
        trade_id=42,
        closed_at=_FIXED_NOW,
        bound_logger=logger,
    )
    error_event_names = [c.args[0] for c in logger.error.call_args_list]
    assert "execution.event_publish_failed" in error_event_names
    assert "execution.trade_closed_publish_failed" in error_event_names
