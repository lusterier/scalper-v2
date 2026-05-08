"""Replay-virtual clock for backtest CLI (T-507b OQ-D=C Belt-and-suspenders).

Stateful object advanced by ``paper.run_replay`` (per OHLC ``bucket_start``)
and signal publisher (per ``signal.received_at``). All components — paper
exchange, ``make_signal_handler``, ``make_per_bot_handler``,
``ExecutionDispatcher`` — receive ``now_fn=replay_clock.now`` so timestamps
stay consistent across replay (paper trades + scoring evaluations +
dispatched orders + reconcile flow).

Without ReplayClock the wall-clock ``now_fn`` produces today's timestamps
for backtest of historical data — ``paper_trades.closed_at`` = today,
while ``signal.received_at`` = weeks ago. Inconsistent semantic + breaks
``max_signal_age_seconds`` DB lookup window in
``services/strategy_engine/app/consumer.py:143`` (lookup window =
``now - max_signal_age_seconds``; with wall-clock now, the window misses
all historical signals).

Monotonic invariant: ``set(t)`` is no-op if ``t < current``. Defends
against out-of-order clock advancement bugs (replay sources should be
chronological by construction; the guard catches regressions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["ReplayClock"]


@dataclass
class ReplayClock:
    """Monotonic replay-virtual clock; latest set wins (must be >= current)."""

    initial: datetime
    _t: datetime = field(init=False)

    def __post_init__(self) -> None:
        self._t = self.initial

    def set(self, t: datetime) -> None:
        """Advance clock to ``t`` (no-op if ``t < current``; non-monotonic protection)."""
        if t > self._t:
            self._t = t

    def now(self) -> datetime:
        """Return current replay-virtual time."""
        return self._t
