"""Tests for :class:`packages.exchange.paper.PaperExchange` (T-211 skeleton).

Pin the protocol-satisfaction contract + per-method NotImplementedError
forward-pointers. After T-213a, ``place_market_order`` and
``set_trading_stop`` are partial-body methods (their messages point at
T-213b directly with computed values); their dedicated tests live in
``test_adapter_fill_semantics.py``. T-211 retains coverage of the
remaining 5 read/write async methods + 2 streams that T-213a leaves as
full stubs forward-pointed at T-213b.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.core import BotId, is_idempotent, is_non_idempotent
from packages.exchange import PaperExchange, SlippageModel
from packages.exchange.protocols import _UNLABELED_METHODS

# --- Constructor helper ---------------------------------------------------


def _make_pool() -> MagicMock:
    """asyncpg.Pool stand-in (T-213b extension).

    Mirror services/execution/tests/conftest.py::_PoolStub pattern.
    """
    pool = MagicMock()
    pool.close = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _make_pe(
    *,
    slippage_model: SlippageModel = "fixed_pct",
    slippage_params: dict[str, Decimal] | None = None,
) -> PaperExchange:
    """T-213a/b-extended PaperExchange constructor for stub/lifecycle tests.

    Tests asserting on constructor-level behaviour (attribute storage,
    validation, allow-list) construct PaperExchange directly to keep the
    call-site explicit; stub/lifecycle tests use this helper.
    """
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    return PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model=slippage_model,
        fee_rate=Decimal("0.0006"),
        bot_id=BotId("test-bot"),
        bus=bus,
        slippage_params=slippage_params or {"fixed_slippage_pct": Decimal("0.0005")},
        pool=_make_pool(),
    )


# --- Constructor ----------------------------------------------------------


def test_constructs_with_required_kwargs() -> None:
    bus = MagicMock()
    pe = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
        bot_id=BotId("test-bot"),
        bus=bus,
        slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
        pool=_make_pool(),
    )
    assert pe._seed_balance == Decimal("10000.00")
    assert pe._slippage_model == "fixed_pct"
    assert pe._fee_rate == Decimal("0.0006")


def test_rejects_unknown_slippage_model() -> None:
    bus = MagicMock()
    with pytest.raises(ValueError, match="slippage_model must be one of"):
        PaperExchange(
            seed_balance=Decimal("10000.00"),
            slippage_model="invalid",  # type: ignore[arg-type]
            fee_rate=Decimal("0.0006"),
            bot_id=BotId("test-bot"),
            bus=bus,
            slippage_params={},
            pool=_make_pool(),
        )


def test_rejects_typo_in_slippage_model() -> None:
    """Allow-list defends against subtle typos like trailing underscores."""
    bus = MagicMock()
    with pytest.raises(ValueError, match="slippage_model must be one of"):
        PaperExchange(
            seed_balance=Decimal("10000.00"),
            slippage_model="fixed_pct_",  # type: ignore[arg-type]
            fee_rate=Decimal("0.0006"),
            bot_id=BotId("test-bot"),
            bus=bus,
            slippage_params={},
            pool=_make_pool(),
        )


@pytest.mark.parametrize(
    ("model", "params"),
    [
        ("fixed_pct", {"fixed_slippage_pct": Decimal("0.0005")}),
        ("proportional_to_qty", {"qty_slippage_coeff": Decimal("0.0001")}),
        ("half_spread", {"half_spread_factor": Decimal("1.0")}),
    ],
)
def test_each_slippage_model_accepted(
    model: SlippageModel,
    params: dict[str, Decimal],
) -> None:
    """All three §12.1 verbatim slippage models accepted at construction."""
    bus = MagicMock()
    pe = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model=model,
        fee_rate=Decimal("0.0006"),
        bot_id=BotId("test-bot"),
        bus=bus,
        slippage_params=params,
        pool=_make_pool(),
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


# --- Method stubs raise NotImplementedError pointing at T-213c ------------
# T-213b narrowing: set_leverage is now no-op (Decision #13);
# place_market_order, set_trading_stop, cancel_order, stream_executions,
# stream_positions land full bodies (covered in test_adapter_fill_semantics.py
# + test_paper_emission.py + test_paper_persistence.py). Remaining read-method
# stubs (get_positions, get_fill_price, get_closed_pnl_cumulative) forward
# to T-213c.


_STUBBED_ASYNC_METHODS = (
    ("get_positions", lambda pe: pe.get_positions("BTCUSDT")),
    ("get_fill_price", lambda pe: pe.get_fill_price("BTCUSDT", "ord-1")),
    ("get_closed_pnl_cumulative", lambda pe: pe.get_closed_pnl_cumulative("sub-1")),
)


@pytest.mark.parametrize(("method_name", "invoke"), _STUBBED_ASYNC_METHODS)
async def test_async_method_stub_raises_with_t213_message(
    method_name: str,
    invoke: object,
) -> None:
    """Each remaining read-method stub raises NotImplementedError with T-213 forward-pointer.

    Post-T-213b: only the 3 read methods (get_positions / get_fill_price /
    get_closed_pnl_cumulative) remain stubbed; T-213c picks them up. The
    message-contains-"T-213" assertion is the fail-loud forward-pointer
    contract.
    """
    pe = _make_pe()
    assert callable(invoke)
    with pytest.raises(NotImplementedError) as info:
        await invoke(pe)
    assert method_name in str(info.value)
    assert "T-213" in str(info.value)


# --- Lifecycle ------------------------------------------------------------


async def test_close_is_noop_at_skeleton() -> None:
    """``close()`` is the only non-stub method at T-211 — returns without raising.

    Typed ``-> None``, so we await it for side-effect (or absence thereof);
    asserting the return-value triggers mypy ``func-returns-value``. The
    contract being pinned is "no exception raised", not "returns None".
    """
    pe = _make_pe()
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
