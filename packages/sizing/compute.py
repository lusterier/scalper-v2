"""§B.1 tier-ladder position-sizing math (T-527b2a).

Caller-agnostic pure functions; no I/O, no caller (T-527b2b is the first
consumer — wires these into the execution-service placement seam per
ADR-0013). Consumes ``total_equity`` + ``mark_price`` + the §B.1
``sizing:`` block components (tiers / score_multipliers /
max_notional_per_symbol). Decimal arithmetic throughout per §5.3 / §N1 (no
float casts). ``score`` is ``float`` (mirrors ``ScoringResult.total_score``)
and is used ONLY for ``floor(score)`` → an int multiplier-key index — never
in the money value path (notional + multipliers + division are Decimal).

Single-rounding-point: ``compute_qty_from_sizing`` returns the qty at full
``Decimal`` context precision and does NOT round. The shipped T-529
``packages.exchange.quantize.quantize_qty`` does the sole qty_step
round-down downstream in T-527b2b; rounding here too would double-round.

Tier-promotion / tier-demotion (§B.1 alpha.yaml 3146-3149) are operator
OQ-2=A deferred (separate ``T-F5+`` backlog) — NOT modeled here.
"""

from __future__ import annotations

from math import floor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal

    from packages.scoring.types import SizingTier

__all__ = [
    "apply_score_multiplier",
    "cap_notional",
    "compute_qty_from_sizing",
    "select_tier",
]


def select_tier(total_equity: Decimal, tiers: list[SizingTier]) -> SizingTier | None:
    """Return the highest tier whose ``balance_min <= total_equity``.

    Precondition (guaranteed by ``SizingSection._structural_guards`` — NOT
    re-validated): ``tiers`` is non-empty and strictly-ascending by
    ``balance_min``. ``balance_min`` boundary is inclusive (``>=``).

    ``total_equity < tiers[0].balance_min`` → ``None`` — the OQ-4=A
    sub-lowest-tier sentinel; T-527b2b translates ``None`` into
    skip-the-signal-before-``place_market_order``.

    Hand-verified (math-validator Gate-4 fixture; §B.1 alpha.yaml tiers
    ``[{500,700},{1000,1400},{2000,2100},{4000,2800}]``):

    - ``Decimal("1500")`` → ``{1000,1400}`` (1500 ≥ 1000, < 2000).
    - ``Decimal("500")`` → ``{500,700}`` (boundary-inclusive ≥ 500).
    - ``Decimal("499.99")`` → ``None`` (< lowest 500).
    - ``Decimal("50000")`` → ``{4000,2800}`` (highest; ≥ 4000).
    """
    selected: SizingTier | None = None
    for tier in tiers:
        if tier.balance_min <= total_equity:
            selected = tier
        else:
            # strictly-ascending precondition → no later tier qualifies.
            break
    return selected


def apply_score_multiplier(
    notional: Decimal,
    score: float | None,
    score_multipliers: dict[str, Decimal],
) -> Decimal:
    """Scale ``notional`` by the §B.1 ``score_multipliers`` entry for ``score``.

    OQ-3=A: ``key = str(floor(score))`` clamped into the ``[min, max]`` of
    the present digit-string keys; missing/sparse-after-clamp key OR
    ``score is None`` OR empty ``score_multipliers`` → *1.0 (``notional``
    unchanged — never block a scored signal on a sizing-config gap).

    ``score`` (``float``) feeds ONLY ``floor()`` → int (a key index, not
    money); the multiplier + ``notional`` are ``Decimal`` (§N1 value path).

    Hand-verified (notional ``Decimal("1400")``; §B.1 multipliers
    ``{"4":0.75,"5":1.0,"6":1.25,"7":1.5,"8":1.5,"9":1.5}``):

    - ``score=6.7`` → ``floor 6``, clamp[4,9]→6, ``"6"``→1.25 →
      ``Decimal("1750.00")``.
    - ``score=2.5`` → ``floor 2``, clamp→4, ``"4"``→0.75 →
      ``Decimal("1050.00")``.
    - ``score=11.0`` → ``floor 11``, clamp→9, ``"9"``→1.5 →
      ``Decimal("2100.0")``.
    - ``score=None`` → ``Decimal("1400")`` (*1.0).
    - sparse ``{"4":0.75,"9":1.5}``, ``score=6.0`` → clamp→6, ``"6"`` ∉ →
      ``Decimal("1400")`` (*1.0).
    """
    if score is None or not score_multipliers:
        return notional
    present = sorted(int(k) for k in score_multipliers)
    clamped = min(max(floor(score), present[0]), present[-1])
    key = str(clamped)
    if key in score_multipliers:
        return notional * score_multipliers[key]
    return notional


def cap_notional(
    notional: Decimal,
    max_notional_per_symbol: dict[str, Decimal],
    symbol: str,
) -> Decimal:
    """Clamp the (post-multiplier) ``notional`` to the per-symbol cap.

    ``cap = max_notional_per_symbol.get(symbol, max_notional_per_symbol["default"])``
    — the ``"default"`` key is guaranteed by ``SizingSection`` (precondition,
    NOT re-validated). Returns ``min(notional, cap)``. Applied AFTER the
    score multiplier (§B.1 order: tier.size → *multiplier → cap).

    Hand-verified (caps ``{"default":3000,"BTCUSDT":5000}``):

    - ``cap_notional(Decimal("1750.00"), …, "ETHUSDT")`` → no ``ETHUSDT`` →
      default 3000 → ``min(1750.00, 3000) = Decimal("1750.00")``.
    - ``cap_notional(Decimal("6000"), …, "BTCUSDT")`` → ``BTCUSDT`` 5000 →
      ``min(6000, 5000) = Decimal("5000")``.
    """
    cap = max_notional_per_symbol.get(symbol, max_notional_per_symbol["default"])
    return min(notional, cap)


def compute_qty_from_sizing(
    *,
    total_equity: Decimal,
    mark_price: Decimal,
    tiers: list[SizingTier],
    score: float | None,
    score_multipliers: dict[str, Decimal],
    max_notional_per_symbol: dict[str, Decimal],
    symbol: str,
) -> Decimal | None:
    """Full §B.1 pipeline: balance → tier → *score-mult → cap → qty.

    Guard ordering (T-527b2a WG#4): ``select_tier`` → ``None`` short-circuits
    to ``return None`` (the sub-lowest-tier skip sentinel — propagated
    unchanged) BEFORE the ``mark_price`` check; a non-positive
    ``mark_price`` (≤ 0, not just == 0) raises :class:`ValueError`
    (defensive fail-loud — an upstream/exchange anomaly must never silently
    mis-size; mirrors ``quantize.py``'s pre-flight raise posture).

    Returns the qty at full ``Decimal`` context precision and does NOT
    round — the shipped T-529 ``quantize_qty`` does the sole qty_step
    round-down downstream in T-527b2b (single rounding point).

    Hand-verified (``total_equity=Decimal("1500")``,
    ``mark_price=Decimal("42500.75")``, ``score=6.7``, ``symbol="ETHUSDT"``,
    §B.1 block): tier ``{1000,1400}`` → ``apply`` ``"6"``=1.25 →
    ``Decimal("1750.00")`` → cap default 3000 → ``Decimal("1750.00")`` →
    ``Decimal("1750.00") / Decimal("42500.75")`` (full Decimal precision).
    ``mark_price=Decimal("0")`` → :class:`ValueError`.
    ``total_equity=Decimal("499")`` → ``select_tier`` → ``None`` → ``None``.
    """
    tier = select_tier(total_equity, tiers)
    if tier is None:
        return None
    if mark_price <= 0:
        msg = f"mark_price must be positive for sizing; got {mark_price}"
        raise ValueError(msg)
    notional = apply_score_multiplier(tier.size, score, score_multipliers)
    notional = cap_notional(notional, max_notional_per_symbol, symbol)
    return notional / mark_price
