"""Deterministic tests for :class:`EmaFeature` (§N4 TDD-for-financial-math).

Hand-computable 5-bar EMA(3) fixture lets a reader verify the SMA-seed
+ alpha-smoothing convention against pen-and-paper arithmetic — see the
``test_compute_5bar_ema3_matches_hand_computation`` docstring for the
intermediate values.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from packages.features import (
    Feature,
    FeatureUnderflowError,
    FeatureValue,
    OhlcCandle,
)
from packages.features.builtins import EmaFeature


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


class TestEmaFeatureConstructor:
    def test_attributes_for_period_20(self) -> None:
        feature = EmaFeature(period=20)
        assert feature.period == 20
        assert feature.interval == "15m"
        assert feature.name_template == "ind.{symbol}.{interval}.ema_20"
        assert feature.source_version == "builtin.ema.v1"
        assert feature.warmup_candles == 20

    def test_period_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="period must be"):
            EmaFeature(period=0)

    def test_period_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="period must be"):
            EmaFeature(period=-1)

    def test_custom_interval(self) -> None:
        feature = EmaFeature(period=14, interval="1h")
        assert feature.interval == "1h"


class TestEmaFeatureCompute:
    def test_underflow_raises(self) -> None:
        feature = EmaFeature(period=20)
        candles = [_candle(Decimal(i)) for i in range(19)]  # 19 < 20
        with pytest.raises(FeatureUnderflowError, match="EMA"):
            feature.compute(candles)

    def test_compute_5bar_ema3_matches_hand_computation(self) -> None:
        """5-bar EMA(3) on closes [10, 12, 14, 16, 18].

        SMA seed = (10 + 12 + 14) / 3 = 12
        alpha = 2 / (3 + 1) = 0.5
        Bar 4: 16 * 0.5 + 12 * 0.5 = 14
        Bar 5: 18 * 0.5 + 14 * 0.5 = 16
        Expected: Decimal("16")
        """
        feature = EmaFeature(period=3)
        candles = [
            _candle(Decimal(10), minute=0),
            _candle(Decimal(12), minute=1),
            _candle(Decimal(14), minute=2),
            _candle(Decimal(16), minute=3),
            _candle(Decimal(18), minute=4),
        ]
        result = feature.compute(candles)
        assert result == FeatureValue(value_num=Decimal("16"))

    def test_compute_at_warmup_boundary_returns_sma_seed(self) -> None:
        """Exactly ``period`` candles → result is the SMA of those closes."""
        feature = EmaFeature(period=3)
        candles = [_candle(Decimal(10)), _candle(Decimal(12)), _candle(Decimal(14))]
        result = feature.compute(candles)
        assert result == FeatureValue(value_num=Decimal("12"))

    def test_decimal_precision_preserved(self) -> None:
        """Closes at NUMERIC(30, 12) precision floor stay precise through compute.

        period=2; closes = [a, b, c] where:
          a = 50000.123456789012
          b = 50100.987654321098
          c = 50050.555555555555
        SMA seed (a, b) = (a + b) / 2 = 50050.555555555055
        alpha = 2 / 3
        EMA = c * alpha + seed * (1 - alpha)
            = (c * 2 + seed) / 3
            = (100101.111111111110 + 50050.555555555055) / 3
            = 150151.666666666165 / 3
            = 50050.555555555388333...

        Assertion uses a |delta| < 1e-12 tolerance band against the
        12-digit rounded expectation; the tail digits past 12 places
        come from default Decimal-context divisions, which is fine for
        a precision-preservation invariant (input precision survives
        through compute, no truncation to float).
        """
        feature = EmaFeature(period=2)
        precise_closes = [
            Decimal("50000.123456789012"),
            Decimal("50100.987654321098"),
            Decimal("50050.555555555555"),
        ]
        candles = [_candle(c, minute=i) for i, c in enumerate(precise_closes)]
        result = feature.compute(candles)
        assert result.value_num is not None
        expected = Decimal("50050.555555555388")
        delta = result.value_num - expected
        assert -Decimal("0.000000000001") <= delta <= Decimal("0.000000000001")


def test_name_template_substitutes_symbol_and_interval() -> None:
    feature = EmaFeature(period=20)
    expanded = feature.name_template.format(symbol="BTCUSDT", interval="15m")
    assert expanded == "ind.BTCUSDT.15m.ema_20"


def test_satisfies_feature_protocol() -> None:
    """mypy-time guard: an :class:`EmaFeature` typechecks as :class:`Feature`."""
    feature: Feature = EmaFeature(period=14)
    assert feature.warmup_candles == 14
