"""§N5 unit tests for :mod:`packages.scoring.types` (T-300, F3 §10).

Pure schema / validation tests — no I/O, no business logic. Coverage
target ≥80% on `packages/scoring/` per §N5.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.scoring import (
    BotConfig,
    RuleResult,
    ScoringConfig,
    ScoringResult,
    ScoringRule,
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
        BotConfig(bot_id="", symbols=["BTCUSDT"], scoring=_basic_scoring())


def test_bot_config_rejects_invalid_bot_id_charset() -> None:
    """Convention regex: lowercase + digits + underscore + hyphen, must start with letter."""
    for bad in ("Alpha", "1alpha", "alpha bot", "alpha.beta", "alpha/beta"):
        with pytest.raises(ValidationError, match="bot_id"):
            BotConfig(bot_id=bad, symbols=["BTCUSDT"], scoring=_basic_scoring())


def test_bot_config_accepts_valid_bot_id_charset() -> None:
    for good in ("alpha", "alpha-2", "alpha_beta", "a", "alpha-bot-1"):
        cfg = BotConfig(bot_id=good, symbols=["BTCUSDT"], scoring=_basic_scoring())
        assert cfg.bot_id == good


def test_bot_config_version_default_one_minimum_one() -> None:
    cfg = _basic_bot()
    assert cfg.version == 1
    with pytest.raises(ValidationError):
        BotConfig(bot_id="alpha", version=0, symbols=["BTCUSDT"], scoring=_basic_scoring())


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
