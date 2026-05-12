"""§N4 unit tests for :mod:`services.feature_engine.app.auto_backfill` (T-518).

Mock-based: ``bus.kv_get`` / ``kv_put`` + ``conn.fetch`` / ``insert_feature``
return canned values. Pin the public contract per plan §16:

* Empty registry → returns 0.
* All seen → returns 0; no asyncio.create_task call.
* NEW feature → task scheduled into ``background_tasks``.
* Successful backfill → ``kv_put`` called with ISO-8601 UTC bytes.
* Compute exception → ``kv_put`` SKIPPED; ERROR logged.
* Empty OHLC range → ``kv_put`` STILL called (per edge case #5).
* ``window_days`` propagates to ``from_dt = now - window_days``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.feature_engine.app.auto_backfill import (
    KV_BUCKET,
    _backfill_and_mark,
    schedule_auto_backfills,
)

_FIXED_NOW = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)


def _now_fn() -> datetime:
    return _FIXED_NOW


def _make_feature(*, name: str = "ema_20", warmup: int = 2) -> MagicMock:
    """Mock Feature protocol; compute returns trivial FeatureValue mock."""
    feat = MagicMock()
    feat.name_template = f"ind.{{symbol}}.{{interval}}.{name}"
    feat.source_version = f"builtin.{name}.v1"
    feat.warmup_candles = warmup
    fv = MagicMock()
    fv.value_num = Decimal("100.5")
    fv.value_bool = None
    fv.value_json = None
    feat.compute = MagicMock(return_value=fv)
    return feat


def _make_bus(seen: dict[str, bytes] | None = None) -> MagicMock:
    """Mock NatsClient with kv_get/kv_put backed by in-memory dict."""
    state = dict(seen or {})

    async def kv_get(bucket: str, key: str) -> tuple[bytes, int] | None:
        assert bucket == KV_BUCKET
        return (state[key], 1) if key in state else None

    async def kv_put(bucket: str, key: str, value: bytes) -> int:
        assert bucket == KV_BUCKET
        state[key] = value
        return 1

    bus = MagicMock()
    bus.kv_get = AsyncMock(side_effect=kv_get)
    bus.kv_put = AsyncMock(side_effect=kv_put)
    bus._state = state  # test access
    return bus


def _make_pool(rows: list[Any] | None = None) -> MagicMock:
    """Mock asyncpg.Pool with conn.fetch returning canned rows."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows if rows is not None else [])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)

    class _Acq:
        async def __aenter__(self) -> Any:
            return conn

        async def __aexit__(self, *_: Any) -> None:
            return None

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Acq())
    pool._conn = conn
    return pool


async def test_schedule_auto_backfills_empty_registry_returns_zero() -> None:
    """Empty features_by_key → returns 0; no kv_get calls; no tasks scheduled."""
    bus = _make_bus()
    pool = _make_pool()
    bg: set[Any] = set()
    n = await schedule_auto_backfills(
        pool=pool,
        bus=bus,
        features_by_key={},
        window_days=30,
        source="binance",
        logger=MagicMock(),
        background_tasks=bg,
        now_fn=_now_fn,
    )
    assert n == 0
    bus.kv_get.assert_not_awaited()
    assert bg == set()


async def test_schedule_auto_backfills_all_seen_skips() -> None:
    """kv_get returns non-None for every feature_name → returns 0; no tasks."""
    seen = {
        "ind.BTCUSDT.15m.ema_20": _FIXED_NOW.isoformat().encode(),
    }
    bus = _make_bus(seen=seen)
    pool = _make_pool()
    feat = _make_feature(name="ema_20")
    features_by_key = {("BTCUSDT", "15m"): [("ind.BTCUSDT.15m.ema_20", feat)]}
    bg: set[Any] = set()
    n = await schedule_auto_backfills(
        pool=pool,
        bus=bus,
        features_by_key=features_by_key,  # type: ignore[arg-type]
        window_days=30,
        source="binance",
        logger=MagicMock(),
        background_tasks=bg,
        now_fn=_now_fn,
    )
    assert n == 0
    bus.kv_get.assert_awaited_once_with(KV_BUCKET, "ind.BTCUSDT.15m.ema_20")
    assert bg == set()


async def test_schedule_auto_backfills_new_feature_creates_task() -> None:
    """kv_get returns None for one feature → 1 task scheduled into background_tasks."""
    bus = _make_bus()  # nothing seen
    pool = _make_pool()  # empty rows → backfill loop is no-op + kv_put marker
    feat = _make_feature(name="ema_20")
    features_by_key = {("BTCUSDT", "15m"): [("ind.BTCUSDT.15m.ema_20", feat)]}
    bg: set[Any] = set()
    n = await schedule_auto_backfills(
        pool=pool,
        bus=bus,
        features_by_key=features_by_key,  # type: ignore[arg-type]
        window_days=30,
        source="binance",
        logger=MagicMock(),
        background_tasks=bg,
        now_fn=_now_fn,
    )
    assert n == 1
    # Task scheduled; let it complete to drain pool/bus mocks.
    assert len(bg) == 1
    task = next(iter(bg))
    await task
    # Self-removal via add_done_callback after completion.
    assert bg == set()


async def test_backfill_and_mark_kv_put_after_success() -> None:
    """Successful backfill → kv_put called with (KV_BUCKET, name, ISO-8601 UTC bytes)."""
    bus = _make_bus()
    pool = _make_pool()  # empty OHLC; backfill loop no-op; marker still written
    feat = _make_feature(name="ema_20")
    await _backfill_and_mark(
        pool=pool,
        bus=bus,
        feature=feat,
        feature_name="ind.BTCUSDT.15m.ema_20",
        symbol="BTCUSDT",
        interval="15m",
        source="binance",
        window_days=30,
        logger=MagicMock(),
        now_fn=_now_fn,
    )
    bus.kv_put.assert_awaited_once_with(
        KV_BUCKET,
        "ind.BTCUSDT.15m.ema_20",
        _FIXED_NOW.isoformat().encode(),
    )


def _ohlc_record(*, bucket_start: datetime) -> dict[str, Any]:
    """asyncpg.Record-like (dict supports r['symbol'] keyed access used by fetch_ohlc_range)."""
    return {
        "symbol": "BTCUSDT",
        "bucket_start": bucket_start,
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100"),
        "volume": Decimal("1.0"),
        "source": "binance",
    }


async def test_backfill_and_mark_kv_put_skipped_on_compute_exception() -> None:
    """feature.compute raises → kv_put NOT called; ERROR logged; no re-raise."""
    bus = _make_bus()
    # 2 candles (warmup=2 → buffer fills → compute called).
    rows = [
        _ohlc_record(bucket_start=_FIXED_NOW - timedelta(minutes=30)),
        _ohlc_record(bucket_start=_FIXED_NOW - timedelta(minutes=15)),
    ]
    pool = _make_pool(rows=rows)
    feat = _make_feature(name="ema_20")
    feat.compute = MagicMock(side_effect=ValueError("bad data"))
    logger = MagicMock()
    # Body must NOT raise (fire-and-forget contract).
    await _backfill_and_mark(
        pool=pool,
        bus=bus,
        feature=feat,
        feature_name="ind.BTCUSDT.15m.ema_20",
        symbol="BTCUSDT",
        interval="15m",
        source="binance",
        window_days=30,
        logger=logger,
        now_fn=_now_fn,
    )
    bus.kv_put.assert_not_awaited()
    logger.error.assert_called_once()
    # Verify error log includes feature name + error type.
    call_kwargs = logger.error.call_args.kwargs
    assert call_kwargs["feature"] == "ind.BTCUSDT.15m.ema_20"
    assert call_kwargs["error_type"] == "ValueError"


async def test_backfill_and_mark_handles_empty_ohlc_range_marks_seen() -> None:
    """Empty OHLC range → kv_put STILL called (edge case #5: feature processed)."""
    bus = _make_bus()
    pool = _make_pool(rows=[])  # empty OHLC
    feat = _make_feature(name="ema_20")
    logger = MagicMock()
    await _backfill_and_mark(
        pool=pool,
        bus=bus,
        feature=feat,
        feature_name="ind.BTCUSDT.15m.ema_20",
        symbol="BTCUSDT",
        interval="15m",
        source="binance",
        window_days=30,
        logger=logger,
        now_fn=_now_fn,
    )
    # Marker IS written even though no rows backfilled.
    bus.kv_put.assert_awaited_once()
    # `auto_backfill_no_data` info log emitted.
    info_calls = [c for c in logger.info.call_args_list if c.args[0] == "auto_backfill_no_data"]
    assert len(info_calls) == 1


async def test_backfill_window_days_setting_propagates_to_from_dt() -> None:
    """window_days=7 → fetch_ohlc_range called with from_dt = now - 7d."""
    bus = _make_bus()
    pool = _make_pool(rows=[])
    feat = _make_feature(name="ema_20")
    await _backfill_and_mark(
        pool=pool,
        bus=bus,
        feature=feat,
        feature_name="ind.BTCUSDT.15m.ema_20",
        symbol="BTCUSDT",
        interval="15m",
        source="binance",
        window_days=7,
        logger=MagicMock(),
        now_fn=_now_fn,
    )
    # conn.fetch was called via fetch_ohlc_range; first positional arg is the
    # SQL string, then bind args. Inspect AsyncMock.await_args directly to
    # verify from_dt = _FIXED_NOW - 7d was bound.
    pool._conn.fetch.assert_awaited_once()
    bind_args = pool._conn.fetch.await_args.args
    expected_from = _FIXED_NOW - timedelta(days=7)
    assert expected_from in bind_args
    assert _FIXED_NOW in bind_args


@pytest.mark.parametrize("window_days", [1, 30, 90])
async def test_schedule_auto_backfills_logs_new_features_count(window_days: int) -> None:
    """Logger info call includes new_features count + ordering invariant."""
    bus = _make_bus()
    pool = _make_pool()
    feat = _make_feature(name="ema_20")
    features_by_key = {("BTCUSDT", "15m"): [("ind.BTCUSDT.15m.ema_20", feat)]}
    bg: set[Any] = set()
    logger = MagicMock()
    n = await schedule_auto_backfills(
        pool=pool,
        bus=bus,
        features_by_key=features_by_key,  # type: ignore[arg-type]
        window_days=window_days,
        source="binance",
        logger=logger,
        background_tasks=bg,
        now_fn=_now_fn,
    )
    assert n == 1
    # Drain task before assertion exits to avoid pending-task leak.
    for task in list(bg):
        await task
    info_calls = [c for c in logger.info.call_args_list if c.args[0] == "auto_backfill_scheduled"]
    assert len(info_calls) == 1
    assert info_calls[0].kwargs["new_features"] == 1
