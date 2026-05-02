"""§N5 unit tests for :mod:`packages.scoring.conditions.series` (T-303).

TDD discipline (§N4 spirit per WG#3 T-200 precedent): tests written
before implementation. Three series condition variants:
``RisingCondition`` / ``FallingCondition`` / ``EmaStackCondition`` per
BRIEF §10.2:1700-1702.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.bus.schemas.signals import SignalValidated
from packages.features.types import FeatureValue
from packages.scoring.conditions import (
    Condition,
    EmaStackCondition,
    FallingCondition,
    RisingCondition,
    RuleContext,
)


def _signal() -> SignalValidated:
    return SignalValidated(
        source="webhook",
        idempotency_key="test-key-1",
        received_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
        symbol="BTCUSDT",
        original_symbol="BTCUSDT",
        action="LONG",
        expires_at=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=60),
        payload={},
    )


def _fv(n: str | int | float) -> FeatureValue:
    return FeatureValue(value_num=Decimal(str(n)))


def _ctx_history(history: list[FeatureValue], ref: str = "f1") -> RuleContext:
    return RuleContext(
        signal=_signal(),
        feature_snapshot={},
        feature_ref=ref,
        feature_history={ref: history},
    )


def _ctx_snapshot(snapshot: dict[str, FeatureValue]) -> RuleContext:
    return RuleContext(
        signal=_signal(),
        feature_snapshot=snapshot,
        feature_ref="unused",
    )


# region: rising -------------------------------------------------------------


def test_rising_3_sample_strict_monotone_success() -> None:
    c = RisingCondition(n_samples=3)
    ctx = _ctx_history([_fv(1), _fv(2), _fv(3)])
    assert c.evaluate(ctx) == (True, None)


def test_rising_2_sample_minimum() -> None:
    c = RisingCondition(n_samples=2)
    assert c.evaluate(_ctx_history([_fv(1), _fv(2)])) == (True, None)


def test_rising_takes_last_n_window_when_history_longer() -> None:
    """6-sample history with n_samples=3 → uses last 3 = [4,5,6]."""
    c = RisingCondition(n_samples=3)
    ctx = _ctx_history([_fv(1), _fv(3), _fv(2), _fv(4), _fv(5), _fv(6)])
    assert c.evaluate(ctx) == (True, None)


def test_rising_equal_adjacent_pair_fails_strict() -> None:
    """Strict <: [1, 2, 2] fails (last pair equal); returns (False, None) — not error dict."""
    c = RisingCondition(n_samples=3)
    assert c.evaluate(_ctx_history([_fv(1), _fv(2), _fv(2)])) == (False, None)


def test_rising_descending_input_fails() -> None:
    c = RisingCondition(n_samples=3)
    assert c.evaluate(_ctx_history([_fv(3), _fv(2), _fv(1)])) == (False, None)


def test_rising_history_missing_returns_error() -> None:
    c = RisingCondition(n_samples=3)
    ctx = RuleContext(signal=_signal(), feature_snapshot={}, feature_ref="missing")
    outcome, err = c.evaluate(ctx)
    assert outcome is False
    assert err == {"error": "feature_history_missing", "feature_ref": "missing"}


def test_rising_history_too_short_returns_error() -> None:
    c = RisingCondition(n_samples=3)
    outcome, err = c.evaluate(_ctx_history([_fv(1), _fv(2)]))
    assert outcome is False
    assert err == {"error": "feature_history_too_short", "have": 2, "need": 3}


# region: falling ------------------------------------------------------------


def test_falling_3_sample_strict_monotone_success() -> None:
    c = FallingCondition(n_samples=3)
    assert c.evaluate(_ctx_history([_fv(3), _fv(2), _fv(1)])) == (True, None)


def test_falling_equal_adjacent_pair_fails_strict() -> None:
    c = FallingCondition(n_samples=3)
    assert c.evaluate(_ctx_history([_fv(3), _fv(2), _fv(2)])) == (False, None)


def test_falling_ascending_input_fails() -> None:
    c = FallingCondition(n_samples=3)
    assert c.evaluate(_ctx_history([_fv(1), _fv(2), _fv(3)])) == (False, None)


def test_falling_history_missing_returns_error() -> None:
    c = FallingCondition(n_samples=3)
    ctx = RuleContext(signal=_signal(), feature_snapshot={}, feature_ref="missing")
    outcome, err = c.evaluate(ctx)
    assert outcome is False
    assert err is not None
    assert err["error"] == "feature_history_missing"


def test_falling_history_too_short_returns_error() -> None:
    c = FallingCondition(n_samples=3)
    _, err = c.evaluate(_ctx_history([_fv(3), _fv(2)]))
    assert err == {"error": "feature_history_too_short", "have": 2, "need": 3}


# region: construction validation -------------------------------------------


def test_rising_n_samples_below_2_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        RisingCondition(n_samples=1)


def test_falling_n_samples_below_2_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        FallingCondition(n_samples=0)


def test_ema_stack_features_count_validators() -> None:
    """min_length=3, max_length=3 — exactly 3 features per BRIEF §10.2:1700."""
    with pytest.raises(ValidationError):
        EmaStackCondition(features=[], direction="up")
    with pytest.raises(ValidationError):
        EmaStackCondition(features=["a", "b"], direction="up")
    with pytest.raises(ValidationError):
        EmaStackCondition(features=["a", "b", "c", "d"], direction="up")


# region: ema_stack direction="up" ------------------------------------------


def test_ema_stack_up_ordered_descending_values_success() -> None:
    """direction='up': features[0] > features[1] > features[2]."""
    c = EmaStackCondition(features=["ema20", "ema50", "ema200"], direction="up")
    ctx = _ctx_snapshot({"ema20": _fv(60000), "ema50": _fv(55000), "ema200": _fv(50000)})
    assert c.evaluate(ctx) == (True, None)


def test_ema_stack_up_ascending_input_fails() -> None:
    c = EmaStackCondition(features=["ema20", "ema50", "ema200"], direction="up")
    ctx = _ctx_snapshot({"ema20": _fv(50000), "ema50": _fv(55000), "ema200": _fv(60000)})
    assert c.evaluate(ctx) == (False, None)


def test_ema_stack_up_equal_pair_fails_strict() -> None:
    c = EmaStackCondition(features=["ema20", "ema50", "ema200"], direction="up")
    ctx = _ctx_snapshot({"ema20": _fv(60000), "ema50": _fv(60000), "ema200": _fv(50000)})
    assert c.evaluate(ctx) == (False, None)


def test_ema_stack_missing_feature_reports_failed_ref() -> None:
    """Per WG#2: error dict identifies WHICH of 3 features failed."""
    c = EmaStackCondition(features=["ema20", "ema50", "ema200"], direction="up")
    ctx = _ctx_snapshot({"ema20": _fv(60000), "ema200": _fv(50000)})  # ema50 missing
    outcome, err = c.evaluate(ctx)
    assert outcome is False
    assert err == {"error": "feature_missing", "feature_ref": "ema50"}


def test_ema_stack_type_mismatch_on_value_bool_feature_reports_failed_ref() -> None:
    c = EmaStackCondition(features=["ema20", "ema50", "ema200"], direction="up")
    ctx = _ctx_snapshot(
        {"ema20": _fv(60000), "ema50": FeatureValue(value_bool=True), "ema200": _fv(50000)}
    )
    outcome, err = c.evaluate(ctx)
    assert outcome is False
    assert err == {"error": "type_mismatch", "expected": "value_num", "feature_ref": "ema50"}


# region: ema_stack direction="down" ----------------------------------------


def test_ema_stack_down_ordered_ascending_values_success() -> None:
    """direction='down': features[0] < features[1] < features[2]."""
    c = EmaStackCondition(features=["ema20", "ema50", "ema200"], direction="down")
    ctx = _ctx_snapshot({"ema20": _fv(50000), "ema50": _fv(55000), "ema200": _fv(60000)})
    assert c.evaluate(ctx) == (True, None)


def test_ema_stack_down_descending_input_fails() -> None:
    c = EmaStackCondition(features=["ema20", "ema50", "ema200"], direction="down")
    ctx = _ctx_snapshot({"ema20": _fv(60000), "ema50": _fv(55000), "ema200": _fv(50000)})
    assert c.evaluate(ctx) == (False, None)


def test_ema_stack_down_equal_pair_fails_strict() -> None:
    c = EmaStackCondition(features=["ema20", "ema50", "ema200"], direction="down")
    ctx = _ctx_snapshot({"ema20": _fv(50000), "ema50": _fv(50000), "ema200": _fv(60000)})
    assert c.evaluate(ctx) == (False, None)


# region: discriminator pin -------------------------------------------------


def test_rising_discriminator_value() -> None:
    assert RisingCondition(n_samples=3).type == "rising"


def test_falling_discriminator_value() -> None:
    assert FallingCondition(n_samples=3).type == "falling"


def test_ema_stack_discriminator_value() -> None:
    assert EmaStackCondition(features=["a", "b", "c"], direction="up").type == "ema_stack"


# region: frozen invariant --------------------------------------------------


def test_rising_frozen_rejects_mutation() -> None:
    c = RisingCondition(n_samples=3)
    with pytest.raises(ValidationError):
        c.n_samples = 5


def test_ema_stack_frozen_rejects_mutation() -> None:
    c = EmaStackCondition(features=["a", "b", "c"], direction="up")
    with pytest.raises(ValidationError):
        c.direction = "down"


def test_falling_frozen_rejects_mutation() -> None:
    c = FallingCondition(n_samples=3)
    with pytest.raises(ValidationError):
        c.n_samples = 5


# region: Decimal precision -------------------------------------------------


def test_rising_decimal_precision_exact_no_float_coercion() -> None:
    """Decimal('1.0001') < Decimal('1.0002') exact (no rounding)."""
    c = RisingCondition(n_samples=2)
    assert c.evaluate(_ctx_history([_fv("1.0001"), _fv("1.0002")])) == (True, None)


# region: backward-compat with T-302 ----------------------------------------


def test_t302_simple_condition_works_with_default_feature_history() -> None:
    """feature_history default_factory=dict doesn't break T-302 simple conditions."""
    from packages.scoring.conditions import EqualsCondition

    ctx = RuleContext(
        signal=_signal(),
        feature_snapshot={"f1": _fv(50000)},
        feature_ref="f1",
        # feature_history omitted — default empty mapping
    )
    c = EqualsCondition(value=Decimal("50000"))
    assert c.evaluate(ctx) == (True, None)


# region: Protocol runtime_checkable ----------------------------------------


def test_rising_satisfies_condition_protocol() -> None:
    c: object = RisingCondition(n_samples=3)
    assert isinstance(c, Condition)


def test_falling_satisfies_condition_protocol() -> None:
    c: object = FallingCondition(n_samples=3)
    assert isinstance(c, Condition)


def test_ema_stack_satisfies_condition_protocol() -> None:
    c: object = EmaStackCondition(features=["a", "b", "c"], direction="up")
    assert isinstance(c, Condition)
