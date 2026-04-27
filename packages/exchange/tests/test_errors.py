"""§11.3 error taxonomy invariants for :mod:`packages.exchange.errors` (T-201).

Verifies inheritance hierarchy, named-attribute access on the two
parameterized errors (:class:`OrderRejected.reason`,
:class:`UnknownState.last_known_action`), and the public surface
exported via ``__all__``.
"""

from __future__ import annotations

import pytest

import packages.exchange as ex_mod
from packages.core import ScalperError
from packages.exchange import (
    AuthError,
    ExchangeError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
    UnknownState,
)


def test_exchange_error_inherits_scalper_error() -> None:
    assert issubclass(ExchangeError, ScalperError)


def test_subclasses_inherit_exchange_error() -> None:
    for cls in (RateLimitError, AuthError, OrderRejected, NetworkTimeout, UnknownState):
        assert issubclass(cls, ExchangeError)
        assert issubclass(cls, ScalperError)


def test_order_rejected_carries_reason_attribute() -> None:
    e = OrderRejected("insufficient_margin")
    assert e.reason == "insufficient_margin"
    assert "insufficient_margin" in str(e)


def test_order_rejected_reason_passes_through_str_repr() -> None:
    e = OrderRejected("price_deviation")
    assert "price_deviation" in repr(e) or "price_deviation" in str(e)


def test_unknown_state_carries_last_known_action_attribute() -> None:
    """H-003: place_market_order timeout — adapter cannot determine state."""
    e = UnknownState("place_market_order_timeout")
    assert e.last_known_action == "place_market_order_timeout"
    assert "place_market_order_timeout" in str(e)


def test_rate_limit_auth_network_timeout_take_no_named_args() -> None:
    """The base ExchangeError shape — no field, just a message."""
    for cls in (RateLimitError, AuthError, NetworkTimeout):
        e = cls("some message")
        assert "some message" in str(e)


def test_public_all_exports_six_classes() -> None:
    expected = {
        "AuthError",
        "ExchangeError",
        "NetworkTimeout",
        "OrderRejected",
        "RateLimitError",
        "UnknownState",
    }
    assert expected.issubset(set(ex_mod.__all__))
    for name in expected:
        assert hasattr(ex_mod, name)


def test_order_rejected_can_be_caught_as_exchange_error() -> None:
    with pytest.raises(ExchangeError) as info:
        raise OrderRejected("insufficient_margin")
    assert isinstance(info.value, OrderRejected)
    assert info.value.reason == "insufficient_margin"


def test_unknown_state_can_be_caught_as_exchange_error() -> None:
    with pytest.raises(ExchangeError) as info:
        raise UnknownState("place_market_order_timeout")
    assert isinstance(info.value, UnknownState)
    assert info.value.last_known_action == "place_market_order_timeout"
