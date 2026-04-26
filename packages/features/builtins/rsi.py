"""Wilder's Relative Strength Index (§9.3, §B.2).

For a sequence of closes:

- ``delta_i = close_i - close_{i-1}`` for ``i in 1..N`` (so we need
  ``period + 1`` closes to produce ``period`` deltas).
- ``gain_i = max(delta_i, 0)``; ``loss_i = max(-delta_i, 0)``.
- Seed: ``avg_gain = SMA`` of first ``period`` gains; ``avg_loss = SMA``
  of first ``period`` losses.
- Wilder smoothing for subsequent steps::

      avg_n = (avg_{n-1} * (period - 1) + current) / period

- If ``avg_loss == 0``: RSI = 100. Else ``RS = avg_gain / avg_loss``;
  ``RSI = 100 - 100 / (1 + RS)``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.features.errors import FeatureUnderflowError
from packages.features.types import FeatureValue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from packages.features.types import OhlcCandle

__all__ = ["RsiFeature"]


class RsiFeature:
    """RSI indicator parameterised by ``period`` and candle ``interval``."""

    def __init__(self, period: int, interval: str = "15m") -> None:
        if period < 1:
            msg = f"period must be >= 1, got {period}"
            raise ValueError(msg)
        self.period = period
        self.interval = interval
        self.name_template = f"ind.{{symbol}}.{{interval}}.rsi_{period}"
        self.source_version = "builtin.rsi.v1"
        self.warmup_candles = period + 1

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue:
        if len(candles) < self.warmup_candles:
            msg = (
                f"RSI(period={self.period}) requires >= {self.warmup_candles} "
                f"candles, got {len(candles)}"
            )
            raise FeatureUnderflowError(msg)
        closes = [c.close for c in candles]
        gains: list[Decimal] = []
        losses: list[Decimal] = []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            if delta >= 0:
                gains.append(delta)
                losses.append(Decimal(0))
            else:
                gains.append(Decimal(0))
                losses.append(-delta)

        period_dec = Decimal(self.period)
        avg_gain = sum(gains[: self.period], Decimal(0)) / period_dec
        avg_loss = sum(losses[: self.period], Decimal(0)) / period_dec
        for g, loss in zip(gains[self.period :], losses[self.period :], strict=True):
            avg_gain = (avg_gain * (period_dec - 1) + g) / period_dec
            avg_loss = (avg_loss * (period_dec - 1) + loss) / period_dec

        if avg_loss == 0:
            rsi = Decimal(100) if avg_gain > 0 else Decimal(50)
        else:
            rs = avg_gain / avg_loss
            rsi = Decimal(100) - Decimal(100) / (Decimal(1) + rs)
        return FeatureValue(value_num=rsi)
