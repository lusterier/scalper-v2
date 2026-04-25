"""Unit tests for :class:`packages.market.rest.BinanceRestClient`.

Uses ``httpx.MockTransport`` to inject canned responses without
hitting the network — fast, deterministic, no external dependency.
The transport is mounted on a dedicated client constructed in each
test rather than via the production ``BinanceRestClient`` so we
exercise the real ``BinanceRestClient.get_klines`` parsing path
against the mocked transport.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from packages.market import BinanceRestError
from packages.market.rest import BinanceRestClient

_SAMPLE_KLINE_ROW: list[object] = [
    1499040000000,  # open time (ms) — 2017-07-03 00:00:00 UTC
    "0.01634790",
    "0.80000000",
    "0.01575800",
    "0.01577100",
    "148976.11427815",
    1499040059999,  # close time (ms)
    "2434.19055334",
    308,
    "1756.87402397",
    "28.46694368",
    "17928899.62484339",
]


def _make_client(handler: httpx.MockTransport) -> BinanceRestClient:
    """Build a `BinanceRestClient` whose underlying httpx client uses `handler`."""
    client = BinanceRestClient()
    # Replace the production AsyncClient with one wired to the mock transport.
    # Closing the original first avoids a leak warning at GC time.
    raw = client._client
    client._client = httpx.AsyncClient(
        base_url="https://api.binance.com",
        transport=handler,
    )
    # Schedule the original close on event-loop next-tick equivalent — we
    # discard the coroutine since it's a no-op on a never-used client.
    raw.aclose  # touched to keep type-checkers honest, awaited at GC.
    return client


async def test_get_klines_parses_response_into_ohlc_candles() -> None:
    """Each Binance kline row → one :class:`OhlcCandle` with Decimal prices."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/klines"
        return httpx.Response(200, json=[_SAMPLE_KLINE_ROW])

    async with _make_client(httpx.MockTransport(handler)) as client:
        candles = await client.get_klines("BTCUSDT", "1m")

    assert len(candles) == 1
    candle = candles[0]
    assert candle.symbol == "BTCUSDT"
    assert candle.bucket_start == datetime(2017, 7, 3, 0, 0, 0, tzinfo=UTC)
    assert candle.open == Decimal("0.01634790")
    assert candle.high == Decimal("0.80000000")
    assert candle.low == Decimal("0.01575800")
    assert candle.close == Decimal("0.01577100")
    assert candle.volume == Decimal("148976.11427815")
    assert candle.source == "binance"


async def test_get_klines_serializes_time_window_and_limit() -> None:
    """`start_time`/`end_time`/`limit` flow into query params correctly."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    async with _make_client(httpx.MockTransport(handler)) as client:
        await client.get_klines(
            "BTCUSDT",
            "1m",
            start_time=datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC),
            end_time=datetime(2026, 4, 25, 13, 0, 0, tzinfo=UTC),
            limit=1000,
        )

    assert captured["symbol"] == "BTCUSDT"
    assert captured["interval"] == "1m"
    assert captured["limit"] == "1000"
    assert captured["startTime"] == "1777118400000"  # ms epoch for 2026-04-25 12:00 UTC
    assert captured["endTime"] == "1777122000000"  # ms epoch for 2026-04-25 13:00 UTC


async def test_get_klines_default_limit_is_500() -> None:
    """When `limit` is omitted, the default 500 is sent."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    async with _make_client(httpx.MockTransport(handler)) as client:
        await client.get_klines("BTCUSDT", "1m")

    assert captured["limit"] == "500"


async def test_get_klines_naive_datetime_treated_as_utc() -> None:
    """A `tzinfo=None` datetime is interpreted as UTC, not local time."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    async with _make_client(httpx.MockTransport(handler)) as client:
        await client.get_klines(
            "BTCUSDT",
            "1m",
            start_time=datetime(2026, 4, 25, 12, 0, 0),  # noqa: DTZ001 — explicit naive test
        )

    assert captured["startTime"] == "1777118400000"


async def test_get_klines_4xx_with_api_error_body() -> None:
    """4xx with Binance JSON body surfaces api_code + api_message on the error."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    async with _make_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(BinanceRestError) as excinfo:
            await client.get_klines("BAD", "1m")

    assert excinfo.value.api_code == -1121
    assert excinfo.value.api_message == "Invalid symbol."
    assert "400" in str(excinfo.value)
    assert "-1121" in str(excinfo.value)


async def test_get_klines_5xx_without_json_body() -> None:
    """5xx with non-JSON body raises BinanceRestError, api_code=None."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"Service Unavailable")

    async with _make_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(BinanceRestError) as excinfo:
            await client.get_klines("BTCUSDT", "1m")

    assert excinfo.value.api_code is None
    assert excinfo.value.api_message is None
    assert "503" in str(excinfo.value)


async def test_get_klines_transport_error_chains_underlying() -> None:
    """Transport failure raises BinanceRestError with httpx.HTTPError as __cause__."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure")

    async with _make_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(BinanceRestError) as excinfo:
            await client.get_klines("BTCUSDT", "1m")

    assert excinfo.value.api_code is None
    assert isinstance(excinfo.value.__cause__, httpx.HTTPError)
    assert "transport error" in str(excinfo.value)
