"""Unit tests for :func:`services.feature_engine.app.yaml_loader.load_indicators_yaml` (T-111).

Pure unit tests using ``tmp_path`` for YAML files; no real
``configs/features/indicators.yaml`` access. Maps onto plan §"Hand
verification" trace rows 1-13 (cross-product, lowercase substitution,
drift checks, edge cases).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from packages.features.builtins import (
    AtrFeature,
    BollingerFeature,
    EmaFeature,
    MacdFeature,
    RsiFeature,
    VwapFeature,
)
from services.feature_engine.app.yaml_loader import load_indicators_yaml

if TYPE_CHECKING:
    from pathlib import Path


_FULL_B2_YAML = """\
features:
  - name_template: ind.{symbol}.15m.ema_20
    type: builtin.ema
    interval: 15m
    params: { period: 20 }
    source_version: builtin.ema.v1

  - name_template: ind.{symbol}.15m.ema_50
    type: builtin.ema
    interval: 15m
    params: { period: 50 }
    source_version: builtin.ema.v1

  - name_template: ind.{symbol}.15m.ema_200
    type: builtin.ema
    interval: 15m
    params: { period: 200 }
    source_version: builtin.ema.v1

  - name_template: ind.{symbol}.15m.rsi_14
    type: builtin.rsi
    interval: 15m
    params: { period: 14 }
    source_version: builtin.rsi.v1

  - name_template: ind.{symbol}.15m.atr_14
    type: builtin.atr
    interval: 15m
    params: { period: 14 }
    source_version: builtin.atr.v1

  - name_template: ind.{symbol}.1m.vwap_session
    type: builtin.vwap
    interval: 1m
    params: { session: daily }
    source_version: builtin.vwap.v1
"""


def test_load_yaml_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """No file at path → empty mapping (no-op composition)."""
    missing = tmp_path / "does_not_exist.yaml"
    assert load_indicators_yaml(missing, ["BTCUSDT"]) == {}


def test_load_yaml_returns_empty_when_features_key_absent(tmp_path: Path) -> None:
    """YAML without top-level ``features:`` key → empty."""
    path = tmp_path / "no_features.yaml"
    path.write_text("other_key: 42\n")
    assert load_indicators_yaml(path, ["BTCUSDT"]) == {}


def test_load_yaml_returns_empty_when_symbols_empty(tmp_path: Path) -> None:
    """Populated YAML + empty symbols → empty mapping."""
    path = tmp_path / "indicators.yaml"
    path.write_text(_FULL_B2_YAML)
    assert load_indicators_yaml(path, []) == {}


def test_load_yaml_returns_empty_when_file_is_empty_string(tmp_path: Path) -> None:
    """yaml.safe_load("") returns None; ``if not raw`` guard kicks in (Write-time guidance #4)."""
    path = tmp_path / "empty.yaml"
    path.write_text("")
    assert load_indicators_yaml(path, ["BTCUSDT"]) == {}


def test_load_yaml_cross_products_symbols_and_indicators(tmp_path: Path) -> None:
    """§B.2 6 indicators x 2 symbols = 12 tuples grouped by (symbol, interval)."""
    path = tmp_path / "indicators.yaml"
    path.write_text(_FULL_B2_YAML)
    result = load_indicators_yaml(path, ["BTCUSDT", "ETHUSDT"])
    # Expected key shape: 5 entries on (X, 15m), 1 entry on (X, 1m), per symbol.
    assert ("BTCUSDT", "15m") in result
    assert ("ETHUSDT", "15m") in result
    assert ("BTCUSDT", "1m") in result
    assert ("ETHUSDT", "1m") in result
    assert len(result[("BTCUSDT", "15m")]) == 5  # EMA-20 + EMA-50 + EMA-200 + RSI-14 + ATR-14
    assert len(result[("ETHUSDT", "15m")]) == 5
    assert len(result[("BTCUSDT", "1m")]) == 1  # VWAP
    assert len(result[("ETHUSDT", "1m")]) == 1
    total = sum(len(v) for v in result.values())
    assert total == 12


def test_load_yaml_substitutes_lowercase_symbol_in_name(tmp_path: Path) -> None:
    """Substituted feature_name uses lowercase symbol per §1.7/§7.2/§8.4 examples."""
    path = tmp_path / "indicators.yaml"
    path.write_text(_FULL_B2_YAML)
    result = load_indicators_yaml(path, ["BTCUSDT"])
    feature_names = {fname for entries in result.values() for fname, _ in entries}
    assert "ind.btcusdt.15m.ema_20" in feature_names  # lowercase btcusdt
    assert "ind.btcusdt.1m.vwap_session" in feature_names


def test_load_yaml_routing_key_is_canonical_case(tmp_path: Path) -> None:
    """Routing key tuple stays canonical Bybit-shape (BTCUSDT uppercase)."""
    path = tmp_path / "indicators.yaml"
    path.write_text(_FULL_B2_YAML)
    result = load_indicators_yaml(path, ["BTCUSDT"])
    assert all(symbol == "BTCUSDT" for symbol, _ in result)


def test_load_yaml_unknown_type_raises_KeyError(tmp_path: Path) -> None:
    """Unknown ``type:`` value (typo) raises KeyError before constructor."""
    path = tmp_path / "bad.yaml"
    path.write_text(
        "features:\n"
        "  - name_template: ind.{symbol}.15m.emm_20\n"
        "    type: builtin.emm\n"
        "    interval: 15m\n"
        "    params: { period: 20 }\n"
        "    source_version: builtin.emm.v1\n"
    )
    with pytest.raises(KeyError, match=r"builtin\.emm"):
        load_indicators_yaml(path, ["BTCUSDT"])


def test_load_yaml_source_version_mismatch_raises_ValueError(tmp_path: Path) -> None:
    """YAML source_version drifted from Feature instance → ValueError citing both values."""
    path = tmp_path / "drift.yaml"
    path.write_text(
        "features:\n"
        "  - name_template: ind.{symbol}.15m.ema_20\n"
        "    type: builtin.ema\n"
        "    interval: 15m\n"
        "    params: { period: 20 }\n"
        "    source_version: builtin.ema.v2\n"  # YAML says v2, Feature is v1
    )
    with pytest.raises(ValueError, match=r"builtin\.ema\.v2.*builtin\.ema\.v1"):
        load_indicators_yaml(path, ["BTCUSDT"])


def test_load_yaml_name_template_mismatch_raises_ValueError(tmp_path: Path) -> None:
    """YAML name_template drifted from Feature instance → ValueError citing both values."""
    path = tmp_path / "drift.yaml"
    path.write_text(
        "features:\n"
        "  - name_template: ind.{symbol}.15m.ema_xxx\n"  # spec drift
        "    type: builtin.ema\n"
        "    interval: 15m\n"
        "    params: { period: 20 }\n"
        "    source_version: builtin.ema.v1\n"
    )
    with pytest.raises(ValueError, match=r"ema_xxx.*ema_20"):
        load_indicators_yaml(path, ["BTCUSDT"])


def test_load_yaml_all_six_builtins_instantiate(tmp_path: Path) -> None:
    """Full §B.2 YAML + 1 symbol = 6 tuples; verify Feature class types match `_TYPE_TO_FEATURE`."""
    path = tmp_path / "indicators.yaml"
    path.write_text(_FULL_B2_YAML)
    result = load_indicators_yaml(path, ["BTCUSDT"])
    classes = {type(feature) for entries in result.values() for _, feature in entries}
    assert classes == {EmaFeature, RsiFeature, AtrFeature, VwapFeature}
    # Note: BollingerFeature + MacdFeature are NOT in §B.2 (ship-time scope);
    # this test asserts what `_FULL_B2_YAML` actually instantiates.
    assert BollingerFeature not in classes
    assert MacdFeature not in classes
