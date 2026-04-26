"""Tests for :class:`BufferRegistry` and :class:`BufferHandle` (T-110a).

15 cases mapping 1:1 onto the rows / invariants of the §"Hand
verification — refcount lifecycle trace" table in
``docs/plans/T-110a.md``. Pure unit tests — no DB, no NATS, no
asyncio.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.features.buffers import BufferHandle, BufferRegistry
from packages.features.types import OhlcCandle

_CAPACITY_MAP = {("BTCUSDT", "15m"): 50}


def _make_candle(
    symbol: str = "BTCUSDT",
    interval: str = "15m",
    *,
    minute: int = 0,
) -> OhlcCandle:
    """Construct an :class:`OhlcCandle` with a distinguishable timestamp.

    Field values other than ``symbol``/``interval``/``bucket_start``
    are constants; only the timestamp distinguishes successive
    candles in the buffer-ordering tests.
    """
    return OhlcCandle(
        symbol=symbol,
        interval=interval,
        bucket_start=datetime(2026, 4, 26, 12, 0, tzinfo=UTC) + timedelta(minutes=minute),
        open=Decimal("50000.0"),
        high=Decimal("50100.0"),
        low=Decimal("49900.0"),
        close=Decimal("50050.0"),
        volume=Decimal("1.0"),
        source="binance",
    )


def test_acquire_creates_buffer_with_declared_capacity() -> None:
    """0→1 transition allocates an empty deque sized to capacity_map."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle = registry.acquire("BTCUSDT", "15m")
    assert isinstance(handle, BufferHandle)
    assert handle.symbol == "BTCUSDT"
    assert handle.interval == "15m"
    assert handle.tail(100) == ()


def test_acquire_unknown_key_raises() -> None:
    """Acquire on a key absent from capacity_map raises KeyError."""
    registry = BufferRegistry(_CAPACITY_MAP)
    with pytest.raises(KeyError):
        registry.acquire("ETHUSDT", "15m")


def test_two_acquires_same_key_share_buffer() -> None:
    """Two handles on the same key see the same pushed candle."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle_a = registry.acquire("BTCUSDT", "15m")
    handle_b = registry.acquire("BTCUSDT", "15m")
    candle = _make_candle(minute=0)
    registry.push("BTCUSDT", "15m", candle)
    assert handle_a.tail(1) == (candle,)
    assert handle_b.tail(1) == (candle,)


def test_acquires_different_keys_isolated() -> None:
    """Push to (BTC,15m) does not appear in (BTC,1h) buffer."""
    capacity_map = {("BTCUSDT", "15m"): 50, ("BTCUSDT", "1h"): 50}
    registry = BufferRegistry(capacity_map)
    handle_15m = registry.acquire("BTCUSDT", "15m")
    handle_1h = registry.acquire("BTCUSDT", "1h")
    candle_15m = _make_candle(interval="15m", minute=0)
    registry.push("BTCUSDT", "15m", candle_15m)
    assert handle_15m.tail(1) == (candle_15m,)
    assert handle_1h.tail(1) == ()


def test_refcount_buffer_survives_one_holder_releasing() -> None:
    """H-014: trace steps 1-9. Releasing one of three holders does not dealloc.

    Mirrors ``test_refcount_sub_survives_one_caller_releasing`` from
    T-101c (``packages/market/subscription.py``) generalised from WS
    to in-memory buffer.
    """
    registry = BufferRegistry(_CAPACITY_MAP)
    handle_a = registry.acquire("BTCUSDT", "15m")  # step 1
    handle_b = registry.acquire("BTCUSDT", "15m")  # step 2
    handle_c = registry.acquire("BTCUSDT", "15m")  # step 3
    candle1 = _make_candle(minute=0)
    registry.push("BTCUSDT", "15m", candle1)  # step 4
    assert handle_b.tail(1) == (candle1,)  # step 5
    handle_a.__exit__(None, None, None)  # step 6 — H-014 release
    assert handle_b.tail(1) == (candle1,)  # step 7 — buffer survives
    candle2 = _make_candle(minute=15)
    registry.push("BTCUSDT", "15m", candle2)  # step 8 — push still works
    assert handle_c.tail(2) == (candle1, candle2)
    handle_b.__exit__(None, None, None)  # step 9
    assert handle_c.tail(2) == (candle1, candle2)


def test_release_to_zero_deallocates_buffer() -> None:
    """Trace step 10: 1→0 transition removes key from internal state."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle = registry.acquire("BTCUSDT", "15m")
    registry.push("BTCUSDT", "15m", _make_candle(minute=0))
    handle.__exit__(None, None, None)
    assert registry._counts == {}
    assert registry._buffers == {}


def test_push_with_no_holders_silent_drop() -> None:
    """Trace step 11: push after all holders released is a silent no-op."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle = registry.acquire("BTCUSDT", "15m")
    handle.__exit__(None, None, None)
    registry.push("BTCUSDT", "15m", _make_candle(minute=0))
    assert registry._buffers == {}


def test_push_unknown_key_silent_drop() -> None:
    """Push to a key not in capacity_map raises nothing (defensive)."""
    registry = BufferRegistry(_CAPACITY_MAP)
    registry.push("ETHUSDT", "15m", _make_candle(symbol="ETHUSDT", minute=0))
    assert registry._buffers == {}


def test_tail_returns_immutable_snapshot() -> None:
    """Snapshot is a tuple; later pushes do not mutate earlier reads."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle = registry.acquire("BTCUSDT", "15m")
    candle1 = _make_candle(minute=0)
    registry.push("BTCUSDT", "15m", candle1)
    snapshot = handle.tail(50)
    assert snapshot == (candle1,)
    candle2 = _make_candle(minute=15)
    registry.push("BTCUSDT", "15m", candle2)
    assert snapshot == (candle1,)
    assert isinstance(snapshot, tuple)


def test_tail_underfill_returns_short_tuple() -> None:
    """Buffer with fewer than n candles returns the available prefix."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle = registry.acquire("BTCUSDT", "15m")
    candles = [_make_candle(minute=i * 15) for i in range(3)]
    for candle in candles:
        registry.push("BTCUSDT", "15m", candle)
    assert handle.tail(50) == tuple(candles)


def test_tail_zero_buffer_returns_empty_tuple() -> None:
    """Empty buffer returns ()."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle = registry.acquire("BTCUSDT", "15m")
    assert handle.tail(5) == ()


def test_tail_after_release_raises() -> None:
    """Trace step 12: released handle rejects tail() with RuntimeError."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle = registry.acquire("BTCUSDT", "15m")
    handle.__exit__(None, None, None)
    with pytest.raises(RuntimeError, match="released"):
        handle.tail(1)


def test_double_release_idempotent() -> None:
    """Trace step 13: second __exit__ is a no-op; refcount not double-decremented."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle_a = registry.acquire("BTCUSDT", "15m")
    handle_b = registry.acquire("BTCUSDT", "15m")
    handle_a.__exit__(None, None, None)
    handle_a.__exit__(None, None, None)  # second exit must not decrement
    assert registry._counts[("BTCUSDT", "15m")] == 1
    assert handle_b.tail(0) == ()


def test_capacity_overflow_drops_oldest() -> None:
    """Buffer at maxlen drops the oldest candle on the next push (deque semantics)."""
    registry = BufferRegistry(_CAPACITY_MAP)
    handle = registry.acquire("BTCUSDT", "15m")
    candles = [_make_candle(minute=i * 15) for i in range(51)]
    for candle in candles:
        registry.push("BTCUSDT", "15m", candle)
    snapshot = handle.tail(60)
    assert len(snapshot) == 50
    assert snapshot[0] == candles[1]
    assert snapshot[-1] == candles[50]


def test_acquire_after_dealloc_creates_fresh_buffer() -> None:
    """Trace step 15: re-acquire after full release produces a fresh buffer.

    Tested by behavioural divergence per Write-time guidance #3 —
    push data into the post-dealloc fresh buffer, assert prior data
    is gone. Object-identity check is intentionally NOT used so the
    test asserts the contract (no resurrection of prior state)
    rather than a hash-implementation detail.
    """
    registry = BufferRegistry(_CAPACITY_MAP)
    handle_a = registry.acquire("BTCUSDT", "15m")
    candle_old = _make_candle(minute=0)
    registry.push("BTCUSDT", "15m", candle_old)
    handle_a.__exit__(None, None, None)  # 1→0: dealloc
    handle_b = registry.acquire("BTCUSDT", "15m")  # fresh buffer
    candle_new = _make_candle(minute=15)
    registry.push("BTCUSDT", "15m", candle_new)
    snapshot = handle_b.tail(50)
    assert snapshot == (candle_new,)  # prior candle absent
    assert candle_old not in snapshot
