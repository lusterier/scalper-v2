"""Error hierarchy tests for :mod:`packages.bus.errors`."""

from __future__ import annotations

import pytest

from packages.bus import BusError, NotConnectedError
from packages.core import ScalperError


def test_bus_error_inherits_scalper_error() -> None:
    assert issubclass(BusError, ScalperError)


def test_not_connected_error_inherits_bus_error() -> None:
    assert issubclass(NotConnectedError, BusError)


def test_not_connected_error_catches_as_bus_error() -> None:
    with pytest.raises(BusError):
        raise NotConnectedError("no active connection")


def test_not_connected_error_catches_as_scalper_error() -> None:
    """Callers that only know `ScalperError` must still catch bus failures."""
    with pytest.raises(ScalperError):
        raise NotConnectedError("no active connection")
