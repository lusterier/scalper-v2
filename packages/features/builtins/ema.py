"""Exponential Moving Average — TradingView/Wilder convention (§9.3, §B.2).

Seed = SMA of first ``period`` close prices; subsequent values use
smoothing factor ``alpha = 2 / (period + 1)``::

    EMA_t = alpha * close_t + (1 - alpha) * EMA_{t-1}

Stateless: each :meth:`EmaFeature.compute` call re-processes the
entire candle window from scratch. The feature-engine (T-110) holds
the rolling buffer.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.features.errors import FeatureUnderflowError
from packages.features.types import FeatureValue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from packages.features.types import OhlcCandle

__all__ = ["EmaFeature"]


class EmaFeature:
    """EMA indicator parameterised by ``period`` and candle ``interval``."""

    def __init__(self, period: int, interval: str = "15m") -> None:
        if period < 1:
            msg = f"period must be >= 1, got {period}"
            raise ValueError(msg)
        self.period = period
        self.interval = interval
        self.name_template = f"ind.{{symbol}}.{{interval}}.ema_{period}"
        self.source_version = "builtin.ema.v1"
        self.warmup_candles = period

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue:
        if len(candles) < self.warmup_candles:
            msg = (
                f"EMA(period={self.period}) requires >= {self.warmup_candles} "
                f"candles, got {len(candles)}"
            )
            raise FeatureUnderflowError(msg)
        closes = [c.close for c in candles]
        sma_seed = sum(closes[: self.period], Decimal(0)) / Decimal(self.period)
        alpha = Decimal(2) / Decimal(self.period + 1)
        ema = sma_seed
        for close in closes[self.period :]:
            ema = close * alpha + ema * (Decimal(1) - alpha)
        return FeatureValue(value_num=ema)
