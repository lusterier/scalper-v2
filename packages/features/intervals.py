"""Per-interval timeshift mapping (§9.3 bucket-end semantics).

:data:`INTERVAL_DELTA` maps an ``ohlc_*`` candle interval label to its
window length. Used by:

* T-110c :class:`~services.feature_engine.app.pipeline.FeaturePipeline`
  ``_build_update`` → ``computed_at = bucket_start + INTERVAL_DELTA[interval]``
  (1m live; F1+ multi-interval cagg-trigger extends without map change).
* T-112 :mod:`scripts.backfill_features` → same ``computed_at`` semantics
  for historical iteration over any interval.

Single source of truth (§N9 config-as-data); F1+ multi-interval extension
just adds keys here. ``KeyError`` on unknown interval is intentional
fail-loud per §0.4 — caller should validate against an allow-list (e.g.,
:data:`packages.db.queries.feature_engine._INTERVAL_TO_TABLE`) before
lookup.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping


__all__ = ["INTERVAL_DELTA"]


INTERVAL_DELTA: Final[Mapping[str, timedelta]] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}
