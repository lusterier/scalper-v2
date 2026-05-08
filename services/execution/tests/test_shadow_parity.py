"""T-511b2 BRIEF §13.7 parity test — anti-DRY guard for T-511b1 verbatim-copy decision.

Shadow worker FSM helpers MUST match live ``lifecycle.py:233-268`` byte-for-byte
(modulo docstring wording). Drift between the two copies → math regression →
live + shadow diverge → variant outcomes no longer comparable to live (BRIEF
§13.7 load-bearing invariant).

3 helpers x 6 grid cases = 18 assertions. Hand-computed Decimal values per
plan-doc §"Hand verification".
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from services.execution.app import lifecycle as live
from services.execution.app import shadow_worker as shadow

# Hand-computed grid (plan-doc §"Hand verification" exhaustive enumeration).
_BE_TRIGGER_GRID: tuple[tuple[str, Decimal, Decimal, Decimal, bool], ...] = (
    # (side, current, entry, be_trigger, expected)
    # Long, exactly-at-threshold: (65325 - 65000) / 65000 = 0.005 → True.
    ("buy", Decimal("65325"), Decimal("65000"), Decimal("0.005"), True),
    # Long, one-tick-below: 0.00498... → False.
    ("buy", Decimal("65324"), Decimal("65000"), Decimal("0.005"), False),
    # Long, well-above: 0.0077... → True.
    ("buy", Decimal("65500"), Decimal("65000"), Decimal("0.005"), True),
    # Short, exactly-at-threshold: (65000 - 64675) / 65000 = 0.005 → True.
    ("sell", Decimal("64675"), Decimal("65000"), Decimal("0.005"), True),
    # Short, one-tick-below: 0.00498... → False.
    ("sell", Decimal("64676"), Decimal("65000"), Decimal("0.005"), False),
    # be_trigger=0 edge case: helper returns True since 0 >= 0; caller-side
    # `if be_trigger > 0` guard at shadow_worker.py + lifecycle.py filters it
    # out before helper invocation. Parity test verifies HELPER math identical.
    ("buy", Decimal("65000"), Decimal("65000"), Decimal("0"), True),
)


@pytest.mark.parametrize(("side", "current", "entry", "be_trigger", "expected"), _BE_TRIGGER_GRID)
def test_variant_step_transitions_match_live_lifecycle_fsm_check_be_trigger(
    side: str,
    current: Decimal,
    entry: Decimal,
    be_trigger: Decimal,
    expected: bool,
) -> None:
    """BRIEF §13.7 — shadow_worker._check_be_trigger == lifecycle._check_be_trigger."""
    live_result = live._check_be_trigger(side, current, entry, be_trigger)
    shadow_result = shadow._check_be_trigger(side, current, entry, be_trigger)
    assert live_result == shadow_result
    assert live_result is expected


_BE_SL_PRICE_GRID: tuple[tuple[str, Decimal, Decimal, Decimal], ...] = (
    # (side, entry_price, be_sl_level, expected_sl_price)
    # Long: 65000 * (1 + 0.001) = 65065
    ("buy", Decimal("65000"), Decimal("0.001"), Decimal("65065")),
    # Short: 65000 * (1 - 0.001) = 64935
    ("sell", Decimal("65000"), Decimal("0.001"), Decimal("64935")),
)


@pytest.mark.parametrize(("side", "entry", "be_sl_level", "expected"), _BE_SL_PRICE_GRID)
def test_variant_step_transitions_match_live_lifecycle_fsm_compute_be_sl_price(
    side: str,
    entry: Decimal,
    be_sl_level: Decimal,
    expected: Decimal,
) -> None:
    """BRIEF §13.7 — shadow_worker._compute_be_sl_price == lifecycle._compute_be_sl_price."""
    live_result = live._compute_be_sl_price(side, entry, be_sl_level)
    shadow_result = shadow._compute_be_sl_price(side, entry, be_sl_level)
    assert live_result == shadow_result
    assert live_result == expected


_TRAIL_SL_PRICE_GRID: tuple[tuple[str, Decimal, Decimal, Decimal], ...] = (
    # (side, best_price, trail_pct, expected_sl_price)
    # Long: best=66000, trail=0.003 → 66000 * (1 - 0.003) = 65802
    ("buy", Decimal("66000"), Decimal("0.003"), Decimal("65802")),
    # Short: best=64000, trail=0.003 → 64000 * (1 + 0.003) = 64192
    ("sell", Decimal("64000"), Decimal("0.003"), Decimal("64192")),
)


@pytest.mark.parametrize(("side", "best", "trail_pct", "expected"), _TRAIL_SL_PRICE_GRID)
def test_variant_step_transitions_match_live_lifecycle_fsm_compute_trail_sl_price(
    side: str,
    best: Decimal,
    trail_pct: Decimal,
    expected: Decimal,
) -> None:
    """BRIEF §13.7 — shadow_worker._compute_trail_sl_price == lifecycle._compute_trail_sl_price."""
    live_result = live._compute_trail_sl_price(side, best, trail_pct)
    shadow_result = shadow._compute_trail_sl_price(side, best, trail_pct)
    assert live_result == shadow_result
    assert live_result == expected
