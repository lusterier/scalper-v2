"""Error hierarchy tests for :mod:`packages.bus.errors`.

The inheritance chain (``PublishError`` / ``SubscribeError`` /
``NotConnectedError`` → ``BusError`` → ``ScalperError``) is
load-bearing: services broaden to ``except BusError`` or
``except ScalperError`` without importing ``nats``. Every link is
tested explicitly.
"""

from __future__ import annotations

import pytest

from packages.bus import (
    BusError,
    NotConnectedError,
    PublishError,
    SubscribeError,
)
from packages.core import ScalperError


def test_bus_error_inherits_scalper_error() -> None:
    assert issubclass(BusError, ScalperError)


def test_not_connected_error_inherits_bus_error() -> None:
    assert issubclass(NotConnectedError, BusError)


def test_publish_error_inherits_bus_error() -> None:
    assert issubclass(PublishError, BusError)


def test_subscribe_error_inherits_bus_error() -> None:
    assert issubclass(SubscribeError, BusError)


def test_not_connected_error_catches_as_bus_error() -> None:
    with pytest.raises(BusError):
        raise NotConnectedError("no active connection")


def test_not_connected_error_catches_as_scalper_error() -> None:
    """Callers that only know `ScalperError` must still catch bus failures."""
    with pytest.raises(ScalperError):
        raise NotConnectedError("no active connection")


def test_publish_error_catches_as_bus_error() -> None:
    with pytest.raises(BusError):
        raise PublishError("js publish failed")


def test_publish_error_catches_as_scalper_error() -> None:
    with pytest.raises(ScalperError):
        raise PublishError("js publish failed")


def test_subscribe_error_catches_as_bus_error() -> None:
    with pytest.raises(BusError):
        raise SubscribeError("core subscribe failed")


def test_subscribe_error_catches_as_scalper_error() -> None:
    with pytest.raises(ScalperError):
        raise SubscribeError("core subscribe failed")


def test_publish_error_preserves_cause() -> None:
    """Wrapped ``nats`` exceptions must remain reachable via ``__cause__``."""
    original = RuntimeError("stream not found")

    def _wrap() -> None:
        try:
            raise original
        except RuntimeError as exc:
            raise PublishError("publish failed") from exc

    with pytest.raises(PublishError) as excinfo:
        _wrap()
    assert excinfo.value.__cause__ is original


def test_subscribe_error_preserves_cause() -> None:
    original = RuntimeError("no responders")

    def _wrap() -> None:
        try:
            raise original
        except RuntimeError as exc:
            raise SubscribeError("subscribe failed") from exc

    with pytest.raises(SubscribeError) as excinfo:
        _wrap()
    assert excinfo.value.__cause__ is original
