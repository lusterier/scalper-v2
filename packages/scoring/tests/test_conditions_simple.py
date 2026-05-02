"""§N5 unit tests for :mod:`packages.scoring.conditions.simple` (T-302).

TDD discipline (§N4 spirit per WG#3 T-200 precedent): tests written
before implementation. Each condition's evaluate body landed AFTER the
corresponding test pin set fails red, then passes green.

Mock-free: each test constructs condition + RuleContext + FeatureValue
and asserts the (outcome, error_info) tuple directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.bus.schemas.signals import SignalValidated
from packages.features.types import FeatureValue
from packages.scoring.conditions import (
    BetweenCondition,
    Condition,
    EqualsCondition,
    GtCondition,
    GteCondition,
    InCondition,
    LtCondition,
    LteCondition,
    NotEqualsCondition,
    RuleContext,
)


def _signal() -> SignalValidated:
    return SignalValidated(
        source="webhook",
        idempotency_key="test-key-1",
        received_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
        symbol="BTCUSDT",
        original_symbol="BTCUSDT",
        action="LONG",
        expires_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=60),
        payload={},
    )


def _ctx(fv: FeatureValue, ref: str = "f1") -> RuleContext:
    return RuleContext(
        signal=_signal(),
        feature_snapshot={"f1": fv} if ref == "f1" else {},
        feature_ref=ref,
    )


# region: equality ----------------------------------------------------------


def test_equals_numeric_match() -> None:
    c = EqualsCondition(value=Decimal("50000"))
    ctx = _ctx(FeatureValue(value_num=Decimal("50000")))
    assert c.evaluate(ctx) == (True, None)


def test_equals_numeric_mismatch_exact_decimal_inequality() -> None:
    """Decimal exact inequality — no rounding."""
    c = EqualsCondition(value=Decimal("50000"))
    ctx = _ctx(FeatureValue(value_num=Decimal("50000.0001")))
    assert c.evaluate(ctx) == (False, None)


def test_equals_bool_variant() -> None:
    c = EqualsCondition(value=True)
    ctx = _ctx(FeatureValue(value_bool=True))
    assert c.evaluate(ctx) == (True, None)


def test_equals_type_mismatch_decimal_value_against_bool_feature() -> None:
    c = EqualsCondition(value=Decimal("1"))
    ctx = _ctx(FeatureValue(value_bool=True))
    outcome, err = c.evaluate(ctx)
    assert outcome is False
    assert err == {"error": "type_mismatch", "expected": "value_num", "got": "value_bool"}


def test_equals_value_json_unsupported() -> None:
    c = EqualsCondition(value=Decimal("1"))
    ctx = _ctx(FeatureValue(value_json={"k": "v"}))
    outcome, err = c.evaluate(ctx)
    assert outcome is False
    assert err is not None
    assert err["error"] == "value_json equality unsupported"


def test_not_equals_numeric_inverse() -> None:
    c = NotEqualsCondition(value=Decimal("50000"))
    ctx = _ctx(FeatureValue(value_num=Decimal("50001")))
    assert c.evaluate(ctx) == (True, None)


# region: ordering ----------------------------------------------------------


def test_gt_strict_above() -> None:
    c = GtCondition(value=Decimal("50000"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("50001")))) == (True, None)


def test_gt_strict_at_boundary_returns_false() -> None:
    """Strict `>`: feature == value returns False."""
    c = GtCondition(value=Decimal("50000"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("50000")))) == (False, None)


def test_gte_inclusive_at_boundary_returns_true() -> None:
    """Inclusive `>=`: feature == value returns True."""
    c = GteCondition(value=Decimal("50000"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("50000")))) == (True, None)


def test_lt_strict_below() -> None:
    c = LtCondition(value=Decimal("50000"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("49999")))) == (True, None)


def test_lt_strict_at_boundary_returns_false() -> None:
    c = LtCondition(value=Decimal("50000"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("50000")))) == (False, None)


def test_lte_inclusive_at_boundary() -> None:
    c = LteCondition(value=Decimal("50000"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("50000")))) == (True, None)


def test_gt_type_mismatch_against_bool_feature() -> None:
    c = GtCondition(value=Decimal("50000"))
    outcome, err = c.evaluate(_ctx(FeatureValue(value_bool=True)))
    assert outcome is False
    assert err == {"error": "type_mismatch", "expected": "value_num", "got": "value_bool"}


# region: between -----------------------------------------------------------


def test_between_inclusive_min_boundary() -> None:
    c = BetweenCondition(min=Decimal("100"), max=Decimal("200"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("100")))) == (True, None)


def test_between_inclusive_max_boundary() -> None:
    c = BetweenCondition(min=Decimal("100"), max=Decimal("200"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("200")))) == (True, None)


def test_between_inclusive_mid_value() -> None:
    c = BetweenCondition(min=Decimal("100"), max=Decimal("200"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("150")))) == (True, None)


def test_between_below_range() -> None:
    c = BetweenCondition(min=Decimal("100"), max=Decimal("200"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("99.999")))) == (False, None)


def test_between_above_range() -> None:
    c = BetweenCondition(min=Decimal("100"), max=Decimal("200"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("200.001")))) == (False, None)


def test_between_min_equals_max_degenerate() -> None:
    """min == max valid — single-value match."""
    c = BetweenCondition(min=Decimal("100"), max=Decimal("100"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("100")))) == (True, None)


def test_between_min_greater_than_max_raises_at_construction() -> None:
    with pytest.raises(ValidationError, match=r"min .* > max"):
        BetweenCondition(min=Decimal("200"), max=Decimal("100"))


# region: in ----------------------------------------------------------------


def test_in_membership_true() -> None:
    c = InCondition(values=[Decimal("1"), Decimal("2"), Decimal("3")])
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("2")))) == (True, None)


def test_in_membership_false() -> None:
    c = InCondition(values=[Decimal("1"), Decimal("2")])
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("3")))) == (False, None)


def test_in_empty_values_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        InCondition(values=[])


# region: discriminator pin -------------------------------------------------


def test_equals_discriminator_value() -> None:
    assert EqualsCondition(value=Decimal("1")).type == "equals"


def test_gt_discriminator_value() -> None:
    assert GtCondition(value=Decimal("1")).type == "gt"


def test_between_discriminator_value() -> None:
    assert BetweenCondition(min=Decimal("1"), max=Decimal("2")).type == "between"


def test_in_discriminator_value() -> None:
    assert InCondition(values=[Decimal("1")]).type == "in"


# region: frozen invariant --------------------------------------------------


def test_equals_frozen_rejects_mutation() -> None:
    c = EqualsCondition(value=Decimal("1"))
    with pytest.raises(ValidationError):
        c.value = Decimal("2")


def test_between_frozen_rejects_mutation() -> None:
    c = BetweenCondition(min=Decimal("1"), max=Decimal("2"))
    with pytest.raises(ValidationError):
        c.min = Decimal("0")


def test_in_frozen_rejects_mutation() -> None:
    c = InCondition(values=[Decimal("1")])
    with pytest.raises(ValidationError):
        c.values = [Decimal("2")]


# region: Decimal precision -------------------------------------------------


def test_decimal_precision_exact_no_float_coercion() -> None:
    """Decimal('50000.0001') != Decimal('50000') exact (no rounding)."""
    c = EqualsCondition(value=Decimal("50000"))
    assert c.evaluate(_ctx(FeatureValue(value_num=Decimal("50000.0001")))) == (False, None)


# region: strict-mode bool pin (CONCERN-2 fix) ------------------------------


def test_pydantic_strict_mode_keeps_true_as_bool() -> None:
    """Pydantic 2 strict-mode disambiguates `Decimal | bool` union — `True` stays bool.

    Without strict=True, smart-mode would coerce True → Decimal('1') and silently
    break bool-variant detection in evaluate().
    """
    c = EqualsCondition(value=True)
    assert type(c.value) is bool
    assert c.value is True


# region: Protocol runtime_checkable pin ------------------------------------


def test_equals_satisfies_condition_protocol_via_isinstance() -> None:
    """@runtime_checkable enables isinstance dispatch in T-307 evaluator."""
    c: object = EqualsCondition(value=Decimal("1"))
    assert isinstance(c, Condition)


def test_between_satisfies_condition_protocol_via_isinstance() -> None:
    c: object = BetweenCondition(min=Decimal("1"), max=Decimal("2"))
    assert isinstance(c, Condition)


# region: feature_missing defensive pin -------------------------------------


def test_evaluate_returns_feature_missing_when_ref_not_in_snapshot() -> None:
    """Defensive KeyError catch — racy snapshot edge case."""
    c = GtCondition(value=Decimal("1"))
    ctx = RuleContext(
        signal=_signal(),
        feature_snapshot={},  # empty
        feature_ref="missing_feature",
    )
    outcome, err = c.evaluate(ctx)
    assert outcome is False
    assert err == {"error": "feature_missing", "feature_ref": "missing_feature"}
