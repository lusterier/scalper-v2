"""§N4 TDD for `packages.sizing.compute` (T-527b2a).

Hand-authored Decimal fixtures from the plan `## Hand verification` (NOT
implementation-against-itself / round-trip). §B.1 alpha.yaml block verbatim
(BRIEF §22 lines 3130-3145).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from packages.scoring.types import SizingTier
from packages.sizing import (
    apply_score_multiplier,
    cap_notional,
    compute_qty_from_risk,
    compute_qty_from_sizing,
    select_tier,
)

# §B.1 alpha.yaml `sizing:` block (BRIEF §22 lines 3130-3145), verbatim.
_B1_TIERS: list[SizingTier] = [
    SizingTier(balance_min=Decimal("500"), size=Decimal("700")),
    SizingTier(balance_min=Decimal("1000"), size=Decimal("1400")),
    SizingTier(balance_min=Decimal("2000"), size=Decimal("2100")),
    SizingTier(balance_min=Decimal("4000"), size=Decimal("2800")),
]
_B1_MULTIPLIERS: dict[str, Decimal] = {
    "4": Decimal("0.75"),
    "5": Decimal("1.0"),
    "6": Decimal("1.25"),
    "7": Decimal("1.5"),
    "8": Decimal("1.5"),
    "9": Decimal("1.5"),
}
_B1_CAPS: dict[str, Decimal] = {"default": Decimal("3000"), "BTCUSDT": Decimal("5000")}


# --- select_tier ----------------------------------------------------------


def test_select_tier_picks_highest_below_equity() -> None:
    assert select_tier(Decimal("1500"), _B1_TIERS) is _B1_TIERS[1]  # {1000,1400}


def test_select_tier_balance_min_boundary_is_inclusive() -> None:
    assert select_tier(Decimal("500"), _B1_TIERS) is _B1_TIERS[0]  # {500,700}
    assert select_tier(Decimal("1000"), _B1_TIERS) is _B1_TIERS[1]  # {1000,1400}


def test_select_tier_below_lowest_returns_none() -> None:
    assert select_tier(Decimal("499.99"), _B1_TIERS) is None
    assert select_tier(Decimal("0"), _B1_TIERS) is None


def test_select_tier_above_top_returns_highest() -> None:
    assert select_tier(Decimal("50000"), _B1_TIERS) is _B1_TIERS[3]  # {4000,2800}
    assert select_tier(Decimal("4000"), _B1_TIERS) is _B1_TIERS[3]  # boundary-inclusive


# --- apply_score_multiplier (OQ-3=A) --------------------------------------


def test_apply_score_multiplier_floor_then_lookup() -> None:
    # score 6.7 → floor 6 → "6" → 1.25 ; 1400 * 1.25 = 1750.00 (exact Decimal).
    assert apply_score_multiplier(Decimal("1400"), 6.7, _B1_MULTIPLIERS) == Decimal("1750.00")


def test_apply_score_multiplier_clamps_low() -> None:
    # score 2.5 → floor 2 → clamp to present min 4 → "4" → 0.75 ; 1400*0.75=1050.00.
    assert apply_score_multiplier(Decimal("1400"), 2.5, _B1_MULTIPLIERS) == Decimal("1050.00")


def test_apply_score_multiplier_clamps_high() -> None:
    # score 11.0 → floor 11 → clamp to present max 9 → "9" → 1.5 ; 1400*1.5=2100.0.
    assert apply_score_multiplier(Decimal("1400"), 11.0, _B1_MULTIPLIERS) == Decimal("2100.0")


def test_apply_score_multiplier_none_is_identity() -> None:
    n = Decimal("1400")
    assert apply_score_multiplier(n, None, _B1_MULTIPLIERS) == n


def test_apply_score_multiplier_sparse_gap_after_clamp_is_identity() -> None:
    # keys {4,9}; score 6.0 → floor 6, clamp[4,9]→6, "6" absent → *1.0.
    sparse = {"4": Decimal("0.75"), "9": Decimal("1.5")}
    assert apply_score_multiplier(Decimal("1400"), 6.0, sparse) == Decimal("1400")


def test_apply_score_multiplier_empty_multipliers_is_identity() -> None:
    assert apply_score_multiplier(Decimal("1400"), 6.0, {}) == Decimal("1400")


# --- cap_notional ---------------------------------------------------------


def test_cap_notional_uses_default_when_symbol_absent() -> None:
    # ETHUSDT not in caps → default 3000 ; min(1750.00, 3000) = 1750.00.
    assert cap_notional(Decimal("1750.00"), _B1_CAPS, "ETHUSDT") == Decimal("1750.00")


def test_cap_notional_uses_symbol_override_and_clamps() -> None:
    # BTCUSDT cap 5000 ; min(6000, 5000) = 5000.
    assert cap_notional(Decimal("6000"), _B1_CAPS, "BTCUSDT") == Decimal("5000")


def test_cap_notional_no_clamp_when_under_cap() -> None:
    assert cap_notional(Decimal("1000"), _B1_CAPS, "BTCUSDT") == Decimal("1000")


# --- compute_qty_from_sizing (orchestrator) -------------------------------


def test_compute_qty_full_pipeline_b1() -> None:
    # equity 1500 → tier {1000,1400}; score 6.7 → "6"=1.25 → 1750.00;
    # cap ETHUSDT→default 3000 → 1750.00; qty = 1750.00 / 42500.75 (full Decimal).
    qty = compute_qty_from_sizing(
        total_equity=Decimal("1500"),
        mark_price=Decimal("42500.75"),
        tiers=_B1_TIERS,
        score=6.7,
        score_multipliers=_B1_MULTIPLIERS,
        max_notional_per_symbol=_B1_CAPS,
        symbol="ETHUSDT",
    )
    assert qty == Decimal("1750.00") / Decimal("42500.75")  # numerator hand-derived
    assert isinstance(qty, Decimal)
    assert Decimal("0.04") < qty < Decimal("0.042")  # sanity magnitude


def test_compute_qty_sub_lowest_tier_returns_none() -> None:
    qty = compute_qty_from_sizing(
        total_equity=Decimal("499"),
        mark_price=Decimal("42500.75"),
        tiers=_B1_TIERS,
        score=6.7,
        score_multipliers=_B1_MULTIPLIERS,
        max_notional_per_symbol=_B1_CAPS,
        symbol="ETHUSDT",
    )
    assert qty is None


def test_compute_qty_none_guard_precedes_mark_price_guard() -> None:
    # WG#4 guard ordering: sub-lowest tier → None BEFORE mark_price<=0 check.
    # equity below lowest AND mark_price=0 → must return None, NOT raise.
    qty = compute_qty_from_sizing(
        total_equity=Decimal("499"),
        mark_price=Decimal("0"),
        tiers=_B1_TIERS,
        score=6.7,
        score_multipliers=_B1_MULTIPLIERS,
        max_notional_per_symbol=_B1_CAPS,
        symbol="ETHUSDT",
    )
    assert qty is None


@pytest.mark.parametrize("bad_price", [Decimal("0"), Decimal("-1"), Decimal("-0.01")])
def test_compute_qty_non_positive_mark_price_raises(bad_price: Decimal) -> None:
    with pytest.raises(ValueError, match="mark_price must be positive"):
        compute_qty_from_sizing(
            total_equity=Decimal("1500"),
            mark_price=bad_price,
            tiers=_B1_TIERS,
            score=6.7,
            score_multipliers=_B1_MULTIPLIERS,
            max_notional_per_symbol=_B1_CAPS,
            symbol="ETHUSDT",
        )


def test_compute_qty_applies_max_notional_cap_before_division() -> None:
    # equity 50000 → tier {4000,2800}; score 9 → "9"=1.5 → 2800*1.5=4200.00;
    # symbol BTCUSDT cap 5000 → 4200.00 (under cap, no clamp); qty=4200.00/50000.
    qty = compute_qty_from_sizing(
        total_equity=Decimal("50000"),
        mark_price=Decimal("50000"),
        tiers=_B1_TIERS,
        score=9.0,
        score_multipliers=_B1_MULTIPLIERS,
        max_notional_per_symbol=_B1_CAPS,
        symbol="BTCUSDT",
    )
    assert qty == Decimal("4200.0") / Decimal("50000")  # 2800*1.5=4200.0 (scale 1), hand-derived


def test_compute_qty_cap_actually_clamps() -> None:
    # equity 50000 → tier {4000,2800}; score 9 → *1.5 → 4200.00;
    # symbol absent → default cap 3000 → min(4200,3000)=3000; qty=3000/60000=0.05.
    qty = compute_qty_from_sizing(
        total_equity=Decimal("50000"),
        mark_price=Decimal("60000"),
        tiers=_B1_TIERS,
        score=9.0,
        score_multipliers=_B1_MULTIPLIERS,
        max_notional_per_symbol=_B1_CAPS,
        symbol="ETHUSDT",
    )
    assert qty == Decimal("3000") / Decimal("60000")
    assert qty == Decimal("0.05")


# --- compute_qty_from_risk (T-528a risk-per-SL orchestrator) ---------------
# §N4 hand-authored from docs/plans/T-528a.md `## Hand verification` (NOT
# implementation-against-itself). §B.1 caps {"default":3000,"BTCUSDT":5000}.


def test_compute_qty_from_risk_btcusdt_under_cap() -> None:
    # equity 10000, risk 1%, sl 2%, mark 50000, BTCUSDT.
    # risk_amount=10000*0.01=100; notional=100/0.02=5000; cap BTCUSDT 5000 →
    # min(5000,5000)=5000; qty=5000/50000=0.1.
    qty = compute_qty_from_risk(
        total_equity=Decimal("10000"),
        mark_price=Decimal("50000"),
        sl_pct=Decimal("0.02"),
        risk_pct=Decimal("0.01"),
        max_notional_per_symbol=_B1_CAPS,
        symbol="BTCUSDT",
    )
    assert qty == Decimal("5000") / Decimal("50000")
    assert qty == Decimal("0.1")
    # Independent identity: linear-contract loss at SL == risked equity.
    assert qty * (Decimal("50000") * Decimal("0.02")) == Decimal("10000") * Decimal("0.01")


def test_compute_qty_from_risk_default_cap_clamps() -> None:
    # equity 10000, risk 2%, sl 1%, mark 2000, ETHUSDT (→ default cap 3000).
    # risk_amount=200; notional=200/0.01=20000; cap default 3000 →
    # min(20000,3000)=3000 (clamped); qty=3000/2000=1.5.
    qty = compute_qty_from_risk(
        total_equity=Decimal("10000"),
        mark_price=Decimal("2000"),
        sl_pct=Decimal("0.01"),
        risk_pct=Decimal("0.02"),
        max_notional_per_symbol=_B1_CAPS,
        symbol="ETHUSDT",
    )
    assert qty == Decimal("3000") / Decimal("2000")
    assert qty == Decimal("1.5")


def test_compute_qty_from_risk_division_full_precision_no_round() -> None:
    # equity 1000, risk 0.0333, sl 0.03, mark 42500.75, ETHUSDT.
    # risk_amount=1000*0.0333=33.3000; notional=33.3000/0.03=1110(.00);
    # cap default 3000 → 1110 (under); qty=1110/42500.75 full Decimal, NO round.
    qty = compute_qty_from_risk(
        total_equity=Decimal("1000"),
        mark_price=Decimal("42500.75"),
        sl_pct=Decimal("0.03"),
        risk_pct=Decimal("0.0333"),
        max_notional_per_symbol=_B1_CAPS,
        symbol="ETHUSDT",
    )
    expected = Decimal("1000") * Decimal("0.0333") / Decimal("0.03") / Decimal("42500.75")
    assert qty == expected
    assert isinstance(qty, Decimal)
    assert Decimal("0.026") < qty < Decimal("0.027")  # sanity magnitude


@pytest.mark.parametrize("equity", [Decimal("0"), Decimal("-5")])
def test_compute_qty_from_risk_non_positive_equity_returns_none(equity: Decimal) -> None:
    qty = compute_qty_from_risk(
        total_equity=equity,
        mark_price=Decimal("50000"),
        sl_pct=Decimal("0.02"),
        risk_pct=Decimal("0.01"),
        max_notional_per_symbol=_B1_CAPS,
        symbol="BTCUSDT",
    )
    assert qty is None


def test_compute_qty_from_risk_equity_sentinel_precedes_raises() -> None:
    # Guard ordering: total_equity<=0 → None even when mark_price AND sl_pct
    # are also invalid (the skip sentinel precedes the fail-loud raises —
    # mirror compute_qty_from_sizing's None-before-mark_price guard order).
    qty = compute_qty_from_risk(
        total_equity=Decimal("0"),
        mark_price=Decimal("0"),
        sl_pct=Decimal("0"),
        risk_pct=Decimal("0"),
        max_notional_per_symbol=_B1_CAPS,
        symbol="BTCUSDT",
    )
    assert qty is None


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1"), Decimal("-0.01")])
def test_compute_qty_from_risk_non_positive_mark_price_raises(bad: Decimal) -> None:
    with pytest.raises(ValueError, match="mark_price must be positive"):
        compute_qty_from_risk(
            total_equity=Decimal("10000"),
            mark_price=bad,
            sl_pct=Decimal("0.02"),
            risk_pct=Decimal("0.01"),
            max_notional_per_symbol=_B1_CAPS,
            symbol="BTCUSDT",
        )


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-0.01")])
def test_compute_qty_from_risk_non_positive_sl_pct_raises(bad: Decimal) -> None:
    with pytest.raises(ValueError, match="sl_pct must be positive"):
        compute_qty_from_risk(
            total_equity=Decimal("10000"),
            mark_price=Decimal("50000"),
            sl_pct=bad,
            risk_pct=Decimal("0.01"),
            max_notional_per_symbol=_B1_CAPS,
            symbol="BTCUSDT",
        )


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-0.01")])
def test_compute_qty_from_risk_non_positive_risk_pct_raises(bad: Decimal) -> None:
    with pytest.raises(ValueError, match="risk_pct must be positive"):
        compute_qty_from_risk(
            total_equity=Decimal("10000"),
            mark_price=Decimal("50000"),
            sl_pct=Decimal("0.02"),
            risk_pct=bad,
            max_notional_per_symbol=_B1_CAPS,
            symbol="BTCUSDT",
        )
