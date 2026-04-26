"""YAML-driven feature registry (T-111, refactored T-112).

T-111 shipped a thin delegate over a service-local
``yaml_loader.load_indicators_yaml``. T-112 extracted that loader to
:mod:`packages.features.yaml` as a public port; this module now imports
``INDICATORS_YAML_PATH`` + ``load_indicators_yaml`` from there directly.

The ``load_indicators_yaml`` symbol stays a module-level attribute via
:keyword:`from` import so existing
:func:`monkeypatch.setattr("services.feature_engine.app.features_registry.load_indicators_yaml", …)`
test patches keep working unchanged (Python re-export semantics — T-112
plan CONCERN #2 fix).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from packages.features.yaml import INDICATORS_YAML_PATH, load_indicators_yaml

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from packages.features.protocols import Feature


__all__ = ["INDICATORS_YAML_PATH", "build_features"]


def build_features(
    symbols: Sequence[str],
) -> Mapping[tuple[str, str], list[tuple[str, Feature]]]:
    """Load + cross-product ``indicators.yaml`` against ``symbols``.

    Empty ``symbols`` → empty mapping (no-op composition per T-110d
    Decision #5). T-111 plan §"Decisions committed" #11: the
    ``symbols: Sequence[str]`` parameter replaces T-110c's no-arg
    signature. T-110d ``main.py`` callsite passes ``settings.symbols``.
    """
    return load_indicators_yaml(INDICATORS_YAML_PATH, symbols)
