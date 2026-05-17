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
from decimal import Decimal  # runtime: RiskSection.daily_loss_limit_usd=Decimal("0") (T-525a1)
from itertools import pairwise
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
    "SizingSection",
    "SizingTier",
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
    ``qty: Decimal``; T-527 reifies the §B.1 ``sizing:`` block as
    :class:`SizingSection` (T-527a config foundation; T-527b computes ``qty``
    from it). ``qty`` stays the static fallback when a bot has no ``sizing:``
    block (``BotConfig.sizing is None``).
    """

    model_config = ConfigDict(frozen=True, strict=True)

    # T-310a OQ-4 Path A 2026-05-02: per-bot fixed qty v1. §B.1 sizing:
    # block reified by T-527 (SizingSection); qty = static fallback when
    # BotConfig.sizing is None.
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


class SizingTier(BaseModel):
    """One rung of the §B.1 ``sizing.tiers`` balance ladder (T-527a).

    ``balance_min`` / ``size`` are both **USD notional** (operator OQ-3=A —
    T-527b computes ``qty = size ÷ reference_price``). §B.1 example
    ``{ balance_min: 500, size: 700 }`` (BRIEF §22 alpha.yaml lines 3132-3135).
    """

    model_config = ConfigDict(frozen=True, strict=True)

    balance_min: Decimal = Field(..., ge=0)
    size: Decimal = Field(..., ge=0)


class SizingSection(BaseModel):
    """§B.1 ``sizing:`` block — per-bot position sizing (T-527a + T-528a).

    Reifies the BRIEF-deferred §B.1 ``sizing:`` block (BRIEF §22 Appendix B,
    alpha.yaml lines 3130-3145): per-symbol notional caps + one of two
    ``method`` paths (operator OQ-2=A — additive discriminator, NOT a
    discriminated-union refactor):

    * ``method: "tier"`` (default — backward-compat: a bot with no
      ``method:`` is unchanged from T-527a): a balance→tier ladder +
      ``score_multipliers``. Compute is T-527b (shipped).
    * ``method: "risk_per_sl"`` (T-528a): ``risk_pct`` of ``total_equity``
      risked per trade — ``notional = total_equity * risk_pct / sl_pct``.
      ``score_multipliers`` are NOT applied (operator OQ-3=A — deterministic
      risk model; signal strength is already gated upstream). Compute is
      T-528b.

    ``tiers`` / ``score_multipliers`` carry ``default_factory`` ONLY so a
    ``method: risk_per_sl`` block may omit them. The tier-path validation
    strictness that field-requiredness gave before T-528a is fully RESTORED
    in ``_structural_guards`` for ``method == "tier"`` (§N10 — a silently
    ``[]`` / ``{}`` tier config would be a capital-safety regression). The
    per-symbol cap (``max_notional_per_symbol`` with a ``"default"`` key)
    is REQUIRED and applies to BOTH methods (safety rail).

    ``tier_promotion`` / ``tier_demotion`` (alpha.yaml lines 3146-3149) are
    DELIBERATELY NOT modeled here — operator OQ-2=A (T-527) deferred them to
    a separate later task (under-specified stateful tier-adjustment layer).
    ``extra="forbid"`` here means a stray ``tier_promotion:`` / typo raises
    at YAML load; rationale matches :class:`RiskSection` /
    :class:`ShadowConfig` — net-new feature rejects operator typos at load.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["tier", "risk_per_sl"] = "tier"
    tiers: list[SizingTier] = Field(default_factory=list)
    score_multipliers: dict[str, Decimal] = Field(default_factory=dict)
    risk_pct: Decimal | None = Field(default=None, gt=0)
    max_notional_per_symbol: dict[str, Decimal]

    @model_validator(mode="after")
    def _structural_guards(self) -> SizingSection:
        if self.method == "tier":
            # §N10: byte-preserve the shipped T-527a tier accept/reject
            # surface. `tiers`/`score_multipliers` gained `default_factory`
            # only so a `method: risk_per_sl` block may omit them; under
            # `method: tier` the loud rejections that field-requiredness
            # used to give MUST be restored here — a silently []/{} tier
            # config is a capital-safety regression. Guard order
            # (risk_pct → tiers → score_multipliers → ascending) keeps the
            # shipped empty-tiers test raising the tiers message.
            if self.risk_pct is not None:
                msg = "sizing.risk_pct is only valid with method='risk_per_sl'"
                raise ValueError(msg)
            if not self.tiers:
                msg = "sizing.tiers must be non-empty (at least 1 balance tier)"
                raise ValueError(msg)
            if not self.score_multipliers:
                msg = "sizing.score_multipliers must be non-empty when method='tier'"
                raise ValueError(msg)
            mins = [t.balance_min for t in self.tiers]
            if any(b <= a for a, b in pairwise(mins)):
                msg = (
                    f"sizing.tiers must be strictly ascending by balance_min; "
                    f"got {[str(m) for m in mins]}"
                )
                raise ValueError(msg)
        elif self.risk_pct is None:
            # method == "risk_per_sl": Field(gt=0) rejects a provided
            # non-positive risk_pct; this covers an absent/null one.
            msg = "sizing.risk_pct must be > 0 when method='risk_per_sl'"
            raise ValueError(msg)
        if "default" not in self.max_notional_per_symbol:
            msg = (
                f"sizing.max_notional_per_symbol must contain a 'default' key; "
                f"got keys {sorted(self.max_notional_per_symbol)}"
            )
            raise ValueError(msg)
        non_digit = sorted(k for k in self.score_multipliers if not k.isdigit())
        if non_digit:
            msg = (
                f"sizing.score_multipliers keys must be digit-strings "
                f"(score buckets, e.g. '4'..'9'); got non-digit keys {non_digit}"
            )
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
    * **Daily loss limit (T-525a1 knob / T-525a2 enforcement)**:
      ``daily_loss_limit_usd`` = ``0`` disables. When the bot's cumulative
      realized P&L for the current UTC trading day reaches
      ``<= -daily_loss_limit_usd`` the T-525a2 gate latches a persistent
      kill-switch (``bot_kill_switch_state``). ``Decimal`` per §5.13 (USD
      money). T-525a1 declares the knob; the SUM/threshold arithmetic is
      T-525a2 (this field is declaration-only here — no math).
    * **Opposite-side guard (T-542 / ADR-0016, H-005)**:
      ``block_opposite_side`` — ``bool``, **default ``True`` (blocked)** per
      the BRIEF §20 H-005 "per-bot enable/disable, default blocked" policy.
      When ``True`` and the bot already has an open position for
      ``(bot_id, symbol)`` whose side is opposite the incoming signal's
      mapped side, the T-542 pre-scoring opposite-side gate silently skips
      the signal. ``False`` opts out (short-circuits the gate before any DB
      hit). Boolean, not the ``0``-disabled int convention — a directional
      block has no magnitude. Declaration-only here; enforced by
      :mod:`services.strategy_engine.app.opposite_side_gate`.

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
    # T-525a1 daily loss limit threshold (USD; enforced by T-525a2 gate)
    daily_loss_limit_usd: Decimal = Field(default=Decimal("0"), ge=0)
    # T-525b max-drawdown hard-stop: Decimal fraction give-back of the lifetime
    # profit peak (mirror sl_pct/tp_pct fraction convention — "0.20" = 20%).
    # 0 = disabled; no le bound (give-back can exceed 1.0 when current<0<peak).
    # Enforced by the T-525b drawdown gate; declaration-only here.
    max_drawdown_pct: Decimal = Field(default=Decimal("0"), ge=0)
    # T-542 H-005 opposite-side guard (ADR-0016). bool, default True =
    # blocked (BRIEF §20 "default blocked"); False short-circuits the gate
    # before the DB hit. Enforced by opposite_side_gate; declaration-only.
    block_opposite_side: bool = Field(default=True)


class BotConfig(BaseModel):
    """Top-level bot YAML config (§9.4 + §10).

    ``version`` field threads to ``scoring_evaluations.config_version``
    audit trail per T-301 Migration 0010.

    ``extra="ignore"`` is defense-in-depth per T-308 WG#5 + T-310a: the
    yaml_loader extracts specific kwargs (no ``**data`` splat into ``BotConfig(...)``),
    so unmodeled top-level keys (``display_name``, ``created_at``, ``status``,
    ``trading.primary_interval``) never reach the ctor in the loader path.
    (``shadow`` was on this list pre-T-514; T-514 promotes it to a fully-
    modeled :class:`ShadowConfig` field per BRIEF §13.2. ``sizing`` was on
    this list pre-T-527a; T-527a promotes it to a :class:`SizingSection`
    field — except ``sizing.tier_promotion``/``tier_demotion`` which remain
    extra-absorbed, OQ-2=A deferred to a later task.)
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
    # T-527a: §B.1 sizing block. Optional-sentinel (mirror `shadow`, NOT
    # `risk`'s default_factory): None = no tier sizing → T-527b leaves the
    # static `execution.qty` path byte-unchanged (a defaulted empty-tiers
    # section is invalid by SizingSection's non-empty validator anyway). T-528
    # later adds a `sizing.method` discriminator; T-527 is the `tiers` method.
    sizing: SizingSection | None = None

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
