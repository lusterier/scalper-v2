"""`packages.sizing` — §B.1 position-sizing pure math.

Caller-agnostic Decimal-only compute (no I/O, no caller). Two
``sizing.method`` paths: tier ladder (T-527b2a) + risk-per-SL (T-528a).
T-527b2b / T-528b wire these into the execution-service placement seam
per ADR-0013.
"""

from __future__ import annotations

from packages.sizing.compute import (
    apply_score_multiplier,
    cap_notional,
    compute_qty_from_risk,
    compute_qty_from_sizing,
    select_tier,
)

__all__ = [
    "apply_score_multiplier",
    "cap_notional",
    "compute_qty_from_risk",
    "compute_qty_from_sizing",
    "select_tier",
]
