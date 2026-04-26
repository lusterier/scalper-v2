"""Shared module-private constants for feature-engine (§N9 single-source-of-truth).

:data:`SOURCE_BINANCE` lives here so :mod:`pipeline` and :mod:`warmup`
reference the same literal (T-110d Write-time guidance #2). T-104b
publishes only Binance 1m candles per §8.4 docstring; F1+ multi-source
support requires an ADR. Until then, all feature-engine code paths
assume Binance-sourced OHLC.
"""

from __future__ import annotations

from typing import Final

__all__ = ["SOURCE_BINANCE"]


SOURCE_BINANCE: Final = "binance"
