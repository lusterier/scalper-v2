"""Tests for :class:`packages.exchange.paper.PaperExchange` (T-211 skeleton).

Pin the protocol-satisfaction contract + per-method NotImplementedError
forward-pointers to T-213. If T-213 plan-doc author replaces a stub
without updating the corresponding ``test_*_stub_raises*``, the
message-contains-"T-213" assertion breaks loudly — that is the
intended fail-loud forward-pointer contract.

Behavioural fill-semantics tests (slippage math, fee deduction,
paper_* persistence, execution emission) live at T-213; T-211 ships
only the surface invariants.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from packages.core import is_idempotent, is_non_idempotent
from packages.exchange import PaperExchange, SlippageModel
from packages.exchange.protocols import _UNLABELED_METHODS

# --- Constructor ----------------------------------------------------------


def test_constructs_with_required_kwargs() -> None:
    pe = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
    )
    assert pe._seed_balance == Decimal("10000.00")
    assert pe._slippage_model == "fixed_pct"
    assert pe._fee_rate == Decimal("0.0006")


def test_rejects_unknown_slippage_model() -> None:
    with pytest.raises(ValueError, match="slippage_model must be one of"):
        PaperExchange(
            seed_balance=Decimal("10000.00"),
            slippage_model="invalid",  # type: ignore[arg-type]
            fee_rate=Decimal("0.0006"),
        )


def test_rejects_typo_in_slippage_model() -> None:
    """Allow-list defends against subtle typos like trailing underscores."""
    with pytest.raises(ValueError, match="slippage_model must be one of"):
        PaperExchange(
            seed_balance=Decimal("10000.00"),
            slippage_model="fixed_pct_",  # type: ignore[arg-type]
            fee_rate=Decimal("0.0006"),
        )


@pytest.mark.parametrize("model", ["fixed_pct", "proportional_to_qty", "half_spread"])
def test_each_slippage_model_accepted(model: SlippageModel) -> None:
    """All three §12.1 verbatim slippage models accepted at construction."""
    pe = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model=model,
        fee_rate=Decimal("0.0006"),
    )
    assert pe._slippage_model == model


# --- Protocol satisfaction (T-201 _UNLABELED_METHODS contract) -----------


_LABELED_METHODS_EXPECTED = (
    ("set_leverage", is_idempotent),
    ("place_market_order", is_non_idempotent),
    ("set_trading_stop", is_idempotent),
    ("cancel_order", is_idempotent),
    ("get_positions", is_idempotent),
    ("get_fill_price", is_idempotent),
    ("get_closed_pnl_cumulative", is_idempotent),
)


@pytest.mark.parametrize(("method_name", "predicate"), _LABELED_METHODS_EXPECTED)
def test_paper_exchange_methods_carry_correct_markers(
    method_name: str,
    predicate: object,
) -> None:
    """Each ExchangeClient method on PaperExchange has matching marker.

    Mirrors T-201 ``test_every_non_exempt_protocol_method_carries_a_marker``
    pattern: the 4 writes + 3 reads carry their idempotency markers; the
    3 unlabeled methods (streams + close) are exempt per
    :data:`packages.exchange.protocols._UNLABELED_METHODS`.
    """
    method = getattr(PaperExchange, method_name)
    assert callable(predicate)
    assert predicate(method), f"PaperExchange.{method_name} missing required marker"


def test_unlabeled_methods_carry_no_markers() -> None:
    """Streams + close exempt per T-201 _UNLABELED_METHODS frozenset."""
    for name in _UNLABELED_METHODS:
        method = getattr(PaperExchange, name)
        assert not is_idempotent(method)
        assert not is_non_idempotent(method)


def test_paper_exchange_implements_full_exchange_client_surface() -> None:
    """Every public method declared on ExchangeClient Protocol is present
    on PaperExchange (not necessarily wired — stubs are acceptable)."""
    from packages.exchange import ExchangeClient

    expected = {
        name
        for name, member in inspect.getmembers(ExchangeClient)
        if callable(member) and not name.startswith("_")
    }
    actual = {
        name
        for name, member in inspect.getmembers(PaperExchange)
        if callable(member) and not name.startswith("_")
    }
    missing = expected - actual
    assert not missing, f"PaperExchange missing ExchangeClient methods: {missing}"


# --- Method stubs raise NotImplementedError pointing at T-213 -------------


_STUBBED_ASYNC_METHODS = (
    ("set_leverage", lambda pe: pe.set_leverage("BTCUSDT", 10)),
    (
        "place_market_order",
        lambda pe: pe.place_market_order("BTCUSDT", "buy", Decimal("0.05")),
    ),
    (
        "set_trading_stop",
        lambda pe: pe.set_trading_stop("BTCUSDT", "Full"),
    ),
    ("cancel_order", lambda pe: pe.cancel_order("BTCUSDT", "ord-1")),
    ("get_positions", lambda pe: pe.get_positions("BTCUSDT")),
    ("get_fill_price", lambda pe: pe.get_fill_price("BTCUSDT", "ord-1")),
    ("get_closed_pnl_cumulative", lambda pe: pe.get_closed_pnl_cumulative("sub-1")),
)


@pytest.mark.parametrize(("method_name", "invoke"), _STUBBED_ASYNC_METHODS)
async def test_async_method_stub_raises_with_t213_message(
    method_name: str,
    invoke: object,
) -> None:
    """Each async stub raises NotImplementedError with T-213 forward-pointer.

    The message-contains-"T-213" assertion is the fail-loud forward-pointer
    contract: if T-213 plan-doc author replaces a stub without updating
    this test, the assertion breaks loudly.
    """
    pe = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
    )
    assert callable(invoke)
    with pytest.raises(NotImplementedError) as info:
        await invoke(pe)
    assert method_name in str(info.value)
    assert "T-213" in str(info.value)


def test_stream_executions_stub_raises_synchronously() -> None:
    """``stream_executions`` is ``def``-not-``async-def`` per T-201 OQ-1.

    Calling the method raises NotImplementedError immediately — does not
    return an empty async iterator that swallows the error on first
    ``__anext__``. Tested via direct call, NOT via ``async for``.
    """
    pe = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
    )
    with pytest.raises(NotImplementedError) as info:
        pe.stream_executions()
    assert "stream_executions" in str(info.value)
    assert "T-213" in str(info.value)


def test_stream_positions_stub_raises_synchronously() -> None:
    pe = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
    )
    with pytest.raises(NotImplementedError) as info:
        pe.stream_positions()
    assert "stream_positions" in str(info.value)
    assert "T-213" in str(info.value)


# --- Lifecycle ------------------------------------------------------------


async def test_close_is_noop_at_skeleton() -> None:
    """``close()`` is the only non-stub method at T-211 — returns without raising.

    Typed ``-> None``, so we await it for side-effect (or absence thereof);
    asserting the return-value triggers mypy ``func-returns-value``. The
    contract being pinned is "no exception raised", not "returns None".
    """
    pe = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
    )
    await pe.close()


# --- Public re-exports ----------------------------------------------------


def test_paper_exchange_in_packages_exchange_all() -> None:
    """``from packages.exchange import PaperExchange`` works."""
    import packages.exchange as ex_mod

    assert "PaperExchange" in ex_mod.__all__
    assert ex_mod.PaperExchange is PaperExchange


def test_slippage_model_in_packages_exchange_paper_all() -> None:
    """``from packages.exchange.paper import SlippageModel`` works."""
    import packages.exchange.paper as paper_mod

    assert "SlippageModel" in paper_mod.__all__
    assert paper_mod.SlippageModel is SlippageModel
