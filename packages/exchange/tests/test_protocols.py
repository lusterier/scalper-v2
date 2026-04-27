"""Surface invariants for :mod:`packages.exchange.protocols` (T-201).

Verifies §N3 marker placement on every external-write/-read method,
the H-013 ``tpsl_mode`` no-default invariant via :func:`inspect.signature`,
the ``_UNLABELED_METHODS`` exemption set, and that the protocol does
not silently leak unwanted concrete attributes.

Behavioural hazard tests
(``test_place_order_on_timeout_never_retries_and_raises_unknown_state``,
``test_set_trading_stop_requires_explicit_tpsl_mode`` adapter variant)
land in T-207 / T-208 against real adapters.
"""

from __future__ import annotations

import inspect

from packages.core import is_idempotent, is_non_idempotent
from packages.exchange import ExchangeClient
from packages.exchange.protocols import _UNLABELED_METHODS

# --- §N3 marker placement ---------------------------------------------------


def test_set_leverage_is_idempotent() -> None:
    assert is_idempotent(ExchangeClient.set_leverage)


def test_place_market_order_is_non_idempotent() -> None:
    """H-003: market orders never retry; timeout raises UnknownState."""
    assert is_non_idempotent(ExchangeClient.place_market_order)


def test_set_trading_stop_is_idempotent() -> None:
    assert is_idempotent(ExchangeClient.set_trading_stop)


def test_cancel_order_is_idempotent() -> None:
    assert is_idempotent(ExchangeClient.cancel_order)


def test_get_positions_is_idempotent() -> None:
    assert is_idempotent(ExchangeClient.get_positions)


def test_get_fill_price_is_idempotent() -> None:
    assert is_idempotent(ExchangeClient.get_fill_price)


def test_get_closed_pnl_cumulative_is_idempotent() -> None:
    assert is_idempotent(ExchangeClient.get_closed_pnl_cumulative)


def test_streaming_and_close_methods_are_unlabeled() -> None:
    """Streams + close() are not external writes; markers don't apply (§5.8)."""
    for name in ("stream_executions", "stream_positions", "close"):
        m = getattr(ExchangeClient, name)
        assert not is_idempotent(m)
        assert not is_non_idempotent(m)


# --- _UNLABELED_METHODS exemption set ---------------------------------------


def test_unlabeled_methods_frozenset_contents() -> None:
    """Locked per Write-time guidance #1."""
    assert frozenset({"stream_executions", "stream_positions", "close"}) == _UNLABELED_METHODS


def test_unlabeled_methods_is_frozenset_type() -> None:
    assert isinstance(_UNLABELED_METHODS, frozenset)


def test_every_non_exempt_protocol_method_carries_a_marker() -> None:
    """Conformance contract that T-206 will replicate for every adapter."""
    for name, member in inspect.getmembers(ExchangeClient):
        if name.startswith("_"):
            continue
        if name in _UNLABELED_METHODS:
            continue
        if not callable(member):
            continue
        assert is_idempotent(member) or is_non_idempotent(member), (
            f"{name} on ExchangeClient missing @idempotent / @non_idempotent"
        )


def test_unlabeled_methods_not_publicly_re_exported() -> None:
    import packages.exchange as ex_mod

    assert "_UNLABELED_METHODS" not in ex_mod.__all__
    assert not hasattr(ex_mod, "_UNLABELED_METHODS")


# --- H-013 no-default invariant for tpsl_mode -------------------------------


def test_set_trading_stop_requires_explicit_tpsl_mode() -> None:
    """H-013 / Q11: tpsl_mode has NO default; missing arg = TypeError at call site."""
    sig = inspect.signature(ExchangeClient.set_trading_stop)
    assert sig.parameters["tpsl_mode"].default is inspect.Parameter.empty


def test_set_trading_stop_tpsl_mode_is_first_after_symbol() -> None:
    """No-default param must come before defaulted ones (Python ordering rule)."""
    sig = inspect.signature(ExchangeClient.set_trading_stop)
    names = list(sig.parameters)
    assert names[0] == "self"
    assert names[1] == "symbol"
    assert names[2] == "tpsl_mode"


def test_set_trading_stop_optional_fields_default_to_none() -> None:
    sig = inspect.signature(ExchangeClient.set_trading_stop)
    for name in ("sl_price", "tp_price", "tp_size"):
        assert sig.parameters[name].default is None


# --- Streaming method signature (OQ-1) --------------------------------------


def test_stream_executions_is_not_async_def() -> None:
    """OQ-1 / Write-time guidance #3: def returning AsyncIterator, not async def."""
    assert not inspect.iscoroutinefunction(ExchangeClient.stream_executions)


def test_stream_positions_is_not_async_def() -> None:
    assert not inspect.iscoroutinefunction(ExchangeClient.stream_positions)


# --- Async-write-method shape ----------------------------------------------


def test_writes_and_reads_are_async() -> None:
    for name in (
        "set_leverage",
        "place_market_order",
        "set_trading_stop",
        "cancel_order",
        "get_positions",
        "get_fill_price",
        "get_closed_pnl_cumulative",
        "close",
    ):
        m = getattr(ExchangeClient, name)
        assert inspect.iscoroutinefunction(m), f"{name} should be async"


# --- Public surface ---------------------------------------------------------


def test_exchange_client_in_public_all() -> None:
    import packages.exchange as ex_mod

    assert "ExchangeClient" in ex_mod.__all__
    assert ex_mod.ExchangeClient is ExchangeClient
