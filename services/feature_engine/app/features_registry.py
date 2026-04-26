"""YAML-driven feature registry (T-111).

Replaces T-110c's hardcoded ``build_features()`` demo with a thin
delegate to :func:`yaml_loader.load_indicators_yaml`. Composition root
(T-110d ``main.py``) calls ``build_features(symbols)`` at lifespan
start; ``symbols`` derives from ``settings.symbols`` parsed from the
``FEATURE_ENGINE_SYMBOLS`` env-stopgap (mirror of ``MARKET_DATA_SYMBOLS``).

Path resolution: this module lives at
``services/feature_engine/app/features_registry.py`` →
``parents[3]`` is the repo root → ``configs/features/indicators.yaml``.

T-111 is BUILTINS-only YAML registration. Plugin discovery
(§9.3 line 1486 ``plugin_registry.yaml``) is F1+ deferred.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .yaml_loader import load_indicators_yaml

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from packages.features.protocols import Feature


__all__ = ["INDICATORS_YAML_PATH", "build_features"]


# Repo-root-relative resolution: services/feature_engine/app/features_registry.py
# parents[3] = repo root → configs/features/indicators.yaml.
INDICATORS_YAML_PATH = (
    Path(__file__).resolve().parents[3] / "configs" / "features" / "indicators.yaml"
)


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
