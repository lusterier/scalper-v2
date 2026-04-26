"""Deterministic tests for :class:`VwapFeature` (§N4 TDD-for-financial-math).

Daily-session VWAP filters by UTC date; the cross-session fixture
explicitly spans two dates so the filter is observable, not incidental.
The zero-volume fixture pins the §0.4 fail-loud contract: VWAP is
undefined on zero-volume input and raises :class:`FeatureUnderflowError`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from packages.features import (
    Feature,
    FeatureUnderflowError,
    OhlcCandle,
)
from packages.features.builtins import VwapFeature


def _candle(
    *,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal,
    day: int = 26,
    minute: int = 0,
) -> OhlcCandle:
    return OhlcCandle(
        symbol="BTCUSDT",
        interval="1m",
        bucket_start=datetime(2026, 4, day, 12, minute, tzinfo=UTC),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
        source="binance",
    )


class TestVwapFeatureConstructor:
    def test_attributes_for_defaults(self) -> None:
        feature = VwapFeature()
        assert feature.session == "daily"
        assert feature.interval == "1m"
        assert feature.name_template == "ind.{symbol}.{interval}.vwap_session"
        assert feature.source_version == "builtin.vwap.v1"
        assert feature.warmup_candles == 1

    def test_session_weekly_raises(self) -> None:
        with pytest.raises(ValueError, match="session must be 'daily'"):
            VwapFeature(session="weekly")

    def test_session_hourly_raises(self) -> None:
        with pytest.raises(ValueError, match="session must be 'daily'"):
            VwapFeature(session="hourly")


class TestVwapFeatureCompute:
    def test_underflow_raises(self) -> None:
        feature = VwapFeature()
        with pytest.raises(FeatureUnderflowError, match="VWAP requires"):
            feature.compute([])

    def test_single_bar_vwap_equals_typical_price(self) -> None:
        """Single-bar VWAP: typical = (h+l+c)/3 = (12+8+10)/3 = 10; volume drops out."""
        feature = VwapFeature()
        candles = [
            _candle(
                high=Decimal(12),
                low=Decimal(8),
                close=Decimal(10),
                volume=Decimal(100),
            ),
        ]
        result = feature.compute(candles)
        assert result.value_num == Decimal(10)

    def test_multi_bar_vwap_within_one_session(self) -> None:
        """3-bar VWAP, all on 2026-04-26 (one UTC day).

        Bar (h=12, l=8, c=10, v=100):    typical = 10, weighted = 1000
        Bar (h=22, l=18, c=20, v=200):   typical = 20, weighted = 4000
        Bar (h=33, l=27, c=30, v=300):   typical = 30, weighted = 9000

        VWAP = (1000 + 4000 + 9000) / (100 + 200 + 300) = 14000 / 600
             = 23.33333333333333333333333333 (Decimal default 28-digit ctx)
        """
        feature = VwapFeature()
        candles = [
            _candle(high=Decimal(12), low=Decimal(8), close=Decimal(10),
                    volume=Decimal(100), minute=0),
            _candle(high=Decimal(22), low=Decimal(18), close=Decimal(20),
                    volume=Decimal(200), minute=1),
            _candle(high=Decimal(33), low=Decimal(27), close=Decimal(30),
                    volume=Decimal(300), minute=2),
        ]  # fmt: skip
        result = feature.compute(candles)
        assert result.value_num is not None
        expected = Decimal(14000) / Decimal(600)
        assert result.value_num == expected

    def test_cross_session_filter_keeps_only_latest_date(self) -> None:
        """5 candles spanning two UTC dates → VWAP uses only the latter session.

        Day-25 bars (silently dropped):
          (h=100, l=100, c=100, v=10) typical=100
          (h=200, l=200, c=200, v=10) typical=200
        Day-26 bars (used):
          (h=12, l=8, c=10, v=100) typical=10
          (h=22, l=18, c=20, v=200) typical=20
          (h=33, l=27, c=30, v=300) typical=30
        VWAP = 14000 / 600 (same as the multi-bar single-session test).
        """
        feature = VwapFeature()
        candles = [
            _candle(high=Decimal(100), low=Decimal(100), close=Decimal(100),
                    volume=Decimal(10), day=25, minute=0),
            _candle(high=Decimal(200), low=Decimal(200), close=Decimal(200),
                    volume=Decimal(10), day=25, minute=1),
            _candle(high=Decimal(12), low=Decimal(8), close=Decimal(10),
                    volume=Decimal(100), day=26, minute=0),
            _candle(high=Decimal(22), low=Decimal(18), close=Decimal(20),
                    volume=Decimal(200), day=26, minute=1),
            _candle(high=Decimal(33), low=Decimal(27), close=Decimal(30),
                    volume=Decimal(300), day=26, minute=2),
        ]  # fmt: skip
        result = feature.compute(candles)
        assert result.value_num == Decimal(14000) / Decimal(600)

    def test_zero_volume_session_raises(self) -> None:
        """All candles in current session have ``volume=0`` → undefined → raises."""
        feature = VwapFeature()
        candles = [
            _candle(high=Decimal(12), low=Decimal(8), close=Decimal(10),
                    volume=Decimal(0), minute=0),
            _candle(high=Decimal(22), low=Decimal(18), close=Decimal(20),
                    volume=Decimal(0), minute=1),
        ]  # fmt: skip
        with pytest.raises(FeatureUnderflowError, match="zero-volume"):
            feature.compute(candles)


def test_name_template_substitutes_symbol_and_interval() -> None:
    feature = VwapFeature()
    expanded = feature.name_template.format(symbol="BTCUSDT", interval="1m")
    assert expanded == "ind.BTCUSDT.1m.vwap_session"


def test_satisfies_feature_protocol() -> None:
    feature: Feature = VwapFeature()
    assert feature.warmup_candles == 1
