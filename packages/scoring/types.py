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
from decimal import Decimal  # noqa: TC003 — runtime annotation on Pydantic Decimal fields
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "BotConfig",
    "Decision",
    "ExchangeSection",
    "ExecutionSection",
    "RiskSection",
    "RuleResult",
    "ScoringConfig",
    "ScoringResult",
    "ScoringRule",
    "ShadowConfig",
    "ShadowVariant",
    "SignalsSection",
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


class ExchangeSection(BaseModel):
    """§B.1 ``exchange:`` block (T-310a)."""

    model_config = ConfigDict(frozen=True, strict=True)

    mode: Literal["live", "testnet", "paper"]
    account: str
    api_key_env: str
    api_secret_env: str


class SignalsSection(BaseModel):
    """§B.1 ``signals:`` block (T-310a). Defaults match §B.1 + H-008 spec verbatim."""

    model_config = ConfigDict(frozen=True, strict=True)

    source_filter: list[str] | None = None
    ttl_seconds: int = 120


class ExecutionSection(BaseModel):
    """§B.1 ``execution:`` block (T-310a) plus T-310a ``qty`` per OQ-4 Path A.

    §B.1 brief deviation (operator-approved 2026-05-02): brief §B.1 (lines
    2934-2944) does NOT have ``qty:`` in the ``execution:`` block; brief
    §B.1 ships a separate ``sizing:`` block (lines 3006-3025) with tier-
    based balance sizing. T-310a deliberately simplifies to per-bot fixed
    ``qty: Decimal``; F4+ task replaces with risk-based sizing reading
    ``sizing.*`` block (currently absorbed by ``BotConfig.extra="ignore"``).
    """

    model_config = ConfigDict(frozen=True, strict=True)

    # T-310a OQ-4 Path A 2026-05-02: per-bot fixed qty v1; §B.1 sizing:
    # block (lines 3006-3025) deferred to F4+.
    qty: Decimal
    leverage: int
    sl_pct: Decimal
    tp_pct: Decimal
    tp_qty_pct: Decimal
    be_trigger: Decimal
    be_sl_level: Decimal
    trail_pct: Decimal
    fee_rate: Decimal
    sl_retry_count: int = 3
    emergency_close_on_sl_fail: bool = True


# 9-field subset of ExecutionSection valid as shadow override targets per
# BRIEF §13.2. ExecutionSection has 11 fields total; `sl_retry_count` (int) +
# `emergency_close_on_sl_fail` (bool) are intentionally excluded — they are
# NOT Decimal-coercible numeric risk-management knobs (BRIEF §13.2 examples
# only override Decimal-typed thresholds: sl_pct/tp_pct/be_trigger/trail_pct/
# tp_qty_pct etc.). qty + leverage + fee_rate kept legal even if BRIEF examples
# don't override them — no semantic harm in allowing.
_EXECUTION_OVERRIDE_FIELDS: frozenset[str] = frozenset(
    {
        "qty",
        "leverage",
        "sl_pct",
        "tp_pct",
        "tp_qty_pct",
        "be_trigger",
        "be_sl_level",
        "trail_pct",
        "fee_rate",
    }
)


class ShadowVariant(BaseModel):
    """Per-variant override per BRIEF §13.2 YAML schema.

    ``extra="forbid"`` deviates from BotConfig's defense-in-depth ``extra="ignore"``
    intentionally: shadow is a net-new modeled feature; ``forbid`` catches operator
    typos (`enabld: true` / unknown variant fields) immediately at YAML load instead
    of silent ignore. The BotConfig ``extra="ignore"`` compromise serves legacy
    unmodeled-but-existing top-level keys (display_name/created_at/sizing) — that
    rationale doesn't apply here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    overrides: dict[str, Decimal] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _overrides_target_execution_fields(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        unknown = set(value) - _EXECUTION_OVERRIDE_FIELDS
        if unknown:
            msg = (
                f"shadow variant overrides target unknown ExecutionSection "
                f"fields: {sorted(unknown)}; valid keys: "
                f"{sorted(_EXECUTION_OVERRIDE_FIELDS)}"
            )
            raise ValueError(msg)
        return value


class ShadowConfig(BaseModel):
    """Per-bot shadow simulation config per BRIEF §13.2.

    ``extra="forbid"`` rationale matches :class:`ShadowVariant` — net-new feature
    rejects operator typos at YAML load.

    ``max_duration_hours`` upper bound 24h aligns with intra-day scalper time
    horizon (T-501 backtest_trades hypertable + execution-service in-memory FSM
    per T-511 owner). Long-running variants would increase H-016 task-cleanup
    risk + memory pressure on the in-process shadow-worker. Bound is operator-
    defensive; widen if a legitimate use case appears (BRIEF §13.2 default 4h
    is the canonical operator value; 24h is a safe-by-default cap).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    variants: list[ShadowVariant] = Field(default_factory=list)
    max_duration_hours: float = Field(default=4.0, gt=0.0, le=24.0)

    @field_validator("variants")
    @classmethod
    def _variant_names_unique(cls, value: list[ShadowVariant]) -> list[ShadowVariant]:
        names = [v.name for v in value]
        if len(names) != len(set(names)):
            msg = f"shadow variant names must be unique within bot: {names}"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _enabled_requires_variants(self) -> ShadowConfig:
        if self.enabled and not self.variants:
            msg = "shadow.enabled=True requires at least 1 variant"
            raise ValueError(msg)
        return self


class RiskSection(BaseModel):
    """§B.1 ``risk:`` block — per-bot risk-management knobs (T-526 + T-524).

    Forward-compatible container: T-526 shipped cooldown knobs (single-loss +
    losing-streak); T-524 adds concurrent-trades caps; T-525 will add
    daily-loss-limit + max-drawdown thresholds to this same model.

    Per-knob ``0`` value = disabled. Knob semantics intentionally orthogonal:

    * **Cooldown (T-526)**: any one of ``cooldown_after_loss_minutes`` /
      ``cooldown_after_streak_n_losses`` / ``cooldown_after_streak_n_losses_minutes``
      = ``0`` disables that knob. Both single-loss + streak knobs = ``0``
      short-circuits the cooldown gate before SELECT.
    * **Concurrent caps (T-524)**: ``max_open_trades_per_bot`` /
      ``max_open_trades_global`` = ``0`` disables that cap. Both = ``0``
      short-circuits the caps gate before SELECT (no DB hit per signal when
      feature unused). Block predicate is ``current_open_count >= cap``.

    Mirror :class:`ShadowConfig` ``extra="forbid"`` rationale: net-new feature
    catches operator typos at YAML load.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # T-526 cooldown knobs
    cooldown_after_loss_minutes: int = Field(default=0, ge=0)
    cooldown_after_streak_n_losses: int = Field(default=0, ge=0)
    cooldown_after_streak_n_losses_minutes: int = Field(default=0, ge=0)
    # T-524 concurrent-trades caps
    max_open_trades_per_bot: int = Field(default=0, ge=0)
    max_open_trades_global: int = Field(default=0, ge=0)


class BotConfig(BaseModel):
    """Top-level bot YAML config (§9.4 + §10).

    ``version`` field threads to ``scoring_evaluations.config_version``
    audit trail per T-301 Migration 0010.

    ``extra="ignore"`` is defense-in-depth per T-308 WG#5 + T-310a: the
    yaml_loader extracts specific kwargs (no ``**data`` splat into ``BotConfig(...)``),
    so unmodeled top-level keys (``display_name``, ``created_at``, ``status``,
    ``trading.primary_interval``, ``sizing``) never reach the ctor in the
    loader path. (``shadow`` was on this list pre-T-514; T-514 promotes it to
    a fully-modeled :class:`ShadowConfig` field per BRIEF §13.2.)
    ``extra="ignore"`` defends against alternative caller paths (e.g.
    analytics-api inspector) that might construct BotConfig via ``**raw_dict``.
    Not a workaround — a hardening layer.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    bot_id: str
    version: int = Field(default=1, ge=1)
    symbols: list[str]
    exchange: ExchangeSection
    signals: SignalsSection = Field(default_factory=SignalsSection)
    execution: ExecutionSection
    scoring: ScoringConfig
    shadow: ShadowConfig | None = None
    risk: RiskSection = Field(default_factory=RiskSection)

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
