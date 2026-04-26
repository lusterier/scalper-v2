"""Domain types consumed and produced by :class:`Feature` (§9.3, §8.4).

:class:`OhlcCandle` is the domain candle that :meth:`Feature.compute`
operates over. It mirrors the §7.2 ``ohlc_1m`` schema but lives here
rather than in :mod:`packages.bus` or :mod:`packages.market` so that
``packages.features`` stays decoupled from the wire and HTTP transport
layers — T-110 ``feature-engine`` maps the §8.4 wire
:class:`packages.bus.schemas.OhlcCandlePayload` → :class:`OhlcCandle`
at the seam (~10 LOC mapping fn there). Only **closed** candles cross
that seam (the wire-level ``is_closed`` flag has already discriminated
by then), so :class:`OhlcCandle` has no ``is_closed`` field.

:class:`FeatureValue` mirrors the §8.4 ``FeatureUpdate`` payload value
polymorphism in lean form: exactly one of ``value_num`` / ``value_bool``
/ ``value_json`` is populated. ``value_num`` is :class:`Decimal` to
preserve the input candles' ``NUMERIC(30, 12)`` precision through the
compute step; T-110 converts ``Decimal`` → ``float`` at the
``FeatureUpdate`` publish boundary to match the wire schema.

Metadata (``feature_name``, ``symbol``, ``computed_at``,
``source_version``) is intentionally **not** carried on
:class:`FeatureValue`. :meth:`Feature.compute` is a pure compute kernel
that does not know its own registered name; the feature-engine and
scoring layers hold the metadata alongside the value as needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003  # runtime annotation on frozen dataclass field
from decimal import Decimal  # noqa: TC003  # runtime annotation on frozen dataclass field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["FeatureValue", "OhlcCandle"]


@dataclass(frozen=True, slots=True)
class OhlcCandle:
    """Domain OHLC bucket — input to :meth:`Feature.compute` (§9.3, §7.2).

    ``interval`` is a free-form string (``"1m"``, ``"5m"``, ``"15m"``,
    ``"1h"``, ``"4h"``, ``"1d"``) because feature-engine subscribes to
    multiple intervals — the §8.4 wire payload pins ``interval`` to
    ``"1m"`` (the only interval published live), but the warmup path
    reads higher intervals from continuous aggregates (§7.2 caggs)
    and feeds them through this same domain type.

    Prices and volume are :class:`~decimal.Decimal` to preserve the
    full ``NUMERIC(30, 12)`` precision of the source data. Working in
    Decimal across the compute boundary avoids float-rounding drift
    that would otherwise accumulate through long warmup windows
    (e.g., 200-bar EMA, 14-bar ATR).
    """

    symbol: str
    interval: str
    bucket_start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """Computed feature value — return type of :meth:`Feature.compute`.

    Lean shape: exactly one of the three value fields is non-None,
    enforced via :meth:`__post_init__`. The three field types mirror
    the §8.4 ``FeatureUpdate`` wire schema's ``value_num``/
    ``value_bool``/``value_json`` polymorphism — internal
    representation uses :class:`Decimal` for precision; T-110
    converts to ``float`` at the wire-publish seam.

    Hashability: ``value_num`` and ``value_bool`` variants are
    hashable (Decimal and bool both are); the ``value_json`` variant
    is **not** hashable because :class:`~collections.abc.Mapping`
    instances (typically ``dict``) are unhashable. This is documented
    behaviour, not a defect — features whose result is a mapping
    (Bollinger bands, MACD signal/histogram) cannot be used as set
    members or dict keys without first being normalised by the caller.
    """

    value_num: Decimal | None = None
    value_bool: bool | None = None
    value_json: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        non_none = sum(
            1 for v in (self.value_num, self.value_bool, self.value_json) if v is not None
        )
        if non_none != 1:
            msg = (
                "FeatureValue must have exactly one of "
                "(value_num, value_bool, value_json) set; "
                f"got {non_none}"
            )
            raise ValueError(msg)
