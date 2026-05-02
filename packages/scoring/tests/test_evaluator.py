"""§N5 unit tests for :mod:`packages.scoring.evaluator` (T-307).

TDD discipline (§N4 spirit per WG#3 T-200 precedent): tests written
before implementation. Mock-based: FeatureResolver mocked; conditions
real Pydantic instances; bound_logger MagicMock for emit-pin assertions.

Tests #26-31 pin H-019 fail-open emit per BRIEF §20:2747-2749.
Test #32 pins applies_when v1-skip (T-308 follow-up grep-anchor).
Test #33 pins early-reject ScoringResult 7-field invariant per Validation rule #11.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from packages.bus.schemas.signals import SignalValidated
from packages.features.types import FeatureValue
from packages.scoring import evaluate
from packages.scoring.conditions import (
    GtCondition,
    LtCondition,
)
from packages.scoring.resolver import ResolverResult
from packages.scoring.types import (
    BotConfig,
    ExchangeSection,
    ExecutionSection,
    ScoringConfig,
    ScoringRule,
)

_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _signal(symbol: str = "BTCUSDT", action: str = "LONG") -> SignalValidated:
    return SignalValidated(
        source="webhook",
        idempotency_key="test-key-1",
        received_at=_FIXED_NOW,
        symbol=symbol,
        original_symbol=symbol,
        action=action,  # type: ignore[arg-type]
        expires_at=_FIXED_NOW + timedelta(seconds=60),
        payload={},
    )


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


def _bot(
    *,
    rules: list[ScoringRule],
    threshold: float = 1.0,
    mode: str = "active",
    version: int = 1,
) -> BotConfig:
    return BotConfig(
        bot_id="alpha",
        version=version,
        symbols=["BTCUSDT"],
        exchange=_DEFAULT_EXCHANGE,
        execution=_DEFAULT_EXECUTION,
        scoring=ScoringConfig(
            mode=mode,  # type: ignore[arg-type]
            trigger_threshold=threshold,
            rules=rules,
        ),
    )


def _ok_resolver(value: int | float | str = 60000) -> MagicMock:
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=ResolverResult(
            value=FeatureValue(value_num=Decimal(str(value))),
            status="ok",
        )
    )
    return resolver


def _missing_resolver() -> MagicMock:
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=ResolverResult(
            value=None,
            status="data_missing",
            error_info={"path": "kv+db missing", "feature_ref": "ind.btcusdt.15m.ema_20"},
        )
    )
    return resolver


def _stale_resolver() -> MagicMock:
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=ResolverResult(
            value=None,
            status="data_stale",
            error_info={"age_sec": 3000, "max_staleness_sec": 1800},
        )
    )
    return resolver


def _gt_rule(
    *, name: str = "r1", weight: float = 1.0, required: bool = False, on_error: str = "skip"
) -> ScoringRule:
    return ScoringRule(
        name=name,
        weight=weight,
        feature="ind.${signal.symbol}.15m.ema_20",
        condition={"type": "gt", "value": "50000"},  # placeholder; replaced via _with_condition
        on_error=on_error,  # type: ignore[arg-type]
        required=required,
    )


def _with_condition(rule: ScoringRule, condition_obj: object) -> ScoringRule:
    """Pydantic frozen — rebuild via model_dump+update + condition object injection.

    T-307 v1 reads condition via duck-typed evaluate() call; condition field is
    typed dict[str, Any] at T-300 layer, but at runtime we pass a real Condition
    instance. ScoringRule has frozen=True; we use object.__setattr__ via dataclass
    re-creation by Pydantic's model_construct (skips validation).
    """
    return ScoringRule.model_construct(
        name=rule.name,
        weight=rule.weight,
        feature=rule.feature,
        applies_when=rule.applies_when,
        condition=condition_obj,
        on_error=rule.on_error,
        required=rule.required,
        max_staleness_sec=rule.max_staleness_sec,
    )


# region: 1-3. single-rule decision branches --------------------------------


async def test_single_rule_execute() -> None:
    rule = _with_condition(_gt_rule(weight=2.5), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule], threshold=2.0)
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(60000), bound_logger=MagicMock()
    )
    assert result.decision == "execute"
    assert result.reason is None
    assert result.total_score == 2.5


async def test_single_rule_reject_score_below_threshold() -> None:
    rule = _with_condition(_gt_rule(weight=2.5), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule], threshold=2.0)
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(40000), bound_logger=MagicMock()
    )
    assert result.decision == "reject"
    assert result.reason == "score_below_threshold"
    assert result.total_score == 0.0


async def test_threshold_boundary_inclusive_total_equals_threshold_executes() -> None:
    rule = _with_condition(_gt_rule(weight=2.0), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule], threshold=2.0)
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(60000), bound_logger=MagicMock()
    )
    assert result.decision == "execute"


# region: 4-5. multi-rule sum + negative weight -----------------------------


async def test_two_rule_sum() -> None:
    r1 = _with_condition(
        ScoringRule(
            name="r1",
            weight=1.5,
            feature="ind.${signal.symbol}.15m.ema_20",
            condition={"type": "gt", "value": "50000"},
        ),
        GtCondition(value=Decimal("50000")),
    )
    r2 = _with_condition(
        ScoringRule(
            name="r2",
            weight=2.0,
            feature="ind.${signal.symbol}.15m.ema_20",
            condition={"type": "lt", "value": "70000"},
        ),
        LtCondition(value=Decimal("70000")),
    )
    cfg = _bot(rules=[r1, r2], threshold=3.0)
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(60000), bound_logger=MagicMock()
    )
    assert result.decision == "execute"
    assert result.total_score == 3.5


async def test_negative_weight_subtractive() -> None:
    r1 = _with_condition(
        ScoringRule(
            name="r1",
            weight=2.0,
            feature="ind.${signal.symbol}.15m.ema_20",
            condition={"type": "gt", "value": "50000"},
        ),
        GtCondition(value=Decimal("50000")),
    )
    r2 = _with_condition(
        ScoringRule(
            name="r2",
            weight=-0.5,
            feature="ind.${signal.symbol}.15m.ema_20",
            condition={"type": "lt", "value": "70000"},
        ),
        LtCondition(value=Decimal("70000")),
    )
    cfg = _bot(rules=[r1, r2], threshold=1.0)
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(60000), bound_logger=MagicMock()
    )
    assert result.total_score == 1.5


# region: 6-9. required vs optional x data_missing/data_stale ----------------


async def test_required_data_missing_rejects() -> None:
    rule = _with_condition(_gt_rule(required=True), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule])
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_missing_resolver(), bound_logger=MagicMock()
    )
    assert result.decision == "reject"
    assert result.reason == "required_feature_missing"


async def test_required_data_stale_rejects() -> None:
    rule = _with_condition(_gt_rule(required=True), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule])
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_stale_resolver(), bound_logger=MagicMock()
    )
    assert result.decision == "reject"
    assert result.reason == "required_feature_stale"


async def test_optional_data_missing_continues_with_skipped_rule() -> None:
    r1 = _with_condition(
        ScoringRule(
            name="r1",
            weight=1.0,
            feature="ind.${signal.symbol}.15m.ema_20",
            condition={"type": "gt", "value": "0"},
        ),
        GtCondition(value=Decimal("0")),
    )
    r2 = _with_condition(
        _gt_rule(name="r2", weight=2.5, required=False),
        GtCondition(value=Decimal("50000")),
    )
    cfg = _bot(rules=[r1, r2], threshold=1.0)
    # First rule resolves ok; second resolver returns missing.
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        side_effect=[
            ResolverResult(value=FeatureValue(value_num=Decimal("60000")), status="ok"),
            ResolverResult(value=None, status="data_missing", error_info={"path": "miss"}),
        ]
    )
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=resolver, bound_logger=MagicMock()
    )
    assert result.decision == "execute"  # r1 alone meets threshold 1.0
    assert len(result.rule_results) == 2
    assert result.rule_results[1].result == "data_missing"
    assert result.rule_results[1].applied_weight == 0.0


async def test_optional_data_stale_continues_with_skipped_rule() -> None:
    rule = _with_condition(_gt_rule(required=False), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule])
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_stale_resolver(), bound_logger=MagicMock()
    )
    assert result.decision == "reject"  # 0 < 1.0
    assert result.rule_results[0].result == "data_stale"


# region: 10-11. on_error policy --------------------------------------------


async def test_on_error_reject_when_condition_raises() -> None:
    class _RaisingCondition:
        type = "raising"

        def evaluate(self, ctx: object) -> tuple[bool, dict[str, object] | None]:
            msg = "boom"
            raise RuntimeError(msg)

    rule = _with_condition(_gt_rule(on_error="reject"), _RaisingCondition())
    cfg = _bot(rules=[rule])
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(), bound_logger=MagicMock()
    )
    assert result.decision == "reject"
    assert result.reason == "rule_error"


async def test_on_error_skip_when_condition_raises_continues() -> None:
    class _RaisingCondition:
        type = "raising"

        def evaluate(self, ctx: object) -> tuple[bool, dict[str, object] | None]:
            msg = "boom"
            raise RuntimeError(msg)

    rule = _with_condition(_gt_rule(on_error="skip"), _RaisingCondition())
    cfg = _bot(rules=[rule])
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(), bound_logger=MagicMock()
    )
    assert result.decision == "reject"  # 0 < threshold; clean post-loop
    assert result.rule_results[0].result == "error_skipped"


# region: 12-14. mode=passthrough -------------------------------------------


async def test_passthrough_below_threshold_decision_passthrough() -> None:
    rule = _with_condition(_gt_rule(weight=0.5), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule], threshold=2.0, mode="passthrough")
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(60000), bound_logger=MagicMock()
    )
    assert result.decision == "passthrough"
    assert result.total_score == 0.5
    assert result.reason is None


async def test_passthrough_above_threshold_decision_passthrough() -> None:
    rule = _with_condition(_gt_rule(weight=5.0), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule], threshold=2.0, mode="passthrough")
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(60000), bound_logger=MagicMock()
    )
    assert result.decision == "passthrough"


async def test_passthrough_empty_rules_decision_passthrough() -> None:
    cfg = _bot(rules=[], threshold=2.0, mode="passthrough")
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(), bound_logger=MagicMock()
    )
    assert result.decision == "passthrough"
    assert result.total_score == 0.0


# region: 15. empty rules + active mode -------------------------------------


async def test_empty_rules_active_mode_rejects_score_below_threshold() -> None:
    cfg = _bot(rules=[], threshold=2.0)
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(), bound_logger=MagicMock()
    )
    assert result.decision == "reject"
    assert result.reason == "score_below_threshold"
    assert result.total_score == 0.0


# region: 16-17. resolver call args contract --------------------------------


async def test_resolver_called_with_rule_feature_signal_max_staleness() -> None:
    rule = _with_condition(_gt_rule(), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule])
    resolver = _ok_resolver()
    await evaluate(bot_config=cfg, signal=_signal(), resolver=resolver, bound_logger=MagicMock())
    resolver.resolve.assert_awaited_once()
    kwargs = resolver.resolve.await_args.kwargs
    assert kwargs["rule_feature"] == "ind.${signal.symbol}.15m.ema_20"
    assert kwargs["signal"].symbol == "BTCUSDT"


async def test_resolver_max_staleness_sec_none_passes_none() -> None:
    rule = _with_condition(_gt_rule(), GtCondition(value=Decimal("50000")))
    assert rule.max_staleness_sec is None
    cfg = _bot(rules=[rule])
    resolver = _ok_resolver()
    await evaluate(bot_config=cfg, signal=_signal(), resolver=resolver, bound_logger=MagicMock())
    assert resolver.resolve.await_args.kwargs["max_staleness_sec"] is None


# region: 18-19. RuleContext build per-rule ---------------------------------


async def test_rule_context_feature_snapshot_keyed_on_resolved_ref() -> None:
    captured: list[object] = []

    class _CapturingCondition:
        type = "capturing"

        def evaluate(self, ctx: object) -> tuple[bool, dict[str, object] | None]:
            captured.append(ctx)
            return True, None

    rule = _with_condition(_gt_rule(), _CapturingCondition())
    cfg = _bot(rules=[rule], threshold=0.5)
    await evaluate(
        bot_config=cfg,
        signal=_signal("BTCUSDT"),
        resolver=_ok_resolver(60000),
        bound_logger=MagicMock(),
    )
    ctx = captured[0]
    assert ctx.feature_ref == "ind.btcusdt.15m.ema_20"  # type: ignore[attr-defined]
    assert "ind.btcusdt.15m.ema_20" in ctx.feature_snapshot  # type: ignore[attr-defined]


async def test_rule_context_signal_propagated() -> None:
    captured: list[object] = []

    class _CapturingCondition:
        type = "capturing"

        def evaluate(self, ctx: object) -> tuple[bool, dict[str, object] | None]:
            captured.append(ctx)
            return True, None

    rule = _with_condition(_gt_rule(), _CapturingCondition())
    cfg = _bot(rules=[rule])
    sig = _signal("ETHUSDT")
    await evaluate(bot_config=cfg, signal=sig, resolver=_ok_resolver(), bound_logger=MagicMock())
    assert captured[0].signal is sig  # type: ignore[attr-defined]


# region: 20-21. feature_snapshot serialization + config_version -----------


async def test_feature_snapshot_serialized_decimal_to_str() -> None:
    rule = _with_condition(_gt_rule(), GtCondition(value=Decimal("0")))
    cfg = _bot(rules=[rule])
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver("50000.5"), bound_logger=MagicMock()
    )
    snapshot = result.feature_snapshot["ind.btcusdt.15m.ema_20"]
    assert snapshot["value_num"] == "50000.5"
    assert snapshot["value_bool"] is None


async def test_config_version_propagated_to_scoring_result() -> None:
    rule = _with_condition(_gt_rule(), GtCondition(value=Decimal("0")))
    cfg = _bot(rules=[rule], version=42)
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(), bound_logger=MagicMock()
    )
    assert result.config_version == 42


# region: 22-24. error propagation + properties ----------------------------


async def test_error_info_propagated_when_condition_returns_false_with_error() -> None:
    class _ErroringCondition:
        type = "erroring"

        def evaluate(self, ctx: object) -> tuple[bool, dict[str, object] | None]:
            return False, {"error": "type_mismatch", "expected": "value_num"}

    rule = _with_condition(_gt_rule(), _ErroringCondition())
    cfg = _bot(rules=[rule])
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(), bound_logger=MagicMock()
    )
    assert result.rule_results[0].error == {"error": "type_mismatch", "expected": "value_num"}


async def test_partial_rule_results_on_early_reject() -> None:
    """Required+missing reject mid-loop → rule_results has processed rules + the rejecting one."""
    r1 = _with_condition(
        ScoringRule(
            name="r1",
            weight=1.0,
            feature="ind.${signal.symbol}.15m.ema_20",
            condition={"type": "gt", "value": "0"},
        ),
        GtCondition(value=Decimal("0")),
    )
    r2 = _with_condition(_gt_rule(name="r2", required=True), GtCondition(value=Decimal("50000")))
    r3 = _with_condition(
        ScoringRule(
            name="r3",
            weight=10.0,
            feature="ind.${signal.symbol}.15m.ema_20",
            condition={"type": "gt", "value": "0"},
        ),
        GtCondition(value=Decimal("0")),
    )
    cfg = _bot(rules=[r1, r2, r3])
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        side_effect=[
            ResolverResult(value=FeatureValue(value_num=Decimal("50")), status="ok"),
            ResolverResult(value=None, status="data_missing"),
        ]
    )
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=resolver, bound_logger=MagicMock()
    )
    assert result.decision == "reject"
    assert result.reason == "required_feature_missing"
    assert len(result.rule_results) == 2  # r1 + r2 (r3 not reached)


async def test_total_score_equals_sum_of_applied_weights() -> None:
    r1 = _with_condition(
        ScoringRule(
            name="r1",
            weight=1.5,
            feature="ind.${signal.symbol}.15m.ema_20",
            condition={"type": "gt", "value": "0"},
        ),
        GtCondition(value=Decimal("0")),
    )
    r2 = _with_condition(
        ScoringRule(
            name="r2",
            weight=2.5,
            feature="ind.${signal.symbol}.15m.ema_20",
            condition={"type": "gt", "value": "999999"},  # False: 60000 > 999999 = False
        ),
        GtCondition(value=Decimal("999999")),
    )
    cfg = _bot(rules=[r1, r2], threshold=0.0)
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(60000), bound_logger=MagicMock()
    )
    expected = sum(rr.applied_weight for rr in result.rule_results)
    assert result.total_score == expected
    assert result.total_score == 1.5  # only r1 True


# region: 25. (combined into 22-24)


# region: 26-31. H-019 scoring_failed_open emit pins ------------------------


from typing import Any  # noqa: E402 — used in helpers below


def _warning_calls(bound_logger: MagicMock, event_label: str) -> list[Any]:
    return [
        call
        for call in bound_logger.warning.call_args_list
        if call.args and call.args[0] == event_label
    ]


def _info_calls(bound_logger: MagicMock, event_label: str) -> list[Any]:
    return [
        call
        for call in bound_logger.info.call_args_list
        if call.args and call.args[0] == event_label
    ]


async def test_rule_error_emits_logged_event_not_silent_pass() -> None:
    """BRIEF §20:2749 verbatim test-name. on_error=skip + condition raises → scoring_failed_open."""

    class _RaisingCondition:
        type = "raising"

        def evaluate(self, ctx: object) -> tuple[bool, dict[str, object] | None]:
            msg = "boom"
            raise RuntimeError(msg)

    rule = _with_condition(_gt_rule(on_error="skip"), _RaisingCondition())
    cfg = _bot(rules=[rule])
    bound_logger = MagicMock()
    await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(), bound_logger=bound_logger
    )
    failed_open = _warning_calls(bound_logger, "scoring_failed_open")
    assert len(failed_open) == 1
    assert failed_open[0].kwargs["reason"] == "condition_exception"
    assert failed_open[0].kwargs["rule_name"] == "r1"


async def test_optional_data_missing_emits_failed_open() -> None:
    rule = _with_condition(_gt_rule(required=False), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule])
    bound_logger = MagicMock()
    await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_missing_resolver(), bound_logger=bound_logger
    )
    failed_open = _warning_calls(bound_logger, "scoring_failed_open")
    assert len(failed_open) == 1
    assert failed_open[0].kwargs["reason"] == "data_missing"


async def test_optional_data_stale_emits_failed_open() -> None:
    rule = _with_condition(_gt_rule(required=False), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule])
    bound_logger = MagicMock()
    await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_stale_resolver(), bound_logger=bound_logger
    )
    failed_open = _warning_calls(bound_logger, "scoring_failed_open")
    assert len(failed_open) == 1
    assert failed_open[0].kwargs["reason"] == "data_stale"


async def test_required_feature_missing_does_not_emit_failed_open() -> None:
    rule = _with_condition(_gt_rule(required=True), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule])
    bound_logger = MagicMock()
    await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_missing_resolver(), bound_logger=bound_logger
    )
    assert _warning_calls(bound_logger, "scoring_failed_open") == []
    assert len(_info_calls(bound_logger, "scoring_required_feature_missing")) == 1


async def test_required_feature_stale_does_not_emit_failed_open() -> None:
    rule = _with_condition(_gt_rule(required=True), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule])
    bound_logger = MagicMock()
    await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_stale_resolver(), bound_logger=bound_logger
    )
    assert _warning_calls(bound_logger, "scoring_failed_open") == []
    assert len(_info_calls(bound_logger, "scoring_required_feature_stale")) == 1


async def test_on_error_reject_does_not_emit_failed_open() -> None:
    class _RaisingCondition:
        type = "raising"

        def evaluate(self, ctx: object) -> tuple[bool, dict[str, object] | None]:
            msg = "boom"
            raise RuntimeError(msg)

    rule = _with_condition(_gt_rule(on_error="reject"), _RaisingCondition())
    cfg = _bot(rules=[rule])
    bound_logger = MagicMock()
    await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_ok_resolver(), bound_logger=bound_logger
    )
    assert _warning_calls(bound_logger, "scoring_failed_open") == []
    assert len(_info_calls(bound_logger, "scoring_rule_error_reject")) == 1


# region: 32. applies_when v1-skip pin --------------------------------------


async def test_applies_when_v1_skipped_rule_still_evaluates() -> None:
    """T-307 v1 ignores rule.applies_when. Doc marker for T-308 follow-up."""
    rule = ScoringRule.model_construct(
        name="r1",
        weight=1.0,
        feature="ind.${signal.symbol}.15m.ema_20",
        applies_when={"signal.action": "open_short"},  # mismatched: signal is LONG
        condition=GtCondition(value=Decimal("50000")),
        on_error="skip",
        required=False,
        max_staleness_sec=None,
    )
    cfg = _bot(rules=[rule], threshold=0.5)
    result = await evaluate(
        bot_config=cfg,
        signal=_signal(action="LONG"),
        resolver=_ok_resolver(60000),
        bound_logger=MagicMock(),
    )
    # Rule STILL evaluates despite applies_when mismatch (v1 skip).
    assert result.decision == "execute"
    assert result.rule_results[0].result == "True"


# region: 33. early-reject ScoringResult 7-field invariant ------------------


async def test_early_reject_scoring_result_populates_all_seven_fields() -> None:
    """Validation rule #11 — early-reject populates all 7 ScoringResult fields explicitly."""
    rule = _with_condition(_gt_rule(required=True), GtCondition(value=Decimal("50000")))
    cfg = _bot(rules=[rule], threshold=2.0, version=7)
    result = await evaluate(
        bot_config=cfg, signal=_signal(), resolver=_missing_resolver(), bound_logger=MagicMock()
    )
    # All 7 fields populated.
    assert result.decision == "reject"
    assert result.reason == "required_feature_missing"
    assert result.total_score == 0.0
    assert result.threshold == 2.0
    assert len(result.rule_results) == 1
    assert isinstance(result.feature_snapshot, dict)
    assert result.config_version == 7
