"""§10.2 simple comparison conditions (T-302).

8 Pydantic frozen models with ``strict=True`` to prevent Pydantic 2
union coercion (``True → Decimal("1")``) which would silently break
bool-variant detection in :meth:`evaluate`.

``value`` field is ``Decimal | bool`` (NOT ``Decimal | bool | str``):
:class:`packages.features.types.FeatureValue` per file lines 89-91 has
only ``value_num`` / ``value_bool`` / ``value_json`` variants — no
``value_str``. ``value_json`` deep equality stays deferred to T-305
plugin per OQ-5.

Implementation note (WG#2): each ``evaluate`` body branches on
``isinstance(self.value, bool)`` BEFORE ``isinstance(self.value, Decimal)``
— ``bool`` is a subtype of ``int`` in Python and order matters for the
ladder.
"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 — runtime use in isinstance() checks
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from .base import RuleContext


__all__ = [
    "BetweenCondition",
    "EqualsCondition",
    "GtCondition",
    "GteCondition",
    "InCondition",
    "LtCondition",
    "LteCondition",
    "NotEqualsCondition",
]


# region: helpers


def _lookup_feature_value(ctx: RuleContext) -> tuple[Any, dict[str, Any] | None]:
    """Defensive lookup. Returns (feature_value, None) on hit; (None, error_dict) on miss."""
    try:
        return ctx.feature_snapshot[ctx.feature_ref], None
    except KeyError:
        return None, {"error": "feature_missing", "feature_ref": ctx.feature_ref}


def _expected_variant_for_value(value: Decimal | bool) -> str:
    return "value_bool" if isinstance(value, bool) else "value_num"


def _got_variant(feature_value: Any) -> str:
    if feature_value.value_num is not None:
        return "value_num"
    if feature_value.value_bool is not None:
        return "value_bool"
    return "value_json"


def _type_mismatch(expected: str, feature_value: Any) -> dict[str, Any]:
    return {"error": "type_mismatch", "expected": expected, "got": _got_variant(feature_value)}


# region: equality


class EqualsCondition(BaseModel):
    """§10.2 ``equals`` — auto-detects numeric vs bool variant from `value` type."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["equals"] = "equals"
    value: Decimal | bool

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        feature_value, miss = _lookup_feature_value(ctx)
        if miss is not None:
            return False, miss
        if feature_value.value_json is not None:
            return False, {"error": "value_json equality unsupported"}
        if isinstance(self.value, bool):
            if feature_value.value_bool is None:
                return False, _type_mismatch("value_bool", feature_value)
            return feature_value.value_bool == self.value, None
        if feature_value.value_num is None:
            return False, _type_mismatch("value_num", feature_value)
        return feature_value.value_num == self.value, None


class NotEqualsCondition(BaseModel):
    """§10.2 ``not_equals`` — inverse of equals; same variant detection."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["not_equals"] = "not_equals"
    value: Decimal | bool

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        feature_value, miss = _lookup_feature_value(ctx)
        if miss is not None:
            return False, miss
        if feature_value.value_json is not None:
            return False, {"error": "value_json equality unsupported"}
        if isinstance(self.value, bool):
            if feature_value.value_bool is None:
                return False, _type_mismatch("value_bool", feature_value)
            return feature_value.value_bool != self.value, None
        if feature_value.value_num is None:
            return False, _type_mismatch("value_num", feature_value)
        return feature_value.value_num != self.value, None


# region: ordering


class GtCondition(BaseModel):
    """§10.2 ``gt`` — strict greater-than; numeric only."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["gt"] = "gt"
    value: Decimal

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        feature_value, miss = _lookup_feature_value(ctx)
        if miss is not None:
            return False, miss
        if feature_value.value_num is None:
            return False, _type_mismatch("value_num", feature_value)
        return feature_value.value_num > self.value, None


class GteCondition(BaseModel):
    """§10.2 ``gte`` — inclusive greater-than-or-equal; numeric only."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["gte"] = "gte"
    value: Decimal

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        feature_value, miss = _lookup_feature_value(ctx)
        if miss is not None:
            return False, miss
        if feature_value.value_num is None:
            return False, _type_mismatch("value_num", feature_value)
        return feature_value.value_num >= self.value, None


class LtCondition(BaseModel):
    """§10.2 ``lt`` — strict less-than; numeric only."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["lt"] = "lt"
    value: Decimal

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        feature_value, miss = _lookup_feature_value(ctx)
        if miss is not None:
            return False, miss
        if feature_value.value_num is None:
            return False, _type_mismatch("value_num", feature_value)
        return feature_value.value_num < self.value, None


class LteCondition(BaseModel):
    """§10.2 ``lte`` — inclusive less-than-or-equal; numeric only."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["lte"] = "lte"
    value: Decimal

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        feature_value, miss = _lookup_feature_value(ctx)
        if miss is not None:
            return False, miss
        if feature_value.value_num is None:
            return False, _type_mismatch("value_num", feature_value)
        return feature_value.value_num <= self.value, None


# region: range / membership


class BetweenCondition(BaseModel):
    """§10.2 ``between`` — inclusive ``min ≤ feature ≤ max``; numeric only."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["between"] = "between"
    min: Decimal
    max: Decimal

    @model_validator(mode="after")
    def _min_le_max(self) -> BetweenCondition:
        if self.min > self.max:
            msg = f"between condition: min ({self.min}) > max ({self.max})"
            raise ValueError(msg)
        return self

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        feature_value, miss = _lookup_feature_value(ctx)
        if miss is not None:
            return False, miss
        if feature_value.value_num is None:
            return False, _type_mismatch("value_num", feature_value)
        return self.min <= feature_value.value_num <= self.max, None


class InCondition(BaseModel):
    """§10.2 ``in`` — membership in a non-empty list of allowed values."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["in"] = "in"
    values: list[Decimal | bool] = Field(min_length=1)

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        feature_value, miss = _lookup_feature_value(ctx)
        if miss is not None:
            return False, miss
        # Variant-detect from FIRST element per Validation rule #3.
        first = self.values[0]
        if isinstance(first, bool):
            if feature_value.value_bool is None:
                return False, _type_mismatch("value_bool", feature_value)
            return feature_value.value_bool in self.values, None
        if feature_value.value_num is None:
            return False, _type_mismatch("value_num", feature_value)
        return feature_value.value_num in self.values, None
