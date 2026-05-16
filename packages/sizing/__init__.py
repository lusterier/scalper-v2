"""`packages.sizing` — §B.1 tier-ladder position-sizing pure math (T-527b2a).

Caller-agnostic Decimal-only compute (no I/O, no caller). T-527b2b wires
these into the execution-service placement seam per ADR-0013.
"""

from __future__ import annotations

from packages.sizing.compute import (
    apply_score_multiplier,
    cap_notional,
    compute_qty_from_sizing,
    select_tier,
)

__all__ = [
    "apply_score_multiplier",
    "cap_notional",
    "compute_qty_from_sizing",
    "select_tier",
]
