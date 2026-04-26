"""Bollinger Bands — SMA middle ± k * sigma_pop bands (§9.3).

middle = SMA(close, period)
sigma_pop  = sqrt(sum((close_i - middle)^2 for i in last `period`) / period)  # population, /n
upper  = middle + std_dev_multiplier * sigma_pop
lower  = middle - std_dev_multiplier * sigma_pop

Returns ``FeatureValue(value_json={"upper", "middle", "lower"})`` with
Decimal sub-keys; T-110 converts to float at the wire seam.

Hashability: the returned :class:`FeatureValue` is the ``value_json``
variant, which per T-106 contract is **not** hashable (Mapping/dict
instances are not). Callers requiring set/dict membership must
normalise first.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.features.errors import FeatureUnderflowError
from packages.features.types import FeatureValue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from packages.features.types import OhlcCandle

__all__ = ["BollingerFeature"]


class BollingerFeature:
    """Bollinger Bands indicator parameterised by ``period`` and ``std_dev_multiplier``."""

    def __init__(
        self,
        period: int = 20,
        std_dev_multiplier: Decimal = Decimal("2"),
        interval: str = "15m",
    ) -> None:
        if period < 2:
            msg = f"period must be >= 2, got {period}"
            raise ValueError(msg)
        if std_dev_multiplier <= 0:
            msg = f"std_dev_multiplier must be > 0, got {std_dev_multiplier}"
            raise ValueError(msg)
        self.period = period
        self.std_dev_multiplier = std_dev_multiplier
        self.interval = interval
        # Stringify multiplier via .normalize() so Decimal("2") → "2"
        # (not "2E+0") and Decimal("2.5") → "2.5".
        mult_str = format(std_dev_multiplier.normalize(), "f")
        self.name_template = f"ind.{{symbol}}.{{interval}}.bollinger_{period}_{mult_str}"
        self.source_version = "builtin.bollinger.v1"
        self.warmup_candles = period

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue:
        if len(candles) < self.warmup_candles:
            msg = (
                f"Bollinger(period={self.period}) requires >= {self.warmup_candles} "
                f"candles, got {len(candles)}"
            )
            raise FeatureUnderflowError(msg)
        closes = [c.close for c in candles[-self.period :]]
        period_dec = Decimal(self.period)
        middle = sum(closes, Decimal(0)) / period_dec
        variance = sum(((c - middle) ** 2 for c in closes), Decimal(0)) / period_dec
        sigma = variance.sqrt()
        offset = self.std_dev_multiplier * sigma
        return FeatureValue(
            value_json={
                "upper": middle + offset,
                "middle": middle,
                "lower": middle - offset,
            },
        )
