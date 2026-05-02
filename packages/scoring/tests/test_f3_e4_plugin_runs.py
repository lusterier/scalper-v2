"""F3 E4 exit-criterion tests (T-313).

§19:2550 verbatim: "Plugin rule example (oi_squeeze) runs and contributes to score."

Split per BLOCKER#1: T-307 evaluate(...) builds RuleContext with empty
feature_history (T-306 deferred to F4+); OISqueezeRule on empty history
returns False with reason=history_insufficient → applied_weight=0.0 →
plugin runs through pipeline but does NOT contribute to score in v1.

Test (a) `test_oi_squeeze_runs_in_evaluator_pipeline` — full E2E via evaluate(...);
asserts plugin runs (RuleResult present in result.rule_results) with T-306-
limitation acknowledgment.

Test (b) `test_oi_squeeze_contributes_to_score_when_history_sufficient` —
bypasses orchestrator; directly invokes Rule.evaluate(populated_ctx) per
WG#3 / Path A access via condition.rule attribute (PluginCondition wraps
the underlying Rule). Mirror T-312 test #7 Fixture A pattern verbatim.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from packages.bus.schemas.signals import SignalValidated
from packages.features.types import FeatureValue
from packages.scoring import evaluate, load_bot_config
from packages.scoring.protocol import RuleContext
from packages.scoring.registry import load_plugin_registry
from packages.scoring.resolver import ResolverResult

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
_FEATURE_REF = "ind.btcusdt.15m.oi_change"


def _signal() -> SignalValidated:
    return SignalValidated(
        source="webhook",
        idempotency_key="key-1",
        received_at=_FIXED_NOW,
        symbol="BTCUSDT",
        original_symbol="BTCUSDT",
        action="LONG",
        expires_at=_FIXED_NOW + timedelta(seconds=60),
        payload={},
    )


async def test_oi_squeeze_runs_in_evaluator_pipeline() -> None:
    """E4 (a) — plugin runs through full evaluate() pipeline; T-306 limitation acknowledged.

    T-306 limitation: feature_history empty → OISqueezeRule returns False → applied_weight=0.0;
    T-313 §0.8 anti-hypothetical acknowledges this. F4+ T-306 upgrade may flip this.
    """
    plugin_registry = load_plugin_registry(_REPO_ROOT / "configs" / "plugin_registry.yaml")
    beta = load_bot_config(
        _REPO_ROOT / "configs" / "bots" / "beta.yaml",
        plugin_registry=plugin_registry,
    )

    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=ResolverResult(
            value=FeatureValue(value_num=Decimal("100")),
            status="ok",
        ),
    )

    result = await evaluate(
        bot_config=beta,
        signal=_signal(),
        resolver=resolver,
        bound_logger=MagicMock(),
    )

    # Plugin ran — RuleResult present in audit trail.
    assert len(result.rule_results) == 1
    rr = result.rule_results[0]
    assert rr.name == "oi_squeeze_v2"
    # T-306 limitation: feature_history empty → OISqueezeRule returns False → applied_weight=0.0.
    assert rr.applied_weight == 0.0
    assert rr.result == "False"
    assert rr.error is not None
    assert rr.error["reason"] == "history_insufficient"


def test_oi_squeeze_contributes_to_score_when_history_sufficient() -> None:
    """E4 (b) — bypasses orchestrator; condition layer pins "contributes to score" semantics.

    Path A access per WG#3: PluginCondition wraps Rule; Rule.evaluate(ctx) returns
    RuleOutcome with .result/.metadata. Mirror T-312 test #7 Fixture A.
    """
    plugin_registry = load_plugin_registry(_REPO_ROOT / "configs" / "plugin_registry.yaml")
    beta = load_bot_config(
        _REPO_ROOT / "configs" / "bots" / "beta.yaml",
        plugin_registry=plugin_registry,
    )

    # PluginCondition wraps Rule; access the underlying Rule for direct .evaluate.
    plugin_condition = beta.scoring.rules[0].condition
    rule = plugin_condition.rule  # OISqueezeRule instance

    history = [FeatureValue(value_num=Decimal(v)) for v in ("100", "99.5", "99", "98.5", "98")]
    populated_ctx = RuleContext(
        signal=_signal(),
        feature_snapshot={},
        feature_ref=_FEATURE_REF,
        feature_history={_FEATURE_REF: history},
    )

    outcome = rule.evaluate(populated_ctx)
    assert outcome.result is True
    assert outcome.metadata is not None
    assert outcome.metadata["drop_pct"] >= Decimal("1.0")
    assert outcome.metadata["oldest_oi"] == Decimal("100")
    assert outcome.metadata["newest_oi"] == Decimal("98")
