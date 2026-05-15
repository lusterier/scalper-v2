"""§11.2 BybitV5Adapter unit tests (T-208a).

§N4 TDD steps 1-7 per plan-doc. Mock BybitV5Client.request +
SharedRateLimiter + Counter; no real HTTP/NATS. Hand-verifiable
Bybit V5 wire-payload snapshots per method (Hand verification §F.1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.exchange.bybit_v5.adapter import (
    _DEFAULT_LEVERAGE_CACHE_TTL_S,
    BybitV5Adapter,
    _to_bybit_side,
)
from packages.exchange.errors import NetworkTimeout, RateLimitError, UnknownState

_FIXED_NOW = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)


def _make_client_mock() -> MagicMock:
    client = MagicMock()
    client.request = AsyncMock(return_value={"orderId": "ord-abc-123", "orderLinkId": "link-x"})
    return client


def _make_limiter_mock() -> MagicMock:
    limiter = MagicMock()
    limiter.acquire = AsyncMock()
    limiter.signal_upstream_rate_limit = AsyncMock()
    return limiter


def _make_counter_mock() -> MagicMock:
    counter = MagicMock()
    label_handle = MagicMock()
    label_handle.inc = MagicMock()
    counter.labels = MagicMock(return_value=label_handle)
    return counter


def _make_ws_mock() -> MagicMock:
    ws = MagicMock()
    ws.close = AsyncMock()
    ws.executions = MagicMock()
    ws.positions = MagicMock()
    return ws


def _make_adapter(
    *,
    client: MagicMock | None = None,
    ws: MagicMock | None = None,
    limiter: MagicMock | None = None,
    counter: MagicMock | None = None,
    sub_account: str = "sub-a",
    leverage_cache_ttl_s: float = _DEFAULT_LEVERAGE_CACHE_TTL_S,
    now: datetime | None = None,
) -> BybitV5Adapter:
    fixed_now = now or _FIXED_NOW
    return BybitV5Adapter(
        client=client or _make_client_mock(),
        ws=ws or _make_ws_mock(),
        limiter=limiter or _make_limiter_mock(),
        bus=MagicMock(),
        sub_account=sub_account,
        metrics_counter=counter,
        leverage_cache_ttl_s=leverage_cache_ttl_s,
        now_fn=lambda: fixed_now,
    )


# --- Constructor + setup (3 tests) -----------------------------------------


def test_constructor_accepts_required_kwargs() -> None:
    """8-kwarg ctor (6 baseline + leverage_cache_ttl_s + now_fn)."""
    client = _make_client_mock()
    ws = _make_ws_mock()
    limiter = _make_limiter_mock()
    counter = _make_counter_mock()
    adapter = BybitV5Adapter(
        client=client,
        ws=ws,
        limiter=limiter,
        bus=MagicMock(),
        sub_account="sub-a",
        metrics_counter=counter,
        leverage_cache_ttl_s=120.0,
        now_fn=lambda: _FIXED_NOW,
    )
    assert adapter._client is client
    assert adapter._ws is ws
    assert adapter._limiter is limiter
    assert adapter._sub_account == "sub-a"
    assert adapter._metrics_counter is counter
    assert adapter._leverage_cache_ttl_s == 120.0


def test_constructor_initializes_empty_leverage_cache() -> None:
    adapter = _make_adapter()
    assert adapter._leverage_cache == {}


def test_constructor_uses_default_ttl_when_not_provided() -> None:
    """Default = _DEFAULT_LEVERAGE_CACHE_TTL_S (3600.0s per Q9)."""
    adapter = BybitV5Adapter(
        client=_make_client_mock(),
        ws=_make_ws_mock(),
        limiter=_make_limiter_mock(),
        bus=MagicMock(),
        sub_account="sub-a",
    )
    assert adapter._leverage_cache_ttl_s == _DEFAULT_LEVERAGE_CACHE_TTL_S
    assert adapter._leverage_cache_ttl_s == 3600.0


# --- set_leverage (5 tests) -------------------------------------------------


async def test_set_leverage_calls_upstream_with_bybit_v5_body_shape() -> None:
    """§F.1 vector: POST /v5/position/set-leverage with category/symbol/buyLeverage/sellLeverage."""
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.set_leverage("BTCUSDT", 10)
    client.request.assert_awaited_once()
    call = client.request.await_args
    assert call.args == ("POST", "/v5/position/set-leverage")
    assert call.kwargs["body"] == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "buyLeverage": "10",
        "sellLeverage": "10",
    }
    assert call.kwargs["retries"] == 3


async def test_set_leverage_caches_call_for_default_ttl() -> None:
    """Within TTL window → no second upstream call."""
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.set_leverage("BTCUSDT", 10)
    await adapter.set_leverage("BTCUSDT", 10)
    assert client.request.await_count == 1


async def test_set_leverage_re_calls_upstream_after_ttl_expires() -> None:
    """Past TTL → fresh upstream call."""
    client = _make_client_mock()
    times = [_FIXED_NOW, _FIXED_NOW + timedelta(seconds=3700)]
    iter_times = iter(times)

    def fake_now() -> datetime:
        return next(iter_times)

    adapter = BybitV5Adapter(
        client=client,
        ws=_make_ws_mock(),
        limiter=_make_limiter_mock(),
        bus=MagicMock(),
        sub_account="sub-a",
        now_fn=fake_now,
    )
    # Pre-seed cache as if a prior call succeeded an hour ago.
    adapter._leverage_cache[("BTCUSDT", 10)] = _FIXED_NOW - timedelta(seconds=4000)
    await adapter.set_leverage("BTCUSDT", 10)
    assert client.request.await_count == 1


async def test_set_leverage_acquires_limiter_with_positions_group() -> None:
    limiter = _make_limiter_mock()
    adapter = _make_adapter(limiter=limiter)
    await adapter.set_leverage("BTCUSDT", 10)
    limiter.acquire.assert_awaited_once_with("sub-a", "positions")


async def test_set_leverage_uses_custom_ttl_when_provided() -> None:
    """L-001 fix: per-instance TTL threading; both hit + miss paths exercised."""
    client = _make_client_mock()
    times: list[datetime] = []

    def fake_now() -> datetime:
        return times.pop(0) if times else _FIXED_NOW

    # Sequence (cache check short-circuits on None entries):
    #   Call 1: 1 now_fn call (cache write at t0; no check since miss).
    #   Call 2: 1 now_fn call (cache check at t+5s — hit; no upstream).
    #   Call 3: 2 now_fn calls (cache check at t+15s → miss; cache write at t+15s).
    times[:] = [
        _FIXED_NOW,  # call 1 cache write
        _FIXED_NOW + timedelta(seconds=5),  # call 2 cache check — hit
        _FIXED_NOW + timedelta(seconds=15),  # call 3 cache check — miss (past 10s TTL)
        _FIXED_NOW + timedelta(seconds=15),  # call 3 cache write
    ]
    adapter = BybitV5Adapter(
        client=client,
        ws=_make_ws_mock(),
        limiter=_make_limiter_mock(),
        bus=MagicMock(),
        sub_account="sub-a",
        leverage_cache_ttl_s=10.0,
        now_fn=fake_now,
    )
    await adapter.set_leverage("BTCUSDT", 10)  # call 1: miss → upstream + cache
    await adapter.set_leverage("BTCUSDT", 10)  # call 2: hit → no upstream
    await adapter.set_leverage("BTCUSDT", 10)  # call 3: miss → upstream + cache
    assert client.request.await_count == 2


# --- place_market_order (6 tests) ------------------------------------------


async def test_place_market_order_calls_upstream_with_bybit_v5_body_shape() -> None:
    """§F.1 vector: POST /v5/order/create with linear/symbol/side/orderType/qty/reduceOnly."""
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    call = client.request.await_args
    assert call.args == ("POST", "/v5/order/create")
    assert call.kwargs["body"] == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "orderType": "Market",
        "qty": "0.5",
        "reduceOnly": False,
    }
    assert call.kwargs["retries"] == 0  # H-003


async def test_place_market_order_serializes_qty_as_decimal_string_not_float() -> None:
    """H-015 partial pin (W#5): wire qty is str of Decimal, not float."""
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.place_market_order("BTCUSDT", "buy", Decimal("0.500000000001"))
    body = client.request.await_args.kwargs["body"]
    assert isinstance(body["qty"], str)  # W#5: type pin
    assert body["qty"] == "0.500000000001"


async def test_place_market_order_maps_lowercase_side_to_capitalized_wire() -> None:
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.place_market_order("BTCUSDT", "sell", Decimal("0.5"))
    assert client.request.await_args.kwargs["body"]["side"] == "Sell"


async def test_place_market_order_returns_order_place_result() -> None:
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    result = await adapter.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    assert result.exchange_order_id == "ord-abc-123"
    assert result.placed_at == _FIXED_NOW


async def test_place_order_on_timeout_never_retries_and_raises_unknown_state() -> None:
    """H-003 verbatim per §20 line 2621."""
    client = _make_client_mock()
    client.request = AsyncMock(side_effect=NetworkTimeout("timed out"))
    adapter = _make_adapter(client=client)
    with pytest.raises(UnknownState) as info:
        await adapter.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    assert info.value.last_known_action == "place_market_order"
    assert isinstance(info.value.__cause__, NetworkTimeout)
    assert client.request.await_count == 1
    assert client.request.await_args.kwargs["retries"] == 0


async def test_place_market_order_acquires_limiter_with_orders_group() -> None:
    limiter = _make_limiter_mock()
    adapter = _make_adapter(limiter=limiter)
    await adapter.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    limiter.acquire.assert_awaited_once_with("sub-a", "orders")


# --- set_trading_stop (4 tests) --------------------------------------------


async def test_set_trading_stop_calls_upstream_with_bybit_v5_body_shape() -> None:
    """§F.1 vector: POST /v5/position/trading-stop with conditional fields."""
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.set_trading_stop(
        "BTCUSDT",
        "Partial",
        sl_price=Decimal("64500"),
        tp_price=Decimal("65500"),
        tp_size=Decimal("0.1"),
    )
    call = client.request.await_args
    assert call.args == ("POST", "/v5/position/trading-stop")
    assert call.kwargs["body"] == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "tpslMode": "Partial",
        "stopLoss": "64500",
        "takeProfit": "65500",
        "tpSize": "0.1",
    }
    assert call.kwargs["retries"] == 3


async def test_set_trading_stop_omits_none_fields_from_body() -> None:
    """Partial-args call only includes provided fields + tpslMode + symbol + category."""
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.set_trading_stop("BTCUSDT", "Full", sl_price=Decimal("64500"))
    body = client.request.await_args.kwargs["body"]
    assert body == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "tpslMode": "Full",
        "stopLoss": "64500",
    }
    assert "takeProfit" not in body
    assert "tpSize" not in body


async def test_set_trading_stop_serializes_decimal_prices_as_strings() -> None:
    """H-015 partial pin: prices wire-serialized as str, not float."""
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.set_trading_stop(
        "BTCUSDT",
        "Full",
        sl_price=Decimal("64500.123456789012"),
    )
    body = client.request.await_args.kwargs["body"]
    assert isinstance(body["stopLoss"], str)
    assert body["stopLoss"] == "64500.123456789012"


async def test_set_trading_stop_acquires_limiter_with_positions_group() -> None:
    limiter = _make_limiter_mock()
    adapter = _make_adapter(limiter=limiter)
    await adapter.set_trading_stop("BTCUSDT", "Full", sl_price=Decimal("64500"))
    limiter.acquire.assert_awaited_once_with("sub-a", "positions")


# --- cancel_order (3 tests) ------------------------------------------------


async def test_cancel_order_calls_upstream_with_bybit_v5_body_shape() -> None:
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.cancel_order("BTCUSDT", "ord-abc")
    call = client.request.await_args
    assert call.args == ("POST", "/v5/order/cancel")
    assert call.kwargs["body"] == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "orderId": "ord-abc",
    }


async def test_cancel_order_acquires_limiter_with_orders_group() -> None:
    limiter = _make_limiter_mock()
    adapter = _make_adapter(limiter=limiter)
    await adapter.cancel_order("BTCUSDT", "ord-abc")
    limiter.acquire.assert_awaited_once_with("sub-a", "orders")


async def test_cancel_order_uses_3_retries_per_section_11_2() -> None:
    client = _make_client_mock()
    adapter = _make_adapter(client=client)
    await adapter.cancel_order("BTCUSDT", "ord-abc")
    assert client.request.await_args.kwargs["retries"] == 3


# --- RateLimitError handling (4 tests) -------------------------------------


async def test_set_leverage_on_RateLimitError_signals_upstream_and_re_raises() -> None:
    client = _make_client_mock()
    client.request = AsyncMock(side_effect=RateLimitError("retCode=10006"))
    limiter = _make_limiter_mock()
    adapter = _make_adapter(client=client, limiter=limiter)
    with pytest.raises(RateLimitError):
        await adapter.set_leverage("BTCUSDT", 10)
    limiter.signal_upstream_rate_limit.assert_awaited_once()


async def test_place_market_order_on_RateLimitError_signals_upstream_and_re_raises() -> None:
    client = _make_client_mock()
    client.request = AsyncMock(side_effect=RateLimitError("retCode=10006"))
    limiter = _make_limiter_mock()
    adapter = _make_adapter(client=client, limiter=limiter)
    with pytest.raises(RateLimitError):
        await adapter.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    limiter.signal_upstream_rate_limit.assert_awaited_once()


async def test_RateLimitError_increments_prometheus_counter_with_correct_labels() -> None:
    """§15.3 deferred from T-205: rate_limit_hits_total{exchange, endpoint_group}."""
    client = _make_client_mock()
    client.request = AsyncMock(side_effect=RateLimitError("retCode=10016"))
    counter = _make_counter_mock()
    adapter = _make_adapter(client=client, counter=counter)
    with pytest.raises(RateLimitError):
        await adapter.cancel_order("BTCUSDT", "ord-abc")
    counter.labels.assert_called_once_with(exchange="bybit", endpoint_group="orders")
    counter.labels.return_value.inc.assert_called_once()


async def test_RateLimitError_does_not_increment_counter_when_metrics_counter_is_None() -> None:
    """ctor `metrics_counter=None` (default) → no AttributeError on rate-limit hit."""
    client = _make_client_mock()
    client.request = AsyncMock(side_effect=RateLimitError("retCode=10006"))
    adapter = _make_adapter(client=client, counter=None)
    with pytest.raises(RateLimitError):
        await adapter.cancel_order("BTCUSDT", "ord-abc")
    # No exception about counter; just RateLimitError propagated.


# --- T-209 stream + close delegation (3 tests) -----------------------------


def test_stream_executions_delegates_to_ws_executions_iterator() -> None:
    ws = _make_ws_mock()
    sentinel = object()
    ws.executions = MagicMock(return_value=sentinel)
    adapter = _make_adapter(ws=ws)
    result = adapter.stream_executions()
    ws.executions.assert_called_once_with()
    assert result is sentinel


def test_stream_positions_delegates_to_ws_positions_iterator() -> None:
    ws = _make_ws_mock()
    sentinel = object()
    ws.positions = MagicMock(return_value=sentinel)
    adapter = _make_adapter(ws=ws)
    result = adapter.stream_positions()
    ws.positions.assert_called_once_with()
    assert result is sentinel


async def test_adapter_close_calls_ws_close_then_client_close() -> None:
    """ws.close() invoked BEFORE client.close() to drain WS state cleanly."""
    ws = _make_ws_mock()
    client = _make_client_mock()
    client.close = AsyncMock()
    call_order: list[str] = []
    ws.close = AsyncMock(side_effect=lambda: call_order.append("ws"))
    client.close = AsyncMock(side_effect=lambda: call_order.append("client"))
    adapter = _make_adapter(client=client, ws=ws)
    await adapter.close()
    assert call_order == ["ws", "client"]


# --- Side mapping helper ---------------------------------------------------


def test_to_bybit_side_returns_capitalized_literal() -> None:
    assert _to_bybit_side("buy") == "Buy"
    assert _to_bybit_side("sell") == "Sell"


# --- T-208b: get_positions (5 tests) ---------------------------------------


def _position_row(
    *,
    symbol: str = "BTCUSDT",
    side: str = "Buy",
    size: str = "0.5",
    avg_price: str = "65000",
    leverage: str = "10",
    unrealised_pnl: str = "12.34",
    stop_loss: str = "63000",
) -> dict[str, str]:
    return {
        "symbol": symbol,
        "side": side,
        "size": size,
        "avgPrice": avg_price,
        "leverage": leverage,
        "unrealisedPnl": unrealised_pnl,
        "stopLoss": stop_loss,
    }


async def test_get_positions_returns_empty_list_for_empty_response() -> None:
    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": [], "nextPageCursor": ""})
    adapter = _make_adapter(client=client)
    assert await adapter.get_positions() == []


async def test_get_positions_maps_active_position_row_to_position_dataclass() -> None:
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": [_position_row()]})
    adapter = _make_adapter(client=client)
    positions = await adapter.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "BTCUSDT"
    assert p.side == "buy"
    assert p.size == _D("0.5")
    assert p.entry_price == _D("65000")
    assert p.leverage == 10
    assert p.unrealized_pnl == _D("12.34")
    assert p.sl_price == _D("63000")  # T-534a


async def test_get_positions_with_symbol_filter_passes_query_param() -> None:
    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": []})
    adapter = _make_adapter(client=client)
    await adapter.get_positions("BTCUSDT")
    call = client.request.await_args
    assert call.args == ("GET", "/v5/position/list")
    assert call.kwargs["params"] == {"category": "linear", "symbol": "BTCUSDT"}
    assert call.kwargs["retries"] == 3


async def test_get_positions_preserves_qty_string_through_decimal_round_trip() -> None:
    """W#2 H-015 round-trip pin: isinstance + value + str triad."""
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={"list": [_position_row(size="0.500000000001")]},
    )
    adapter = _make_adapter(client=client)
    positions = await adapter.get_positions()
    p = positions[0]
    assert isinstance(p.size, _D)
    assert p.size == _D("0.500000000001")
    assert str(p.size) == "0.500000000001"


async def test_get_positions_maps_flat_row_with_empty_side_to_none_side() -> None:
    """OQ-3 default A: side=='' → None; size==0 + None per T-201 flat-state semantic."""
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                _position_row(
                    side="",
                    size="0",
                    avg_price="",
                    leverage="",
                    unrealised_pnl="",
                    stop_loss="",
                ),
            ],
        },
    )
    adapter = _make_adapter(client=client)
    p = (await adapter.get_positions())[0]
    assert p.side is None
    assert p.size == _D("0")
    assert p.entry_price is None
    assert p.leverage is None
    assert p.unrealized_pnl is None
    assert p.sl_price is None  # T-534a: flat → no SL


async def test_get_positions_preserves_zero_string_in_avg_price_and_unrealised_pnl() -> None:
    """W#3: avgPrice='0' → Decimal('0') (NOT None). Same for unrealisedPnl='0'."""
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                _position_row(side="Buy", avg_price="0", unrealised_pnl="0"),
            ],
        },
    )
    adapter = _make_adapter(client=client)
    p = (await adapter.get_positions())[0]
    assert p.entry_price == _D("0")
    assert p.unrealized_pnl == _D("0")


@pytest.mark.parametrize(
    ("stop_loss", "expected"),
    [
        ("27000.5", Decimal("27000.5")),  # real SL → exact Decimal, no float
        ("", None),  # blank → no SL
        ("0", None),  # Bybit no-SL sentinel
        ("0.00", None),  # Bybit no-SL sentinel
        ("-1", None),  # defensive: non-positive never a real SL
    ],
)
async def test_get_positions_sl_price_decode_golden(
    stop_loss: str,
    expected: Decimal | None,
) -> None:
    """T-534a: stopLoss decode — Decimal(str(...)) no-float; blank/non-
    positive → None (deliberate divergence from avgPrice '0'-preserve W#3;
    an exchange SL at price 0 is semantically impossible = 'no SL set')."""
    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={"list": [_position_row(stop_loss=stop_loss)]},
    )
    adapter = _make_adapter(client=client)
    p = (await adapter.get_positions())[0]
    assert p.sl_price == expected
    if expected is not None:
        assert isinstance(p.sl_price, Decimal)
        assert str(p.sl_price) == "27000.5"  # no float coercion


# --- T-208b: get_fill_price (3 tests) --------------------------------------


async def test_get_fill_price_returns_decimal_from_single_leg_fill() -> None:
    """T-538 / H-035 — single-leg fill: VWAP degenerate case = first row's price."""
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={"list": [{"execPrice": "65032.5", "execQty": "0.01", "execId": "ex-1"}]},
    )
    adapter = _make_adapter(client=client)
    price = await adapter.get_fill_price("BTCUSDT", "ord-abc")
    assert price == _D("65032.5")
    assert isinstance(price, _D)


async def test_get_fill_price_returns_none_when_no_executions() -> None:
    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": []})
    adapter = _make_adapter(client=client)
    assert await adapter.get_fill_price("BTCUSDT", "ord-missing") is None


async def test_get_fill_price_preserves_price_string_through_decimal_round_trip() -> None:
    """W#2 H-015 round-trip pin: isinstance + value + str triad. Single-leg VWAP."""
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={"list": [{"execPrice": "65000.123456789012", "execQty": "0.01"}]},
    )
    adapter = _make_adapter(client=client)
    price = await adapter.get_fill_price("BTCUSDT", "ord-abc")
    assert isinstance(price, _D)
    assert price == _D("65000.123456789012")
    assert str(price) == "65000.123456789012"


async def test_get_fill_price_calls_upstream_with_bybit_v5_query_shape() -> None:
    """T-538 / H-035 — request includes explicit limit=100 (Bybit doc max).

    /v5/execution/list endpoint cap.
    """
    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": []})
    adapter = _make_adapter(client=client)
    await adapter.get_fill_price("BTCUSDT", "ord-abc")
    call = client.request.await_args
    assert call.args == ("GET", "/v5/execution/list")
    assert call.kwargs["params"] == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "orderId": "ord-abc",
        "limit": 100,
    }


async def test_get_fill_price_returns_vwap_for_multi_leg_fill() -> None:
    """T-538 / H-035 — VWAP across all exec items per OQ-1.

    Hand-verified fixture (verbatim across both adapters' tests):
      prices=[100, 101, 99] * qty=[2, 5, 3]
      numerator = 100*2 + 101*5 + 99*3 = 200 + 505 + 297 = 1002
      denominator = 2 + 5 + 3 = 10
      VWAP = 1002 / 10 = 100.2 (exact in Decimal)
    """
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                {"execPrice": "100", "execQty": "2", "execId": "ex-1"},
                {"execPrice": "101", "execQty": "5", "execId": "ex-2"},
                {"execPrice": "99", "execQty": "3", "execId": "ex-3"},
            ],
        },
    )
    adapter = _make_adapter(client=client)
    price = await adapter.get_fill_price("BTCUSDT", "ord-multileg")
    assert price == _D("100.2")
    assert isinstance(price, _D)


async def test_get_fill_price_emits_warning_when_next_page_cursor_present() -> None:
    """T-538 / H-035 — nextPageCursor warns about truncation per OQ-1."""
    from unittest.mock import patch

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [{"execPrice": "100", "execQty": "1"}],
            "nextPageCursor": "abc-cursor-token",
        },
    )
    adapter = _make_adapter(client=client)
    with patch("packages.exchange.bybit_v5.adapter.logger") as logger_mock:
        await adapter.get_fill_price("BTCUSDT", "ord-truncated")
    warn_calls = logger_mock.warning.call_args_list
    warn_keys = [c.args[0] for c in warn_calls]
    assert "bybit_v5.get_fill_price_paginated_truncation" in warn_keys
    truncation_call = next(
        c for c in warn_calls if c.args[0] == "bybit_v5.get_fill_price_paginated_truncation"
    )
    # stdlib logger uses extra={} kwarg per closed_pnl_pagination_capped precedent.
    extra = truncation_call.kwargs["extra"]
    assert extra["symbol"] == "BTCUSDT"
    assert extra["order_id"] == "ord-truncated"
    assert extra["page_size"] == 1


async def test_get_fill_price_zero_total_qty_returns_none_with_warning() -> None:
    """T-538 / H-035 — defensive: zero total qty (malformed Bybit response) → None + warn."""
    from unittest.mock import patch

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [{"execPrice": "100", "execQty": "0"}, {"execPrice": "101", "execQty": "0"}]
        },
    )
    adapter = _make_adapter(client=client)
    with patch("packages.exchange.bybit_v5.adapter.logger") as logger_mock:
        price = await adapter.get_fill_price("BTCUSDT", "ord-zeroqty")
    assert price is None
    warn_keys = [c.args[0] for c in logger_mock.warning.call_args_list]
    assert "bybit_v5.get_fill_price_zero_total_qty" in warn_keys


# --- T-529 / H-036: get_instrument_info (5 tests) -------------------------


async def test_get_instrument_info_calls_upstream_with_market_endpoint_and_category() -> None:
    """HTTP shape: GET /v5/market/instruments-info with category=linear + symbol."""
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                {
                    "lotSizeFilter": {
                        "qtyStep": "0.001",
                        "minOrderQty": "0.001",
                        "minNotionalValue": "5",
                    }
                }
            ]
        }
    )
    adapter = _make_adapter(client=client)
    info = await adapter.get_instrument_info("BTCUSDT")
    call = client.request.await_args
    assert call.args == ("GET", "/v5/market/instruments-info")
    assert call.kwargs["params"] == {"category": "linear", "symbol": "BTCUSDT"}
    assert info.symbol == "BTCUSDT"
    assert info.qty_step == _D("0.001")
    assert info.min_order_qty == _D("0.001")
    assert info.min_notional_usd == _D("5")


async def test_get_instrument_info_caches_within_ttl() -> None:
    """Second call within TTL → no second upstream call (mirror set_leverage cache)."""
    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                {
                    "lotSizeFilter": {
                        "qtyStep": "0.001",
                        "minOrderQty": "0.001",
                        "minNotionalValue": "5",
                    }
                }
            ]
        }
    )
    adapter = _make_adapter(client=client)
    await adapter.get_instrument_info("BTCUSDT")
    await adapter.get_instrument_info("BTCUSDT")
    assert client.request.await_count == 1


async def test_get_instrument_info_re_calls_upstream_after_ttl_expires() -> None:
    """Past TTL → fresh upstream call."""
    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                {
                    "lotSizeFilter": {
                        "qtyStep": "0.001",
                        "minOrderQty": "0.001",
                        "minNotionalValue": "5",
                    }
                }
            ]
        }
    )
    # 1st call writes cache at _FIXED_NOW; 2nd call checks _FIXED_NOW + 4000s (past 3600 TTL).
    # Mirror set_leverage TTL test pattern: list-based time advance, fall-through default.
    times: list[datetime] = [
        _FIXED_NOW,  # call 1: cache write
        _FIXED_NOW + timedelta(seconds=4000),  # call 2: cache check — miss (past 3600s TTL)
        _FIXED_NOW + timedelta(seconds=4000),  # call 2: cache write
    ]

    def fake_now() -> datetime:
        return times.pop(0) if times else _FIXED_NOW + timedelta(seconds=4000)

    adapter = _make_adapter(client=client)
    adapter._now_fn = fake_now
    await adapter.get_instrument_info("BTCUSDT")
    await adapter.get_instrument_info("BTCUSDT")
    assert client.request.await_count == 2


async def test_get_instrument_info_raises_order_rejected_when_instrument_not_found() -> None:
    """Empty list response → OrderRejected (delisted / typo'd symbol)."""
    from packages.exchange.errors import OrderRejected

    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": []})
    adapter = _make_adapter(client=client)
    with pytest.raises(OrderRejected) as exc_info:
        await adapter.get_instrument_info("UNKNOWN")
    assert "UNKNOWN" in str(exc_info.value)


async def test_get_instrument_info_acquires_limiter_with_market_group() -> None:
    """Rate-limit token from 'market' group (separate from orders/positions)."""
    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                {
                    "lotSizeFilter": {
                        "qtyStep": "0.001",
                        "minOrderQty": "0.001",
                        "minNotionalValue": "5",
                    }
                }
            ]
        }
    )
    limiter = _make_limiter_mock()
    adapter = _make_adapter(client=client, limiter=limiter)
    await adapter.get_instrument_info("BTCUSDT")
    limiter.acquire.assert_awaited_once_with("sub-a", "market")


# --- T-208b: get_closed_pnl_cumulative (5 tests) ---------------------------


async def test_get_closed_pnl_cumulative_validates_sub_account_match() -> None:
    """OQ-10/W#5: ValueError BEFORE limiter.acquire — no token consumed on caller mistake."""
    client = _make_client_mock()
    limiter = _make_limiter_mock()
    adapter = _make_adapter(client=client, limiter=limiter)
    with pytest.raises(ValueError, match="sub_account mismatch"):
        await adapter.get_closed_pnl_cumulative("other-sub")
    assert limiter.acquire.await_count == 0
    assert client.request.await_count == 0


async def test_get_closed_pnl_cumulative_sums_single_page_response() -> None:
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                {"closedPnl": "10.50"},
                {"closedPnl": "-2.25"},
                {"closedPnl": "5.00"},
            ],
            "nextPageCursor": "",
        },
    )
    adapter = _make_adapter(client=client)
    total = await adapter.get_closed_pnl_cumulative("sub-a")
    assert total == _D("13.25")  # 10.50 - 2.25 + 5.00


async def test_get_closed_pnl_cumulative_returns_zero_for_empty_response() -> None:
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": [], "nextPageCursor": ""})
    adapter = _make_adapter(client=client)
    total = await adapter.get_closed_pnl_cumulative("sub-a")
    assert total == _D("0")


async def test_get_closed_pnl_cumulative_paginates_via_next_page_cursor() -> None:
    """2-page response → cursor chain → sum across pages."""
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        side_effect=[
            {"list": [{"closedPnl": "10.00"}], "nextPageCursor": "page-2"},
            {"list": [{"closedPnl": "20.00"}], "nextPageCursor": ""},
        ],
    )
    adapter = _make_adapter(client=client)
    total = await adapter.get_closed_pnl_cumulative("sub-a")
    assert total == _D("30.00")
    assert client.request.await_count == 2
    # Page 2 query has cursor.
    page_2_call = client.request.await_args_list[1]
    assert page_2_call.kwargs["params"]["cursor"] == "page-2"


async def test_get_closed_pnl_cumulative_caps_at_max_pages_with_warn_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """W#1: 10-page hypothetical, all non-empty cursors → cap at 10 + WARN log."""
    import logging
    from decimal import Decimal as _D

    client = _make_client_mock()
    limiter = _make_limiter_mock()
    # 10 pages each with closedPnl=1 + non-empty cursor → loop exits via range exhaustion.
    client.request = AsyncMock(
        side_effect=[
            {"list": [{"closedPnl": "1.0"}], "nextPageCursor": f"page-{i + 1}"} for i in range(10)
        ],
    )
    adapter = _make_adapter(client=client, limiter=limiter)
    with caplog.at_level(logging.WARNING, logger="packages.exchange.bybit_v5.adapter"):
        total = await adapter.get_closed_pnl_cumulative("sub-a")
    assert total == _D("10.0")
    assert client.request.await_count == 10
    assert limiter.acquire.await_count == 10
    warn_records = [
        r
        for r in caplog.records
        if r.message == "bybit_v5.closed_pnl_pagination_capped_at_max_pages"
    ]
    assert len(warn_records) == 1


async def test_get_closed_pnl_cumulative_raises_on_empty_closed_pnl_field() -> None:
    """W#4: strict-mode raise on closedPnl='' — no silent default to '0'."""
    from decimal import InvalidOperation

    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={"list": [{"closedPnl": ""}], "nextPageCursor": ""},
    )
    adapter = _make_adapter(client=client)
    with pytest.raises(InvalidOperation):
        await adapter.get_closed_pnl_cumulative("sub-a")


async def test_get_closed_pnl_cumulative_calls_upstream_with_bybit_v5_query_shape() -> None:
    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": [], "nextPageCursor": ""})
    adapter = _make_adapter(client=client)
    await adapter.get_closed_pnl_cumulative("sub-a")
    call = client.request.await_args
    assert call.args == ("GET", "/v5/position/closed-pnl")
    assert call.kwargs["params"] == {"category": "linear", "limit": 200}


# --- T-220a: get_closed_pnl_window time-windowed companion ----------------


async def test_get_closed_pnl_window_passes_starttime_ms_param() -> None:
    """T-220a — `since` datetime → Unix ms via int(timestamp() * 1000)."""
    from datetime import UTC, datetime

    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": [], "nextPageCursor": ""})
    adapter = _make_adapter(client=client)
    since = datetime(2026, 5, 2, 9, 0, 0, tzinfo=UTC)
    await adapter.get_closed_pnl_window("sub-a", since)
    call = client.request.await_args
    assert call.args == ("GET", "/v5/position/closed-pnl")
    assert call.kwargs["params"]["startTime"] == int(since.timestamp() * 1000)
    assert call.kwargs["params"]["category"] == "linear"
    assert call.kwargs["params"]["limit"] == 200


async def test_get_closed_pnl_window_validates_sub_account_before_limiter() -> None:
    """OQ-10/W#5 mirror — ValueError BEFORE limiter.acquire — no token consumed."""
    from datetime import UTC, datetime

    client = _make_client_mock()
    limiter = _make_limiter_mock()
    adapter = _make_adapter(client=client, limiter=limiter)
    since = datetime(2026, 5, 2, 9, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="sub_account mismatch"):
        await adapter.get_closed_pnl_window("other-sub", since)
    assert limiter.acquire.await_count == 0
    assert client.request.await_count == 0


async def test_get_closed_pnl_window_paginates_via_next_page_cursor() -> None:
    from datetime import UTC, datetime
    from decimal import Decimal as _D

    client = _make_client_mock()
    client.request = AsyncMock(
        side_effect=[
            {"list": [{"closedPnl": "10.00"}], "nextPageCursor": "page-2"},
            {"list": [{"closedPnl": "20.00"}], "nextPageCursor": ""},
        ],
    )
    adapter = _make_adapter(client=client)
    since = datetime(2026, 5, 2, 9, 0, 0, tzinfo=UTC)
    total = await adapter.get_closed_pnl_window("sub-a", since)
    assert total == _D("30.00")
    assert client.request.await_count == 2


# --- T-208b: endpoint-group routing + RateLimitError handler --------------


async def test_get_positions_acquires_limiter_with_positions_group() -> None:
    limiter = _make_limiter_mock()
    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": []})
    adapter = _make_adapter(client=client, limiter=limiter)
    await adapter.get_positions()
    limiter.acquire.assert_awaited_once_with("sub-a", "positions")


async def test_get_fill_price_acquires_limiter_with_orders_group() -> None:
    limiter = _make_limiter_mock()
    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": []})
    adapter = _make_adapter(client=client, limiter=limiter)
    await adapter.get_fill_price("BTCUSDT", "ord-abc")
    limiter.acquire.assert_awaited_once_with("sub-a", "orders")


async def test_get_closed_pnl_cumulative_acquires_limiter_with_positions_group() -> None:
    limiter = _make_limiter_mock()
    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": [], "nextPageCursor": ""})
    adapter = _make_adapter(client=client, limiter=limiter)
    await adapter.get_closed_pnl_cumulative("sub-a")
    limiter.acquire.assert_awaited_once_with("sub-a", "positions")


@pytest.mark.parametrize(
    ("method_name", "invoke", "expected_group"),
    [
        ("get_positions", lambda a: a.get_positions(), "positions"),
        ("get_fill_price", lambda a: a.get_fill_price("BTCUSDT", "ord-1"), "orders"),
        (
            "get_closed_pnl_cumulative",
            lambda a: a.get_closed_pnl_cumulative("sub-a"),
            "positions",
        ),
        (
            "get_account_balance",
            lambda a: a.get_account_balance("sub-a"),
            "positions",
        ),
    ],
)
async def test_read_methods_on_rate_limit_error_signal_upstream_and_re_raise(
    method_name: str,
    invoke: object,
    expected_group: str,
) -> None:
    client = _make_client_mock()
    client.request = AsyncMock(side_effect=RateLimitError("retCode=10006"))
    limiter = _make_limiter_mock()
    counter = _make_counter_mock()
    adapter = _make_adapter(client=client, limiter=limiter, counter=counter)
    assert callable(invoke)
    with pytest.raises(RateLimitError):
        await invoke(adapter)
    limiter.signal_upstream_rate_limit.assert_awaited_once()
    counter.labels.assert_called_once_with(exchange="bybit", endpoint_group=expected_group)
    counter.labels.return_value.inc.assert_called_once()
    assert method_name  # silence unused-arg


async def test_get_positions_calls_upstream_with_bybit_v5_query_shape_no_filter() -> None:
    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": []})
    adapter = _make_adapter(client=client)
    await adapter.get_positions()
    call = client.request.await_args
    assert call.args == ("GET", "/v5/position/list")
    assert call.kwargs["params"] == {"category": "linear"}
    assert call.kwargs["retries"] == 3


# --- T-530: get_account_balance (UNIFIED account snapshot) ----------------


async def test_get_account_balance_validates_sub_account_before_limiter() -> None:
    """OQ-10/W: ValueError BEFORE limiter.acquire — caller mistake costs no token.

    L-017 bilateral pin: the mismatch path BOTH raises AND touches neither
    the limiter nor the HTTP client (no token consumed, no request issued).
    """
    client = _make_client_mock()
    limiter = _make_limiter_mock()
    adapter = _make_adapter(client=client, limiter=limiter)
    with pytest.raises(ValueError, match="sub_account mismatch"):
        await adapter.get_account_balance("other-sub")
    assert limiter.acquire.await_count == 0
    assert client.request.await_count == 0


async def test_get_account_balance_decodes_unified_totals_exact_decimal() -> None:
    """Golden Bybit V5 /v5/account/wallet-balance UNIFIED response → 5 exact Decimal.

    Hand-verified: ``list[0]`` account-level totals map 1:1 to the 5
    AccountBalance fields; the negative ``totalPerpUPL`` is preserved as a
    signed Decimal (no float cast, no abs) — a loss-side equity must stay
    negative for T-531 equity snapshots.
    """
    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                {
                    "accountType": "UNIFIED",
                    "totalEquity": "9875.25",
                    "totalWalletBalance": "10000.50",
                    "totalMarginBalance": "10000.50",
                    "totalAvailableBalance": "9875.25",
                    "totalPerpUPL": "-125.2500",
                    "coin": [{"coin": "USDT", "walletBalance": "10000.50"}],
                },
            ],
        },
    )
    adapter = _make_adapter(client=client)
    bal = await adapter.get_account_balance("sub-a")
    assert bal.wallet_balance == Decimal("10000.50")
    assert bal.available_balance == Decimal("9875.25")
    assert bal.total_equity == Decimal("9875.25")
    assert bal.margin_balance == Decimal("10000.50")
    assert bal.unrealized_pnl == Decimal("-125.2500")


async def test_get_account_balance_raises_exchange_error_on_empty_list() -> None:
    """Empty ``result.list`` → ExchangeError.

    A valid authed UNIFIED key always returns ``list[0]``; an empty list is
    an auth/account anomaly, NOT OrderRejected (which is order-semantic).
    """
    from packages.exchange.errors import ExchangeError

    client = _make_client_mock()
    client.request = AsyncMock(return_value={"list": []})
    adapter = _make_adapter(client=client)
    with pytest.raises(ExchangeError, match="empty account list"):
        await adapter.get_account_balance("sub-a")


async def test_get_account_balance_calls_upstream_with_unified_account_type() -> None:
    """Bybit V5 query shape: GET /v5/account/wallet-balance ?accountType=UNIFIED, 3x retry."""
    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                {
                    "totalWalletBalance": "1",
                    "totalAvailableBalance": "1",
                    "totalEquity": "1",
                    "totalMarginBalance": "1",
                    "totalPerpUPL": "0",
                },
            ],
        },
    )
    adapter = _make_adapter(client=client)
    await adapter.get_account_balance("sub-a")
    call = client.request.await_args
    assert call.args == ("GET", "/v5/account/wallet-balance")
    assert call.kwargs["params"] == {"accountType": "UNIFIED"}
    assert call.kwargs["retries"] == 3


async def test_get_account_balance_acquires_limiter_with_positions_group() -> None:
    """Shares the account-level 'positions' bucket with get_closed_pnl_cumulative
    (no new EndpointGroup — /v5/account/* maps to the positions group)."""
    limiter = _make_limiter_mock()
    client = _make_client_mock()
    client.request = AsyncMock(
        return_value={
            "list": [
                {
                    "totalWalletBalance": "1",
                    "totalAvailableBalance": "1",
                    "totalEquity": "1",
                    "totalMarginBalance": "1",
                    "totalPerpUPL": "0",
                },
            ],
        },
    )
    adapter = _make_adapter(client=client, limiter=limiter)
    await adapter.get_account_balance("sub-a")
    limiter.acquire.assert_awaited_once_with("sub-a", "positions")
