"""Self-contained Rule subclass used by registry/plugin tests (T-305 WG#3).

Loader imports via ``importlib.import_module(
"packages.scoring.tests.fixtures.foo_plugin")``. Operator-supplied params
dict is preserved on the instance for test introspection.
"""

from __future__ import annotations

from typing import Any, ClassVar

from packages.scoring.protocol import Rule, RuleContext, RuleOutcome


class FooRule(Rule):
    """Trivial Rule that returns RuleOutcome with the configured result."""

    name: ClassVar[str] = "foo"
    version: ClassVar[str] = "1"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params
        self.result = bool(params.get("result", True))

    def evaluate(self, ctx: RuleContext) -> RuleOutcome:
        return RuleOutcome(result=self.result, metadata={"params": self.params})


class WrongNameRule(Rule):
    """Rule whose ``name`` ClassVar mismatches the YAML registry entry — used to test ValueError."""

    name: ClassVar[str] = "actually_bar"
    version: ClassVar[str] = "1"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def evaluate(self, ctx: RuleContext) -> RuleOutcome:
        return RuleOutcome(result=True)


class WrongVersionRule(Rule):
    """Rule whose ``version`` ClassVar mismatches YAML — used to test ValueError."""

    name: ClassVar[str] = "foo"
    version: ClassVar[str] = "999"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def evaluate(self, ctx: RuleContext) -> RuleOutcome:
        return RuleOutcome(result=True)


class NotARule:
    """Plain Python class without Rule inheritance — used to test TypeError."""

    name = "foo"
    version = "1"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params
