"""§10.4 evaluator pipeline (T-307).

Per-signal scoring loop. Iterates ``bot_config.scoring.rules``, resolves
templated ``feature`` via injected :class:`FeatureResolver` (T-306),
invokes ``rule.condition.evaluate(ctx)`` (T-302 Path C), accumulates
``total_score``, returns :class:`ScoringResult` (T-300) per BRIEF §10.4
verbatim pseudocode.

H-019 fail-open emit (Path A operator-approved 2026-05-02): 3 fail-open
branches (skip-exception, optional+data_missing, optional+data_stale)
emit ``scoring_failed_open`` warning to ``system.log`` per §20:2747.
Reject paths use info-level labels.

V1 limitations: ``rule.applies_when`` IGNORED (T-308 follow-up);
RuleContext single-feature per rule (multi-feature composite deferred);
series conditions return ``data_missing`` (T-306 doesn't populate
``feature_history``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .conditions.base import RuleContext
from .types import Decision, RuleResult, ScoringResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from structlog.stdlib import BoundLogger

    from packages.bus.schemas.signals import SignalValidated
    from packages.features.types import FeatureValue

    from .resolver import FeatureResolver, ResolverResult
    from .types import BotConfig, ScoringRule


__all__ = ["evaluate"]


def _required_history_window(condition: object) -> int | None:
    """Detect history window size from condition shape (T-303 series + T-305 plugin).

    Per BLOCKER#1 plan-reviewer Gate 1 REVISE 2026-05-12: explicit fallback
    chain rather than getattr-by-attr-name sniff that silently misses plugin
    Rules. Two known consumer shapes:

    * T-303 RisingCondition / FallingCondition: ``condition.n_samples`` (int).
    * T-305 PluginCondition wrapping a Rule with history-window need
      (oi_squeeze plugin): ``condition.rule.lookback_candles`` (int).

    Returns int when condition needs history, None for simple conditions
    (T-302 ThresholdCondition / EmaStackCondition / etc.).
    """
    n_samples = getattr(condition, "n_samples", None)
    if isinstance(n_samples, int):
        return n_samples
    rule = getattr(condition, "rule", None)
    if rule is not None:
        lookback = getattr(rule, "lookback_candles", None)
        if isinstance(lookback, int):
            return lookback
    return None


def _log_failed_open(
    bound_logger: BoundLogger,
    *,
    bot_id: str,
    signal_id: str,
    rule_name: str,
    reason: str,
    error_repr: str | None = None,
) -> None:
    """H-019 emit per BRIEF §20:2747 — single call site for all 3 fail-open branches."""
    bound_logger.warning(
        "scoring_failed_open",
        bot_id=bot_id,
        signal_id=signal_id,
        rule_name=rule_name,
        reason=reason,
        error_repr=error_repr,
    )


def _serialize_feature_value(fv: FeatureValue) -> dict[str, Any]:
    """§N1 Decimal→str. T-300 ScoringResult.feature_snapshot inner shape opaque."""
    return {
        "value_num": str(fv.value_num) if fv.value_num is not None else None,
        "value_bool": fv.value_bool,
        "value_json": dict(fv.value_json) if fv.value_json is not None else None,
    }


def _build_early_reject(
    *,
    decision_reason: str,
    total_score: float,
    threshold: float,
    rule_results: list[RuleResult],
    feature_snapshot: dict[str, dict[str, Any]],
    config_version: int,
) -> ScoringResult:
    """All 7 ScoringResult fields populated per Validation rule #11."""
    return ScoringResult(
        decision="reject",
        reason=decision_reason,
        total_score=total_score,
        threshold=threshold,
        rule_results=rule_results,
        feature_snapshot=feature_snapshot,
        config_version=config_version,
    )


async def evaluate(
    *,
    bot_config: BotConfig,
    signal: SignalValidated,
    resolver: FeatureResolver,
    bound_logger: BoundLogger,
) -> ScoringResult:
    """Per BRIEF §10.4 verbatim pseudocode + Path A H-019 emit + Path C RuleContext."""
    bot_id = bot_config.bot_id
    signal_id = signal.idempotency_key
    threshold = bot_config.scoring.trigger_threshold

    rule_results: list[RuleResult] = []
    feature_snapshot: dict[str, dict[str, Any]] = {}
    total_score = 0.0

    for rule in bot_config.scoring.rules:
        # T-307 v1: rule.applies_when ignored — see OQ-1, T-308 follow-up
        rr = await _evaluate_rule(
            rule=rule,
            signal=signal,
            resolver=resolver,
            bound_logger=bound_logger,
            bot_id=bot_id,
            signal_id=signal_id,
            rule_results=rule_results,
            feature_snapshot=feature_snapshot,
            total_score=total_score,
            threshold=threshold,
            config_version=bot_config.version,
        )
        if isinstance(rr, ScoringResult):
            return rr  # early reject (required+missing/stale or on_error=reject)
        rule_results.append(rr)
        total_score += rr.applied_weight

    decision: Decision
    reason: str | None
    if bot_config.scoring.mode == "passthrough":
        decision = "passthrough"
        reason = None
    elif total_score >= threshold:
        decision = "execute"
        reason = None
    else:
        decision = "reject"
        reason = "score_below_threshold"

    bound_logger.info(
        "scoring_evaluation_complete",
        bot_id=bot_id,
        signal_id=signal_id,
        total_score=total_score,
        decision=decision,
        threshold=threshold,
        mode=bot_config.scoring.mode,
        rule_count=len(bot_config.scoring.rules),
    )

    return ScoringResult(
        decision=decision,
        reason=reason,
        total_score=total_score,
        threshold=threshold,
        rule_results=rule_results,
        feature_snapshot=feature_snapshot,
        config_version=bot_config.version,
    )


async def _evaluate_rule(
    *,
    rule: ScoringRule,
    signal: SignalValidated,
    resolver: FeatureResolver,
    bound_logger: BoundLogger,
    bot_id: str,
    signal_id: str,
    rule_results: list[RuleResult],
    feature_snapshot: dict[str, dict[str, Any]],
    total_score: float,
    threshold: float,
    config_version: int,
) -> RuleResult | ScoringResult:
    """Returns RuleResult (continue loop) or ScoringResult (early reject)."""
    resolver_result: ResolverResult = await resolver.resolve(
        rule_feature=rule.feature,
        signal=signal,
        max_staleness_sec=rule.max_staleness_sec,
    )

    if resolver_result.status in ("data_missing", "data_stale"):
        result_str = resolver_result.status
        if rule.required:
            info_label, reject_reason = (
                ("scoring_required_feature_missing", "required_feature_missing")
                if result_str == "data_missing"
                else ("scoring_required_feature_stale", "required_feature_stale")
            )
            bound_logger.info(
                info_label,
                bot_id=bot_id,
                signal_id=signal_id,
                rule_name=rule.name,
                feature_ref=rule.feature,
            )
            partial_rr = RuleResult(
                name=rule.name,
                weight=rule.weight,
                applied_weight=0.0,
                result=result_str,
                error=resolver_result.error_info,
            )
            return _build_early_reject(
                decision_reason=reject_reason,
                total_score=total_score,
                threshold=threshold,
                rule_results=[*rule_results, partial_rr],
                feature_snapshot=feature_snapshot,
                config_version=config_version,
            )
        _log_failed_open(
            bound_logger,
            bot_id=bot_id,
            signal_id=signal_id,
            rule_name=rule.name,
            reason=result_str,
        )
        return RuleResult(
            name=rule.name,
            weight=rule.weight,
            applied_weight=0.0,
            result=result_str,
            error=resolver_result.error_info,
        )

    # status == "ok" — defensive: value must not be None
    if resolver_result.value is None:
        msg = f"resolver returned ok with None value for rule {rule.name!r}"
        raise RuntimeError(msg)

    resolved_ref = _resolve_feature_ref(rule.feature, signal)
    feature_snapshot[resolved_ref] = _serialize_feature_value(resolver_result.value)

    # T-520 sub-commit #2 — populate feature_history for series + plugin
    # conditions per BLOCKER#1 fix. Simple conditions (T-302) skip this.
    n_history = _required_history_window(rule.condition)
    feature_history: dict[str, Sequence[FeatureValue]] = {}
    if n_history is not None:
        history = await resolver.resolve_history(
            rule_feature=rule.feature,
            signal=signal,
            n_samples=n_history,
        )
        if history:
            feature_history[resolved_ref] = history

    ctx = RuleContext(
        signal=signal,
        feature_snapshot={resolved_ref: resolver_result.value},
        feature_ref=resolved_ref,
        feature_history=feature_history,
    )
    try:
        outcome, error_info = rule.condition.evaluate(ctx)
    except Exception as exc:
        if rule.on_error == "reject":
            bound_logger.info(
                "scoring_rule_error_reject",
                bot_id=bot_id,
                signal_id=signal_id,
                rule_name=rule.name,
                error_repr=repr(exc),
            )
            partial_rr = RuleResult(
                name=rule.name,
                weight=rule.weight,
                applied_weight=0.0,
                result="error_skipped",
                error={"error": repr(exc)},
            )
            return _build_early_reject(
                decision_reason="rule_error",
                total_score=total_score,
                threshold=threshold,
                rule_results=[*rule_results, partial_rr],
                feature_snapshot=feature_snapshot,
                config_version=config_version,
            )
        _log_failed_open(
            bound_logger,
            bot_id=bot_id,
            signal_id=signal_id,
            rule_name=rule.name,
            reason="condition_exception",
            error_repr=repr(exc),
        )
        return RuleResult(
            name=rule.name,
            weight=rule.weight,
            applied_weight=0.0,
            result="error_skipped",
            error={"error": repr(exc)},
        )

    applied_weight = rule.weight if outcome is True else 0.0
    return RuleResult(
        name=rule.name,
        weight=rule.weight,
        applied_weight=applied_weight,
        result=str(outcome),
        error=error_info,
    )


def _resolve_feature_ref(rule_feature: str, signal: SignalValidated) -> str:
    """Re-run T-306 substitute_template for snapshot key (avoids extra resolver round-trip)."""
    from .resolver import substitute_template

    return substitute_template(rule_feature, signal)
