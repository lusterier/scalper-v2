"""Built-in :class:`~packages.features.Feature` implementations (§9.3, §B.2).

Three scalar indicators ship in T-107a (EMA, RSI, ATR); T-107b adds
VWAP, Bollinger, MACD. SMA, OI change, and funding rate listed by §9.3
remain F1+ (no current consumer).

Each indicator is a regular class with ``period``/``interval``
parameters in ``__init__`` and a stateless :meth:`compute` method that
re-processes the entire candle window each call. The feature-engine
(T-110) holds the rolling buffer and dispatches closed-candle events;
indicators do not cache prior state on the instance.
"""

from __future__ import annotations

from .atr import AtrFeature
from .bollinger import BollingerFeature
from .ema import EmaFeature
from .macd import MacdFeature
from .rsi import RsiFeature
from .vwap import VwapFeature

__all__ = [
    "AtrFeature",
    "BollingerFeature",
    "EmaFeature",
    "MacdFeature",
    "RsiFeature",
    "VwapFeature",
]
