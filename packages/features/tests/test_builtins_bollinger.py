"""Deterministic tests for :class:`BollingerFeature` (§N4 TDD-for-financial-math).

Population std_dev (divide by ``n``, not ``n-1``) per TradingView
convention. The constant-prices fixture pins the sigma=0 edge where
upper/middle/lower collapse to one value.
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
from packages.features.builtins import BollingerFeature


def _candle(close: Decimal, *, minute: int = 0) -> OhlcCandle:
    return OhlcCandle(
        symbol="BTCUSDT",
        interval="15m",
        bucket_start=datetime(2026, 4, 26, 12, minute, tzinfo=UTC),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1"),
        source="binance",
    )


class TestBollingerFeatureConstructor:
    def test_attributes_for_defaults(self) -> None:
        feature = BollingerFeature()
        assert feature.period == 20
        assert feature.std_dev_multiplier == Decimal("2")
        assert feature.interval == "15m"
        assert feature.name_template == "ind.{symbol}.{interval}.bollinger_20_2"
        assert feature.source_version == "builtin.bollinger.v1"
        assert feature.warmup_candles == 20

    def test_period_one_raises(self) -> None:
        with pytest.raises(ValueError, match="period must be >= 2"):
            BollingerFeature(period=1)

    def test_period_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="period must be >= 2"):
            BollingerFeature(period=0)

    def test_std_dev_multiplier_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="std_dev_multiplier must be > 0"):
            BollingerFeature(std_dev_multiplier=Decimal("0"))

    def test_std_dev_multiplier_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="std_dev_multiplier must be > 0"):
            BollingerFeature(std_dev_multiplier=Decimal("-1"))

    def test_custom_multiplier_in_name_template(self) -> None:
        """Multiplier 2.5 stringifies cleanly via ``.normalize()`` (no ``2.5E+0``)."""
        feature = BollingerFeature(period=10, std_dev_multiplier=Decimal("2.5"))
        assert feature.name_template == "ind.{symbol}.{interval}.bollinger_10_2.5"


class TestBollingerFeatureCompute:
    def test_underflow_raises(self) -> None:
        feature = BollingerFeature(period=20)
        candles = [_candle(Decimal(100 + i), minute=i) for i in range(19)]
        with pytest.raises(FeatureUnderflowError, match="Bollinger"):
            feature.compute(candles)

    def test_5bar_period5_k2_hand_computation(self) -> None:
        """5-bar Bollinger(period=5, k=2) on closes [10, 12, 14, 16, 18].

        middle = SMA = (10+12+14+16+18)/5 = 14
        deviations²: (10-14)²=16, (12-14)²=4, (14-14)²=0, (16-14)²=4, (18-14)²=16
        variance = (16+4+0+4+16)/5 = 40/5 = 8
        sigma   = sqrt(8) = 2.8284271247461900976033774484...
        offset  = 2 * sigma = 5.6568542494923801952067548967...
        upper   = 14 + offset ≈ 19.6568542494923801952067548967
        lower   = 14 - offset ≈  8.3431457505076198047932451033
        """
        feature = BollingerFeature(period=5, std_dev_multiplier=Decimal("2"))
        candles = [_candle(Decimal(c), minute=i) for i, c in enumerate([10, 12, 14, 16, 18])]
        result = feature.compute(candles)
        assert result.value_json is not None
        middle = result.value_json["middle"]
        upper = result.value_json["upper"]
        lower = result.value_json["lower"]
        assert isinstance(middle, Decimal)
        assert isinstance(upper, Decimal)
        assert isinstance(lower, Decimal)
        assert middle == Decimal(14)
        # Decimal sqrt at default 28-digit context; use a 1e-10 tolerance
        # band against the irrational sqrt(8).
        expected_offset = Decimal(2) * Decimal(8).sqrt()
        tol = Decimal("0.0000000001")
        assert -tol <= (upper - (Decimal(14) + expected_offset)) <= tol
        assert -tol <= (lower - (Decimal(14) - expected_offset)) <= tol

    def test_constant_prices_collapse_to_middle(self) -> None:
        """All closes equal → sigma=0 → upper = middle = lower."""
        feature = BollingerFeature(period=20, std_dev_multiplier=Decimal("2"))
        candles = [_candle(Decimal(100), minute=i) for i in range(20)]
        result = feature.compute(candles)
        assert result.value_json is not None
        assert result.value_json["middle"] == Decimal(100)
        assert result.value_json["upper"] == Decimal(100)
        assert result.value_json["lower"] == Decimal(100)

    def test_uses_only_last_period_closes(self) -> None:
        """30 closes available, period=5 → only the last 5 contribute."""
        feature = BollingerFeature(period=5, std_dev_multiplier=Decimal("2"))
        # First 25 closes are noise, last 5 are [10, 12, 14, 16, 18]
        # — same fixture as the hand-computation test → middle=14.
        noise = [_candle(Decimal(99999 + i), minute=i) for i in range(25)]
        last5 = [_candle(Decimal(c), minute=25 + i) for i, c in enumerate([10, 12, 14, 16, 18])]
        result = feature.compute([*noise, *last5])
        assert result.value_json is not None
        assert result.value_json["middle"] == Decimal(14)


def test_name_template_substitutes_symbol_and_interval() -> None:
    feature = BollingerFeature()
    expanded = feature.name_template.format(symbol="BTCUSDT", interval="15m")
    assert expanded == "ind.BTCUSDT.15m.bollinger_20_2"


def test_satisfies_feature_protocol() -> None:
    feature: Feature = BollingerFeature(period=14)
    assert feature.warmup_candles == 14
