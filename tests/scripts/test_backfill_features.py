"""Unit tests for :mod:`scripts.backfill_features` (T-112).

Pure unit tests with inline fakes — no DB, no NATS. ``BufferRegistry``
not used (backfill uses raw deque). Tests cover:

* Feature-name parsing (canonical case + segment validation).
* ISO datetime parsing (UTC enforcement on naive + aware inputs).
* ``_backfill_one_feature`` compute loop with fake feature + fake conn.
* ``main()`` exit codes for invalid args and unknown features.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.features.types import FeatureValue
from scripts.backfill_features import (
    _backfill_one_feature,
    _parse_feature_name,
    _parse_iso_datetime,
    main,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from packages.features.types import OhlcCandle


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeFeature:
    """Stub matching :class:`packages.features.protocols.Feature`."""

    def __init__(
        self,
        *,
        warmup: int = 3,
        result: FeatureValue | None = None,
        period: int = 20,
    ) -> None:
        self.warmup_candles = warmup
        self.interval = "15m"
        self.source_version = "fake.v1"
        self.name_template = f"ind.{{symbol}}.{{interval}}.ema_{period}"
        self._result = result if result is not None else FeatureValue(value_num=Decimal("70.5"))

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue:
        return self._result


def _row(*, minute: int, source: str = "binance") -> tuple[Any, ...]:
    """Construct a synthetic OhlcRow tuple per T-110b field order."""
    return (
        "BTCUSDT",
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
# _parse_feature_name
# ---------------------------------------------------------------------------


def test_parse_feature_name_extracts_symbol_canonical_and_interval() -> None:
    """ind.btcusdt.15m.ema_20 → (BTCUSDT, 15m, ema_20)."""
    assert _parse_feature_name("ind.btcusdt.15m.ema_20") == ("BTCUSDT", "15m", "ema_20")


def test_parse_feature_name_uppercase_symbol_canonical() -> None:
    """Symbol output is canonical Bybit-shape (uppercase) regardless of input case."""
    assert _parse_feature_name("ind.ethusdt.1m.vwap_session") == ("ETHUSDT", "1m", "vwap_session")


def test_parse_feature_name_rejects_too_few_segments() -> None:
    """`ind.foo` (2 segments) → ValueError."""
    with pytest.raises(ValueError, match=r"<symbol>\.<interval>\.<rest>"):
        _parse_feature_name("ind.foo")


def test_parse_feature_name_rejects_wrong_first_segment() -> None:
    """Wrong prefix `xxx.btcusdt.15m.ema_20` → ValueError."""
    with pytest.raises(ValueError, match=r"<symbol>\.<interval>\.<rest>"):
        _parse_feature_name("xxx.btcusdt.15m.ema_20")


# ---------------------------------------------------------------------------
# _parse_iso_datetime
# ---------------------------------------------------------------------------


def test_parse_iso_datetime_naive_appends_utc() -> None:
    """`2026-04-01` (naive) → datetime UTC-aware."""
    parsed = _parse_iso_datetime("2026-04-01")
    assert parsed == datetime(2026, 4, 1, tzinfo=UTC)


def test_parse_iso_datetime_aware_passes_through() -> None:
    """`2026-04-01T00:00:00+00:00` (aware UTC) → same datetime."""
    parsed = _parse_iso_datetime("2026-04-01T00:00:00+00:00")
    assert parsed == datetime(2026, 4, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _backfill_one_feature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_one_feature_inserts_per_candle_after_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Buffer-fill threshold: only candles AT/AFTER warmup_candles trigger insert."""
    rows = [_row(minute=i) for i in range(5)]
    fetch_mock = AsyncMock(return_value=rows)
    insert_mock = AsyncMock()
    monkeypatch.setattr("scripts.backfill_features.fetch_ohlc_range", fetch_mock)
    monkeypatch.setattr("scripts.backfill_features.insert_feature", insert_mock)
    feature = _FakeFeature(warmup=3)
    inserted = await _backfill_one_feature(
        MagicMock(),
        feature=feature,
        feature_name="ind.btcusdt.15m.ema_20",
        symbol="BTCUSDT",
        interval="15m",
        source="binance",
        from_dt=datetime(2026, 4, 26, 11, 0, tzinfo=UTC),
        to_dt=datetime(2026, 4, 26, 11, 4, tzinfo=UTC),
        logger=_logger(),
    )
    # 5 rows, warmup=3 → first 2 skipped, 3 inserts (rows 2, 3, 4)
    assert inserted == 3
    assert insert_mock.await_count == 3


@pytest.mark.asyncio
async def test_backfill_one_feature_underfill_skips_compute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fewer than warmup_candles rows → no inserts."""
    rows = [_row(minute=i) for i in range(5)]
    monkeypatch.setattr("scripts.backfill_features.fetch_ohlc_range", AsyncMock(return_value=rows))
    insert_mock = AsyncMock()
    monkeypatch.setattr("scripts.backfill_features.insert_feature", insert_mock)
    feature = _FakeFeature(warmup=20)  # warmup > 5 rows
    inserted = await _backfill_one_feature(
        MagicMock(),
        feature=feature,
        feature_name="ind.btcusdt.15m.ema_20",
        symbol="BTCUSDT",
        interval="15m",
        source="binance",
        from_dt=datetime(2026, 4, 26, 11, 0, tzinfo=UTC),
        to_dt=datetime(2026, 4, 26, 11, 4, tzinfo=UTC),
        logger=_logger(),
    )
    assert inserted == 0
    insert_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_one_feature_decimal_to_float_seam_value_num(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """value_num: Decimal('70.5') → float 70.5 in insert_feature kwargs."""
    rows = [_row(minute=i) for i in range(3)]
    monkeypatch.setattr("scripts.backfill_features.fetch_ohlc_range", AsyncMock(return_value=rows))
    insert_mock = AsyncMock()
    monkeypatch.setattr("scripts.backfill_features.insert_feature", insert_mock)
    feature = _FakeFeature(warmup=3, result=FeatureValue(value_num=Decimal("70.5")))
    await _backfill_one_feature(
        MagicMock(),
        feature=feature,
        feature_name="ind.btcusdt.15m.ema_20",
        symbol="BTCUSDT",
        interval="15m",
        source="binance",
        from_dt=datetime(2026, 4, 26, 11, 0, tzinfo=UTC),
        to_dt=datetime(2026, 4, 26, 11, 2, tzinfo=UTC),
        logger=_logger(),
    )
    insert_mock.assert_awaited_once()
    assert insert_mock.await_args is not None
    kwargs = insert_mock.await_args.kwargs
    assert kwargs["value_num"] == 70.5
    assert isinstance(kwargs["value_num"], float)
    assert kwargs["value_bool"] is None
    assert kwargs["value_json"] is None


@pytest.mark.asyncio
async def test_backfill_one_feature_computed_at_is_bucket_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """computed_at = bucket_start + INTERVAL_DELTA[interval] (15m → +15min)."""
    rows = [_row(minute=i * 15) for i in range(3)]  # 11:00, 11:15, 11:30 (15m boundaries)
    monkeypatch.setattr("scripts.backfill_features.fetch_ohlc_range", AsyncMock(return_value=rows))
    insert_mock = AsyncMock()
    monkeypatch.setattr("scripts.backfill_features.insert_feature", insert_mock)
    feature = _FakeFeature(warmup=3)
    await _backfill_one_feature(
        MagicMock(),
        feature=feature,
        feature_name="ind.btcusdt.15m.ema_20",
        symbol="BTCUSDT",
        interval="15m",
        source="binance",
        from_dt=datetime(2026, 4, 26, 11, 0, tzinfo=UTC),
        to_dt=datetime(2026, 4, 26, 11, 30, tzinfo=UTC),
        logger=_logger(),
    )
    assert insert_mock.await_args is not None
    # Last row bucket_start = 11:30 → computed_at = 11:45 (bucket end)
    assert insert_mock.await_args.kwargs["computed_at"] == datetime(2026, 4, 26, 11, 45, tzinfo=UTC)


# ---------------------------------------------------------------------------
# main() — CLI entry
# ---------------------------------------------------------------------------


def test_main_unknown_feature_exits_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Feature not in indicators.yaml → exit code 1."""
    yaml_path = tmp_path / "indicators.yaml"
    yaml_path.write_text(
        "features:\n"
        "  - name_template: ind.{symbol}.15m.ema_20\n"
        "    type: builtin.ema\n"
        "    interval: 15m\n"
        "    params: { period: 20 }\n"
        "    source_version: builtin.ema.v1\n"
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill_features.py",
            "--feature",
            "ind.btcusdt.1m.unknown_indicator",
            "--from",
            "2026-04-01",
            "--to",
            "2026-04-26",
            "--indicators-yaml",
            str(yaml_path),
            "--database-url",
            "postgresql://u@h/d",
        ],
    )
    # Mock asyncpg.connect so we don't need a real DB; the lookup fails before SELECT.
    fake_conn = MagicMock()
    fake_conn.set_type_codec = AsyncMock()
    fake_conn.close = AsyncMock()
    monkeypatch.setattr(
        "scripts.backfill_features.asyncpg.connect",
        AsyncMock(return_value=fake_conn),
    )
    assert main() == 1


def test_main_invalid_feature_format_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed --feature (too few segments) → exit code 1 before DB connect."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill_features.py",
            "--feature",
            "foo",
            "--from",
            "2026-04-01",
            "--to",
            "2026-04-26",
            "--database-url",
            "postgresql://u@h/d",
        ],
    )
    # asyncpg.connect should not be called; if it is, the test would hang.
    monkeypatch.setattr(
        "scripts.backfill_features.asyncpg.connect",
        AsyncMock(side_effect=AssertionError("connect should not be called")),
    )
    assert main() == 1
