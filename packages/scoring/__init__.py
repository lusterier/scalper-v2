"""`packages.scoring` — rule language, condition types, evaluator (F3).

T-300 ships the foundational Pydantic types only (BotConfig,
ScoringConfig, ScoringRule, ScoringResult, RuleResult, Decision). All
public re-exports live in :mod:`packages.scoring.types`; this module
flattens them for ergonomic import.
"""

from __future__ import annotations

from .evaluator import evaluate
from .protocol import Rule, RuleOutcome
from .resolver import FeatureResolver, ResolverResult, ResolverStatus
from .types import (
    BotConfig,
    Decision,
    RuleResult,
    ScoringConfig,
    ScoringResult,
    ScoringRule,
)
from .yaml_loader import load_bot_config, parse_condition

__all__ = [
    "BotConfig",
    "Decision",
    "FeatureResolver",
    "ResolverResult",
    "ResolverStatus",
    "Rule",
    "RuleOutcome",
    "RuleResult",
    "ScoringConfig",
    "ScoringResult",
    "ScoringRule",
    "evaluate",
    "load_bot_config",
    "parse_condition",
]
