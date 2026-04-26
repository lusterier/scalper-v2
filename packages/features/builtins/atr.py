"""Wilder's Average True Range (§9.3, §B.2).

For each bar ``i in 1..N``::

    TR_i = max(
        high_i - low_i,
        abs(high_i - close_{i-1}),
        abs(low_i  - close_{i-1}),
    )

so we need ``period + 1`` candles to produce ``period`` true ranges.

- Seed: ``ATR = SMA`` of first ``period`` TRs.
- Wilder smoothing for subsequent steps::

      ATR_n = (ATR_{n-1} * (period - 1) + TR_n) / period
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.features.errors import FeatureUnderflowError
from packages.features.types import FeatureValue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from packages.features.types import OhlcCandle

__all__ = ["AtrFeature"]


class AtrFeature:
    """ATR indicator parameterised by ``period`` and candle ``interval``."""

    def __init__(self, period: int, interval: str = "15m") -> None:
        if period < 1:
            msg = f"period must be >= 1, got {period}"
            raise ValueError(msg)
        self.period = period
        self.interval = interval
        self.name_template = f"ind.{{symbol}}.{{interval}}.atr_{period}"
        self.source_version = "builtin.atr.v1"
        self.warmup_candles = period + 1

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue:
        if len(candles) < self.warmup_candles:
            msg = (
                f"ATR(period={self.period}) requires >= {self.warmup_candles} "
                f"candles, got {len(candles)}"
            )
            raise FeatureUnderflowError(msg)

        true_ranges: list[Decimal] = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        period_dec = Decimal(self.period)
        atr = sum(true_ranges[: self.period], Decimal(0)) / period_dec
        for tr in true_ranges[self.period :]:
            atr = (atr * (period_dec - 1) + tr) / period_dec
        return FeatureValue(value_num=atr)
