"""`packages.scoring.conditions` — condition variants per §10.2 (T-302+).

T-302 ships :class:`Condition` Protocol + :class:`RuleContext` shape +
8 simple comparison variants. T-303/T-304/T-305 will land series,
composite, and plugin variants in their own submodules.
"""

from __future__ import annotations

from .base import Condition, RuleContext
from .composite import AndCondition, NotCondition, OrCondition, WhenThenElseCondition
from .plugin import PluginCondition
from .series import EmaStackCondition, FallingCondition, RisingCondition
from .simple import (
    BetweenCondition,
    EqualsCondition,
    GtCondition,
    GteCondition,
    InCondition,
    LtCondition,
    LteCondition,
    NotEqualsCondition,
)

__all__ = [
    "AndCondition",
    "BetweenCondition",
    "Condition",
    "EmaStackCondition",
    "EqualsCondition",
    "FallingCondition",
    "GtCondition",
    "GteCondition",
    "InCondition",
    "LtCondition",
    "LteCondition",
    "NotCondition",
    "NotEqualsCondition",
    "OrCondition",
    "PluginCondition",
    "RisingCondition",
    "RuleContext",
    "WhenThenElseCondition",
]
