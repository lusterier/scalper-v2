"""Cumulative session VWAP — daily session (UTC midnight reset) (§9.3, §B.2).

For the latest candle's session window::

    typical_price_i = (high_i + low_i + close_i) / 3
    VWAP = sum(typical_i * volume_i) / sum(volume_i)

The engine (T-110) supplies an arbitrarily-wide window; this indicator
filters to candles whose ``bucket_start.date()`` matches the latest
candle's UTC date (single session). Older candles are silently dropped.

Zero-volume edge: if ``sum(volume_i) == 0`` over the filtered session,
raise :class:`FeatureUnderflowError` — VWAP is undefined on zero-volume
input. The engine sees this as "no VWAP for this bar" rather than a
silent fallback that would drift the published value into nonsense.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.features.errors import FeatureUnderflowError
from packages.features.types import FeatureValue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from packages.features.types import OhlcCandle

__all__ = ["VwapFeature"]


class VwapFeature:
    """Daily-session VWAP indicator (§B.2 ``vwap_session`` default).

    Session parameter is constructor-level for forward-compat with
    F1+ ``hourly``/``weekly`` sessions; the name_template suffix is
    the static ``_session`` per §B.2 verbatim, so the registry layer
    (T-111) carries ``params: {session: daily}`` alongside the name
    rather than encoding it twice.
    """

    def __init__(self, session: str = "daily", interval: str = "1m") -> None:
        if session != "daily":
            msg = f"session must be 'daily', got {session!r} (weekly/hourly are F1+)"
            raise ValueError(msg)
        self.session = session
        self.interval = interval
        self.name_template = "ind.{symbol}.{interval}.vwap_session"
        self.source_version = "builtin.vwap.v1"
        self.warmup_candles = 1

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue:
        if len(candles) < self.warmup_candles:
            msg = f"VWAP requires >= {self.warmup_candles} candles, got {len(candles)}"
            raise FeatureUnderflowError(msg)
        latest_date = candles[-1].bucket_start.date()
        session_candles = [c for c in candles if c.bucket_start.date() == latest_date]
        weighted = Decimal(0)
        total_volume = Decimal(0)
        for c in session_candles:
            typical = (c.high + c.low + c.close) / Decimal(3)
            weighted += typical * c.volume
            total_volume += c.volume
        if total_volume == 0:
            msg = (
                f"VWAP undefined on zero-volume session "
                f"({len(session_candles)} candles, sum(volume)=0)"
            )
            raise FeatureUnderflowError(msg)
        return FeatureValue(value_num=weighted / total_volume)
