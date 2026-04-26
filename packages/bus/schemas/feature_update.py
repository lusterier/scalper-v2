"""``features.updated.<feature_name>.<symbol>`` NATS payload schema (§8.4).

Published by ``feature-engine`` (T-110c ``FeaturePipeline``) onto the
``features.updated.{feature_name}.{symbol}`` subject of the FEATURES
stream after each closed-candle compute. Consumed by ``strategy-engine``
(F2) and the analytics-api / UI live-tail.

The schema mirrors §8.4 lines 1382-1393 verbatim. ``value_num`` /
``value_bool`` / ``value_json`` are wire-level floats / bools / dicts;
exactly one of the three is non-None — enforced by a model_validator
mirroring the :class:`packages.features.types.FeatureValue.__post_init__`
domain invariant. The Decimal→float seam happens on the producer side
(T-110c) before this schema is constructed.

``value_json`` stays ``dict[str, object] | None`` matching
:class:`FeatureValue.value_json: Mapping[str, object]` (L-004 contract)
— open to non-float values; T-110c handles Decimal→float conversion on
numeric sub-keys at the wire seam.

UTC enforcement on ``computed_at`` mirrors
:class:`packages.bus.MessageEnvelope` and
:class:`packages.bus.schemas.OhlcCandlePayload`: naive or non-zero-offset
values are rejected at validation; serialisation emits the explicit
``+00:00`` form per §5.12.

The :func:`subject_for` helper centralises the §8.1 line 1213 publish
subject literal so it lives in exactly one production code site (L-002
control). Callers outside this module MUST import :func:`subject_for`
rather than constructing the ``features.updated.…`` string inline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    field_serializer,
    field_validator,
    model_validator,
)

__all__ = ["FeatureUpdate", "subject_for"]


def subject_for(feature_name: str, symbol: str) -> str:
    """Build the §8.1 line 1213 publish subject ``features.updated.<feature_name>.<symbol>``.

    Centralised here so the spec literal lives in exactly one
    production code site (L-002 pattern). T-110c imports this helper
    rather than constructing the ``features.updated.…`` f-string at
    call sites.
    """
    return f"features.updated.{feature_name}.{symbol}"


class FeatureUpdate(BaseModel):
    """§8.4 ``features.updated.<feature_name>.<symbol>`` payload, frozen.

    Wire-level shape: floats/bools/dicts. T-110c converts the
    domain :class:`packages.features.types.FeatureValue` (Decimal-priced)
    to this wire form at the publish seam. ``schema_version`` pinned
    to ``"1.0"`` so a future breaking change forces an explicit
    bump (§8.6).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    feature_name: str
    symbol: str
    computed_at: datetime
    value_num: float | None = None
    value_bool: bool | None = None
    value_json: dict[str, object] | None = None
    source_version: str

    @field_validator("computed_at")
    @classmethod
    def _must_be_utc(cls, value: datetime) -> datetime:
        """Require UTC-aware datetimes; normalise tzinfo to :data:`datetime.UTC`."""
        if value.tzinfo is None:
            msg = "computed_at must be timezone-aware (tzinfo=datetime.UTC)"
            raise ValueError(msg)
        if value.utcoffset() != timedelta(0):
            msg = "computed_at must be in UTC (utcoffset must be zero)"
            raise ValueError(msg)
        return value if value.tzinfo is UTC else value.replace(tzinfo=UTC)

    @field_serializer("computed_at")
    def _serialize_computed_at(self, value: datetime) -> str:
        """ISO-8601 with explicit ``+00:00`` offset (§5.12)."""
        return value.isoformat()

    @model_validator(mode="after")
    def _exactly_one_value(self) -> Self:
        """Mirror :meth:`FeatureValue.__post_init__` invariant at the wire seam."""
        non_none = sum(
            1 for v in (self.value_num, self.value_bool, self.value_json) if v is not None
        )
        if non_none != 1:
            msg = (
                "FeatureUpdate must have exactly one of "
                "(value_num, value_bool, value_json) set; "
                f"got {non_none}"
            )
            raise ValueError(msg)
        return self
