"""§10.2 + §10.6 Condition Protocol + RuleContext (T-302).

Path C resolution (operator-approved 2026-05-02): all condition variants
(T-302 simple, T-303 series, T-304 composite, T-305 plugin) implement
:class:`Condition.evaluate(ctx: RuleContext)` uniformly. This reconciles
BRIEF §10.4:1746 (``evaluate(signal, feature_snapshot)``) and §10.6:1809
(``evaluate(ctx: RuleContext)``) into one consistent Protocol — no
churn at T-303/T-304/T-305/T-307.

T-305 plugin loader follow-up: BRIEF §10.6:1799 imports plugins from
``packages.scoring.protocol``; T-305 will either re-export this module
or treat that brief path as a forward-ref. T-302 itself uses
``packages.scoring.conditions.base`` per the codebase layout convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from packages.bus.schemas.signals import SignalValidated
    from packages.features.types import FeatureValue


__all__ = ["Condition", "RuleContext"]


@dataclass(frozen=True, slots=True)
class RuleContext:
    """§10.6 RuleContext — single argument to :meth:`Condition.evaluate`.

    Carries the full evaluator-time context. Composite conditions
    (T-304) navigate sub-conditions which may reference different
    features within ``feature_snapshot``. Plugin conditions (T-305)
    receive the same shape per BRIEF §10.6:1809 verbatim.

    ``feature_ref`` is the rule's resolved feature reference (post
    template substitution per §10.3) — simple conditions look up
    ``feature_snapshot[feature_ref]`` to get THEIR feature value.
    Composite conditions ignore ``feature_ref`` and recurse into
    sub-conditions which carry their own resolved refs.
    """

    signal: SignalValidated
    feature_snapshot: Mapping[str, FeatureValue]
    feature_ref: str


@runtime_checkable
class Condition(Protocol):
    """§10.2 + §10.6 Condition Protocol — implemented by ALL condition variants.

    ``@runtime_checkable`` enables ``isinstance(obj, Condition)`` for
    T-307 evaluator dispatch.
    """

    type: str  # Pydantic discriminator field

    def evaluate(self, ctx: RuleContext) -> tuple[bool, dict[str, Any] | None]:
        """Return (outcome, error_info).

        - ``outcome``: True if condition holds, False otherwise.
        - ``error_info``: ``None`` on success; dict with ``"error"``
          key on type-mismatch, missing-feature, or unsupported-variant
          cases.
        """
        ...
