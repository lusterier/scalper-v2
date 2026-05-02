"""§10.2:1707 + §10.6 plugin condition (T-305).

:class:`PluginCondition` wraps an instantiated :class:`Rule` and adapts
:class:`RuleOutcome` to the :class:`Condition` Protocol's ``tuple[bool,
dict | None]`` return per T-302 contract.

T-308 YAML loader is responsible for binding: looks up Rule class in
``plugin_registry`` by ``(name, version)``, instantiates with operator
YAML params, then constructs ``PluginCondition(name=..., version=...,
rule=instance)``.

``arbitrary_types_allowed=True`` is required because :class:`Rule` is a
non-Pydantic ABC. ``strict=True`` is omitted (incompatible with arbitrary
types). Defensive: ``rule`` field is required (Pydantic raises
ValidationError without it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from ..protocol import Rule  # noqa: TC001 — runtime use as Pydantic field type

if TYPE_CHECKING:
    from .base import RuleContext


__all__ = ["PluginCondition"]


class PluginCondition(BaseModel):
    """§10.2:1707 + §10.6 plugin variant — wraps an instantiated Rule."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    type: Literal["plugin"] = "plugin"
    name: str
    version: str
    rule: Rule

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        outcome = self.rule.evaluate(ctx)
        return outcome.result, outcome.metadata
