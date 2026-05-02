"""§10.6 example plugin — OI drop detection over lookback candles (T-312).

Reference plugin per BRIEF §10.6:1796-1812 verbatim. Reads OI feature
history from ``ctx.feature_history[ctx.feature_ref]`` (T-303 field;
T-306 resolver does NOT populate today — F4+ deferred). Computes drop
percentage between oldest (window start) and newest (window end) OI
values; returns ``RuleOutcome(result=True, ...)`` if drop ≥
``oi_drop_pct`` threshold.

Bot YAML usage (per BRIEF §B.1:2985-2991):

    - name: oi_squeeze_v2
      weight: +1.0
      feature: ind.${signal.symbol}.15m.oi_change   # T-308 requires explicit feature
      condition:
        type: plugin
        name: oi_squeeze
        version: 2
        params: { lookback_candles: 5, oi_drop_pct: 1.0 }

v1 limitations: ``feature_history`` empty in T-306 today; plugin returns
``RuleOutcome(result=False, metadata={"reason": "history_insufficient"})``
until F4+ resolver upgrade. Reference implementation per BRIEF §10.6
example — serves as documentation-by-example for operator-authored
plugins. T-313 E4 verifies the plugin loads + evaluates cleanly.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, ClassVar

from packages.scoring.protocol import Rule, RuleOutcome

if TYPE_CHECKING:
    from packages.scoring.protocol import RuleContext

__all__ = ["OISqueezeRule"]


class OISqueezeRule(Rule):
    """§10.6 plugin — OI squeeze detection. Verbatim spec at §10.6:1801-1811."""

    name: ClassVar[str] = "oi_squeeze"
    version: ClassVar[str] = "2"

    def __init__(self, params: dict[str, Any]) -> None:
        # WG#4 — bool-rejection BEFORE int-check (isinstance(True, int) is True).
        lookback = params["lookback_candles"]
        if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
            msg = f"lookback_candles must be int >=1; got {lookback!r}"
            raise ValueError(msg)
        self.lookback_candles: int = lookback

        try:
            drop = Decimal(str(params["oi_drop_pct"]))
        except (InvalidOperation, ValueError) as exc:
            msg = f"oi_drop_pct must be numeric; got {params['oi_drop_pct']!r}"
            raise ValueError(msg) from exc
        if drop < 0:
            msg = f"oi_drop_pct must be >=0; got {drop}"
            raise ValueError(msg)
        self.oi_drop_pct: Decimal = drop

    def evaluate(self, ctx: RuleContext) -> RuleOutcome:
        history = ctx.feature_history.get(ctx.feature_ref, ())
        if len(history) < self.lookback_candles:
            return RuleOutcome(
                result=False,
                metadata={"reason": "history_insufficient", "have": len(history)},
            )

        # WG#3 — newest-window slice; reads last lookback_candles entries.
        window = history[-self.lookback_candles :]
        oldest_fv = window[0]
        newest_fv = window[-1]
        if oldest_fv.value_num is None or newest_fv.value_num is None:
            return RuleOutcome(
                result=False,
                metadata={"reason": "feature_not_numeric"},
            )
        oldest: Decimal = oldest_fv.value_num
        newest: Decimal = newest_fv.value_num
        if oldest == 0:
            return RuleOutcome(
                result=False,
                metadata={"reason": "oldest_oi_zero"},
            )
        # WG#3 Decimal end-to-end; Decimal(100) literal preserves type.
        drop_pct: Decimal = (oldest - newest) / oldest * Decimal(100)
        if drop_pct >= self.oi_drop_pct:
            return RuleOutcome(
                result=True,
                metadata={
                    "drop_pct": drop_pct,
                    "oldest_oi": oldest,
                    "newest_oi": newest,
                },
            )
        return RuleOutcome(
            result=False,
            metadata={
                "reason": "drop_below_threshold",
                "drop_pct": drop_pct,
                "threshold": self.oi_drop_pct,
            },
        )
