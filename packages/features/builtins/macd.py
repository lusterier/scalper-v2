"""MACD — fast/slow EMA crossover indicator with signal line (§9.3).

macd_line[i]   = fast_ema[i] - slow_ema[i]   (aligned at slow's first-bar index)
signal_line[j] = SMA-seeded EMA of macd_line series, period = signal
histogram[j]   = macd_line[j] - signal_line[j]

Both fast_ema and slow_ema use :func:`_sma_seeded_ema_series` (same
algorithm as :class:`packages.features.builtins.EmaFeature`), aligned
at bar index ``slow - 1`` so they share the same time axis.

Warmup math (fast=12, slow=26, signal=9 default):
  bars 0..25  (26 closes) → first slow_ema at bar 25 (SMA seed)
  bars 0..25  → fast_ema series of length 15 (bars 11..25)
  bar 25       → first MACD-line value
  bars 26..33 → 8 more MACD-line values (total 9)
  bar 33       → first signal_line (SMA seed of 9 MACD-line values)
                → first complete (macd, signal, histogram) triple
  Required: 34 closes = slow + signal - 1 = 26 + 9 - 1.

Returns ``FeatureValue(value_json={"macd", "signal", "histogram"})``
with Decimal sub-keys; T-110 converts to float at the wire seam.

Hashability: as with :class:`packages.features.builtins.BollingerFeature`,
the returned :class:`FeatureValue` is the ``value_json`` variant — not
hashable per T-106 contract.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.features.errors import FeatureUnderflowError
from packages.features.types import FeatureValue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from packages.features.types import OhlcCandle

__all__ = ["MacdFeature"]


def _sma_seeded_ema_series(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """SMA-seeded EMA series matching :class:`EmaFeature` semantics.

    Returns the EMA value at each bar from index ``period - 1`` onwards
    (inclusive). Output length is ``len(values) - period + 1``.

    **Index alignment** (load-bearing for :class:`MacdFeature`): output
    index ``i`` corresponds to input bar index ``i + period - 1``. So
    ``_sma_seeded_ema_series(closes, 26)`` produces values for bars
    25, 26, 27, …; ``_sma_seeded_ema_series(closes, 12)`` produces
    values for bars 11, 12, 13, …. Two series with different periods
    can be aligned to the larger period's first-bar index by dropping
    the leading ``slow - fast`` values from the shorter-period series.

    A single-Decimal convenience equivalent to :class:`EmaFeature`'s
    output is just ``_sma_seeded_ema_series(values, period)[-1]`` —
    pinned by the test_builtins_macd golden cross-check.
    """
    if len(values) < period:
        msg = f"_sma_seeded_ema_series requires >= {period} values, got {len(values)}"
        raise ValueError(msg)
    period_dec = Decimal(period)
    sma_seed = sum(values[:period], Decimal(0)) / period_dec
    alpha = Decimal(2) / (period_dec + 1)
    one_minus_alpha = Decimal(1) - alpha
    series = [sma_seed]
    for v in values[period:]:
        series.append(v * alpha + series[-1] * one_minus_alpha)
    return series


class MacdFeature:
    """MACD indicator parameterised by ``fast``/``slow``/``signal`` periods."""

    def __init__(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        interval: str = "15m",
    ) -> None:
        if fast < 1 or slow < 1 or signal < 1:
            msg = f"fast/slow/signal must be >= 1, got fast={fast} slow={slow} signal={signal}"
            raise ValueError(msg)
        if fast >= slow:
            msg = f"fast must be < slow, got fast={fast} slow={slow}"
            raise ValueError(msg)
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.interval = interval
        self.name_template = f"ind.{{symbol}}.{{interval}}.macd_{fast}_{slow}_{signal}"
        self.source_version = "builtin.macd.v1"
        self.warmup_candles = slow + signal - 1

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue:
        if len(candles) < self.warmup_candles:
            msg = (
                f"MACD(fast={self.fast},slow={self.slow},signal={self.signal}) "
                f"requires >= {self.warmup_candles} candles, got {len(candles)}"
            )
            raise FeatureUnderflowError(msg)
        closes = [c.close for c in candles]
        slow_ema = _sma_seeded_ema_series(closes, self.slow)
        fast_ema_full = _sma_seeded_ema_series(closes, self.fast)
        # _sma_seeded_ema_series(closes, slow) starts at bar (slow - 1);
        # _sma_seeded_ema_series(closes, fast) starts at bar (fast - 1).
        # Drop the first (slow - fast) fast_ema values to share slow_ema's
        # first-bar index. After this slice, fast_ema[i] and slow_ema[i]
        # are at the same bar.
        fast_ema = fast_ema_full[self.slow - self.fast :]
        macd_line = [f - s for f, s in zip(fast_ema, slow_ema, strict=True)]
        signal_line_series = _sma_seeded_ema_series(macd_line, self.signal)
        macd_value = macd_line[-1]
        signal_value = signal_line_series[-1]
        return FeatureValue(
            value_json={
                "macd": macd_value,
                "signal": signal_value,
                "histogram": macd_value - signal_value,
            },
        )
