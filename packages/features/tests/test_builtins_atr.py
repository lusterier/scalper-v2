"""Deterministic tests for :class:`AtrFeature` (§N4 TDD-for-financial-math).

True Range chooses the largest of three candidates per bar; the
gap-up and gap-down fixtures explicitly drive the second and third
candidates to dominate so that the branch coverage is observable, not
incidental. The 5-bar period-2 fixture exercises one Wilder smoothing
step against a hand-computable target.
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
from packages.features.builtins import AtrFeature


def _ohlc(
    high: Decimal,
    low: Decimal,
    close: Decimal,
    *,
    minute: int = 0,
) -> OhlcCandle:
    return OhlcCandle(
        symbol="BTCUSDT",
        interval="15m",
        bucket_start=datetime(2026, 4, 26, 12, minute, tzinfo=UTC),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=Decimal("1"),
        source="binance",
    )


class TestAtrFeatureConstructor:
    def test_attributes_for_period_14(self) -> None:
        feature = AtrFeature(period=14)
        assert feature.period == 14
        assert feature.interval == "15m"
        assert feature.name_template == "ind.{symbol}.{interval}.atr_14"
        assert feature.source_version == "builtin.atr.v1"
        assert feature.warmup_candles == 15  # period + 1 candles → period TRs

    def test_period_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="period must be"):
            AtrFeature(period=0)

    def test_custom_interval(self) -> None:
        feature = AtrFeature(period=14, interval="1h")
        assert feature.interval == "1h"


class TestAtrFeatureCompute:
    def test_underflow_raises(self) -> None:
        """``period=14`` needs 15 candles (14 TRs); 14 candles → underflow."""
        feature = AtrFeature(period=14)
        candles = [_ohlc(Decimal(101), Decimal(99), Decimal(100), minute=i) for i in range(14)]
        with pytest.raises(FeatureUnderflowError, match="ATR"):
            feature.compute(candles)

    def test_period_2_one_wilder_step(self) -> None:
        """5-bar ATR(2) with hand-computed expected value.

        Bars (high, low, close):
          B0: (10, 8, 9)        — first bar; no TR (no prev_close)
          B1: (12, 9, 11)       — TR_1 = max(12-9, |12-9|, |9-9|) = 3
          B2: (15, 11, 14)      — TR_2 = max(15-11, |15-11|, |11-11|) = 4
          B3: (14, 12, 13)      — TR_3 = max(14-12, |14-14|, |12-14|) = 2
          B4: (16, 13, 15)      — TR_4 = max(16-13, |16-13|, |13-13|) = 3

        TR sequence: [3, 4, 2, 3]
        SMA seed (first 2 TRs): (3 + 4) / 2 = 3.5
        Wilder step on TR_3=2: (3.5 * 1 + 2) / 2 = 2.75
        Wilder step on TR_4=3: (2.75 * 1 + 3) / 2 = 2.875
        Expected ATR = 2.875
        """
        feature = AtrFeature(period=2)
        candles = [
            _ohlc(Decimal(10), Decimal(8), Decimal(9), minute=0),
            _ohlc(Decimal(12), Decimal(9), Decimal(11), minute=1),
            _ohlc(Decimal(15), Decimal(11), Decimal(14), minute=2),
            _ohlc(Decimal(14), Decimal(12), Decimal(13), minute=3),
            _ohlc(Decimal(16), Decimal(13), Decimal(15), minute=4),
        ]
        result = feature.compute(candles)
        assert result.value_num == Decimal("2.875")

    def test_gap_up_tr_dominated_by_high_minus_prev_close(self) -> None:
        """Gap-up bar: ``|high - prev_close|`` > ``high - low``.

        B0: (10, 9, 9)   — prev_close = 9
        B1: (15, 13, 14) — high-low = 2; |15-9| = 6; |13-9| = 4 → TR = 6
        B2: (15, 13, 14) — high-low = 2; |15-14| = 1; |13-14| = 1 → TR = 2

        Period=2: ATR = SMA([6, 2]) = 4.
        """
        feature = AtrFeature(period=2)
        candles = [
            _ohlc(Decimal(10), Decimal(9), Decimal(9), minute=0),
            _ohlc(Decimal(15), Decimal(13), Decimal(14), minute=1),
            _ohlc(Decimal(15), Decimal(13), Decimal(14), minute=2),
        ]
        result = feature.compute(candles)
        assert result.value_num == Decimal(4)

    def test_gap_down_tr_dominated_by_low_minus_prev_close(self) -> None:
        """Gap-down bar: ``|low - prev_close|`` > ``high - low``.

        B0: (11, 10, 10) — prev_close = 10
        B1: (6, 4, 5)    — high-low = 2; |6-10| = 4; |4-10| = 6 → TR = 6
        B2: (6, 4, 5)    — high-low = 2; |6-5| = 1; |4-5| = 1 → TR = 2

        Period=2: ATR = SMA([6, 2]) = 4.
        """
        feature = AtrFeature(period=2)
        candles = [
            _ohlc(Decimal(11), Decimal(10), Decimal(10), minute=0),
            _ohlc(Decimal(6), Decimal(4), Decimal(5), minute=1),
            _ohlc(Decimal(6), Decimal(4), Decimal(5), minute=2),
        ]
        result = feature.compute(candles)
        assert result.value_num == Decimal(4)


def test_name_template_substitutes_symbol_and_interval() -> None:
    feature = AtrFeature(period=14)
    expanded = feature.name_template.format(symbol="BTCUSDT", interval="1h")
    assert expanded == "ind.BTCUSDT.1h.atr_14"


def test_satisfies_feature_protocol() -> None:
    feature: Feature = AtrFeature(period=14)
    assert feature.warmup_candles == 15
