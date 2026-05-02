"""§N5 unit tests for :mod:`packages.scoring.conditions.plugin` (T-305)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from packages.bus.schemas.signals import SignalValidated
from packages.scoring.conditions import Condition, PluginCondition, RuleContext
from packages.scoring.protocol import Rule, RuleOutcome
from packages.scoring.tests.fixtures.foo_plugin import FooRule


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


def _ctx() -> RuleContext:
    return RuleContext(signal=_signal(), feature_snapshot={}, feature_ref="f1")


def test_plugin_condition_construction_with_valid_rule_succeeds() -> None:
    rule = FooRule(params={"result": True})
    c = PluginCondition(name="foo", version="1", rule=rule)
    assert c.name == "foo"
    assert c.version == "1"
    assert c.rule is rule


def test_plugin_condition_construction_without_rule_field_raises() -> None:
    with pytest.raises(ValidationError):
        PluginCondition(name="foo", version="1")  # type: ignore[call-arg]


def test_plugin_condition_evaluate_adapter_returns_tuple() -> None:
    rule = FooRule(params={"result": True})
    c = PluginCondition(name="foo", version="1", rule=rule)
    outcome, metadata = c.evaluate(_ctx())
    assert outcome is True
    assert metadata == {"params": {"result": True}}


def test_plugin_condition_evaluate_metadata_none_passthrough() -> None:
    """RuleOutcome with metadata=None → tuple metadata is None."""

    class _BareRule(Rule):
        name = "bare"
        version = "1"

        def __init__(self, params: dict[str, object]) -> None: ...

        def evaluate(self, ctx: RuleContext) -> RuleOutcome:
            return RuleOutcome(result=False)

    rule = _BareRule({})
    c = PluginCondition(name="bare", version="1", rule=rule)
    outcome, metadata = c.evaluate(_ctx())
    assert outcome is False
    assert metadata is None


def test_plugin_condition_discriminator_value() -> None:
    rule = FooRule(params={})
    assert PluginCondition(name="foo", version="1", rule=rule).type == "plugin"


def test_plugin_condition_frozen_rejects_mutation() -> None:
    rule = FooRule(params={})
    c = PluginCondition(name="foo", version="1", rule=rule)
    with pytest.raises(ValidationError):
        c.name = "bar"


def test_plugin_condition_satisfies_condition_protocol() -> None:
    rule = FooRule(params={})
    c: object = PluginCondition(name="foo", version="1", rule=rule)
    assert isinstance(c, Condition)


def test_plugin_condition_evaluate_propagates_exception_from_rule() -> None:
    """T-307 evaluator catches exceptions; PluginCondition adapter is transparent."""

    class _RaisingRule(Rule):
        name = "raising"
        version = "1"

        def __init__(self, params: dict[str, object]) -> None: ...

        def evaluate(self, ctx: RuleContext) -> RuleOutcome:
            msg = "plugin runtime error"
            raise RuntimeError(msg)

    rule = _RaisingRule({})
    c = PluginCondition(name="raising", version="1", rule=rule)
    with pytest.raises(RuntimeError, match="plugin runtime error"):
        c.evaluate(_ctx())
