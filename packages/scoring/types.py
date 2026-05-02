"""Foundational Pydantic types for `packages/scoring/` (T-300, F3 §10).

T-300 ships **only** the schema. No I/O, no business logic, no
condition-variant narrowing. Subsequent F3 tasks consume these types:

* T-301 Migration 0010 columns mirror :class:`ScoringResult` JSONB
  serialization (``rule_results``, ``feature_snapshot``).
* T-302..T-305 narrow :attr:`ScoringRule.condition` from placeholder
  ``dict[str, Any]`` to a discriminated Pydantic union.
* T-306 resolver consumes :attr:`ScoringRule.feature` reference +
  :attr:`ScoringRule.max_staleness_sec`.
* T-307 evaluator pipeline returns :class:`ScoringResult` per §10.4
  verbatim pseudocode + owns the ``FeatureValue`` (frozen
  ``@dataclass`` per :mod:`packages.features.types`) → jsonable-dict
  serializer for :attr:`ScoringResult.feature_snapshot` inner shape.
* T-308 YAML loader parses to :class:`BotConfig`.

All models are ``frozen=True`` per §5.3 immutability convention.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "BotConfig",
    "Decision",
    "RuleResult",
    "ScoringConfig",
    "ScoringResult",
    "ScoringRule",
]


type Decision = Literal["execute", "reject", "passthrough"]
"""Per §7.2:1046 — verbatim string set persisted in ``scoring_evaluations.decision``."""


# Task-level convention (NOT verbatim brief). Brief §7.2:850 declares
# ``bots.bot_id TEXT`` with examples ``'alpha', 'beta'``; the regex
# codifies env-var compatibility (``BOT_<ID>_BYBIT_API_KEY`` per
# ADR-0004 H-022) + FK reference safety. Operator-approved Path A
# 2026-05-02 — readers MUST NOT assume this derives from brief and
# relax the format on extension without re-confirming with operator.
_BOT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


class RuleResult(BaseModel):
    """Per-rule evaluation outcome (§10.4 result row).

    The ``error`` ↔ ``result`` cross-field correlation (e.g. ``error``
    non-None ⇒ ``result ∈ {"skipped", "error_skipped", "data_missing",
    "data_stale"}``) is **not** enforced at this layer — that is T-307
    evaluator concern. T-300 keeps both fields as independent
    caller-set values.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    weight: float
    applied_weight: float
    # T-307 evaluator sets one of: True, False, n/a, skipped, error_skipped,
    # data_missing, data_stale. T-300 keeps it loose `str`.
    result: str
    error: dict[str, Any] | None = None


class ScoringResult(BaseModel):
    """Top-level evaluator output (§10.4 return type).

    ``feature_snapshot`` carries an opaque inner shape at T-300 — the
    ``FeatureValue`` (``packages.features.types``) → jsonable dict
    serialization (with Decimal-string preservation per §N1 / §5.13)
    is **T-307 evaluator's responsibility**. T-300 only validates the
    outer shape (``dict[str, dict[str, Any]]``).

    ``reason`` is ``None`` when ``decision in {"execute",
    "passthrough"}`` and a non-empty string when ``decision ==
    "reject"`` (e.g. ``"required_feature_missing"`` /
    ``"rule_error"`` per §10.4:1749/1754). Cross-field invariant
    (``reason`` ↔ ``decision``) is NOT enforced at this layer — same
    principle as :class:`RuleResult` ``error`` ↔ ``result``; T-307
    evaluator owns it.
    """

    model_config = ConfigDict(frozen=True)

    decision: Decision
    total_score: float
    threshold: float
    rule_results: list[RuleResult]
    feature_snapshot: dict[str, dict[str, Any]]
    config_version: int
    reason: str | None = None


class ScoringRule(BaseModel):
    """One rule within a bot's scoring config (§10.1).

    ``applies_when`` is typed ``dict[str, Any] | None`` — raw YAML
    pass-through; T-307 evaluator v1 ignores it (see grep-anchor
    comment at evaluator.py:107).

    ``condition`` is typed ``Any``: T-308 yaml_loader populates with a
    Condition instance from ``parse_condition`` (Pydantic ``BaseModel``
    subclass per T-302..T-305 catalog). Runtime narrowing happens via
    ``@runtime_checkable Condition`` Protocol (T-302), not Pydantic
    discrimination — the Path C / RuleContext architecture made this
    placeholder permanent. ``Any`` is the honest typing.

    T-308b switched ``model_config`` to ``strict=True`` so coercion-
    friendly fields (``required``, ``max_staleness_sec``) reject
    string/int-as-bool footguns at YAML parse time. The yaml_loader's
    manual ``weight`` pre-check (yaml_loader.py:316-319) stays as
    belt-and-suspenders for the error-message clarity it gives on
    ``weight: True`` — Pydantic strict rejects bool→float but with a
    less actionable message.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    name: str
    weight: float
    feature: str  # templated reference, e.g. "ind.${signal.symbol}.15m.ema_20"
    applies_when: dict[str, Any] | None = None
    condition: Any
    on_error: Literal["skip", "reject"] = "skip"
    required: bool = False
    max_staleness_sec: int | None = None  # None: resolver default 2 * interval_seconds


class ScoringConfig(BaseModel):
    """Scoring section of :class:`BotConfig` (§10.4)."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["active", "passthrough"] = "active"
    trigger_threshold: float
    rules: list[ScoringRule]

    @model_validator(mode="after")
    def _rule_names_unique(self) -> ScoringConfig:
        seen: set[str] = set()
        for rule in self.rules:
            if rule.name in seen:
                msg = f"duplicate rule name: {rule.name!r}"
                raise ValueError(msg)
            seen.add(rule.name)
        return self


class BotConfig(BaseModel):
    """Top-level bot YAML config (§9.4 + §10).

    ``version`` field threads to ``scoring_evaluations.config_version``
    audit trail per T-301 Migration 0010.

    ``extra="ignore"`` is the parser-drift firewall per T-308 WG#5: §B.1
    extras (``exchange.*``, ``signals.*``, ``execution.*``, ``display_name``,
    ``created_at``, ``status``, ``trading.primary_interval``) are NOT
    parsed by T-308 yaml_loader; future tasks adding these sections
    land as Pydantic fields here, NOT as another extra-ignore workaround.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    bot_id: str
    version: int = Field(default=1, ge=1)
    symbols: list[str]
    scoring: ScoringConfig

    @field_validator("bot_id")
    @classmethod
    def _bot_id_charset(cls, value: str) -> str:
        if not value:
            msg = "bot_id must be non-empty"
            raise ValueError(msg)
        if not _BOT_ID_PATTERN.match(value):
            msg = (
                f"bot_id {value!r} must match {_BOT_ID_PATTERN.pattern!r} "
                "(task-level convention for BOT_<ID>_* env-var compatibility per "
                "ADR-0004 H-022 + FK reference safety)"
            )
            raise ValueError(msg)
        return value
