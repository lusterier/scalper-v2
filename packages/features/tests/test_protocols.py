"""Structural conformance tests for :class:`Feature`."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.features import Feature, FeatureValue, OhlcCandle

if TYPE_CHECKING:
    from collections.abc import Sequence


class _AlwaysOneFeature:
    """Toy implementation — proves a class with the right shape satisfies the Protocol."""

    name_template = "ind.{symbol}.{interval}.always_one"
    source_version = "test.always_one.v1"
    interval = "15m"
    warmup_candles = 1

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue:
        del candles
        return FeatureValue(value_num=Decimal("1"))


def test_toy_feature_satisfies_protocol() -> None:
    """mypy-time guard: assigning a concrete impl to a ``Feature`` annotation typechecks."""
    feature: Feature = _AlwaysOneFeature()
    assert feature is not None


def test_compute_returns_feature_value() -> None:
    feature: Feature = _AlwaysOneFeature()
    result = feature.compute([])
    assert result == FeatureValue(value_num=Decimal("1"))


def test_attributes_match_declared_literals() -> None:
    feature = _AlwaysOneFeature()
    assert feature.name_template == "ind.{symbol}.{interval}.always_one"
    assert feature.source_version == "test.always_one.v1"
    assert feature.interval == "15m"
    assert feature.warmup_candles == 1
