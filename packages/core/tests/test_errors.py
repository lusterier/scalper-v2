"""Test packages.core.errors — root ScalperError shape."""

from __future__ import annotations

from packages.core.errors import ScalperError


def test_scalper_error_is_subclassable_exception_with_message() -> None:
    class BusError(ScalperError):
        pass

    err = BusError("not connected")
    assert isinstance(err, ScalperError)
    assert isinstance(err, Exception)
    assert str(err) == "not connected"
