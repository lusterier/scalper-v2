"""§10.2 series conditions (T-303): rising, falling, ema_stack.

Three condition variants per BRIEF §10.2:1700-1702:

* :class:`RisingCondition` — strictly increasing over last N samples.
* :class:`FallingCondition` — strictly decreasing over last N samples.
* :class:`EmaStackCondition` — direction-aware ordered relationship of
  3 feature values at the CURRENT snapshot (multi-feature, NOT history).

Rising/falling read ``ctx.feature_history[ctx.feature_ref]`` (chronological
list of past samples, oldest → newest); ema_stack reads
``ctx.feature_snapshot[features[i]]`` for each of its 3 explicit refs.

Strict monotonicity per BRIEF §10.2:1701-1702 verbatim "increasing" /
"decreasing": equal-adjacent values FAIL (not "non-decreasing"). The
False outcome on non-monotonic but well-formed input returns
``(False, None)`` — NOT an error dict (per WG#5).
"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 — runtime use in evaluate
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .base import RuleContext


__all__ = [
    "EmaStackCondition",
    "FallingCondition",
    "RisingCondition",
]


def _check_history_window(
    ctx: RuleContext,
    n_samples: int,
) -> tuple[list[Decimal] | None, dict[str, Any] | None]:
    """Extract last N value_num samples from history; return None + error_dict on failure."""
    history = ctx.feature_history.get(ctx.feature_ref)
    if history is None:
        return None, {"error": "feature_history_missing", "feature_ref": ctx.feature_ref}
    if len(history) < n_samples:
        return None, {
            "error": "feature_history_too_short",
            "have": len(history),
            "need": n_samples,
        }
    window = history[-n_samples:]
    values: list[Decimal] = []
    for fv in window:
        if fv.value_num is None:
            return None, {"error": "type_mismatch", "expected": "value_num"}
        values.append(fv.value_num)
    return values, None


class RisingCondition(BaseModel):
    """§10.2 ``rising`` — strictly increasing over last ``n_samples`` samples."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["rising"] = "rising"
    n_samples: int = Field(ge=2)

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        values, err = _check_history_window(ctx, self.n_samples)
        if err is not None:
            return False, err
        assert values is not None  # narrowed by err check
        for i in range(1, len(values)):
            if not values[i] > values[i - 1]:
                return False, None
        return True, None


class FallingCondition(BaseModel):
    """§10.2 ``falling`` — strictly decreasing over last ``n_samples`` samples."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["falling"] = "falling"
    n_samples: int = Field(ge=2)

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        values, err = _check_history_window(ctx, self.n_samples)
        if err is not None:
            return False, err
        assert values is not None
        for i in range(1, len(values)):
            if not values[i] < values[i - 1]:
                return False, None
        return True, None


class EmaStackCondition(BaseModel):
    """§10.2 ``ema_stack`` — direction-aware ordered relationship of 3 features.

    ``direction="up"`` asserts ``features[0] > features[1] > features[2]``
    (e.g. ema_fast > ema_medium > ema_slow); ``direction="down"`` asserts
    strict ascending. Reads CURRENT snapshot, NOT history.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["ema_stack"] = "ema_stack"
    features: list[str] = Field(min_length=3, max_length=3)
    direction: Literal["up", "down"]

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        values: list[Decimal] = []
        for ref in self.features:
            try:
                fv = ctx.feature_snapshot[ref]
            except KeyError:
                return False, {"error": "feature_missing", "feature_ref": ref}
            if fv.value_num is None:
                return False, {
                    "error": "type_mismatch",
                    "expected": "value_num",
                    "feature_ref": ref,
                }
            values.append(fv.value_num)
        if self.direction == "up":
            for i in range(1, len(values)):
                if not values[i - 1] > values[i]:
                    return False, None
        else:  # direction == "down"
            for i in range(1, len(values)):
                if not values[i - 1] < values[i]:
                    return False, None
        return True, None
