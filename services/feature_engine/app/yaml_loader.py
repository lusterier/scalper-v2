"""T-111 yaml_loader — refactored to thin re-export of :mod:`packages.features.yaml` (T-112).

Preserves the original import path for T-111-internal callsites
(:mod:`features_registry` imports ``load_indicators_yaml`` from here)
while moving the implementation to :mod:`packages.features` as a
public port reusable across services and offline tools (T-112 backfill
CLI).
"""

from packages.features.yaml import INDICATORS_YAML_PATH, load_indicators_yaml

__all__ = ["INDICATORS_YAML_PATH", "load_indicators_yaml"]
