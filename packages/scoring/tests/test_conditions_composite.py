"""§N5 unit tests for :mod:`packages.scoring.conditions.composite` (T-304).

TDD discipline (§N4 spirit per WG#3 T-200 precedent): tests written
before implementation. Four composite condition variants per BRIEF
§10.2:1703-1706 — and/or/not/when_then_else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from packages.bus.schemas.signals import SignalValidated
from packages.features.types import FeatureValue
from packages.scoring.conditions import (
    AndCondition,
    BetweenCondition,
    Condition,
    EqualsCondition,
    GtCondition,
    LtCondition,
    NotCondition,
    OrCondition,
    RisingCondition,
    RuleContext,
    WhenThenElseCondition,
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


def _ctx(value: int | float | str) -> RuleContext:
    return RuleContext(
        signal=_signal(),
        feature_snapshot={"f1": FeatureValue(value_num=Decimal(str(value)))},
        feature_ref="f1",
    )


# region: AND ----------------------------------------------------------------


def test_and_all_subs_true_returns_true() -> None:
    c = AndCondition(
        conditions=[
            GtCondition(value=Decimal("50000")),
            LtCondition(value=Decimal("60000")),
        ]
    )
    assert c.evaluate(_ctx(55000)) == (True, None)


def test_and_first_sub_false_short_circuits() -> None:
    """First sub False → AndCondition returns (False, None) immediately.

    Test instruments second sub with a class that raises NotImplementedError
    on evaluate; if short-circuit works, the second is never called.
    """

    class _ExplodingCondition(BaseModel):
        model_config = ConfigDict(frozen=True, strict=True)
        type: str = "exploding"

        def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
            raise NotImplementedError("should not be called")

    first = GtCondition(value=Decimal("50000"))  # 40000 > 50000 → False
    second = _ExplodingCondition()
    c = AndCondition(conditions=[first, second])
    assert c.evaluate(_ctx(40000)) == (False, None)  # no NotImplementedError raised


def test_and_second_sub_false_returns_false() -> None:
    c = AndCondition(
        conditions=[
            GtCondition(value=Decimal("50000")),
            LtCondition(value=Decimal("60000")),
        ]
    )
    # 70000 > 50000 True; 70000 < 60000 False
    assert c.evaluate(_ctx(70000)) == (False, None)


def test_and_three_subs_all_true() -> None:
    c = AndCondition(
        conditions=[
            GtCondition(value=Decimal("100")),
            LtCondition(value=Decimal("1000")),
            BetweenCondition(min=Decimal("400"), max=Decimal("600")),
        ]
    )
    assert c.evaluate(_ctx(500)) == (True, None)


# region: OR -----------------------------------------------------------------


def test_or_first_sub_true_short_circuits() -> None:
    """First sub True → OrCondition returns (True, None) without evaluating remainder."""

    class _ExplodingCondition(BaseModel):
        model_config = ConfigDict(frozen=True, strict=True)
        type: str = "exploding"

        def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
            raise NotImplementedError("should not be called")

    first = EqualsCondition(value=Decimal("100"))
    second = _ExplodingCondition()
    c = OrCondition(conditions=[first, second])
    assert c.evaluate(_ctx(100)) == (True, None)


def test_or_all_subs_false_returns_false() -> None:
    c = OrCondition(
        conditions=[
            GtCondition(value=Decimal("70000")),
            LtCondition(value=Decimal("30000")),
        ]
    )
    assert c.evaluate(_ctx(50000)) == (False, None)


def test_or_middle_sub_true_returns_true() -> None:
    c = OrCondition(
        conditions=[
            EqualsCondition(value=Decimal("1")),
            EqualsCondition(value=Decimal("2")),
            EqualsCondition(value=Decimal("3")),
        ]
    )
    assert c.evaluate(_ctx(2)) == (True, None)


def test_or_three_subs_all_false() -> None:
    c = OrCondition(
        conditions=[
            EqualsCondition(value=Decimal("10")),
            EqualsCondition(value=Decimal("20")),
            EqualsCondition(value=Decimal("30")),
        ]
    )
    assert c.evaluate(_ctx(99)) == (False, None)


# region: NOT ---------------------------------------------------------------


def test_not_inverts_true_to_false() -> None:
    c = NotCondition(condition=GtCondition(value=Decimal("50000")))
    assert c.evaluate(_ctx(60000)) == (False, None)


def test_not_inverts_false_to_true() -> None:
    c = NotCondition(condition=GtCondition(value=Decimal("50000")))
    assert c.evaluate(_ctx(40000)) == (True, None)


def test_not_of_erroring_sub_returns_true_documented_surprise() -> None:
    """NOT-of-(False, error) returns (True, None) per pure-boolean inversion.

    Documented surprise: error_info is swallowed at composite layer; operator
    who wants strict error propagation avoids ``not`` on erroring sub.
    """
    # GtCondition with feature_ref missing returns (False, error_dict).
    c = NotCondition(condition=GtCondition(value=Decimal("50000")))
    ctx = RuleContext(signal=_signal(), feature_snapshot={}, feature_ref="missing")
    assert c.evaluate(ctx) == (True, None)


# region: WHEN_THEN_ELSE ----------------------------------------------------


def test_when_then_else_when_true_dispatches_then() -> None:
    c = WhenThenElseCondition(
        when=GtCondition(value=Decimal("50000")),
        then_=LtCondition(value=Decimal("60000")),
        else_=GtCondition(value=Decimal("40000")),
    )
    # 55000 > 50000 → when True → dispatch then_; 55000 < 60000 → True
    assert c.evaluate(_ctx(55000)) == (True, None)


def test_when_then_else_when_false_dispatches_else() -> None:
    c = WhenThenElseCondition(
        when=GtCondition(value=Decimal("50000")),
        then_=LtCondition(value=Decimal("60000")),
        else_=GtCondition(value=Decimal("40000")),
    )
    # 45000 > 50000 → when False → dispatch else_; 45000 > 40000 → True
    assert c.evaluate(_ctx(45000)) == (True, None)


def test_when_then_else_when_error_dispatches_else_documented_surprise() -> None:
    """When sub returns (False, error) → composite dispatches to else_."""
    c = WhenThenElseCondition(
        when=GtCondition(value=Decimal("50000")),
        then_=LtCondition(value=Decimal("60000")),
        else_=EqualsCondition(value=Decimal("99")),
    )
    # ctx with missing feature_ref: when returns (False, error) → dispatch else_
    ctx = RuleContext(signal=_signal(), feature_snapshot={}, feature_ref="missing")
    # else_ also fails (feature_missing), returns (False, error)
    outcome, err = c.evaluate(ctx)
    assert outcome is False
    # else_ error_info is preserved (when_then_else returns the dispatched branch's tuple)
    assert err is not None
    assert err["error"] == "feature_missing"


def test_when_then_else_then_branch_returns_false() -> None:
    c = WhenThenElseCondition(
        when=GtCondition(value=Decimal("50000")),
        then_=LtCondition(value=Decimal("60000")),
        else_=GtCondition(value=Decimal("40000")),
    )
    # 65000 > 50000 → when True → dispatch then_; 65000 < 60000 → False
    assert c.evaluate(_ctx(65000)) == (False, None)


# region: construction validation -------------------------------------------


def test_and_empty_conditions_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        AndCondition(conditions=[])


def test_or_empty_conditions_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        OrCondition(conditions=[])


def test_and_non_condition_sub_rejected_at_construction() -> None:
    """Pydantic field type is BaseModel; a raw int can't even reach the validator.

    Pydantic validates field type first → ValidationError at field-level.
    """
    with pytest.raises(ValidationError):
        AndCondition(conditions=[42])  # type: ignore[list-item]


def test_not_non_condition_sub_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        NotCondition(condition=42)  # type: ignore[arg-type]


def test_when_then_else_non_condition_sub_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        WhenThenElseCondition(
            when=GtCondition(value=Decimal("1")),
            then_=42,  # type: ignore[arg-type]
            else_=GtCondition(value=Decimal("2")),
        )


def test_and_basemodel_non_condition_subclass_rejected_via_model_validator() -> None:
    """BaseModel that doesn't satisfy Condition (no evaluate method) rejected."""

    class _NotACondition(BaseModel):
        model_config = ConfigDict(frozen=True)
        # No `type` field, no `evaluate` method — fails @runtime_checkable.

    with pytest.raises(ValidationError, match="does not satisfy Condition"):
        AndCondition(conditions=[_NotACondition()])


# region: discriminator pin -------------------------------------------------


def test_and_discriminator_value() -> None:
    assert AndCondition(conditions=[GtCondition(value=Decimal("1"))]).type == "and"


def test_or_discriminator_value() -> None:
    assert OrCondition(conditions=[GtCondition(value=Decimal("1"))]).type == "or"


def test_not_discriminator_value() -> None:
    assert NotCondition(condition=GtCondition(value=Decimal("1"))).type == "not"


def test_when_then_else_discriminator_value() -> None:
    c = WhenThenElseCondition(
        when=GtCondition(value=Decimal("1")),
        then_=GtCondition(value=Decimal("2")),
        else_=GtCondition(value=Decimal("3")),
    )
    assert c.type == "when_then_else"


# region: frozen invariant --------------------------------------------------


def test_and_frozen_rejects_mutation() -> None:
    c = AndCondition(conditions=[GtCondition(value=Decimal("1"))])
    with pytest.raises(ValidationError):
        c.conditions = [LtCondition(value=Decimal("2"))]


def test_or_frozen_rejects_mutation() -> None:
    c = OrCondition(conditions=[GtCondition(value=Decimal("1"))])
    with pytest.raises(ValidationError):
        c.conditions = [LtCondition(value=Decimal("2"))]


def test_not_frozen_rejects_mutation() -> None:
    c = NotCondition(condition=GtCondition(value=Decimal("1")))
    with pytest.raises(ValidationError):
        c.condition = LtCondition(value=Decimal("2"))


def test_when_then_else_frozen_rejects_mutation() -> None:
    c = WhenThenElseCondition(
        when=GtCondition(value=Decimal("1")),
        then_=GtCondition(value=Decimal("2")),
        else_=GtCondition(value=Decimal("3")),
    )
    with pytest.raises(ValidationError):
        c.when = LtCondition(value=Decimal("4"))


# region: Protocol runtime_checkable ----------------------------------------


def test_and_satisfies_condition_protocol() -> None:
    c: object = AndCondition(conditions=[GtCondition(value=Decimal("1"))])
    assert isinstance(c, Condition)


def test_or_satisfies_condition_protocol() -> None:
    c: object = OrCondition(conditions=[GtCondition(value=Decimal("1"))])
    assert isinstance(c, Condition)


def test_not_satisfies_condition_protocol() -> None:
    c: object = NotCondition(condition=GtCondition(value=Decimal("1")))
    assert isinstance(c, Condition)


def test_when_then_else_satisfies_condition_protocol() -> None:
    c: object = WhenThenElseCondition(
        when=GtCondition(value=Decimal("1")),
        then_=GtCondition(value=Decimal("2")),
        else_=GtCondition(value=Decimal("3")),
    )
    assert isinstance(c, Condition)


# region: recursive nesting -------------------------------------------------


def test_2_level_nested_composite_evaluates_correctly() -> None:
    """AndCondition([OrCondition([Gt(1), Lt(0)]), NotCondition(Equals(5))])."""
    inner_or = OrCondition(
        conditions=[
            GtCondition(value=Decimal("1")),
            LtCondition(value=Decimal("0")),
        ]
    )
    inner_not = NotCondition(condition=EqualsCondition(value=Decimal("5")))
    outer = AndCondition(conditions=[inner_or, inner_not])
    # value=3: Or(3>1=True, 3<0=False) = True; Not(3==5=False) = True; And = True
    assert outer.evaluate(_ctx(3)) == (True, None)


def test_3_level_nested_composite_evaluates_correctly() -> None:
    """Deep nest: And(Or(Not(...), ...), ...)."""
    deep = AndCondition(
        conditions=[
            OrCondition(
                conditions=[
                    NotCondition(condition=EqualsCondition(value=Decimal("0"))),
                    GtCondition(value=Decimal("100")),
                ]
            ),
            LtCondition(value=Decimal("1000")),
        ]
    )
    # value=50: Or(Not(50==0=False)=True, 50>100=False) = True; 50<1000 = True; And = True
    assert deep.evaluate(_ctx(50)) == (True, None)


# region: cross-T-302+T-303 composability -----------------------------------


def test_mixed_simple_series_composite_in_and() -> None:
    """AndCondition with [GtCondition (T-302), RisingCondition (T-303), EqualsCondition (T-302)]."""
    rising = RisingCondition(n_samples=2)
    history = [
        FeatureValue(value_num=Decimal("100")),
        FeatureValue(value_num=Decimal("200")),
    ]
    c = AndCondition(
        conditions=[
            GtCondition(value=Decimal("50")),  # 200 > 50
            rising,  # [100, 200] strict ascending → True
            EqualsCondition(value=Decimal("200")),  # 200 == 200
        ]
    )
    ctx = RuleContext(
        signal=_signal(),
        feature_snapshot={"f1": FeatureValue(value_num=Decimal("200"))},
        feature_ref="f1",
        feature_history={"f1": history},
    )
    assert c.evaluate(ctx) == (True, None)
