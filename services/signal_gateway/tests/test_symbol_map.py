"""Tests for :class:`services.signal_gateway.app.symbol_map.SymbolMapCache`."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from services.signal_gateway.app.symbol_map import SymbolMapCache


class _Clock:
    """Mutable-time clock stub for deterministic TTL tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def set(self, t: float) -> None:
        self._now = t


def _make_mock_pool(mapping: dict[str, str | None]) -> MagicMock:
    """Build an asyncpg.Pool stand-in whose fetchrow returns rows for ``mapping``.

    ``mapping[input_symbol] -> canonical_symbol | None``. Keys absent from
    the dict are treated as DB rows that don't exist (fetchrow → None).
    """
    conn = MagicMock()

    async def _fetchrow(_sql: str, input_symbol: str) -> dict[str, str] | None:
        if input_symbol in mapping:
            value = mapping[input_symbol]
            return {"canonical_symbol": value} if value is not None else None
        return None

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _fetchrow_count(pool: MagicMock) -> int:
    """Shortcut for the nested mock path to the fetchrow call counter."""
    conn = pool.acquire.return_value.__aenter__.return_value
    return int(conn.fetchrow.await_count)


async def test_miss_populates_cache() -> None:
    pool = _make_mock_pool({"BTCUSDT.P": "BTCUSDT"})
    cache = SymbolMapCache(pool, ttl_seconds=60.0, clock=_Clock())
    assert await cache.resolve("BTCUSDT.P") == "BTCUSDT"
    assert _fetchrow_count(pool) == 1


async def test_hit_within_ttl_skips_db() -> None:
    pool = _make_mock_pool({"BTCUSDT.P": "BTCUSDT"})
    clock = _Clock()
    cache = SymbolMapCache(pool, ttl_seconds=60.0, clock=clock)
    assert await cache.resolve("BTCUSDT.P") == "BTCUSDT"
    clock.set(30.0)  # half-way through TTL
    assert await cache.resolve("BTCUSDT.P") == "BTCUSDT"
    assert _fetchrow_count(pool) == 1  # no re-query


async def test_miss_after_ttl_re_queries() -> None:
    pool = _make_mock_pool({"BTCUSDT.P": "BTCUSDT"})
    clock = _Clock()
    cache = SymbolMapCache(pool, ttl_seconds=60.0, clock=clock)
    await cache.resolve("BTCUSDT.P")
    clock.set(61.0)  # past TTL
    await cache.resolve("BTCUSDT.P")
    assert _fetchrow_count(pool) == 2


async def test_negative_result_is_cached() -> None:
    """Unknown symbol cached as None — adversarial unknown streams don't hammer PG."""
    pool = _make_mock_pool({})
    cache = SymbolMapCache(pool, ttl_seconds=60.0, clock=_Clock())
    assert await cache.resolve("UNKNOWN") is None
    assert await cache.resolve("UNKNOWN") is None
    assert _fetchrow_count(pool) == 1


async def test_distinct_keys_cached_independently() -> None:
    pool = _make_mock_pool({"BTCUSDT.P": "BTCUSDT", "ETHUSDT.P": "ETHUSDT"})
    cache = SymbolMapCache(pool, ttl_seconds=60.0, clock=_Clock())
    assert await cache.resolve("BTCUSDT.P") == "BTCUSDT"
    assert await cache.resolve("ETHUSDT.P") == "ETHUSDT"
    # Re-query BTC — served from cache; ETH still cached too.
    assert await cache.resolve("BTCUSDT.P") == "BTCUSDT"
    assert _fetchrow_count(pool) == 2  # one per distinct key, no repeats


async def test_explicitly_none_value_in_db_is_negative_cached() -> None:
    """Guards against the dict-has-key-but-value-None case in ``_make_mock_pool``."""
    pool = _make_mock_pool({"LEGACY.P": None})
    cache = SymbolMapCache(pool, ttl_seconds=60.0, clock=_Clock())
    assert await cache.resolve("LEGACY.P") is None
    assert await cache.resolve("LEGACY.P") is None
    assert _fetchrow_count(pool) == 1
