"""Deterministic tests for :class:`MacdFeature` + ``_sma_seeded_ema_series`` helper.

The helper is module-level (not a class method) so it can be tested
directly + cross-checked against :class:`EmaFeature` without going
through MACD orchestration. The golden test pins mathematical
consistency: a future EMA algorithm change cannot silently desync
MACD's internal EMAs from the published EMA indicator.
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
from packages.features.builtins import EmaFeature, MacdFeature
from packages.features.builtins.macd import _sma_seeded_ema_series


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


class TestSmaSeededEmaSeriesHelper:
    def test_underflow_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="requires >= 5"):
            _sma_seeded_ema_series([Decimal(1), Decimal(2)], 5)

    def test_output_length_equals_input_minus_period_plus_one(self) -> None:
        values = [Decimal(i) for i in range(10)]
        series = _sma_seeded_ema_series(values, 3)
        assert len(series) == 10 - 3 + 1

    def test_index_alignment(self) -> None:
        """``series[0]`` is the SMA of the first ``period`` values.

        Per docstring: output index ``i`` ↔ input bar index ``i + period - 1``.
        So ``series[0]`` corresponds to input bar ``period - 1`` (= index 2 for
        period=3) and equals SMA of inputs at indices 0..2 = ``[0, 1, 2] = 1``.
        """
        values = [Decimal(i) for i in range(10)]
        series = _sma_seeded_ema_series(values, 3)
        assert series[0] == Decimal(1)

    def test_period_3_5value_hand_computation(self) -> None:
        """Hand-computed period-3 EMA on [10, 12, 14, 16, 18].

        SMA seed = (10 + 12 + 14) / 3 = 12 → series[0]
        alpha    = 2 / 4 = 0.5
        idx 3:  16 * 0.5 + 12 * 0.5 = 14 → series[1]
        idx 4:  18 * 0.5 + 14 * 0.5 = 16 → series[2]
        Expected: [12, 14, 16]
        """
        series = _sma_seeded_ema_series(
            [Decimal(10), Decimal(12), Decimal(14), Decimal(16), Decimal(18)],
            3,
        )
        assert series == [Decimal(12), Decimal(14), Decimal(16)]

    def test_golden_cross_check_vs_ema_feature(self) -> None:
        """``series[-1]`` must equal :meth:`EmaFeature.compute` for the same data.

        Pins mathematical consistency between the helper used by MACD's
        internal fast/slow EMAs and the public :class:`EmaFeature`. A
        future change to either's algorithm will fail this test.
        """
        closes = [Decimal(100 + i) for i in range(20)]
        helper_tail = _sma_seeded_ema_series(closes, 14)[-1]
        ema_value = EmaFeature(period=14).compute(_candles_from_closes(closes))
        assert ema_value.value_num == helper_tail


class TestMacdFeatureConstructor:
    def test_attributes_for_defaults(self) -> None:
        feature = MacdFeature()
        assert feature.fast == 12
        assert feature.slow == 26
        assert feature.signal == 9
        assert feature.interval == "15m"
        assert feature.name_template == "ind.{symbol}.{interval}.macd_12_26_9"
        assert feature.source_version == "builtin.macd.v1"
        assert feature.warmup_candles == 34  # slow + signal - 1

    def test_fast_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            MacdFeature(fast=0)

    def test_slow_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            MacdFeature(slow=0)

    def test_signal_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            MacdFeature(signal=0)

    def test_fast_geq_slow_raises(self) -> None:
        with pytest.raises(ValueError, match="fast must be < slow"):
            MacdFeature(fast=12, slow=12)


class TestMacdFeatureCompute:
    def test_underflow_raises(self) -> None:
        """Default 12/26/9 needs 34 candles; 33 → underflow."""
        feature = MacdFeature()
        candles = _candles_from_closes([Decimal(100 + i) for i in range(33)])
        with pytest.raises(FeatureUnderflowError, match="MACD"):
            feature.compute(candles)

    def test_small_macd_hand_computation(self) -> None:
        """fast=2 slow=3 signal=2 (warmup = 4) on closes [10, 12, 14, 16].

        fast(2)_series:
          SMA seed (10+12)/2 = 11             → idx 0 (bar 1)
          alpha = 2/3
          bar 2: 14 * 2/3 + 11 * 1/3 = 13      → idx 1
          bar 3: 16 * 2/3 + 13 * 1/3 = 15      → idx 2
          fast = [11, 13, 15]

        slow(3)_series:
          SMA seed (10+12+14)/3 = 12          → idx 0 (bar 2)
          alpha = 2/4 = 0.5
          bar 3: 16 * 0.5 + 12 * 0.5 = 14     → idx 1
          slow = [12, 14]

        Aligned (drop slow-fast=1 from fast): fast = [13, 15]; slow = [12, 14]
        macd_line = [13-12, 15-14] = [1, 1]

        signal(2)_series of [1, 1]:
          SMA seed (1+1)/2 = 1                → idx 0
          signal = [1]

        Output: macd = 1 (last of macd_line), signal = 1, histogram = 0.
        """
        feature = MacdFeature(fast=2, slow=3, signal=2)
        candles = _candles_from_closes(
            [Decimal(10), Decimal(12), Decimal(14), Decimal(16)],
        )
        result = feature.compute(candles)
        assert result.value_json is not None
        assert result.value_json["macd"] == Decimal(1)
        assert result.value_json["signal"] == Decimal(1)
        assert result.value_json["histogram"] == Decimal(0)

    def test_default_warmup_boundary_returns_complete_triple(self) -> None:
        """Exactly 34 candles → first complete (macd, signal, histogram).

        Sanity check: with default 12/26/9 and 34 closes, compute returns
        a value_json triple with all three Decimal sub-keys present.
        """
        feature = MacdFeature()
        candles = _candles_from_closes(
            [Decimal(100 + i) for i in range(34)],
        )
        result = feature.compute(candles)
        assert result.value_json is not None
        assert "macd" in result.value_json
        assert "signal" in result.value_json
        assert "histogram" in result.value_json
        assert isinstance(result.value_json["macd"], Decimal)
        assert isinstance(result.value_json["signal"], Decimal)
        assert isinstance(result.value_json["histogram"], Decimal)


def test_name_template_substitutes_symbol_and_interval() -> None:
    feature = MacdFeature()
    expanded = feature.name_template.format(symbol="ETHUSDT", interval="1h")
    assert expanded == "ind.ETHUSDT.1h.macd_12_26_9"


def test_satisfies_feature_protocol() -> None:
    feature: Feature = MacdFeature()
    assert feature.warmup_candles == 34
