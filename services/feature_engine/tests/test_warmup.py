"""Unit tests for :func:`services.feature_engine.app.warmup.warmup_load` (T-110d).

Pure unit tests — no DB. ``BufferRegistry`` is real (T-110a is
pure-domain); pool / cursor / fetch_warmup_window are mocked at the
import boundary. Test 5 (``test_warmup_load_ohlc_row_field_order_locked``)
satisfies T-110d Write-time guidance #4: locks the T-110b ``OhlcRow``
tuple field order so a future T-110b refactor cannot silently break
the warmup mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.features.buffers import BufferRegistry
from services.feature_engine.app.warmup import warmup_load

if TYPE_CHECKING:
    from collections.abc import Sequence

    from packages.features.types import OhlcCandle


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal asyncpg pool stub: ``acquire()`` yields a sentinel conn."""

    def __init__(self) -> None:
        self.conn = MagicMock()

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


class _FakeAcquire:
    def __init__(self, conn: MagicMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> MagicMock:
        return self._conn

    async def __aexit__(self, *_: object) -> None:
        return


class _FakeFeature:
    def __init__(self, *, warmup: int = 5, name: str = "ema_5") -> None:
        self.warmup_candles = warmup
        self.name_template = f"ind.{{symbol}}.{{interval}}.{name}"
        self.source_version = "fake.v1"
        self.interval = "1m"

    def compute(self, candles: Sequence[OhlcCandle]) -> Any:
        msg = "_FakeFeature.compute not used in warmup tests"
        raise NotImplementedError(msg)


def _row(symbol: str, *, minute: int, source: str = "binance") -> tuple[Any, ...]:
    """Construct a synthetic OhlcRow tuple per T-110b field order."""
    return (
        symbol,
        datetime(2026, 4, 26, 11, minute, tzinfo=UTC),
        Decimal("50000"),
        Decimal("50100"),
        Decimal("49900"),
        Decimal("50050"),
        Decimal("1.5"),
        source,
    )


def _logger() -> MagicMock:
    stub = MagicMock()
    for level in ("info", "warning", "error", "debug"):
        setattr(stub, level, MagicMock())
    return stub


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warmup_load_pushes_rows_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows from fetch_warmup_window land in registry in iteration order."""
    pool = _FakePool()
    registry = BufferRegistry({("BTCUSDT", "1m"): 5})
    handle = registry.acquire("BTCUSDT", "1m")  # buffer pre-allocated by caller
    feature = _FakeFeature(warmup=5)
    rows = [_row("BTCUSDT", minute=i) for i in range(3)]
    monkeypatch.setattr(
        "services.feature_engine.app.warmup.fetch_warmup_window",
        AsyncMock(return_value=rows),
    )
    await warmup_load(
        pool=pool,  # type: ignore[arg-type]
        registry=registry,
        features_by_key={("BTCUSDT", "1m"): [("ind.btcusdt.1m.ema_5", feature)]},
        source="binance",
        logger=_logger(),
    )
    snapshot = handle.tail(10)
    assert len(snapshot) == 3
    assert snapshot[0].bucket_start == datetime(2026, 4, 26, 11, 0, tzinfo=UTC)
    assert snapshot[2].bucket_start == datetime(2026, 4, 26, 11, 2, tzinfo=UTC)


@pytest.mark.asyncio
async def test_warmup_load_per_key_error_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_warmup_window raising on key A still loads key B."""
    pool = _FakePool()
    registry = BufferRegistry({("BTCUSDT", "1m"): 3, ("ETHUSDT", "1m"): 3})
    eth_handle = registry.acquire("ETHUSDT", "1m")
    registry.acquire("BTCUSDT", "1m")  # held but not asserted

    async def _fake_fetch(
        _conn: object,
        *,
        symbol: str,
        interval: str,
        n: int,
        source: str,
    ) -> list[tuple[Any, ...]]:
        if symbol == "BTCUSDT":
            msg = "transient db error"
            raise RuntimeError(msg)
        return [_row("ETHUSDT", minute=0)]

    monkeypatch.setattr("services.feature_engine.app.warmup.fetch_warmup_window", _fake_fetch)
    logger = _logger()
    await warmup_load(
        pool=pool,  # type: ignore[arg-type]
        registry=registry,
        features_by_key={
            ("BTCUSDT", "1m"): [("ind.btcusdt.1m.ema_3", _FakeFeature(warmup=3))],
            ("ETHUSDT", "1m"): [("ind.ethusdt.1m.ema_3", _FakeFeature(warmup=3))],
        },
        source="binance",
        logger=logger,
    )
    # ETH loaded; error logged for BTC
    assert len(eth_handle.tail(5)) == 1
    assert any(call.args[0] == "feature_warmup_load_error" for call in logger.error.call_args_list)


@pytest.mark.asyncio
async def test_warmup_load_empty_registry_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty features_by_key → no fetch, no log."""
    fetch_mock = AsyncMock()
    monkeypatch.setattr("services.feature_engine.app.warmup.fetch_warmup_window", fetch_mock)
    pool = _FakePool()
    registry = BufferRegistry({})
    await warmup_load(
        pool=pool,  # type: ignore[arg-type]
        registry=registry,
        features_by_key={},
        source="binance",
        logger=_logger(),
    )
    fetch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_warmup_load_uses_max_warmup_per_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """n passed to fetch_warmup_window is max(feature.warmup_candles)."""
    pool = _FakePool()
    registry = BufferRegistry({("BTCUSDT", "1m"): 50})
    registry.acquire("BTCUSDT", "1m")
    fetch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("services.feature_engine.app.warmup.fetch_warmup_window", fetch_mock)
    feature_a = _FakeFeature(warmup=14, name="ema_14")
    feature_b = _FakeFeature(warmup=50, name="ema_50")
    await warmup_load(
        pool=pool,  # type: ignore[arg-type]
        registry=registry,
        features_by_key={
            ("BTCUSDT", "1m"): [
                ("ind.btcusdt.1m.ema_14", feature_a),
                ("ind.btcusdt.1m.ema_50", feature_b),
            ]
        },
        source="binance",
        logger=_logger(),
    )
    fetch_mock.assert_awaited_once()
    assert fetch_mock.await_args is not None
    assert fetch_mock.await_args.kwargs["n"] == 50


@pytest.mark.asyncio
async def test_warmup_load_ohlc_row_field_order_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write-time guidance #4: lock T-110b OhlcRow tuple field order.

    Asserts that ``OhlcCandle.symbol == row[0]`` and
    ``OhlcCandle.bucket_start == row[1]`` from a real returned tuple
    so a future T-110b refactor that reorders ``OhlcRow`` cannot
    silently break the warmup positional mapping.
    """
    pool = _FakePool()
    registry = BufferRegistry({("BTCUSDT", "1m"): 5})
    handle = registry.acquire("BTCUSDT", "1m")
    row = _row("BTCUSDT", minute=42)  # canonical T-110b OhlcRow shape
    monkeypatch.setattr(
        "services.feature_engine.app.warmup.fetch_warmup_window",
        AsyncMock(return_value=[row]),
    )
    await warmup_load(
        pool=pool,  # type: ignore[arg-type]
        registry=registry,
        features_by_key={("BTCUSDT", "1m"): [("ind.btcusdt.1m.ema_5", _FakeFeature(warmup=5))]},
        source="binance",
        logger=_logger(),
    )
    snapshot = handle.tail(1)
    assert len(snapshot) == 1
    candle = snapshot[0]
    # Field-order lock: positional-to-name mapping per T-110b OhlcRow
    assert candle.symbol == row[0]
    assert candle.bucket_start == row[1]
    assert candle.open == row[2]
    assert candle.close == row[5]
    assert candle.source == row[7]
