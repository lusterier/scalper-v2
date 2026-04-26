"""Tests for :mod:`services.feature_engine.app.features_registry` (T-110c).

Pins decision #16 (EMA-20 BTCUSDT 1m demo) and decision #25/#26
(template substitution at registration time + lowercase symbol
convention). T-111 will replace `build_features` with a YAML loader
behind the same API; these tests carry the substitution invariant
forward.
"""

from __future__ import annotations

from packages.features.builtins.ema import EmaFeature
from services.feature_engine.app.features_registry import build_features


def test_build_features_returns_ema_20_btcusdt_1m() -> None:
    """Registry holds (BTCUSDT, 1m) → [(ind.btcusdt.1m.ema_20, EmaFeature(20))].

    The pre-substituted feature_name verifies decision #25 (substitution
    at registration time) + decision #26 (lowercase symbol per §1.7
    line 244 / §7.2 line 904 / §8.4 line 1382 example literals).
    """
    registry = build_features()
    bucket = registry.get(("BTCUSDT", "1m"))
    assert bucket is not None
    assert len(bucket) == 1
    feature_name, feature = bucket[0]
    assert feature_name == "ind.btcusdt.1m.ema_20"
    assert isinstance(feature, EmaFeature)
    assert feature.period == 20
    assert feature.interval == "1m"


def test_build_features_only_one_key() -> None:
    """Demo registry registers exactly one (symbol, interval) key."""
    registry = build_features()
    assert list(registry) == [("BTCUSDT", "1m")]
