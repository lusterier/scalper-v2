"""§N5 unit tests for :mod:`packages.scoring.protocol` (T-305).

TDD discipline (§N4 spirit per WG#3 T-200 precedent).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from packages.scoring import Rule as RuleFromPackage
from packages.scoring import RuleOutcome as RuleOutcomeFromPackage
from packages.scoring.conditions.base import RuleContext as ConditionsBaseRuleContext
from packages.scoring.protocol import Rule, RuleContext, RuleOutcome


def test_rule_outcome_round_trip() -> None:
    o = RuleOutcome(result=True, metadata={"k": "v"})
    assert o.result is True
    assert o.metadata == {"k": "v"}


def test_rule_outcome_metadata_default_none() -> None:
    o = RuleOutcome(result=False)
    assert o.metadata is None


def test_rule_abc_cannot_be_instantiated_directly() -> None:
    """ABCMeta forbids instantiation when abstract methods present."""
    with pytest.raises(TypeError):
        Rule({})  # type: ignore[abstract]


def test_rule_subclass_with_evaluate_can_instantiate() -> None:
    class _MyRule(Rule):
        name: ClassVar[str] = "x"
        version: ClassVar[str] = "1"

        def __init__(self, params: dict[str, Any]) -> None:
            self.params = params

        def evaluate(self, ctx: RuleContext) -> RuleOutcome:
            return RuleOutcome(result=True)

    instance = _MyRule({"foo": 1})
    assert isinstance(instance, Rule)
    assert instance.params == {"foo": 1}


def test_rule_subclass_without_evaluate_fails_at_instantiation() -> None:
    class _IncompleteRule(Rule):
        name: ClassVar[str] = "incomplete"
        version: ClassVar[str] = "1"

        def __init__(self, params: dict[str, Any]) -> None: ...

        # evaluate not implemented

    with pytest.raises(TypeError):
        _IncompleteRule({})  # type: ignore[abstract]


def test_rulecontext_re_exports_from_conditions_base() -> None:
    """§10.6:1799 — protocol.RuleContext is conditions.base.RuleContext (identity)."""
    assert RuleContext is ConditionsBaseRuleContext


def test_packages_scoring_re_exports_rule_and_rule_outcome() -> None:
    """WG#5 pin: `from packages.scoring import Rule, RuleOutcome` works."""
    assert RuleFromPackage is Rule
    assert RuleOutcomeFromPackage is RuleOutcome
