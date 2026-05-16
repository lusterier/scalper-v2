"""§N5 unit tests for :mod:`packages.scoring.types` (T-300, F3 §10).

Pure schema / validation tests — no I/O, no business logic. Coverage
target ≥80% on `packages/scoring/` per §N5.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.scoring import (
    BotConfig,
    ExchangeSection,
    ExecutionSection,
    RiskSection,
    RuleResult,
    ScoringConfig,
    ScoringResult,
    ScoringRule,
    SignalsSection,
    SizingSection,
)
from packages.scoring.types import SizingTier

_DEFAULT_EXCHANGE = ExchangeSection(
    mode="paper",
    account="sub_alpha",
    api_key_env="BOT_ALPHA_BYBIT_API_KEY",
    api_secret_env="BOT_ALPHA_BYBIT_API_SECRET",
)
_DEFAULT_EXECUTION = ExecutionSection(
    qty=Decimal("0.001"),
    leverage=20,
    sl_pct=Decimal("0.01"),
    tp_pct=Decimal("0.01"),
    tp_qty_pct=Decimal("0.5"),
    be_trigger=Decimal("0.005"),
    be_sl_level=Decimal("0.003"),
    trail_pct=Decimal("0.005"),
    fee_rate=Decimal("0.00055"),
)


def _basic_rule(*, name: str = "r1", weight: float = 1.0) -> ScoringRule:
    return ScoringRule(
        name=name,
        weight=weight,
        feature="ind.${signal.symbol}.15m.ema_20",
        condition={"type": "gt", "value": 50000},
    )


def _basic_scoring(*, rules: list[ScoringRule] | None = None) -> ScoringConfig:
    return ScoringConfig(
        trigger_threshold=1.0,
        rules=rules if rules is not None else [_basic_rule()],
    )


def _basic_bot(bot_id: str = "alpha") -> BotConfig:
    return BotConfig(
        bot_id=bot_id,
        symbols=["BTCUSDT"],
        exchange=_DEFAULT_EXCHANGE,
        execution=_DEFAULT_EXECUTION,
        scoring=_basic_scoring(),
    )


# ---------------------------------------------------------------------------
# Decision Literal pin
# ---------------------------------------------------------------------------


def test_decision_literal_accepts_only_three_values() -> None:
    """Pin §7.2:1046 string set — anything else raises ValidationError."""
    for valid in ("execute", "reject", "passthrough"):
        result = ScoringResult(
            decision=valid,
            total_score=0.0,
            threshold=1.0,
            rule_results=[],
            feature_snapshot={},
            config_version=1,
        )
        assert result.decision == valid
    with pytest.raises(ValidationError):
        ScoringResult(
            decision="ignore",  # type: ignore[arg-type]
            total_score=0.0,
            threshold=1.0,
            rule_results=[],
            feature_snapshot={},
            config_version=1,
        )


# ---------------------------------------------------------------------------
# BotConfig
# ---------------------------------------------------------------------------


def test_bot_config_round_trip_via_model_dump() -> None:
    config = _basic_bot()
    dumped = config.model_dump()
    reconstructed = BotConfig.model_validate(dumped)
    assert reconstructed == config


def test_bot_config_rejects_empty_bot_id() -> None:
    with pytest.raises(ValidationError, match="bot_id"):
        BotConfig(
            bot_id="",
            symbols=["BTCUSDT"],
            exchange=_DEFAULT_EXCHANGE,
            execution=_DEFAULT_EXECUTION,
            scoring=_basic_scoring(),
        )


def test_bot_config_rejects_invalid_bot_id_charset() -> None:
    """Convention regex: lowercase + digits + underscore + hyphen, must start with letter."""
    for bad in ("Alpha", "1alpha", "alpha bot", "alpha.beta", "alpha/beta"):
        with pytest.raises(ValidationError, match="bot_id"):
            BotConfig(
                bot_id=bad,
                symbols=["BTCUSDT"],
                exchange=_DEFAULT_EXCHANGE,
                execution=_DEFAULT_EXECUTION,
                scoring=_basic_scoring(),
            )


def test_bot_config_accepts_valid_bot_id_charset() -> None:
    for good in ("alpha", "alpha-2", "alpha_beta", "a", "alpha-bot-1"):
        cfg = BotConfig(
            bot_id=good,
            symbols=["BTCUSDT"],
            exchange=_DEFAULT_EXCHANGE,
            execution=_DEFAULT_EXECUTION,
            scoring=_basic_scoring(),
        )
        assert cfg.bot_id == good


def test_bot_config_version_default_one_minimum_one() -> None:
    cfg = _basic_bot()
    assert cfg.version == 1
    with pytest.raises(ValidationError):
        BotConfig(
            bot_id="alpha",
            version=0,
            symbols=["BTCUSDT"],
            exchange=_DEFAULT_EXCHANGE,
            execution=_DEFAULT_EXECUTION,
            scoring=_basic_scoring(),
        )


# ---------------------------------------------------------------------------
# ScoringConfig
# ---------------------------------------------------------------------------


def test_scoring_config_rejects_duplicate_rule_names() -> None:
    with pytest.raises(ValidationError, match="duplicate rule name"):
        _basic_scoring(rules=[_basic_rule(name="dup"), _basic_rule(name="dup")])


def test_scoring_config_accepts_empty_rules_list() -> None:
    cfg = ScoringConfig(trigger_threshold=1.0, rules=[])
    assert cfg.rules == []


def test_passthrough_mode_with_threshold_accepted() -> None:
    """Per §10.5 — threshold required even in passthrough mode (evaluation runs normally)."""
    cfg = ScoringConfig(mode="passthrough", trigger_threshold=2.5, rules=[_basic_rule()])
    assert cfg.mode == "passthrough"
    assert cfg.trigger_threshold == 2.5


# ---------------------------------------------------------------------------
# ScoringRule
# ---------------------------------------------------------------------------


def test_scoring_rule_negative_weight_accepted() -> None:
    """Subtractive rule per §10.1 — negative weight subtracts from total."""
    rule = _basic_rule(weight=-1.5)
    assert rule.weight == -1.5


def test_scoring_rule_required_default_false() -> None:
    rule = _basic_rule()
    assert rule.required is False


def test_scoring_rule_on_error_default_skip() -> None:
    rule = _basic_rule()
    assert rule.on_error == "skip"


def test_scoring_rule_max_staleness_sec_default_none() -> None:
    """None means resolver uses default `2 * interval_seconds` (§10.3)."""
    rule = _basic_rule()
    assert rule.max_staleness_sec is None


def test_scoring_rule_condition_any_typing_accepts_dict() -> None:
    """Regression pin — T-308b switched ``condition: dict[str, Any]`` → ``condition: Any``.

    The Any typing must still accept dicts (existing T-300 test fixtures use
    ``condition={"type": "gt", "value": 50000}`` shape). yaml_loader (T-308)
    now passes a Condition instance instead, also via Any. This pin guards
    that existing dict-shaped fixtures continue to round-trip unchanged.
    """
    rule = _basic_rule()
    assert rule.condition == {"type": "gt", "value": 50000}


# ---------------------------------------------------------------------------
# T-310a — ExchangeSection / SignalsSection / ExecutionSection (§B.1)
# ---------------------------------------------------------------------------


def test_exchange_section_round_trip() -> None:
    """Happy path: all 4 ExchangeSection fields populated; frozen verified."""
    section = ExchangeSection(
        mode="testnet",
        account="sub_alpha",
        api_key_env="BOT_ALPHA_BYBIT_API_KEY",
        api_secret_env="BOT_ALPHA_BYBIT_API_SECRET",
    )
    assert section.mode == "testnet"
    assert section.account == "sub_alpha"
    with pytest.raises(ValidationError):
        section.mode = "live"


def test_exchange_section_rejects_invalid_mode_literal() -> None:
    """`mode: "demo"` not in Literal["live","testnet","paper"] → ValidationError."""
    with pytest.raises(ValidationError, match=r"mode"):
        ExchangeSection(
            mode="demo",  # type: ignore[arg-type]
            account="sub_alpha",
            api_key_env="K",
            api_secret_env="S",
        )


def test_signals_section_defaults_when_constructed_empty() -> None:
    """`SignalsSection()` returns ttl_seconds=120 + source_filter=None per §B.1 + H-008."""
    section = SignalsSection()
    assert section.ttl_seconds == 120
    assert section.source_filter is None


def test_execution_section_decimal_round_trip() -> None:
    """Decimal precision preserved across all 8 Decimal fields."""
    section = ExecutionSection(
        qty=Decimal("0.001"),
        leverage=20,
        sl_pct=Decimal("0.01"),
        tp_pct=Decimal("0.01"),
        tp_qty_pct=Decimal("0.5"),
        be_trigger=Decimal("0.005"),
        be_sl_level=Decimal("0.003"),
        trail_pct=Decimal("0.005"),
        fee_rate=Decimal("0.00055"),
    )
    assert section.qty == Decimal("0.001")
    assert section.fee_rate == Decimal("0.00055")
    assert section.sl_retry_count == 3
    assert section.emergency_close_on_sl_fail is True
    with pytest.raises(ValidationError):
        section.qty = Decimal("0.002")


def test_bot_config_with_all_three_new_sections() -> None:
    """Full §B.1-shape BotConfig builds with exchange + signals + execution + scoring."""
    cfg = BotConfig(
        bot_id="alpha",
        symbols=["BTCUSDT"],
        exchange=_DEFAULT_EXCHANGE,
        signals=SignalsSection(source_filter=["tv_rsi_v3"], ttl_seconds=60),
        execution=_DEFAULT_EXECUTION,
        scoring=_basic_scoring(),
    )
    assert cfg.exchange.mode == "paper"
    assert cfg.signals.ttl_seconds == 60
    assert cfg.execution.qty == Decimal("0.001")
    assert cfg.exchange is _DEFAULT_EXCHANGE  # frozen instance reused


# ---------------------------------------------------------------------------
# ScoringResult
# ---------------------------------------------------------------------------


def test_scoring_result_round_trip_with_arbitrary_feature_snapshot_shape() -> None:
    """shape is opaque — T-307 owns serializer; this test asserts any nested dict
    passes T-300 validation, not any specific shape contract.
    """
    result = ScoringResult(
        decision="execute",
        total_score=2.5,
        threshold=1.0,
        rule_results=[],
        feature_snapshot={"x": {"arbitrary": [1, 2]}, "y": {"foo": "bar"}},
        config_version=1,
    )
    dumped = result.model_dump()
    reconstructed = ScoringResult.model_validate(dumped)
    assert reconstructed == result


def test_scoring_result_reason_field_optional_and_settable() -> None:
    """Pin §10.4:1749/1754 verbatim — reason is None on execute/passthrough, str on reject."""
    rejected = ScoringResult(
        decision="reject",
        total_score=0.0,
        threshold=1.0,
        rule_results=[],
        feature_snapshot={},
        config_version=1,
        reason="required_feature_missing",
    )
    assert rejected.reason == "required_feature_missing"

    executed = ScoringResult(
        decision="execute",
        total_score=2.0,
        threshold=1.0,
        rule_results=[],
        feature_snapshot={},
        config_version=1,
    )
    assert executed.reason is None

    passthrough = ScoringResult(
        decision="passthrough",
        total_score=0.0,
        threshold=1.0,
        rule_results=[],
        feature_snapshot={},
        config_version=1,
    )
    assert passthrough.reason is None

    rule_error = ScoringResult(
        decision="reject",
        total_score=0.0,
        threshold=1.0,
        rule_results=[],
        feature_snapshot={},
        config_version=1,
        reason="rule_error",
    )
    assert rule_error.reason == "rule_error"


# ---------------------------------------------------------------------------
# RuleResult
# ---------------------------------------------------------------------------


def test_rule_result_error_field_optional() -> None:
    """`error` defaults to None; cross-field correlation with `result` is T-307 concern."""
    no_err = RuleResult(name="r1", weight=1.0, applied_weight=1.0, result="True")
    assert no_err.error is None

    with_err = RuleResult(
        name="r2",
        weight=1.0,
        applied_weight=0.0,
        result="error_skipped",
        error={"error": "feature missing", "traceback": "..."},
    )
    assert with_err.error == {"error": "feature missing", "traceback": "..."}


# ---------------------------------------------------------------------------
# Frozen invariant
# ---------------------------------------------------------------------------


def test_models_are_frozen() -> None:
    """All 5 models reject mutation per §5.3 immutability convention."""
    rule = _basic_rule()
    cfg = _basic_scoring()
    bot = _basic_bot()
    rr = RuleResult(name="r1", weight=1.0, applied_weight=1.0, result="True")
    sr = ScoringResult(
        decision="execute",
        total_score=1.0,
        threshold=1.0,
        rule_results=[rr],
        feature_snapshot={},
        config_version=1,
    )

    for instance, attr in [
        (rule, "name"),
        (cfg, "trigger_threshold"),
        (bot, "bot_id"),
        (rr, "name"),
        (sr, "decision"),
    ]:
        with pytest.raises(ValidationError):
            setattr(instance, attr, "mutated")


# ---------------------------------------------------------------------------
# RiskSection (T-526)
# ---------------------------------------------------------------------------


def test_risk_section_defaults_all_zero() -> None:
    """Default RiskSection has all 3 cooldown knobs at 0 (disabled)."""
    rs = RiskSection()
    assert rs.cooldown_after_loss_minutes == 0
    assert rs.cooldown_after_streak_n_losses == 0
    assert rs.cooldown_after_streak_n_losses_minutes == 0


def test_risk_section_rejects_negative_minutes() -> None:
    """All 3 fields are Field(ge=0); negative ints fail validation."""
    with pytest.raises(ValidationError):
        RiskSection(cooldown_after_loss_minutes=-1)
    with pytest.raises(ValidationError):
        RiskSection(cooldown_after_streak_n_losses=-1)
    with pytest.raises(ValidationError):
        RiskSection(cooldown_after_streak_n_losses_minutes=-1)


def test_risk_section_rejects_extra_keys() -> None:
    """``extra='forbid'`` catches operator typos at YAML load."""
    with pytest.raises(ValidationError):
        RiskSection(cooldwn_after_loss_minutes=5)  # type: ignore[call-arg]


def test_risk_section_is_frozen() -> None:
    """Mirror §5.3 immutability convention."""
    rs = RiskSection(cooldown_after_loss_minutes=10)
    with pytest.raises(ValidationError):
        rs.cooldown_after_loss_minutes = 20


def test_bot_config_default_risk_section_when_absent() -> None:
    """BotConfig.risk defaults to RiskSection() when not provided."""
    bot = _basic_bot()
    assert isinstance(bot.risk, RiskSection)
    assert bot.risk.cooldown_after_loss_minutes == 0


# ---------------------------------------------------------------------------
# RiskSection T-524 concurrent-caps extension
# ---------------------------------------------------------------------------


def test_risk_section_caps_fields_default_zero() -> None:
    """T-524: 2 new cap knobs default to 0 (disabled)."""
    rs = RiskSection()
    assert rs.max_open_trades_per_bot == 0
    assert rs.max_open_trades_global == 0


def test_risk_section_caps_reject_negative() -> None:
    """T-524 cap fields are Field(ge=0); negative ints fail validation."""
    with pytest.raises(ValidationError):
        RiskSection(max_open_trades_per_bot=-1)
    with pytest.raises(ValidationError):
        RiskSection(max_open_trades_global=-1)


def test_risk_section_extra_forbid_still_holds_after_caps_ext() -> None:
    """T-526 invariant regression guard: extra='forbid' still rejects typos
    after the T-524 field additions (and cooldown knobs still coexist)."""
    with pytest.raises(ValidationError):
        RiskSection(max_open_trade_per_bot=3)  # type: ignore[call-arg]  # typo: missing 's'
    # Coexistence: cooldown + caps knobs set together, all preserved.
    rs = RiskSection(
        cooldown_after_loss_minutes=10,
        max_open_trades_per_bot=3,
        max_open_trades_global=20,
    )
    assert rs.cooldown_after_loss_minutes == 10
    assert rs.max_open_trades_per_bot == 3
    assert rs.max_open_trades_global == 20


# ---------------------------------------------------------------------------
# RiskSection T-525a1 daily_loss_limit_usd Decimal extension
# ---------------------------------------------------------------------------


def test_risk_section_daily_loss_limit_default_zero_decimal() -> None:
    """T-525a1: daily_loss_limit_usd defaults to Decimal('0') (disabled)."""
    rs = RiskSection()
    assert rs.daily_loss_limit_usd == Decimal("0")
    assert isinstance(rs.daily_loss_limit_usd, Decimal)


def test_risk_section_daily_loss_limit_accepts_decimal() -> None:
    """Accepts a positive Decimal money value, exact."""
    rs = RiskSection(daily_loss_limit_usd=Decimal("100.50"))
    assert rs.daily_loss_limit_usd == Decimal("100.50")


def test_risk_section_daily_loss_limit_rejects_negative() -> None:
    """Field(ge=0): negative threshold fails validation."""
    with pytest.raises(ValidationError):
        RiskSection(daily_loss_limit_usd=Decimal("-1"))


def test_risk_section_all_three_task_knobs_coexist() -> None:
    """T-526 cooldown + T-524 caps + T-525a1 loss-limit coexist; extra=forbid holds."""
    rs = RiskSection(
        cooldown_after_loss_minutes=10,
        max_open_trades_per_bot=3,
        daily_loss_limit_usd=Decimal("250.00"),
    )
    assert rs.cooldown_after_loss_minutes == 10
    assert rs.max_open_trades_per_bot == 3
    assert rs.daily_loss_limit_usd == Decimal("250.00")
    with pytest.raises(ValidationError):
        RiskSection(daily_loss_limit_used=Decimal("5"))  # type: ignore[call-arg]  # typo


# ---------------------------------------------------------------------------
# RiskSection T-525b max_drawdown_pct Decimal extension
# ---------------------------------------------------------------------------


def test_risk_section_max_drawdown_default_zero_decimal() -> None:
    rs = RiskSection()
    assert rs.max_drawdown_pct == Decimal("0")
    assert isinstance(rs.max_drawdown_pct, Decimal)


def test_risk_section_max_drawdown_accepts_fraction_decimal() -> None:
    rs = RiskSection(max_drawdown_pct=Decimal("0.20"))
    assert rs.max_drawdown_pct == Decimal("0.20")


def test_risk_section_max_drawdown_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        RiskSection(max_drawdown_pct=Decimal("-0.1"))


def test_risk_section_max_drawdown_allows_above_one_no_le_bound() -> None:
    """No le bound (mirror sl_pct): give-back can exceed 1.0 when current<0<peak."""
    rs = RiskSection(max_drawdown_pct=Decimal("1.50"))
    assert rs.max_drawdown_pct == Decimal("1.50")


def test_risk_section_all_four_task_knobs_coexist() -> None:
    """T-526 + T-524 + T-525a1 + T-525b coexist; extra=forbid holds."""
    rs = RiskSection(
        cooldown_after_loss_minutes=10,
        max_open_trades_per_bot=3,
        daily_loss_limit_usd=Decimal("250.00"),
        max_drawdown_pct=Decimal("0.30"),
    )
    assert rs.cooldown_after_loss_minutes == 10
    assert rs.max_open_trades_per_bot == 3
    assert rs.daily_loss_limit_usd == Decimal("250.00")
    assert rs.max_drawdown_pct == Decimal("0.30")
    with pytest.raises(ValidationError):
        RiskSection(max_drawdown_ptc=Decimal("0.2"))  # type: ignore[call-arg]  # typo: ptc≠pct


# ---------------------------------------------------------------------------
# SizingSection / SizingTier (T-527a — §B.1 sizing config foundation)
# ---------------------------------------------------------------------------


def _valid_sizing() -> SizingSection:
    """Representative §B.1 alpha.yaml sizing block (BRIEF §22 lines 3130-3145)."""
    return SizingSection(
        tiers=[
            SizingTier(balance_min=Decimal("500"), size=Decimal("700")),
            SizingTier(balance_min=Decimal("1000"), size=Decimal("1400")),
            SizingTier(balance_min=Decimal("2000"), size=Decimal("2100")),
        ],
        score_multipliers={"4": Decimal("0.75"), "5": Decimal("1.0"), "9": Decimal("1.5")},
        max_notional_per_symbol={"default": Decimal("3000"), "BTCUSDT": Decimal("5000")},
    )


def test_sizing_section_valid_construction() -> None:
    sec = _valid_sizing()
    assert len(sec.tiers) == 3
    assert sec.tiers[0].balance_min == Decimal("500")
    assert sec.score_multipliers["9"] == Decimal("1.5")
    assert sec.max_notional_per_symbol["default"] == Decimal("3000")


def test_sizing_models_are_frozen() -> None:
    """SizingTier + SizingSection reject mutation (§5.3 immutability)."""
    sec = _valid_sizing()
    with pytest.raises(ValidationError):
        setattr(sec.tiers[0], "balance_min", Decimal("1"))
    with pytest.raises(ValidationError):
        setattr(sec, "score_multipliers", {})


def test_sizing_tier_rejects_negative() -> None:
    """SizingTier balance_min/size are Field(ge=0)."""
    with pytest.raises(ValidationError):
        SizingTier(balance_min=Decimal("-1"), size=Decimal("700"))
    with pytest.raises(ValidationError):
        SizingTier(balance_min=Decimal("500"), size=Decimal("-1"))


def test_sizing_section_rejects_empty_tiers() -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        SizingSection(
            tiers=[],
            score_multipliers={"4": Decimal("1")},
            max_notional_per_symbol={"default": Decimal("3000")},
        )


def test_sizing_section_rejects_non_ascending_tiers() -> None:
    """balance_min must be strictly ascending (well-defined lowest tier / scan)."""
    with pytest.raises(ValidationError, match="strictly ascending"):
        SizingSection(
            tiers=[
                SizingTier(balance_min=Decimal("1000"), size=Decimal("1400")),
                SizingTier(balance_min=Decimal("500"), size=Decimal("700")),
            ],
            score_multipliers={"4": Decimal("1")},
            max_notional_per_symbol={"default": Decimal("3000")},
        )
    # equal balance_min is also non-strict → rejected
    with pytest.raises(ValidationError, match="strictly ascending"):
        SizingSection(
            tiers=[
                SizingTier(balance_min=Decimal("500"), size=Decimal("700")),
                SizingTier(balance_min=Decimal("500"), size=Decimal("800")),
            ],
            score_multipliers={"4": Decimal("1")},
            max_notional_per_symbol={"default": Decimal("3000")},
        )


def test_sizing_section_rejects_missing_default_cap() -> None:
    with pytest.raises(ValidationError, match="default"):
        SizingSection(
            tiers=[SizingTier(balance_min=Decimal("500"), size=Decimal("700"))],
            score_multipliers={"4": Decimal("1")},
            max_notional_per_symbol={"BTCUSDT": Decimal("5000")},
        )


def test_sizing_section_rejects_non_digit_multiplier_key() -> None:
    with pytest.raises(ValidationError, match="digit-string"):
        SizingSection(
            tiers=[SizingTier(balance_min=Decimal("500"), size=Decimal("700"))],
            score_multipliers={"high": Decimal("1.5")},
            max_notional_per_symbol={"default": Decimal("3000")},
        )


def test_sizing_section_rejects_deferred_tier_promotion_key() -> None:
    """OQ-2=A: tier_promotion/tier_demotion deliberately NOT modeled →
    extra='forbid' rejects a stray block (pins the deferral at the schema)."""
    with pytest.raises(ValidationError):
        SizingSection(
            tiers=[SizingTier(balance_min=Decimal("500"), size=Decimal("700"))],
            score_multipliers={"4": Decimal("1")},
            max_notional_per_symbol={"default": Decimal("3000")},
            tier_promotion={"min_trades": 10},  # type: ignore[call-arg]
        )
