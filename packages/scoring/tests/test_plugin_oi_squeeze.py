"""§N5 unit tests for `plugins.rules.oi_squeeze` (T-312).

12 tests cover BRIEF §10.6 reference plugin: __init__ validation, evaluate
happy path + 5 defensive edges, lookback window slicing, ClassVar verbatim
pin per §10.6:1801-1803.

Tests live under `packages/scoring/tests/` (per testpaths convention) since
`plugins/` is not in `pyproject.toml:113` testpaths nor coverage source.
Convention for future plugins: `packages/scoring/tests/test_plugin_<name>.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from packages.bus.schemas.signals import SignalValidated
from packages.features.types import FeatureValue
from packages.scoring.protocol import RuleContext
from plugins.rules.oi_squeeze import OISqueezeRule

if TYPE_CHECKING:
    from collections.abc import Sequence


_FEATURE_REF = "ind.btcusdt.15m.oi_change"


def _signal() -> SignalValidated:
    now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
    return SignalValidated(
        source="webhook",
        idempotency_key="key-1",
        received_at=now,
        symbol="BTCUSDT",
        original_symbol="BTCUSDT",
        action="LONG",
        expires_at=now + timedelta(seconds=60),
        payload={},
    )


def _ctx(history: Sequence[FeatureValue]) -> RuleContext:
    return RuleContext(
        signal=_signal(),
        feature_snapshot={},
        feature_ref=_FEATURE_REF,
        feature_history={_FEATURE_REF: history},
    )


def _hist(*nums: str) -> list[FeatureValue]:
    return [FeatureValue(value_num=Decimal(n)) for n in nums]


# region: __init__ validation -----------------------------------------------


def test_init_stores_params() -> None:
    rule = OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": "1.0"})
    assert rule.lookback_candles == 5
    assert rule.oi_drop_pct == Decimal("1.0")


@pytest.mark.parametrize("bad", [0, -1, 0.5, "5", True, False])
def test_init_rejects_bad_lookback(bad: Any) -> None:
    """Reject non-positive int, str, bool. WG#4: bool BEFORE int check."""
    with pytest.raises(ValueError, match="lookback_candles"):
        OISqueezeRule({"lookback_candles": bad, "oi_drop_pct": "1.0"})


def test_init_rejects_negative_oi_drop_pct() -> None:
    with pytest.raises(ValueError, match="oi_drop_pct"):
        OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": "-1.0"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.0", Decimal("1.0")),
        (1, Decimal("1")),
        (1.5, Decimal("1.5")),
        ("0.5", Decimal("0.5")),
    ],
)
def test_init_coerces_oi_drop_pct(raw: Any, expected: Decimal) -> None:
    rule = OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": raw})
    assert rule.oi_drop_pct == expected


# region: evaluate ----------------------------------------------------------


def test_evaluate_returns_false_when_history_empty() -> None:
    rule = OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": "1.0"})
    outcome = rule.evaluate(_ctx([]))
    assert outcome.result is False
    assert outcome.metadata is not None
    assert outcome.metadata["reason"] == "history_insufficient"
    assert outcome.metadata["have"] == 0


def test_evaluate_returns_false_when_history_below_lookback() -> None:
    rule = OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": "1.0"})
    outcome = rule.evaluate(_ctx(_hist("100", "99")))
    assert outcome.result is False
    assert outcome.metadata is not None
    assert outcome.metadata["reason"] == "history_insufficient"
    assert outcome.metadata["have"] == 2


def test_evaluate_returns_true_on_drop_above_threshold() -> None:
    """Fixture A: history [100,99.5,99,98.5,98], drop=2.0 >= 1.0 → True."""
    rule = OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": "1.0"})
    outcome = rule.evaluate(_ctx(_hist("100", "99.5", "99", "98.5", "98")))
    assert outcome.result is True
    assert outcome.metadata is not None
    assert outcome.metadata["drop_pct"] == Decimal("2.00")
    assert outcome.metadata["oldest_oi"] == Decimal("100")
    assert outcome.metadata["newest_oi"] == Decimal("98")


def test_evaluate_returns_true_on_drop_equal_to_threshold() -> None:
    """Fixture B: drop_pct == oi_drop_pct boundary, >= semantics."""
    rule = OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": "1.0"})
    outcome = rule.evaluate(_ctx(_hist("100", "99.75", "99.5", "99.25", "99")))
    assert outcome.result is True
    assert outcome.metadata is not None
    assert outcome.metadata["drop_pct"] == Decimal("1.00")


def test_evaluate_returns_false_on_drop_below_threshold() -> None:
    """Fixture C: drop=0.5 < 1.0 → False with reason."""
    rule = OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": "1.0"})
    outcome = rule.evaluate(_ctx(_hist("100", "99.9", "99.8", "99.7", "99.5")))
    assert outcome.result is False
    assert outcome.metadata is not None
    assert outcome.metadata["reason"] == "drop_below_threshold"
    assert outcome.metadata["drop_pct"] == Decimal("0.500")
    assert outcome.metadata["threshold"] == Decimal("1.0")


def test_evaluate_returns_false_on_zero_oldest() -> None:
    """Fixture E: oldest=0 → division-by-zero defensive return."""
    rule = OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": "1.0"})
    outcome = rule.evaluate(_ctx(_hist("0", "1", "2", "3", "4")))
    assert outcome.result is False
    assert outcome.metadata is not None
    assert outcome.metadata["reason"] == "oldest_oi_zero"


def test_evaluate_returns_false_on_non_numeric_feature() -> None:
    """value_bool variant in history → False with reason."""
    rule = OISqueezeRule({"lookback_candles": 2, "oi_drop_pct": "1.0"})
    history: list[FeatureValue] = [
        FeatureValue(value_bool=True),
        FeatureValue(value_num=Decimal("99")),
    ]
    outcome = rule.evaluate(_ctx(history))
    assert outcome.result is False
    assert outcome.metadata is not None
    assert outcome.metadata["reason"] == "feature_not_numeric"


def test_evaluate_uses_lookback_window_from_history_tail() -> None:
    """WG#3 — newest-window slice; oldest-window would yield False, newest yields True.

    History: [100,100,100,100,100,100,99,99,99,98] (10 entries). lookback=5.
    Newest 5 = [100,99,99,99,98] → drop=(100-98)/100*100=2.0 ≥ 1.0 → True.
    Oldest 5 = [100,100,100,100,100] → drop=0 → False.
    Test asserts True; off-by-one slice (e.g. history[-N-1:-1]) would assert False.
    """
    rule = OISqueezeRule({"lookback_candles": 5, "oi_drop_pct": "1.0"})
    outcome = rule.evaluate(
        _ctx(_hist("100", "100", "100", "100", "100", "100", "99", "99", "99", "98")),
    )
    assert outcome.result is True
    assert outcome.metadata is not None
    assert outcome.metadata["oldest_oi"] == Decimal("100")
    assert outcome.metadata["newest_oi"] == Decimal("98")


# region: ClassVar verbatim per BRIEF §10.6:1801-1803 -----------------------


def test_class_vars_match_brief_spec() -> None:
    """Verbatim spec pin (L-002): name='oi_squeeze', version='2' as strings."""
    assert OISqueezeRule.name == "oi_squeeze"
    assert OISqueezeRule.version == "2"
    assert isinstance(OISqueezeRule.name, str)
    assert isinstance(OISqueezeRule.version, str)
