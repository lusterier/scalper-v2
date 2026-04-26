"""Unit tests for :class:`packages.market.backfill.OhlcBackfill` (T-105).

Coverage matrix:

* **Cold-start** — no prior rows in DB → start_time = now - initial_hours,
  REST paginates 1440 candles for a 24h gap across two pages.
* **Gap-fill** — last_bucket exists → start_time = last + 1m.
* **Empty REST page** — loop terminates without re-issuing forever.
* **REST error** — :class:`BinanceRestError` per-symbol → log + skip,
  remaining symbols still run.
* **Persist error** — DB raises mid-page → per-symbol catch logs +
  next symbol runs (no full-batch transactional rollback expected;
  T-104a's ON CONFLICT contract makes per-row idempotency safe).
* **Multi-symbol seriality** — per_symbol_pause_seconds yields
  ``asyncio.sleep`` exactly between symbols (n-1 sleeps for n symbols);
  REST calls happen in submitted order.
* **Decimal precision flow** — Decimal values from REST round-trip into
  ``insert_ohlc_1m`` unchanged (NUMERIC(30, 12) precision floor).
* **Page boundary** — second page's start_time = previous page's last
  bucket + 1m (no overlap, no gap).
* **end_time floor** — never fetches the in-progress current minute.
* **CancelledError** — per-symbol ``except Exception`` does NOT absorb
  :class:`asyncio.CancelledError`; service shutdown propagates.
* **Empty symbols list** — no-op (no REST call, no DB acquire).

The tests use pure-Python fakes (``_FakeRest``, ``_FakePool``) instead
of mock libraries because the assertions need fine-grained call/insert
ordering and the fakes give the cleanest narrative. ``now_utc`` is
patched per-test to a fixed UTC instant so backfill's ``end_time``
floor (``now - 1m`` rounded to minute) is deterministic.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from packages.market import BinanceRestError, OhlcBackfill, OhlcCandle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 4, 26, 12, 30, 45, 123456, tzinfo=UTC)
"""Reference ``now`` used by every test that patches ``now_utc``.

Backfill's end_time floor = ``2026-04-26T12:29:00+00:00`` (one minute
before, rounded to minute). Cold-start at 24h initial_hours yields
start_time = ``2026-04-25T12:29:00+00:00``.
"""


def _bucket(when: datetime) -> datetime:
    return when.replace(second=0, microsecond=0)


def _candle(symbol: str, when: datetime, *, open_: str = "1.0") -> OhlcCandle:
    return OhlcCandle(
        symbol=symbol,
        bucket_start=_bucket(when),
        open=Decimal(open_),
        high=Decimal("2.0"),
        low=Decimal("0.5"),
        close=Decimal("1.5"),
        volume=Decimal("10.0"),
    )


def _logger() -> Any:
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    log = logging.getLogger("test_backfill")
    log.handlers = []
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return structlog.stdlib.get_logger("test_backfill")


class _FakeRest:
    """Scriptable stand-in for :class:`BinanceRestClient`.

    ``script[symbol]`` is a list of return values for successive
    ``get_klines`` calls; each entry is either a list of candles
    (success) or an Exception (raised). A list shorter than the number
    of calls raises :exc:`AssertionError` — keeps tests honest.
    """

    def __init__(
        self,
        script: dict[str, list[list[OhlcCandle] | BaseException]],
    ) -> None:
        self._script = {sym: list(pages) for sym, pages in script.items()}
        self.calls: list[tuple[str, datetime, datetime, int]] = []

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: datetime,
        end_time: datetime,
        limit: int,
    ) -> list[OhlcCandle]:
        assert interval == "1m"
        self.calls.append((symbol, start_time, end_time, limit))
        pages = self._script.get(symbol)
        if not pages:
            msg = f"_FakeRest: no scripted page left for {symbol!r}"
            raise AssertionError(msg)
        page = pages.pop(0)
        if isinstance(page, BaseException):
            raise page
        return page


class _FakeConn:
    def __init__(self, parent: _FakePool) -> None:
        self._parent = parent

    async def fetchval(self, _query: str, *params: object) -> datetime | None:
        symbol = str(params[0])
        source = str(params[1])
        return self._parent.last_bucket.get((symbol, source))

    async def execute(self, _query: str, *params: object) -> None:
        if self._parent.execute_raises is not None:
            raise self._parent.execute_raises
        symbol = str(params[0])
        bucket_start = params[1]
        assert isinstance(bucket_start, datetime)
        self._parent.inserts.append((symbol, bucket_start, params))


class _FakePool:
    """Async-CM-compatible asyncpg.Pool stand-in.

    ``last_bucket[(symbol, source)] = datetime`` seeds the
    ``fetch_latest_ohlc_bucket`` query result; missing keys → ``None``.
    Every persisted insert is recorded in ``inserts`` for assertions.
    Set ``execute_raises = exc`` to fault-inject a DB error.
    """

    def __init__(self) -> None:
        self.last_bucket: dict[tuple[str, str], datetime] = {}
        self.inserts: list[tuple[str, datetime, tuple[object, ...]]] = []
        self.execute_raises: BaseException | None = None
        self.acquire_calls = 0

    def acquire(self) -> _FakePoolAcquire:
        self.acquire_calls += 1
        return _FakePoolAcquire(self)


class _FakePoolAcquire:
    def __init__(self, parent: _FakePool) -> None:
        self._parent = parent

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._parent)

    async def __aexit__(self, *_args: object) -> None:
        return None


def _build_backfill(
    *,
    rest: _FakeRest,
    pool: _FakePool,
    initial_hours: int = 24,
    per_symbol_pause_seconds: float = 0.05,
) -> OhlcBackfill:
    return OhlcBackfill(
        rest=rest,  # type: ignore[arg-type]  # structural fake
        pool=pool,  # type: ignore[arg-type]  # structural fake
        logger=_logger(),
        initial_hours=initial_hours,
        per_symbol_pause_seconds=per_symbol_pause_seconds,
    )


def _candles_minutely(symbol: str, *, start: datetime, count: int) -> list[OhlcCandle]:
    return [_candle(symbol, start + timedelta(minutes=i)) for i in range(count)]


@pytest.fixture(autouse=True)
def _patch_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``packages.market.backfill.now_utc`` to ``_FIXED_NOW``."""
    monkeypatch.setattr(
        "packages.market.backfill.now_utc",
        lambda: _FIXED_NOW,
    )


# ---------------------------------------------------------------------------
# Cold-start: no prior rows
# ---------------------------------------------------------------------------


async def test_cold_start_uses_initial_hours_window() -> None:
    """No DB rows → start_time = end_time - 24h."""
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    expected_start = end_time - timedelta(hours=24)
    # 24h x 60m = 1440 candles. Page 1 returns 1000, page 2 returns 440.
    page1 = _candles_minutely("BTCUSDT", start=expected_start, count=1000)
    page2 = _candles_minutely(
        "BTCUSDT",
        start=page1[-1].bucket_start + timedelta(minutes=1),
        count=440,
    )
    rest = _FakeRest({"BTCUSDT": [page1, page2, []]})
    pool = _FakePool()
    backfill = _build_backfill(rest=rest, pool=pool)
    await backfill.run_for_symbols(["BTCUSDT"])

    assert len(rest.calls) == 2
    first_call = rest.calls[0]
    assert first_call[0] == "BTCUSDT"
    assert first_call[1] == expected_start
    assert first_call[2] == end_time
    assert first_call[3] == 1000

    # 1440 inserts persisted (no overlap, no gap).
    assert len(pool.inserts) == 1440
    persisted_buckets = [b for _sym, b, _ in pool.inserts]
    assert persisted_buckets[0] == expected_start
    assert persisted_buckets[-1] == end_time - timedelta(minutes=1)


async def test_gap_fill_uses_last_bucket_plus_one_minute() -> None:
    """DB has a row → start_time = last_bucket + 1m, not initial-hours fallback."""
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    last_bucket = end_time - timedelta(minutes=10)  # 10-minute gap
    expected_start = last_bucket + timedelta(minutes=1)
    page = _candles_minutely("BTCUSDT", start=expected_start, count=10)

    rest = _FakeRest({"BTCUSDT": [page, []]})
    pool = _FakePool()
    pool.last_bucket[("BTCUSDT", "binance")] = last_bucket
    backfill = _build_backfill(rest=rest, pool=pool)
    await backfill.run_for_symbols(["BTCUSDT"])

    assert rest.calls[0][1] == expected_start
    assert len(pool.inserts) == 10


async def test_no_gap_when_last_bucket_at_end_time_skips_rest() -> None:
    """last_bucket + 1m >= end_time → no REST call, no insert."""
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    pool = _FakePool()
    pool.last_bucket[("BTCUSDT", "binance")] = end_time  # already at floor
    rest = _FakeRest({"BTCUSDT": []})
    backfill = _build_backfill(rest=rest, pool=pool)
    await backfill.run_for_symbols(["BTCUSDT"])

    assert rest.calls == []
    assert pool.inserts == []


# ---------------------------------------------------------------------------
# Page-loop termination
# ---------------------------------------------------------------------------


async def test_empty_rest_page_breaks_the_loop() -> None:
    """REST returns ``[]`` mid-fill → loop breaks (no infinite retry)."""
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    expected_start = end_time - timedelta(hours=24)
    rest = _FakeRest({"BTCUSDT": [[]]})  # first page empty → break
    pool = _FakePool()
    backfill = _build_backfill(rest=rest, pool=pool)
    await backfill.run_for_symbols(["BTCUSDT"])

    assert len(rest.calls) == 1
    assert rest.calls[0][1] == expected_start
    assert pool.inserts == []


async def test_page_boundary_advances_cursor_correctly() -> None:
    """Second page's start_time equals first page's last bucket + 1m."""
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    expected_start = end_time - timedelta(hours=24)
    page1 = _candles_minutely("BTCUSDT", start=expected_start, count=1000)
    page2_start = page1[-1].bucket_start + timedelta(minutes=1)
    page2 = _candles_minutely("BTCUSDT", start=page2_start, count=440)
    rest = _FakeRest({"BTCUSDT": [page1, page2, []]})
    pool = _FakePool()
    backfill = _build_backfill(rest=rest, pool=pool)
    await backfill.run_for_symbols(["BTCUSDT"])

    assert rest.calls[1][1] == page2_start


# ---------------------------------------------------------------------------
# end_time floor — never fetches the in-progress minute
# ---------------------------------------------------------------------------


async def test_end_time_excludes_in_progress_minute() -> None:
    """end_time = now.replace(s=0, us=0) - 1m, not now itself."""
    expected_end = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    rest = _FakeRest({"BTCUSDT": [[]]})
    pool = _FakePool()
    backfill = _build_backfill(rest=rest, pool=pool)
    await backfill.run_for_symbols(["BTCUSDT"])

    assert rest.calls[0][2] == expected_end


# ---------------------------------------------------------------------------
# Per-symbol error isolation
# ---------------------------------------------------------------------------


async def test_rest_error_skips_symbol_and_continues() -> None:
    """REST raises for symbol A → log + skip; B still runs."""
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    expected_start = end_time - timedelta(hours=24)
    page_b = _candles_minutely("ETHUSDT", start=expected_start, count=5)
    rest = _FakeRest(
        {
            "BTCUSDT": [BinanceRestError("boom")],
            "ETHUSDT": [page_b, []],
        },
    )
    pool = _FakePool()
    backfill = _build_backfill(rest=rest, pool=pool, per_symbol_pause_seconds=0)
    await backfill.run_for_symbols(["BTCUSDT", "ETHUSDT"])

    assert len(pool.inserts) == 5
    assert all(insert[0] == "ETHUSDT" for insert in pool.inserts)


async def test_persist_error_skips_symbol_and_continues() -> None:
    """DB raises for A's first page → log + skip; B persists."""
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    expected_start = end_time - timedelta(hours=24)
    page_a = _candles_minutely("BTCUSDT", start=expected_start, count=3)
    page_b = _candles_minutely("ETHUSDT", start=expected_start, count=3)
    rest = _FakeRest({"BTCUSDT": [page_a], "ETHUSDT": [page_b, []]})
    pool = _FakePool()
    pool.execute_raises = RuntimeError("db down")

    # Re-arm: only B's persists must succeed. We toggle execute_raises after
    # the BTCUSDT page lifts the exception. _FakeConn checks the flag at
    # call-time so this works mid-symbol.
    async def run_with_recovery() -> None:
        # First symbol fails; flip flag so second symbol persists.
        original = pool.execute_raises

        async def patched_exec(self: _FakeConn, query: str, *params: object) -> None:
            del self, query
            symbol = str(params[0])
            if symbol == "BTCUSDT" and original is not None:
                raise original
            bucket_start = params[1]
            assert isinstance(bucket_start, datetime)
            pool.inserts.append((symbol, bucket_start, params))

        from unittest.mock import patch

        with patch.object(_FakeConn, "execute", patched_exec):
            await backfill.run_for_symbols(["BTCUSDT", "ETHUSDT"])

    backfill = _build_backfill(rest=rest, pool=pool, per_symbol_pause_seconds=0)
    await run_with_recovery()

    assert all(insert[0] == "ETHUSDT" for insert in pool.inserts)
    assert len(pool.inserts) == 3


# ---------------------------------------------------------------------------
# Multi-symbol seriality + per-symbol pause
# ---------------------------------------------------------------------------


async def test_symbols_processed_serially_in_order() -> None:
    """REST calls land in submitted symbol order — not concurrently."""
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    expected_start = end_time - timedelta(hours=24)
    rest = _FakeRest(
        {sym: [[]] for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT")},
    )
    pool = _FakePool()
    backfill = _build_backfill(rest=rest, pool=pool, per_symbol_pause_seconds=0)
    await backfill.run_for_symbols(["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    call_symbols = [c[0] for c in rest.calls]
    assert call_symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    # Each symbol gets the same start_time given identical cold-start state.
    for call in rest.calls:
        assert call[1] == expected_start


async def test_per_symbol_pause_yields_n_minus_one_sleeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 symbols → exactly 2 inter-symbol sleeps at 50 ms each."""
    sleeps: list[float] = []

    async def _capture(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("packages.market.backfill.asyncio.sleep", _capture)

    rest = _FakeRest({sym: [[]] for sym in ("A", "B", "C")})
    pool = _FakePool()
    backfill = _build_backfill(rest=rest, pool=pool, per_symbol_pause_seconds=0.05)
    await backfill.run_for_symbols(["A", "B", "C"])

    assert sleeps == [0.05, 0.05]


async def test_per_symbol_pause_zero_is_no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """``per_symbol_pause_seconds=0`` short-circuits the inter-symbol pause."""
    sleeps: list[float] = []

    async def _capture(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("packages.market.backfill.asyncio.sleep", _capture)

    rest = _FakeRest({sym: [[]] for sym in ("A", "B")})
    pool = _FakePool()
    backfill = _build_backfill(rest=rest, pool=pool, per_symbol_pause_seconds=0)
    await backfill.run_for_symbols(["A", "B"])

    assert sleeps == []


# ---------------------------------------------------------------------------
# Decimal precision flow
# ---------------------------------------------------------------------------


async def test_decimal_precision_roundtrips_unchanged() -> None:
    """REST ``OhlcCandle`` decimals land in ``insert_ohlc_1m`` params verbatim."""
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    bucket = end_time - timedelta(minutes=1)
    candle = OhlcCandle(
        symbol="BTCUSDT",
        bucket_start=bucket,
        open=Decimal("50000.123456789012"),
        high=Decimal("50100.000000000001"),
        low=Decimal("49950.999999999999"),
        close=Decimal("50050.555555555555"),
        volume=Decimal("123.456789012345"),
    )
    rest = _FakeRest({"BTCUSDT": [[candle], []]})
    pool = _FakePool()
    pool.last_bucket[("BTCUSDT", "binance")] = bucket - timedelta(minutes=1)
    backfill = _build_backfill(rest=rest, pool=pool)
    await backfill.run_for_symbols(["BTCUSDT"])

    assert len(pool.inserts) == 1
    _sym, _bucket_start, params = pool.inserts[0]
    # Order from insert_ohlc_1m: symbol, bucket_start, open, high, low, close, volume, source
    assert params[2] == Decimal("50000.123456789012")
    assert params[3] == Decimal("50100.000000000001")
    assert params[4] == Decimal("49950.999999999999")
    assert params[5] == Decimal("50050.555555555555")
    assert params[6] == Decimal("123.456789012345")
    assert params[7] == "binance"


# ---------------------------------------------------------------------------
# Cancellation propagation
# ---------------------------------------------------------------------------


async def test_cancelled_error_propagates() -> None:
    """``except Exception`` in run_for_symbols does NOT absorb CancelledError."""

    class _CancellingRest:
        async def get_klines(
            self,
            _symbol: str,
            _interval: str,
            *,
            start_time: datetime,
            end_time: datetime,
            limit: int,
        ) -> list[OhlcCandle]:
            del start_time, end_time, limit
            raise asyncio.CancelledError

    pool = _FakePool()
    backfill = OhlcBackfill(
        rest=_CancellingRest(),  # type: ignore[arg-type]
        pool=pool,  # type: ignore[arg-type]
        logger=_logger(),
        per_symbol_pause_seconds=0,
    )
    with pytest.raises(asyncio.CancelledError):
        await backfill.run_for_symbols(["BTCUSDT"])


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


async def test_empty_symbols_is_noop() -> None:
    """``run_for_symbols([])`` issues no REST call, no DB acquire."""
    rest = _FakeRest({})
    pool = _FakePool()
    backfill = _build_backfill(rest=rest, pool=pool)
    await backfill.run_for_symbols([])

    assert rest.calls == []
    assert pool.acquire_calls == 0


async def test_default_constructor_uses_documented_defaults() -> None:
    """Public defaults (24h, 50ms) match the constants pinned in the module."""
    rest = MagicMock()
    rest.get_klines = AsyncMock(return_value=[])
    pool = _FakePool()
    backfill = OhlcBackfill(
        rest=rest,
        pool=pool,  # type: ignore[arg-type]
        logger=_logger(),
    )
    await backfill.run_for_symbols(["BTCUSDT"])
    # No prior rows + 24h default → start_time = end_time - 24h.
    end_time = _FIXED_NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    expected_start = end_time - timedelta(hours=24)
    rest.get_klines.assert_awaited_once_with(
        "BTCUSDT",
        "1m",
        start_time=expected_start,
        end_time=end_time,
        limit=1000,
    )
