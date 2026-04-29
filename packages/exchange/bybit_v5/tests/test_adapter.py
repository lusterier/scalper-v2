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


def _make_adapter(
    *,
    client: MagicMock | None = None,
    limiter: MagicMock | None = None,
    counter: MagicMock | None = None,
    sub_account: str = "sub-a",
    leverage_cache_ttl_s: float = _DEFAULT_LEVERAGE_CACHE_TTL_S,
    now: datetime | None = None,
) -> BybitV5Adapter:
    fixed_now = now or _FIXED_NOW
    return BybitV5Adapter(
        client=client or _make_client_mock(),
        limiter=limiter or _make_limiter_mock(),
        bus=MagicMock(),
        sub_account=sub_account,
        metrics_counter=counter,
        leverage_cache_ttl_s=leverage_cache_ttl_s,
        now_fn=lambda: fixed_now,
    )


# --- Constructor + setup (3 tests) -----------------------------------------


def test_constructor_accepts_required_kwargs() -> None:
    """7-kwarg ctor (5 baseline + leverage_cache_ttl_s + now_fn)."""
    client = _make_client_mock()
    limiter = _make_limiter_mock()
    counter = _make_counter_mock()
    adapter = BybitV5Adapter(
        client=client,
        limiter=limiter,
        bus=MagicMock(),
        sub_account="sub-a",
        metrics_counter=counter,
        leverage_cache_ttl_s=120.0,
        now_fn=lambda: _FIXED_NOW,
    )
    assert adapter._client is client
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


# --- Stub forward-pointers (1 parametrized test, 6 stubs) ------------------


@pytest.mark.parametrize(
    ("method_name", "owner", "invoke"),
    [
        ("get_positions", "T-208b", lambda a: a.get_positions("BTCUSDT")),
        ("get_fill_price", "T-208b", lambda a: a.get_fill_price("BTCUSDT", "ord-1")),
        (
            "get_closed_pnl_cumulative",
            "T-208b",
            lambda a: a.get_closed_pnl_cumulative("sub-a"),
        ),
        ("close", "T-209", lambda a: a.close()),
    ],
)
async def test_stubbed_async_methods_raise_NotImplementedError_with_owner_substring(
    method_name: str,
    owner: str,
    invoke: object,
) -> None:
    """T-211 stub-pin precedent: owner substring is the fail-loud forward-pointer."""
    adapter = _make_adapter()
    assert callable(invoke)
    with pytest.raises(NotImplementedError) as info:
        await invoke(adapter)
    assert method_name in str(info.value)
    assert owner in str(info.value)


@pytest.mark.parametrize("method_name", ["stream_executions", "stream_positions"])
def test_stubbed_stream_methods_raise_NotImplementedError_with_T_209_substring(
    method_name: str,
) -> None:
    """Stream methods are def-not-async-def per T-201 OQ-1; raise synchronously."""
    adapter = _make_adapter()
    method = getattr(adapter, method_name)
    with pytest.raises(NotImplementedError) as info:
        method()
    assert method_name in str(info.value)
    assert "T-209" in str(info.value)


# --- Side mapping helper ---------------------------------------------------


def test_to_bybit_side_returns_capitalized_literal() -> None:
    assert _to_bybit_side("buy") == "Buy"
    assert _to_bybit_side("sell") == "Sell"
