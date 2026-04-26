"""Deterministic tests for :class:`RsiFeature` (§N4 TDD-for-financial-math).

The 15-close synthetic fixture exercises the SMA-seed boundary
(exactly ``period + 1`` candles → no Wilder step yet); the 4-close
period-2 fixture exercises one Wilder smoothing step against a
hand-computable target. Edge cases cover the all-gains and all-losses
branches where the RS quotient saturates at infinity (RSI = 100) or
zero (RSI = 0).
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
from packages.features.builtins import RsiFeature


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


def _candles_from_closes(closes: list[Decimal]) -> list[OhlcCandle]:
    return [_candle(c, minute=i) for i, c in enumerate(closes)]


class TestRsiFeatureConstructor:
    def test_attributes_for_period_14(self) -> None:
        feature = RsiFeature(period=14)
        assert feature.period == 14
        assert feature.interval == "15m"
        assert feature.name_template == "ind.{symbol}.{interval}.rsi_14"
        assert feature.source_version == "builtin.rsi.v1"
        assert feature.warmup_candles == 15  # period + 1 closes → period deltas

    def test_period_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="period must be"):
            RsiFeature(period=0)

    def test_custom_interval(self) -> None:
        feature = RsiFeature(period=14, interval="1h")
        assert feature.interval == "1h"


class TestRsiFeatureCompute:
    def test_underflow_raises(self) -> None:
        """``period=14`` needs 15 candles (14 deltas); 14 candles → underflow."""
        feature = RsiFeature(period=14)
        candles = _candles_from_closes([Decimal(100 + i) for i in range(14)])
        with pytest.raises(FeatureUnderflowError, match="RSI"):
            feature.compute(candles)

    def test_sma_seed_only_15_closes_period_14(self) -> None:
        """Synthetic 15-close fixture, period=14 → SMA-seed-only RSI (no Wilder step yet).

        Closes: [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]

        Deltas (14 total):
          -0.25, +0.06, -0.54, +0.72, +0.50, +0.27, +0.32, +0.42,
          +0.24, -0.19, +0.14, -0.42, +0.67, 0.00

        Sum gains  = 0.06 + 0.72 + 0.50 + 0.27 + 0.32 + 0.42 + 0.24
                   + 0.14 + 0.67 = 3.34
        Sum losses = 0.25 + 0.54 + 0.19 + 0.42 = 1.40

        avg_gain = 3.34 / 14 = 0.2385714285714285...
        avg_loss = 1.40 / 14 = 0.1
        RS       = avg_gain / avg_loss = 2.385714285714285...
        RSI      = 100 - 100 / (1 + RS) = 70.46413502109704...
        """
        feature = RsiFeature(period=14)
        closes = [
            Decimal("44.34"), Decimal("44.09"), Decimal("44.15"),
            Decimal("43.61"), Decimal("44.33"), Decimal("44.83"),
            Decimal("45.10"), Decimal("45.42"), Decimal("45.84"),
            Decimal("46.08"), Decimal("45.89"), Decimal("46.03"),
            Decimal("45.61"), Decimal("46.28"), Decimal("46.28"),
        ]  # fmt: skip
        result = feature.compute(_candles_from_closes(closes))
        assert result.value_num is not None
        expected = Decimal("70.46413502109704")
        delta = result.value_num - expected
        assert -Decimal("0.0000000001") <= delta <= Decimal("0.0000000001")

    def test_wilder_smoothing_one_step_period_2(self) -> None:
        """Period-2 fixture exercises one Wilder smoothing step past SMA seed.

        Closes: [10, 12, 11, 13] → deltas [+2, -1, +2]

        SMA seed on first 2 deltas:
          avg_gain_seed = (2 + 0) / 2 = 1
          avg_loss_seed = (0 + 1) / 2 = 0.5

        Wilder step on delta_3 (gain=2, loss=0), period=2:
          new_avg_gain = (1 * 1 + 2) / 2 = 1.5
          new_avg_loss = (0.5 * 1 + 0) / 2 = 0.25

        RS  = 1.5 / 0.25 = 6
        RSI = 100 - 100 / 7 = 600 / 7 = 85.71428571428571428...
        """
        feature = RsiFeature(period=2)
        candles = _candles_from_closes(
            [Decimal(10), Decimal(12), Decimal(11), Decimal(13)],
        )
        result = feature.compute(candles)
        assert result.value_num is not None
        expected = Decimal(600) / Decimal(7)
        delta = result.value_num - expected
        assert -Decimal("0.0000000001") <= delta <= Decimal("0.0000000001")

    def test_all_gains_returns_100(self) -> None:
        """Monotonically increasing closes → ``avg_loss = 0`` → RSI = 100."""
        feature = RsiFeature(period=14)
        candles = _candles_from_closes([Decimal(10 + i) for i in range(15)])
        result = feature.compute(candles)
        assert result.value_num == Decimal(100)

    def test_all_losses_returns_0(self) -> None:
        """Monotonically decreasing closes → ``avg_gain = 0`` → RSI = 0."""
        feature = RsiFeature(period=14)
        candles = _candles_from_closes([Decimal(24 - i) for i in range(15)])
        result = feature.compute(candles)
        assert result.value_num == Decimal(0)

    def test_no_movement_returns_50(self) -> None:
        """Flat closes → ``avg_gain = 0`` AND ``avg_loss = 0`` → RSI = 50.

        Defensive fallback for the dual-zero edge: ``100 - 100 / (1 + 0/0)``
        is undefined. 50 is the neutral midpoint of the 0-100 RSI scale,
        which matches the "no information" semantics of a flat market.
        """
        feature = RsiFeature(period=14)
        candles = _candles_from_closes([Decimal(100) for _ in range(15)])
        result = feature.compute(candles)
        assert result.value_num == Decimal(50)


def test_name_template_substitutes_symbol_and_interval() -> None:
    feature = RsiFeature(period=14)
    expanded = feature.name_template.format(symbol="ETHUSDT", interval="15m")
    assert expanded == "ind.ETHUSDT.15m.rsi_14"


def test_satisfies_feature_protocol() -> None:
    feature: Feature = RsiFeature(period=14)
    assert feature.warmup_candles == 15
