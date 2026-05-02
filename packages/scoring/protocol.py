"""§10.6 plugin Rule ABC + RuleOutcome (T-305).

This module is the public import surface for plugin authors per BRIEF
§10.6:1799 verbatim:

    from packages.scoring.protocol import Rule, RuleContext, RuleOutcome

Built-in conditions (T-302/T-303/T-304) live under
``packages.scoring.conditions``; plugins inherit :class:`Rule` and are
registered via ``plugin_registry.yaml`` (see :mod:`packages.scoring.registry`).
:class:`PluginCondition` (T-305) wraps an instantiated :class:`Rule` and
adapts :class:`RuleOutcome` to the :class:`Condition` Protocol's
``tuple[bool, dict | None]`` return per T-302 contract.

``RuleContext`` is re-exported from :mod:`packages.scoring.conditions.base`
so the verbatim §10.6:1799 import path resolves without code duplication
(single source of truth).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from .conditions.base import RuleContext as _ConditionsBaseRuleContext


__all__ = ["Rule", "RuleContext", "RuleOutcome"]


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """§10.6 plugin Rule output. Adapter target for Condition Protocol tuple.

    ``result`` is the boolean outcome; ``metadata`` is optional dict carried
    through to T-307 evaluator's ``RuleResult.error`` field for audit.
    """

    result: bool
    metadata: dict[str, Any] | None = None


class Rule(ABC):
    """§10.6 plugin Rule base class.

    Subclasses MUST set :attr:`name` and :attr:`version` ClassVar
    attributes (registry validates against YAML name/version) and
    implement :meth:`__init__` accepting a single ``params: dict``
    + :meth:`evaluate` accepting a :class:`RuleContext` and returning
    a :class:`RuleOutcome`.
    """

    name: ClassVar[str]
    version: ClassVar[str]

    @abstractmethod
    def __init__(self, params: dict[str, Any]) -> None:
        """Construct with operator-supplied YAML params dict."""

    @abstractmethod
    def evaluate(self, ctx: _ConditionsBaseRuleContext) -> RuleOutcome:
        """Evaluate the rule against the current context and return outcome."""


# Re-export RuleContext for §10.6:1799 verbatim import path. Placed at end
# of file (after Rule definition) so circular imports cannot trigger.
from .conditions.base import RuleContext  # noqa: E402
