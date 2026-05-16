"""§B.1 position-sizing math (T-527b2a tier ladder + T-528a risk-per-SL).

Caller-agnostic pure functions; no I/O, no caller (T-527b2b / T-528b are the
consumers — wire these into the execution-service placement seam per
ADR-0013). Two ``sizing.method`` paths share the cap + single-rounding-point
discipline:

* **tier** (T-527b2a): ``total_equity`` → ``select_tier`` →
  ``apply_score_multiplier`` → ``cap_notional`` → ``÷ mark_price`` via
  :func:`compute_qty_from_sizing`.
* **risk_per_sl** (T-528a): ``total_equity * risk_pct / sl_pct`` (the
  notional that loses exactly ``risk_pct`` of equity if the SL is hit) →
  ``cap_notional`` → ``÷ mark_price`` via :func:`compute_qty_from_risk`.
  Operator OQ-3=A: risk-per-SL is a deterministic risk model and does NOT
  apply ``score_multipliers`` (signal strength is gated upstream).

Decimal arithmetic throughout per §5.3 / §N1 (no float casts). ``score`` is
``float`` (mirrors ``ScoringResult.total_score``) and is used ONLY for
``floor(score)`` → an int multiplier-key index — never in the money value
path (notional + multipliers + division are Decimal).

Single-rounding-point: the orchestrators return the qty at full ``Decimal``
context precision and do NOT round. The shipped T-529
``packages.exchange.quantize.quantize_qty`` does the sole qty_step
round-down downstream in T-527b2b / T-528b; rounding here too would
double-round.

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
    "compute_qty_from_risk",
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


def compute_qty_from_risk(
    *,
    total_equity: Decimal,
    mark_price: Decimal,
    sl_pct: Decimal,
    risk_pct: Decimal,
    max_notional_per_symbol: dict[str, Decimal],
    symbol: str,
) -> Decimal | None:
    """risk-per-SL §B.1 pipeline: equity*risk_pct/sl_pct → cap → qty (T-528a).

    The ``risk_per_sl`` ``sizing.method`` alternative to the tier ladder
    (operator OQ-2=A). Sizes the position so that hitting the stop-loss
    loses exactly ``risk_pct`` of ``total_equity``: a linear-contract loss
    at the SL is ``qty * (mark_price * sl_pct)``; set equal to
    ``total_equity * risk_pct`` and solve ⇒
    ``notional = qty * mark_price = total_equity * risk_pct / sl_pct``.
    ``score_multipliers`` are NOT applied (OQ-3=A — deterministic risk
    model; signal strength is already gated upstream in scoring). The
    per-symbol ``cap_notional`` IS applied (safety rail, both methods).

    Guard ordering mirrors :func:`compute_qty_from_sizing` (the skip
    sentinel precedes the fail-loud raises): non-positive ``total_equity``
    (≤ 0 — no capital to size against) → ``None``, the risk-per-SL
    sub-capital skip sentinel (analogous to the tier path's sub-lowest-tier
    ``None``; T-528b translates ``None`` into
    skip-the-signal-before-``place_market_order``), checked BEFORE the
    ``ValueError``s. A non-positive ``mark_price`` / ``sl_pct`` /
    ``risk_pct`` (≤ 0) raises :class:`ValueError` (defensive fail-loud — an
    upstream/exchange/config anomaly must never silently mis-size; mirrors
    :func:`compute_qty_from_sizing`'s ``mark_price`` posture). ``sl_pct`` is
    the genuine external input (``OrderRequest.sl_pct`` — a signal with no
    usable SL); ``risk_pct`` is validator-guaranteed ``> 0`` by
    ``SizingSection`` but re-checked here for caller-agnostic correctness.
    T-528b maps the ``ValueError`` to the verbatim T-527b2b
    ``except ValueError`` skip-before-place path (``reason=compute_error``).

    Returns the qty at full ``Decimal`` context precision and does NOT
    round — the shipped T-529 ``quantize_qty`` does the sole qty_step
    round-down downstream in T-528b (single rounding point).

    Hand-verified (math-validator Gate-4 fixture; §B.1 caps
    ``{"default":3000,"BTCUSDT":5000}``):

    - equity ``10000``, risk_pct ``0.01``, sl_pct ``0.02``, mark ``50000``,
      ``"BTCUSDT"``: risk_amount ``100`` → notional ``100/0.02 = 5000`` →
      cap BTCUSDT 5000 → ``5000`` → qty ``5000/50000 = Decimal("0.1")``.
      Identity: loss-at-SL ``0.1*(50000*0.02=1000)=100`` = risk_amount.
    - equity ``10000``, risk_pct ``0.02``, sl_pct ``0.01``, mark ``2000``,
      ``"ETHUSDT"`` (→ default 3000): risk_amount ``200`` → notional
      ``200/0.01 = 20000`` → cap default 3000 → ``3000`` (capped) → qty
      ``3000/2000 = Decimal("1.5")``.
    - equity ``Decimal("0")`` / ``Decimal("-5")`` → ``None`` (skip).
    - mark ``Decimal("0")`` / sl_pct ``Decimal("0")`` / sl_pct
      ``Decimal("-0.01")`` / risk_pct ``Decimal("0")`` → :class:`ValueError`.
    """
    if total_equity <= 0:
        return None
    if mark_price <= 0:
        msg = f"mark_price must be positive for sizing; got {mark_price}"
        raise ValueError(msg)
    if sl_pct <= 0:
        msg = f"sl_pct must be positive for risk-per-SL sizing; got {sl_pct}"
        raise ValueError(msg)
    if risk_pct <= 0:
        msg = f"risk_pct must be positive for risk-per-SL sizing; got {risk_pct}"
        raise ValueError(msg)
    risk_amount = total_equity * risk_pct
    notional = risk_amount / sl_pct
    notional = cap_notional(notional, max_notional_per_symbol, symbol)
    return notional / mark_price
