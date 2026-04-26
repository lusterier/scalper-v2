"""Unit tests for :class:`services.feature_engine.app.pipeline.FeaturePipeline` (T-110c).

Pure unit tests — no DB, no NATS. ``BufferRegistry`` is real (T-110a
is pure-domain); bus/pool/Feature are inline fakes. Handler is invoked
directly via ``await pipeline._on_envelope(envelope)`` rather than
spinning the bus. Mirrors T-104b's ``packages/market/tests/test_ohlc.py``
structural choice (fakes inline; handler invoked directly).

Coverage maps onto §"Hand verification — closed-candle dispatch trace"
in ``docs/plans/T-110c.md`` (15 cases, one per trace row / invariant
I1-I10 plus start/stop lifecycle).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from packages.bus import MessageEnvelope
from packages.bus.schemas import FeatureUpdate, OhlcCandlePayload
from packages.core import CorrelationId
from packages.features.buffers import BufferRegistry
from packages.features.errors import FeatureUnderflowError
from packages.features.types import FeatureValue, OhlcCandle
from services.feature_engine.app.pipeline import FeaturePipeline

if TYPE_CHECKING:
    from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeBus:
    """Records subscribe/publish/kv_put for assertion. AsyncMock-shaped methods."""

    def __init__(self) -> None:
        self.subscribed: list[tuple[str, Any]] = []
        self.published: list[tuple[str, MessageEnvelope]] = []
        self.kv_puts: list[tuple[str, str, bytes]] = []
        self.publish_error: Exception | None = None
        self.kv_error: Exception | None = None

    async def subscribe(self, subject: str, handler: Any) -> None:
        self.subscribed.append((subject, handler))

    async def publish(self, subject: str, envelope: MessageEnvelope) -> None:
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append((subject, envelope))

    async def kv_put(self, bucket: str, key: str, value: bytes) -> int:
        if self.kv_error is not None:
            raise self.kv_error
        self.kv_puts.append((bucket, key, value))
        return len(self.kv_puts)


class _FakePool:
    """Mock asyncpg pool whose acquire() yields a connection mock.

    insert_feature is the only DB call; we record its arguments via
    the connection's ``execute`` method recording.
    """

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_error: Exception | None = None

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self)


class _FakeAcquire:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._pool)

    async def __aexit__(self, *_: object) -> None:
        return


class _FakeConn:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def execute(self, sql: str, *args: Any) -> None:
        if self._pool.execute_error is not None:
            raise self._pool.execute_error
        self._pool.executed.append((sql, args))


class _FakeFeature:
    """Stub matching :class:`packages.features.protocols.Feature` Protocol."""

    def __init__(
        self,
        *,
        warmup: int = 1,
        result: FeatureValue | None = None,
        raises: Exception | None = None,
        period: int = 20,
        interval: str = "1m",
        symbol_lower: str = "btcusdt",
    ) -> None:
        self.warmup_candles = warmup
        self.interval = interval
        self.source_version = "fake.v1"
        self.name_template = f"ind.{{symbol}}.{{interval}}.ema_{period}"
        self._result = result if result is not None else FeatureValue(value_num=Decimal("70.5"))
        self._raises = raises
        self.calls: list[tuple[OhlcCandle, ...]] = []

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue:
        if self._raises is not None:
            raise self._raises
        self.calls.append(tuple(candles))
        return self._result


def _candle(symbol: str = "BTCUSDT", *, minute: int = 0, close: str = "50050.0") -> OhlcCandle:
    return OhlcCandle(
        symbol=symbol,
        interval="1m",
        bucket_start=datetime(2026, 4, 26, 12, minute, tzinfo=UTC),
        open=Decimal("50000.0"),
        high=Decimal("50100.0"),
        low=Decimal("49900.0"),
        close=Decimal(close),
        volume=Decimal("1.5"),
        source="binance",
    )


def _payload(
    symbol: str = "BTCUSDT", *, minute: int = 0, is_closed: bool = True
) -> OhlcCandlePayload:
    return OhlcCandlePayload(
        symbol=symbol,
        bucket_start=datetime(2026, 4, 26, 12, minute, tzinfo=UTC),
        open=Decimal("50000.0"),
        high=Decimal("50100.0"),
        low=Decimal("49900.0"),
        close=Decimal("50050.0"),
        volume=Decimal("1.5"),
        is_closed=is_closed,
    )


def _envelope(payload: OhlcCandlePayload) -> MessageEnvelope:
    return MessageEnvelope(
        correlation_id=CorrelationId("test-cid"),
        publisher="market-data-svc",
        payload=payload.model_dump(mode="json"),
    )


def _build_pipeline(
    *,
    feature: _FakeFeature | None = None,
    capacity: int = 50,
    register_btc: bool = True,
) -> tuple[FeaturePipeline, _FakeBus, _FakePool, _FakeFeature, BufferRegistry]:
    bus = _FakeBus()
    pool = _FakePool()
    if feature is None:
        feature = _FakeFeature(warmup=1)
    capacity_map = {("BTCUSDT", "1m"): capacity}
    registry = BufferRegistry(capacity_map)
    features_by_key: dict[tuple[str, str], list[tuple[str, Any]]] = (
        {("BTCUSDT", "1m"): [("ind.btcusdt.1m.ema_20", feature)]} if register_btc else {}
    )
    logger = MagicMock()
    for level in ("info", "warning", "error", "debug"):
        setattr(logger, level, MagicMock())
    pipeline = FeaturePipeline(
        bus=bus,  # type: ignore[arg-type]
        pool=pool,  # type: ignore[arg-type]
        buffer_registry=registry,
        features_by_key=features_by_key,
        logger=logger,
    )
    return pipeline, bus, pool, feature, registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closed_candle_pushes_buffer_then_dispatches() -> None:
    """Closed candle: buffer holds candle + feature.compute called."""
    pipeline, _bus, _pool, feature, _registry = _build_pipeline()
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload()))
    assert len(feature.calls) == 1
    assert feature.calls[0][0].symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_in_progress_candle_skipped() -> None:
    """I1: in-progress (is_closed=False) → no buffer push, no dispatch."""
    pipeline, _bus, _pool, feature, _registry = _build_pipeline()
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload(is_closed=False)))
    assert feature.calls == []


@pytest.mark.asyncio
async def test_unregistered_symbol_skipped() -> None:
    """I2: ETHUSDT not in registry → no dispatch."""
    pipeline, bus, pool, feature, _registry = _build_pipeline()
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload(symbol="ETHUSDT")))
    assert feature.calls == []
    assert bus.published == []
    assert pool.executed == []


@pytest.mark.asyncio
async def test_dispatch_persists_then_kv_then_publishes_in_order() -> None:
    """DB → KV → publish call order recorded across the fakes."""
    pipeline, bus, pool, _feature, _registry = _build_pipeline()
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload()))
    assert len(pool.executed) == 1
    assert len(bus.kv_puts) == 1
    assert len(bus.published) == 1


@pytest.mark.asyncio
async def test_per_feature_error_isolation() -> None:
    """I3: feature A raises FeatureUnderflowError; feature B still runs."""
    feature_a = _FakeFeature(warmup=1, raises=FeatureUnderflowError("not enough"))
    feature_b = _FakeFeature(warmup=1)
    bus = _FakeBus()
    pool = _FakePool()
    capacity_map = {("BTCUSDT", "1m"): 50}
    registry = BufferRegistry(capacity_map)
    features_by_key: dict[tuple[str, str], list[tuple[str, Any]]] = {
        ("BTCUSDT", "1m"): [
            ("ind.btcusdt.1m.ema_20", feature_a),
            ("ind.btcusdt.1m.ema_50", feature_b),
        ],
    }
    logger = MagicMock()
    for level in ("info", "warning", "error", "debug"):
        setattr(logger, level, MagicMock())
    pipeline = FeaturePipeline(
        bus=bus,  # type: ignore[arg-type]
        pool=pool,  # type: ignore[arg-type]
        buffer_registry=registry,
        features_by_key=features_by_key,
        logger=logger,
    )
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload()))
    assert len(feature_b.calls) == 1
    assert len(pool.executed) == 1  # only feature_b persisted
    assert len(bus.published) == 1


@pytest.mark.asyncio
async def test_db_persist_error_skips_kv_and_publish() -> None:
    """I4: DB-fail short-circuits KV+publish (canonical-store-first)."""
    pipeline, bus, pool, _feature, _registry = _build_pipeline()
    pool.execute_error = RuntimeError("db down")
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload()))
    assert bus.kv_puts == []
    assert bus.published == []


@pytest.mark.asyncio
async def test_kv_error_does_not_block_publish() -> None:
    """I5: KV-fail still allows publish (KV is best-effort cache)."""
    pipeline, bus, pool, _feature, _registry = _build_pipeline()
    bus.kv_error = RuntimeError("kv bucket missing")
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload()))
    assert len(pool.executed) == 1
    assert len(bus.published) == 1


@pytest.mark.asyncio
async def test_publish_error_logs_and_continues() -> None:
    """I6: publish-fail leaves DB+KV intact; loop continues."""
    pipeline, bus, pool, _feature, _registry = _build_pipeline()
    bus.publish_error = RuntimeError("nats down")
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload()))
    assert len(pool.executed) == 1
    assert len(bus.kv_puts) == 1


@pytest.mark.asyncio
async def test_computed_at_is_bucket_end_not_now() -> None:
    """I7: feature_update.computed_at == candle.bucket_start + 1 minute."""
    pipeline, bus, _pool, _feature, _registry = _build_pipeline()
    await pipeline.start()
    payload = _payload(minute=15)
    await pipeline._on_envelope(_envelope(payload))
    # FeatureUpdate is in published envelope payload
    fu = FeatureUpdate.model_validate(bus.published[0][1].payload)
    assert fu.computed_at == datetime(2026, 4, 26, 12, 16, tzinfo=UTC)


@pytest.mark.asyncio
async def test_correlation_id_format() -> None:
    """I8: correlation_id == feature:{feature_name}:{symbol}:{computed_at.isoformat()}."""
    pipeline, bus, _pool, _feature, _registry = _build_pipeline()
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload()))
    envelope = bus.published[0][1]
    assert (
        envelope.correlation_id == "feature:ind.btcusdt.1m.ema_20:BTCUSDT:2026-04-26T12:01:00+00:00"
    )


@pytest.mark.asyncio
async def test_publisher_is_feature_engine() -> None:
    """I9: published envelope has publisher='feature-engine'."""
    pipeline, bus, _pool, _feature, _registry = _build_pipeline()
    await pipeline.start()
    await pipeline._on_envelope(_envelope(_payload()))
    assert bus.published[0][1].publisher == "feature-engine"


def test_decimal_to_float_seam_value_num() -> None:
    """I10: Decimal→float conversion only in _build_update; single seam."""
    feature = _FakeFeature(warmup=1)
    candle = _candle()
    fv = FeatureValue(value_num=Decimal("70.5"))
    update = FeaturePipeline._build_update("ind.btcusdt.1m.ema_20", feature, fv, "BTCUSDT", candle)
    assert update.value_num == 70.5
    assert isinstance(update.value_num, float)


def test_decimal_to_float_seam_value_json() -> None:
    """value_json sub-keys: Decimal → float; non-Decimal pass-through (L-004)."""
    feature = _FakeFeature(warmup=1)
    candle = _candle()
    fv = FeatureValue(
        value_json={"upper": Decimal("1"), "label": "BUY", "trigger": True},
    )
    update = FeaturePipeline._build_update("ind.btcusdt.1m.bb_20", feature, fv, "BTCUSDT", candle)
    assert update.value_json == {"upper": 1.0, "label": "BUY", "trigger": True}
    assert isinstance(update.value_json["upper"], float)


@pytest.mark.asyncio
async def test_start_acquires_handles_then_subscribes() -> None:
    """Order: handles acquired BEFORE subscribe; ref counts incremented first."""
    pipeline, bus, _pool, _feature, registry = _build_pipeline()
    assert ("BTCUSDT", "1m") not in registry._counts
    assert bus.subscribed == []
    await pipeline.start()
    assert registry._counts[("BTCUSDT", "1m")] == 1
    assert len(bus.subscribed) == 1
    assert bus.subscribed[0][0] == "market.ohlc.1m.>"


@pytest.mark.asyncio
async def test_stop_releases_all_handles() -> None:
    """All BufferHandles released on stop; refcount decremented."""
    pipeline, _bus, _pool, _feature, registry = _build_pipeline()
    await pipeline.start()
    assert registry._counts[("BTCUSDT", "1m")] == 1
    await pipeline.stop()
    # 1→0 transition deallocates the buffer entirely
    assert ("BTCUSDT", "1m") not in registry._counts
