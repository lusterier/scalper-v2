"""`packages.scoring` — rule language, condition types, evaluator (F3).

T-300 ships the foundational Pydantic types only (BotConfig,
ScoringConfig, ScoringRule, ScoringResult, RuleResult, Decision). All
public re-exports live in :mod:`packages.scoring.types`; this module
flattens them for ergonomic import.
"""

from __future__ import annotations

from .protocol import Rule, RuleOutcome
from .types import (
    BotConfig,
    Decision,
    RuleResult,
    ScoringConfig,
    ScoringResult,
    ScoringRule,
)

__all__ = [
    "BotConfig",
    "Decision",
    "Rule",
    "RuleOutcome",
    "RuleResult",
    "ScoringConfig",
    "ScoringResult",
    "ScoringRule",
]
