"""ADR-0011 SL/TP-verification cluster — trailing-SL drift audit (T-536).

APScheduler-driven tick per ADR-0007 D1-D7 (4th execution-service scheduled
job — siblings: ``pnl_audit`` T-220b, ``equity_snapshot`` T-531,
``sl_watchdog`` T-534b2; shares the lifespan-owned ``AsyncIOScheduler``).
Final task of the SL/TP-verification cluster.

For every actively-trailing position (``position_state.sl_type == 'trail'``
with a ``best_price``), recompute the FSM's expected trail stop-loss via the
**reused** :func:`services.execution.app.lifecycle._compute_trail_sl_price`
(OQ-2=A — the same function the lifecycle FSM uses, so the audit is a true
exchange-vs-FSM-intent cross-check with structurally-zero two-impl math
drift; L-003) and compare it to the live Bybit ``Position.sl_price``
(T-534a). A relative divergence beyond ``drift_tolerance_pct`` ⇒ the FSM's
``set_trading_stop`` silently failed / partially applied, or the trail did
not keep pace with ``best_price`` ⇒ emit a ``trail_sl_drift_detected``
``trading_events`` audit row + WARN.

Emit-only / non-destructive (OQ-3=A — mirror T-535): the per-trade
lifecycle monitor remains the single authoritative trail writer and
re-asserts on its next tick. No close, no halt, no re-assert, no NATS
event (§0.8 — no consumer), no counter (stateless; contrast the H-028
``sl_watchdog`` consecutive-counter), no ``bus``.

Disjoint three-arm SL/TP seam (OQ-3=A — no cross-suppression; the arms
have different tick/site/trigger-basis and distinct root-cause
hypotheses, so a genuinely-wrong SL may legitimately raise more than one
— operator triages):

- ``exchange_sl is None`` (SL *removed* / naked, or paper whose
  ``get_positions`` returns ``sl_price=None`` per T-534a) → T-534b2 /
  H-028 watchdog domain (emergency-close). Skipped here.
- ``sl_type != 'trail'`` (protective / be) → no expected-trail to verify.
  Skipped here.
- exchange SL *modified* vs DB-recorded intent (exact) → T-535 / H-029
  (`sl_overwrite_detected`, FSM-site). Independent — runs in its own
  tick; not suppressed by/for T-536.
- exchange SL *drifts* vs freshly-recomputed-from-``best_price``
  (tolerance) → **T-536** (`trail_sl_drift_detected`, this).
- position flat / gone → T-221 reconcile. Skipped here.

NO new §20 H-NNN: ADR-0011 §"Anticipated hazards" reserved slots only for
H-027/H-028/H-029 (T-525/T-534/T-535); T-536 has no reserved slot and is
an emit-only diagnostic (no capital-safety enforcement — it neither
closes nor re-asserts). Per §0.8 anti-hypothetical, no speculative
catalog entry; behaviour is pinned by named tests only.

A failed/transient ``get_positions`` read is NEVER evidence of drift
(skip the bot this tick, no emit — uncertainty ≠ drift; the exception
taxonomy is the sibling ``set_trading_stop`` / T-535 5-tuple).

Gate-4 (REAL Decimal arithmetic — unlike T-534b2/T-535 comparison-only):
``drift = abs(exchange_sl - expected) / expected`` (28-digit Decimal,
ROUND_HALF_EVEN); ``expected <= 0`` is guarded before the division
(degenerate config only — Long ``best*(1-trail_pct) <= 0`` iff
``trail_pct >= 1``; Short ``best*(1+trail_pct) > 0`` always when
``best > 0``). Strict ``>`` to emit (boundary ``drift == tolerance`` ⇒
NOT emitted). No ``float()``.

Conn lifecycle (mirror ``sl_watchdog`` "bulk DB read up-front → release →
exchange I/O conn-free → re-acquire for writes"): Phase A one
``pool.acquire()`` for ``select_position_states_for_bots`` +
``select_trade_fsm_params`` per trailing trade; Phase B conn-free per-bot
``get_positions`` + drift compute; Phase C one ``pool.acquire()`` for the
emits. The pool conn is never held across a ``get_positions`` network
call.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.db.queries.execution import (
    insert_trading_event,
    select_position_states_for_bots,
    select_trade_fsm_params,
)
from packages.exchange.errors import (
    AuthError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
    UnknownState,
)

from .lifecycle import (
    _compute_trail_sl_price,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.core import BotId
    from packages.db.queries.execution import PositionStateRow
    from packages.exchange.protocols import ExchangeClient


__all__ = ["run_trail_audit_tick"]


async def run_trail_audit_tick(
    *,
    pool: asyncpg.Pool,
    adapters: dict[BotId, ExchangeClient],
    paper_bot_ids: frozenset[BotId],
    drift_tolerance_pct: Decimal,
    bound_logger: BoundLogger,
    now_fn: Callable[[], datetime],
) -> None:
    """One trail-SL-audit tick — flag trailing positions whose exchange SL
    has drifted beyond ``drift_tolerance_pct`` from the recomputed FSM trail.
    """
    tick_at = now_fn()
    bound_logger.info("trail_audit.tick_start", tick_at=tick_at.isoformat())

    # OQ-3=A — live/testnet only; paper skipped (H-031 + T-534a paper
    # sl_price=None double-safety). Deterministic sorted BotId keys avoid
    # `# type: ignore[index]` (mirror sl_watchdog).
    live_bot_ids = [b for b in sorted(adapters) if b not in paper_bot_ids]
    if not live_bot_ids:
        bound_logger.info("trail_audit.no_live_bots_no_op")
        return

    # Phase A — bulk DB read (ps rows + per-trailing-trade fsm params),
    # conn released at block exit BEFORE any exchange I/O.
    cands_by_bot: dict[str, list[tuple[PositionStateRow, Decimal, Decimal]]] = {}
    async with pool.acquire() as conn:
        ps_rows = await select_position_states_for_bots(
            conn,
            [str(b) for b in live_bot_ids],
        )
        for row in ps_rows:
            best_price = row.best_price
            if row.sl_type != "trail" or best_price is None:
                continue
            fsm = await select_trade_fsm_params(conn, row.trade_id)
            if fsm is None:
                continue
            cands_by_bot.setdefault(row.bot_id, []).append(
                (row, best_price, fsm["trail_pct"]),
            )

    if not cands_by_bot:
        return

    # Phase B — conn-free per-bot get_positions + drift compute.
    hits: list[tuple[str, str, dict[str, object]]] = []
    for bot_id in live_bot_ids:
        bot_key = str(bot_id)
        candidates = cands_by_bot.get(bot_key)
        if not candidates:
            continue
        adapter = adapters[bot_id]
        try:
            positions = await adapter.get_positions()
        except (
            AuthError,
            OrderRejected,
            NetworkTimeout,
            RateLimitError,
            UnknownState,
        ) as exc:
            # Uncertainty is NOT evidence of drift (sibling false-positive
            # guard; taxonomy verbatim = lifecycle BE/trail / T-535).
            bound_logger.error(
                "trail_audit.get_positions_failed",
                bot_id=bot_key,
                error=str(exc),
            )
            continue
        ex_by_symbol = {p.symbol: p for p in positions if p.size > 0}

        for ps, best_price, trail_pct in candidates:
            pos = ex_by_symbol.get(ps.symbol)
            if pos is None:
                continue  # flat / gone — T-221 reconcile domain
            exchange_sl = pos.sl_price
            if exchange_sl is None:
                continue  # SL removed — T-534b2/H-028 domain (+ paper)
            expected = _compute_trail_sl_price(ps.side, best_price, trail_pct)
            if expected <= Decimal("0"):
                continue  # degenerate config guard (div-by-zero); never under valid config
            drift = abs(exchange_sl - expected) / expected
            if drift <= drift_tolerance_pct:
                continue  # within tolerance — steady state
            payload: dict[str, object] = {
                "bot_id": bot_key,
                "symbol": ps.symbol,
                "trade_id": ps.trade_id,
                "side": ps.side,
                "best_price": str(best_price),
                "trail_pct": str(trail_pct),
                "expected_sl_price": str(expected),
                "observed_sl_price": str(exchange_sl),
                "drift_pct": str(drift),
                "tolerance_pct": str(drift_tolerance_pct),
            }
            hits.append(
                (
                    bot_key,
                    f"trail-audit-{bot_key}-{ps.symbol}-{ps.trade_id}",
                    payload,
                ),
            )

    if not hits:
        return

    # Phase C — re-acquire conn for the audit-row emits (emit-only,
    # non-destructive; FSM untouched).
    async with pool.acquire() as conn:
        for bot_key, correlation_id, payload in hits:
            await insert_trading_event(
                conn,
                occurred_at=now_fn(),
                bot_id=bot_key,
                correlation_id=correlation_id,
                event_type="trail_sl_drift_detected",
                payload=payload,
            )
            bound_logger.warning(
                "trail_audit.trail_sl_drift_detected",
                bot_id=bot_key,
                symbol=payload["symbol"],
                trade_id=payload["trade_id"],
                expected_sl_price=payload["expected_sl_price"],
                observed_sl_price=payload["observed_sl_price"],
                drift_pct=payload["drift_pct"],
                tolerance_pct=payload["tolerance_pct"],
            )
