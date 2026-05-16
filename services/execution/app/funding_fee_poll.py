"""T-532b — funding-fee poll loop (ADR-0011 account-balance/equity sub-cluster).

APScheduler-driven tick per ADR-0007 D1-D7 (sibling to the T-220b P&L audit
+ T-531 equity-snapshot jobs; shares the lifespan-owned
``AsyncIOScheduler``). Consumer leaf of the L-007 split T-532→{a,b}; the
foundation T-532a shipped ``ExchangeClient.get_funding_fees_window`` +
migration 0021 ``funding_fees`` + ``insert_funding_fee``.

For each sub-account, over the window ``[now - window_seconds, now]``:

1. Pull funding settlements once via
   :meth:`ExchangeClient.get_funding_fees_window` (T-532a; ``@idempotent``).
2. **Storage fan-out** — one ``funding_fees`` row per settlement *per bot*
   sharing that sub-account (``sub_account_to_bot_ids``) via
   :func:`insert_funding_fee`. Verbatim the T-531 ``equity_snapshot``
   per-bot fan-out precedent: a Bybit transaction-log SETTLEMENT row is
   sub-account-scoped (it IS the same account); fanning it per bot keeps
   each row distinct via the surrogate ``id`` (composite PK). This is a
   MONITORING time-series, NOT P&L-truth — financial truth is the T-220
   cumulative-delta audit (ADR-0006 H-017). A cumulative reader uses the
   ``funding_settlement_window`` event (step 3) or ``DISTINCT``s the rows,
   NEVER ``SUM(funding_fees)`` (overlapping windows → surrogate-id-distinct
   append-trail dup, the documented T-531 "near-dup monitoring row" class).
3. **Cumulative emit** — ONE ``funding_settlement_window`` ``trading_events``
   row per sub-account (operator OQ-B1=A emit-only / OQ-B2=A self-contained
   in this tick — NOT wired into ``run_pnl_audit_tick``, the H-017-sensitive
   audit loop is untouched). ``cumulative_funding`` is summed ONCE from the
   in-memory ``list[FundingFee]`` (NOT a ``SELECT SUM`` over the fanned
   rows → structurally no N-bot double-count). ``bot_id=None``: the
   cumulative funding is a sub-account attribution, NOT per-bot/per-trade
   (mirror the T-220 ``trade_pnl_deltas`` ``sub_account``-keying). Each
   event is a SELF-CONTAINED windowed snapshot — NOT additive across emits
   (verbatim the T-220 cumulative-snapshot semantic per ADR-0006: a reader
   reads the LATEST per sub-account, NEVER sums the emit series). This is
   the operator OQ-3=A SEPARATE cumulative funding term: H-017-clean,
   NEVER folded into ``trades.realized_pnl`` (T-219 close flow stays the
   realized-pnl source of truth per ADR-0006).

``insert_trading_event`` (``packages.db.queries.execution``) does an
INTERNAL ``json.dumps(payload)``; the execution-service registers NO
asyncpg jsonb codec → single-encode, correct (the L-011 double-encode trap
does NOT apply; ``lifecycle.py`` documents the same single-encode fact).
The payload here is MANUALLY JSON-native (Decimals pre-``str()``'d,
timestamps ``.isoformat()``'d, ints native, ``bot_id``/``correlation_id``
``None``) — zero datetime/UUID/Decimal *objects* in the dict — so L-013's
``_to_jsonable`` wrapper is **N/A by construction** (not omitted-in-error):
``insert_trading_event`` is reused UNMODIFIED.

``now_fn()`` is computed ONCE at tick start and shared by every row +
the window calculation (mirror ``equity_snapshot`` ``snapshot_at`` /
``audit.py`` ``audit_run_at``) — clean time-series alignment.

**Connection / transaction model (mechanically-required divergence from
the T-531 mirror — NOT a silent refactor):** the T-531 ``equity_snapshot``
tick does ``async with pool.acquire() as conn:`` *per insert* (one
write-kind per bot). T-532b has a per-sub-account dual-write GROUP (the
NxM ``insert_funding_fee`` fan-out + the 1 ``insert_trading_event`` emit),
so ONE ``pool.acquire()`` per sub-account wrapping the whole group is the
correct grouping (avoids NxM+1 acquire churn). **NO ``conn.transaction()``**
(deliberate): a partial state (some ``funding_fees`` rows + emit fails, or
vice-versa) is ACCEPTED monitoring degradation, NOT a correctness bug — it
SELF-HEALS on the next tick (the WINDOWED pull, ``window_seconds`` ≫ the
poll interval → consecutive windows OVERLAP → a missed/partial settlement
is re-pulled + re-emitted; each event is a self-contained snapshot, not
additive). §N3: both writers ``@non_idempotent`` append-only, no retry
(ADR-0007 D7) — consistent with the self-healing overlap.

Resilience mirrors ``equity_snapshot.py`` / ``audit.py`` two-tier
per-iteration handling (ADR-0007 D3 — one bad sub-account must not drop
the others):

- ``funding_fee_poll.fetch_failed`` — ``get_funding_fees_window`` raised;
  skip this sub-account, continue (analog ``equity_snapshot.fetch_failed``
  / ``audit.bybit_api_failed``).
- ``funding_fee_poll.persist_failed`` — a fan-out INSERT / the emit
  raised; skip this sub-account's remaining work, continue (analog
  ``equity_snapshot.persist_failed`` / ``audit.db_failed``).

Empty window (``get_funding_fees_window`` → ``[]``; the common case —
Bybit perps fund ~every 8h while the tick runs every interval): skip BOTH
(0 ``insert_funding_fee``, 0 ``insert_trading_event``) for that
sub-account — mirror ``audit.py`` sub-threshold "0 rows written" /
``equity_snapshot`` empty-bot_ids no-op. No empty-emit noise.

No new §20 H-NNN — emit-only diagnostic + append-only monitoring rows
(verbatim the T-534b2 / T-535 / T-536 emit-only posture; §0.8).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from packages.db.queries.execution import insert_trading_event
from packages.db.queries.funding import insert_funding_fee

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.exchange.protocols import ExchangeClient


__all__ = ["run_funding_fee_poll_tick"]


async def run_funding_fee_poll_tick(
    *,
    pool: asyncpg.Pool,
    sub_account_to_adapter: dict[str, ExchangeClient],
    sub_account_to_bot_ids: dict[str, list[str]],
    window_seconds: int,
    bound_logger: BoundLogger,
    now_fn: Callable[[], datetime],
) -> None:
    """One funding-fee poll tick — iterate all sub-accounts; store + emit."""
    tick_at = now_fn()
    window_start = tick_at - timedelta(seconds=window_seconds)
    bound_logger.info(
        "funding_fee_poll.tick_start",
        tick_at=tick_at.isoformat(),
        window_start=window_start.isoformat(),
    )

    for sub_account, adapter in sub_account_to_adapter.items():
        try:
            fees = await adapter.get_funding_fees_window(sub_account, window_start)
        except Exception as exc:
            bound_logger.error(
                "funding_fee_poll.fetch_failed",
                sub_account=sub_account,
                error=str(exc),
            )
            continue

        if not fees:
            # No settlements in the window (common — Bybit perps fund ~8h).
            # Skip BOTH: no empty-emit noise (mirror audit sub-threshold).
            continue

        try:
            # ONE acquire per sub-account wrapping the dual-write GROUP
            # (NxM fan-out + 1 emit). Deliberate divergence from the T-531
            # per-insert acquire — NOT a silent refactor (see module
            # docstring). NO conn.transaction(): partial failure is
            # accepted monitoring degradation, self-heals via the
            # overlapping windowed re-poll.
            async with pool.acquire() as conn:
                bot_ids = sub_account_to_bot_ids.get(sub_account, [])
                for fee in fees:
                    for bot_id in bot_ids:
                        await insert_funding_fee(
                            conn,
                            bot_id=bot_id,
                            symbol=fee.symbol,
                            settled_at=fee.settled_at,
                            funding=fee.funding,
                        )
                # Cumulative term summed ONCE from the in-memory list
                # (NOT a SELECT SUM over the N-fanned rows → no double
                # count). Self-contained windowed snapshot, NOT additive
                # across emits (T-220 cumulative-snapshot semantic).
                cumulative_funding = sum((f.funding for f in fees), start=Decimal("0"))
                await insert_trading_event(
                    conn,
                    occurred_at=tick_at,
                    bot_id=None,
                    correlation_id=None,
                    event_type="funding_settlement_window",
                    payload={
                        "sub_account": sub_account,
                        "window_start": window_start.isoformat(),
                        "window_end": tick_at.isoformat(),
                        "cumulative_funding": str(cumulative_funding),
                        "settlement_count": len(fees),
                    },
                )
            bound_logger.info(
                "funding_fee_poll.sub_account_recorded",
                sub_account=sub_account,
                settlement_count=len(fees),
                cumulative_funding=str(cumulative_funding),
            )
        except Exception as exc:
            bound_logger.error(
                "funding_fee_poll.persist_failed",
                sub_account=sub_account,
                error=str(exc),
            )
            continue
