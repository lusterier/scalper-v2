"""Feature port + value types (§9.3, §8.4).

Public API for built-in indicator implementations (T-107) and plugin
authors. :class:`Feature` is the port T-110 ``feature-engine`` calls
on closed-candle messages; :class:`FeatureValue` mirrors the §8.4
``FeatureUpdate`` wire schema with internal :class:`~decimal.Decimal`
precision (converted to ``float`` at the wire boundary).
"""

from __future__ import annotations

from .protocols import Feature
from .types import FeatureValue, OhlcCandle

__all__ = ["Feature", "FeatureValue", "OhlcCandle"]
