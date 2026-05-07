"""§N4 unit tests for :mod:`packages.exchange.paper.historical_ohlc_source` (T-503).

Mock-based: ``asyncpg.Pool`` + ``conn.transaction()`` + ``conn.cursor(...)``
async iterator + injectable ``now_fn`` clock + mocked ``asyncio.sleep``.

11 tests covering:

* §A chronological order — 3 rows yield in ASC order at pace=max.
* §B pace="10x" sleeps proportionally (60s sim → ~6s target → ~4s actual sleep).
* §C pace="1x" sleeps full simulated duration.
* §D Decimal preservation on all 5 OHLCV fields per WG#5.
* Symbol filter binds to $1; time window to $2/$3; source filter to $4.
* §E empty symbols → ValueError.
* §F to_at <= from_at → ValueError (parametrize).
* §G invalid source → ValueError.
* pace="max" skips sleep entirely (asyncio.sleep.assert_not_called per WG#2).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.exchange.paper.historical_ohlc_source import (
    HistoricalOHLCSource,
    OHLCRow,
)

_FROM_AT = datetime(2026, 4, 1, tzinfo=UTC)
_TO_AT = datetime(2026, 5, 1, tzinfo=UTC)
_BUCKET_0 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def _make_ohlc_row(
    *,
    symbol: str = "BTCUSDT",
    bucket_start: datetime | None = None,
    open_: Decimal = Decimal("65000"),
    high: Decimal = Decimal("65100"),
    low: Decimal = Decimal("64900"),
    close: Decimal = Decimal("65050"),
    volume: Decimal = Decimal("1.5"),
    source: str = "binance",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "bucket_start": bucket_start or _BUCKET_0,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "source": source,
    }


class _MockCursor:
    """Mock asyncpg cursor — async iterator over canned rows + records bind args."""

    def __init__(self, rows: list[dict[str, Any]], captured: dict[str, Any]) -> None:
        self._rows = rows
        self._captured = captured

    def __call__(
        self,
        sql: str,
        *args: Any,
        prefetch: int | None = None,
    ) -> _MockCursor:
        self._captured["sql"] = sql
        self._captured["args"] = args
        self._captured["prefetch"] = prefetch
        return self

    def __aiter__(self) -> _MockCursor:
        self._iter = iter(self._rows)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


def _make_pool(rows: list[dict[str, Any]], captured: dict[str, Any]) -> MagicMock:
    cursor_callable = _MockCursor(rows, captured)

    @asynccontextmanager
    async def _tx_cm() -> Any:
        yield None

    conn = MagicMock()
    conn.cursor = cursor_callable
    conn.transaction = _tx_cm

    @asynccontextmanager
    async def _acquire_cm() -> Any:
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire_cm
    return pool


async def test_yields_rows_chronologically_at_pace_max() -> None:
    """§A — 3 rows ASC, pace=max yields without sleeping."""
    captured: dict[str, Any] = {}
    rows = [
        _make_ohlc_row(bucket_start=_BUCKET_0),
        _make_ohlc_row(bucket_start=_BUCKET_0 + timedelta(minutes=1)),
        _make_ohlc_row(bucket_start=_BUCKET_0 + timedelta(minutes=2)),
    ]
    pool = _make_pool(rows, captured)
    source = HistoricalOHLCSource(
        pool,
        symbols=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
        pace="max",
    )
    yielded = [ohlc async for ohlc in source]
    assert len(yielded) == 3
    assert all(isinstance(o, OHLCRow) for o in yielded)
    assert yielded[0].bucket_start == _BUCKET_0
    assert yielded[2].bucket_start == _BUCKET_0 + timedelta(minutes=2)


async def test_filters_by_symbols_via_bind_args() -> None:
    """Symbol universe binds to $1 (ANY array)."""
    captured: dict[str, Any] = {}
    pool = _make_pool([], captured)
    source = HistoricalOHLCSource(
        pool,
        symbols=["BTCUSDT", "ETHUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
    )
    _ = [o async for o in source]
    assert captured["args"][0] == ["BTCUSDT", "ETHUSDT"]


async def test_time_window_half_open_interval() -> None:
    """from_at + to_at bind to $2 + $3."""
    captured: dict[str, Any] = {}
    pool = _make_pool([], captured)
    source = HistoricalOHLCSource(
        pool,
        symbols=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
    )
    _ = [o async for o in source]
    assert captured["args"][1] == _FROM_AT
    assert captured["args"][2] == _TO_AT


async def test_source_filter_binds_to_arg_4() -> None:
    """source binds to $4."""
    captured: dict[str, Any] = {}
    pool = _make_pool([], captured)
    source = HistoricalOHLCSource(
        pool,
        symbols=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
        source="bybit",
    )
    _ = [o async for o in source]
    assert captured["args"][3] == "bybit"


async def test_pace_max_skips_sleep_entirely() -> None:
    """WG#2: pace='max' → _pace_factor != inf branch skipped → asyncio.sleep NEVER called."""
    captured: dict[str, Any] = {}
    rows = [
        _make_ohlc_row(bucket_start=_BUCKET_0),
        _make_ohlc_row(bucket_start=_BUCKET_0 + timedelta(minutes=1)),
    ]
    pool = _make_pool(rows, captured)
    source = HistoricalOHLCSource(
        pool,
        symbols=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
        pace="max",
    )
    with patch(
        "packages.exchange.paper.historical_ohlc_source.asyncio.sleep",
        new=AsyncMock(),
    ) as sleep_mock:
        _ = [o async for o in source]
    sleep_mock.assert_not_called()


async def test_pace_10x_sleeps_proportionally() -> None:
    """§B — pace='10x' with 60s simulated gap → target=6s, elapsed=2s → sleep=4s."""
    captured: dict[str, Any] = {}
    rows = [
        _make_ohlc_row(bucket_start=_BUCKET_0),
        _make_ohlc_row(bucket_start=_BUCKET_0 + timedelta(seconds=60)),
    ]
    pool = _make_pool(rows, captured)

    # Mock now_fn: replay starts at t0; on second yield, t0 + 2s.
    real_t0 = datetime(2030, 1, 1, tzinfo=UTC)
    clock_calls = iter([real_t0, real_t0 + timedelta(seconds=2)])

    def fake_now() -> datetime:
        return next(clock_calls)

    source = HistoricalOHLCSource(
        pool,
        symbols=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
        pace="10x",
        now_fn=fake_now,
    )
    with patch(
        "packages.exchange.paper.historical_ohlc_source.asyncio.sleep",
        new=AsyncMock(),
    ) as sleep_mock:
        _ = [o async for o in source]
    # sim_dt=60s ; target=6s ; elapsed=2s ; sleep_for=4s.
    sleep_mock.assert_called_once()
    sleep_arg = sleep_mock.call_args.args[0]
    assert sleep_arg == pytest.approx(4.0)


async def test_pace_1x_sleeps_full_simulated_duration() -> None:
    """§C — pace='1x' with 60s simulated gap → target=60s, elapsed=0 → sleep=60s."""
    captured: dict[str, Any] = {}
    rows = [
        _make_ohlc_row(bucket_start=_BUCKET_0),
        _make_ohlc_row(bucket_start=_BUCKET_0 + timedelta(seconds=60)),
    ]
    pool = _make_pool(rows, captured)

    real_t0 = datetime(2030, 1, 1, tzinfo=UTC)
    # On second yield, no real time passed.
    clock_calls = iter([real_t0, real_t0])

    def fake_now() -> datetime:
        return next(clock_calls)

    source = HistoricalOHLCSource(
        pool,
        symbols=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
        pace="1x",
        now_fn=fake_now,
    )
    with patch(
        "packages.exchange.paper.historical_ohlc_source.asyncio.sleep",
        new=AsyncMock(),
    ) as sleep_mock:
        _ = [o async for o in source]
    sleep_mock.assert_called_once()
    sleep_arg = sleep_mock.call_args.args[0]
    assert sleep_arg == pytest.approx(60.0)


async def test_decimal_ohlcv_preserved_on_all_5_fields() -> None:
    """§D + WG#5 — 12-fractional-digit Decimal preserved across all 5 OHLCV fields."""
    captured: dict[str, Any] = {}
    o = Decimal("65000.123456789012")
    h = Decimal("65010.987654321098")
    lo = Decimal("64990.111111111111")
    c = Decimal("65005.555555555555")
    v = Decimal("1.234567890123")
    rows = [_make_ohlc_row(open_=o, high=h, low=lo, close=c, volume=v)]
    pool = _make_pool(rows, captured)
    source = HistoricalOHLCSource(
        pool,
        symbols=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
    )
    yielded = [ohlc async for ohlc in source]
    row = yielded[0]
    # WG#5 — isinstance Decimal on all 5 OHLCV fields.
    assert all(isinstance(p, Decimal) for p in (row.open, row.high, row.low, row.close, row.volume))
    assert row.open == o
    assert row.high == h
    assert row.low == lo
    assert row.close == c
    assert row.volume == v


def test_empty_symbols_raises_value_error() -> None:
    """§E — empty symbols raises ValueError at construction."""
    pool = MagicMock()
    with pytest.raises(ValueError, match="symbols must not be empty"):
        HistoricalOHLCSource(
            pool,
            symbols=[],
            from_at=_FROM_AT,
            to_at=_TO_AT,
        )


@pytest.mark.parametrize(
    ("from_at", "to_at"),
    [
        (_FROM_AT, _FROM_AT),  # equal
        (_TO_AT, _FROM_AT),  # reversed
    ],
)
def test_to_at_not_after_from_at_raises_value_error(from_at: datetime, to_at: datetime) -> None:
    """§F — to_at must be strictly > from_at."""
    pool = MagicMock()
    with pytest.raises(ValueError, match="to_at must be > from_at"):
        HistoricalOHLCSource(
            pool,
            symbols=["BTCUSDT"],
            from_at=from_at,
            to_at=to_at,
        )


def test_invalid_source_raises_value_error() -> None:
    """§G — source must be 'binance' or 'bybit'."""
    pool = MagicMock()
    with pytest.raises(ValueError, match="invalid source"):
        HistoricalOHLCSource(
            pool,
            symbols=["BTCUSDT"],
            from_at=_FROM_AT,
            to_at=_TO_AT,
            source="kraken",
        )
