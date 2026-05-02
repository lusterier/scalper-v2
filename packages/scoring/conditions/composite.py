"""§10.2 composite conditions (T-304): and, or, not, when_then_else.

Four condition variants per BRIEF §10.2:1703-1706:

* :class:`AndCondition` — all sub-conditions True. Short-circuits on first False.
* :class:`OrCondition` — any sub-condition True. Short-circuits on first True.
* :class:`NotCondition` — negation of single sub-condition outcome.
* :class:`WhenThenElseCondition` — ternary: evaluate ``when``; dispatch to
  ``then_`` if True, ``else_`` if False.

Sub-condition type is :class:`pydantic.BaseModel` at schema layer; runtime
validation via ``model_validator(mode="after")`` checks ``isinstance(sub,
Condition)`` per T-302 ``@runtime_checkable`` Protocol.

**Sub-condition error handling**: composites are PURE BOOLEAN COMBINATORS.
Sub's ``error_info`` dict is **swallowed** at composite level — outcome is
treated as raw boolean regardless of error_info. T-307 evaluator's
``on_error`` policy applies at TOP-level rule.condition.evaluate, not
at sub-level.

**Documented surprises**:

* ``NotCondition`` of error-sub: ``(False, {"error": "..."})`` →
  ``(True, None)``. The error's underlying state is "unknown / failed",
  but composite reports True per pure-boolean inversion.
* ``WhenThenElseCondition`` with erroring ``when``: dispatch to ``else_``
  branch (boolean False after error swallow).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import Condition

if TYPE_CHECKING:
    from .base import RuleContext


__all__ = [
    "AndCondition",
    "NotCondition",
    "OrCondition",
    "WhenThenElseCondition",
]


def _check_subs_are_conditions(subs: list[BaseModel], field_name: str) -> None:
    """Validate every sub satisfies T-302 @runtime_checkable Condition Protocol.

    Raises ``ValueError`` (NOT ``TypeError``) so Pydantic wraps in
    ``ValidationError`` per BetweenCondition._min_le_max precedent at
    simple.py:201-202.
    """
    for i, sub in enumerate(subs):
        if not isinstance(sub, Condition):
            msg = f"{field_name}[{i}] does not satisfy Condition protocol"
            raise ValueError(msg)


def _check_sub_is_condition(sub: BaseModel, field_name: str) -> None:
    if not isinstance(sub, Condition):
        msg = f"{field_name} does not satisfy Condition protocol"
        raise ValueError(msg)


class AndCondition(BaseModel):
    """§10.2 ``and`` — all sub-conditions True. Short-circuits on first False."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["and"] = "and"
    # BaseModel + isinstance via @runtime_checkable Condition (T-302); discriminated
    # union construction lives in yaml_loader.py (T-308) — kept private to that module.
    conditions: list[BaseModel] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_subs(self) -> AndCondition:
        _check_subs_are_conditions(self.conditions, "conditions")
        return self

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        for sub in self.conditions:
            outcome, _err = sub.evaluate(ctx)  # type: ignore[attr-defined]
            if not outcome:
                return False, None
        return True, None


class OrCondition(BaseModel):
    """§10.2 ``or`` — any sub-condition True. Short-circuits on first True."""

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["or"] = "or"
    # BaseModel + isinstance via @runtime_checkable Condition (T-302); discriminated
    # union construction lives in yaml_loader.py (T-308) — kept private to that module.
    conditions: list[BaseModel] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_subs(self) -> OrCondition:
        _check_subs_are_conditions(self.conditions, "conditions")
        return self

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        for sub in self.conditions:
            outcome, _err = sub.evaluate(ctx)  # type: ignore[attr-defined]
            if outcome:
                return True, None
        return False, None


class NotCondition(BaseModel):
    """§10.2 ``not`` — negation of single sub-condition outcome.

    **Documented surprise**: NOT of an erroring sub returns ``(True, None)``.
    The error's underlying state is "unknown / failed", but composite reports
    True per pure-boolean inversion. Operator who wants strict error
    propagation avoids ``not`` on potentially-erroring sub.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["not"] = "not"
    # BaseModel + isinstance via @runtime_checkable Condition (T-302); discriminated
    # union construction lives in yaml_loader.py (T-308) — kept private to that module.
    condition: BaseModel

    @model_validator(mode="after")
    def _check_sub(self) -> NotCondition:
        _check_sub_is_condition(self.condition, "condition")
        return self

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        outcome, _err = self.condition.evaluate(ctx)  # type: ignore[attr-defined]
        return not outcome, None


class WhenThenElseCondition(BaseModel):
    """§10.2 ``when_then_else`` — ternary: evaluate ``when``; dispatch to ``then_`` or ``else_``.

    Field-name asymmetry: ``when`` has no trailing underscore (not a Python
    keyword); ``then_`` / ``else_`` have trailing underscore per PEP-8
    keyword conflict resolution (``then``/``else`` ARE Python keywords).
    Discriminator string ``"when_then_else"`` stays verbatim per BRIEF §10.2.

    **Documented surprise**: if ``when`` returns ``(False, error_info)``,
    composite dispatches to ``else_`` branch (boolean False after error
    swallow). Caller-aware behavior.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    type: Literal["when_then_else"] = "when_then_else"
    # BaseModel + isinstance via @runtime_checkable Condition (T-302); discriminated
    # union construction lives in yaml_loader.py (T-308) — kept private to that module.
    when: BaseModel
    then_: BaseModel
    else_: BaseModel

    @model_validator(mode="after")
    def _check_subs(self) -> WhenThenElseCondition:
        _check_sub_is_condition(self.when, "when")
        _check_sub_is_condition(self.then_, "then_")
        _check_sub_is_condition(self.else_, "else_")
        return self

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        when_outcome, _err = self.when.evaluate(ctx)  # type: ignore[attr-defined]
        if when_outcome:
            return self.then_.evaluate(ctx)  # type: ignore[attr-defined,no-any-return]
        return self.else_.evaluate(ctx)  # type: ignore[attr-defined,no-any-return]
