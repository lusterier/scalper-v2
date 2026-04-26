"""Invariants for :class:`FeaturesError` and :class:`FeatureUnderflowError`.

The exception module roots :mod:`packages.features` errors in the
project hierarchy (:class:`packages.core.ScalperError`) while keeping
``FeatureUnderflowError`` catchable via the Pythonic ``ValueError``
surface — see ``packages/features/errors.py`` for the contract.
"""

from __future__ import annotations

from packages.core import ScalperError
from packages.features import FeaturesError, FeatureUnderflowError


class TestFeaturesError:
    def test_inherits_from_scalper_error(self) -> None:
        assert issubclass(FeaturesError, ScalperError)

    def test_constructs_with_no_args(self) -> None:
        err = FeaturesError()
        assert isinstance(err, FeaturesError)


class TestFeatureUnderflowError:
    def test_is_features_error(self) -> None:
        """Primary contract: caught by ``except FeaturesError``."""
        assert issubclass(FeatureUnderflowError, FeaturesError)

    def test_is_value_error(self) -> None:
        """Convenience surface: also caught by ``except ValueError``."""
        assert issubclass(FeatureUnderflowError, ValueError)

    def test_is_scalper_error(self) -> None:
        """Transitive: caught by the project root ``except ScalperError``."""
        assert issubclass(FeatureUnderflowError, ScalperError)

    def test_str_carries_message(self) -> None:
        err = FeatureUnderflowError("EMA(period=14) requires >= 14 candles, got 5")
        assert "got 5" in str(err)

    def test_constructs_with_no_args(self) -> None:
        err = FeatureUnderflowError()
        assert isinstance(err, FeatureUnderflowError)
