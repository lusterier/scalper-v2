"""Tests for :mod:`services.feature_engine.app.features_registry` (T-111).

Refactored from T-110c hardcoded EMA-20 demo to a thin delegation test.
T-111 ships :func:`build_features` as a wrapper over
:func:`yaml_loader.load_indicators_yaml`; the loader's behaviour is
exhaustively covered by ``test_yaml_loader.py``. This file pins:

* Pass-through: ``build_features(symbols)`` calls
  ``load_indicators_yaml(INDICATORS_YAML_PATH, symbols)`` verbatim.
* Path resolution: ``INDICATORS_YAML_PATH`` resolves to
  ``<repo-root>/configs/features/indicators.yaml`` so the lifespan
  finds the file in production.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from services.feature_engine.app.features_registry import (
    INDICATORS_YAML_PATH,
    build_features,
)

if TYPE_CHECKING:
    import pytest


def test_build_features_delegates_to_yaml_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """``build_features(symbols)`` forwards verbatim to ``load_indicators_yaml``."""
    sentinel: dict[tuple[str, str], list[tuple[str, object]]] = {}
    loader_mock = MagicMock(return_value=sentinel)
    monkeypatch.setattr(
        "services.feature_engine.app.features_registry.load_indicators_yaml",
        loader_mock,
    )
    result = build_features(["BTCUSDT", "ETHUSDT"])
    assert result is sentinel
    loader_mock.assert_called_once_with(INDICATORS_YAML_PATH, ["BTCUSDT", "ETHUSDT"])


def test_indicators_yaml_path_resolves_to_repo_configs_features() -> None:
    """Path is repo-root-relative ``configs/features/indicators.yaml``."""
    assert INDICATORS_YAML_PATH.parts[-3:] == ("configs", "features", "indicators.yaml")
