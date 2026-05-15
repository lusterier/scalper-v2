"""§15.3:2161 + T-531 equity-snapshot loop.

APScheduler-driven tick per ADR-0007 D1-D7 (sibling to the T-220b P&L audit
job; shares the lifespan-owned ``AsyncIOScheduler``). For each sub-account:

1. Snapshot the account financial state once via
   :meth:`ExchangeClient.get_account_balance` (T-530; ``@idempotent`` read).
2. Fan the single result out to one row per bot sharing that sub-account
   (``sub_account_to_bot_ids``): :func:`insert_equity_snapshot` +
   ``virtual_balance{bot_id}`` Gauge set to ``total_equity``.

OQ-4=A: one balance fetch per sub-account, fanned out per-bot. Paper bots
each have their own sub-account (``sub_account == str(bot_id)``) → naturally
per-bot. Live bots sharing one sub-account get identical account-level
figures at the same ``snapshot_at`` (correct — it IS the same account; the
composite PK ``(snapshot_at, id)`` keeps each row distinct via the
surrogate ``id``).

``snapshot_at = now_fn()`` is computed ONCE at tick start and shared by
every row in the tick (mirror ``audit.py`` ``audit_run_at``) — clean
time-series alignment across bots.

Resilience mirrors ``audit.py`` two-tier per-iteration handling (ADR-0007
D3 philosophy — one bad sub-account/bot must not drop the others):

- ``equity_snapshot.fetch_failed`` — ``get_account_balance`` raised; skip
  this sub-account, continue (analog of ``audit.bybit_api_failed``).
- ``equity_snapshot.persist_failed`` — the per-bot INSERT / gauge raised;
  skip this bot, continue (analog of ``audit.db_failed``).

Idempotency contract per ADR-0007 D7: :func:`insert_equity_snapshot` is
``@non_idempotent`` append-only; a misfire re-fire writes at most one
near-dup monitoring row (surrogate-``id``-distinct). This is a monitoring
time-series, NOT P&L-truth — financial truth is the T-220 cumulative-delta
audit (ADR-0006). No retry.

Numeric boundaries (both intentional + documented, Gate-4):
(a) the 5 unbounded T-530 ``Decimal`` values land in ``NUMERIC(20,4)`` →
PG round-half-even to scale 4 at INSERT;
(b) the gauge is set from the **pre-persist in-memory** ``bal.total_equity``
(full T-530 precision, then ``float`` cast — prometheus_client requires
float), so gauge and row may diverge at the 5th+ decimal — both are
monitoring views, neither is P&L-truth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from packages.db.queries.equity import insert_equity_snapshot

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.exchange.protocols import ExchangeClient

    from .metrics import Metrics


__all__ = ["run_equity_snapshot_tick"]


async def run_equity_snapshot_tick(
    *,
    pool: asyncpg.Pool,
    sub_account_to_adapter: dict[str, ExchangeClient],
    sub_account_to_bot_ids: dict[str, list[str]],
    metrics: Metrics,
    bound_logger: BoundLogger,
    now_fn: Callable[[], datetime],
) -> None:
    """One equity-snapshot tick — iterate all sub-accounts; persist per bot."""
    snapshot_at = now_fn()
    bound_logger.info("equity_snapshot.tick_start", snapshot_at=snapshot_at.isoformat())

    for sub_account, adapter in sub_account_to_adapter.items():
        try:
            bal = await adapter.get_account_balance(sub_account)
        except Exception as exc:
            bound_logger.error(
                "equity_snapshot.fetch_failed",
                sub_account=sub_account,
                error=str(exc),
            )
            continue

        for bot_id in sub_account_to_bot_ids.get(sub_account, []):
            try:
                async with pool.acquire() as conn:
                    await insert_equity_snapshot(
                        conn,
                        bot_id=bot_id,
                        snapshot_at=snapshot_at,
                        wallet_balance=bal.wallet_balance,
                        available_balance=bal.available_balance,
                        total_equity=bal.total_equity,
                        margin_balance=bal.margin_balance,
                        unrealized_pnl=bal.unrealized_pnl,
                    )
                # Gauge from the pre-persist Decimal (full precision → float);
                # monitoring-only, NOT read by sizing/P&L (Gate-4 boundary b).
                metrics.virtual_balance.labels(bot_id=bot_id).set(
                    float(bal.total_equity),
                )
                bound_logger.info(
                    "equity_snapshot.bot_recorded",
                    bot_id=bot_id,
                    sub_account=sub_account,
                    total_equity=str(bal.total_equity),
                )
            except Exception as exc:
                bound_logger.error(
                    "equity_snapshot.persist_failed",
                    bot_id=bot_id,
                    sub_account=sub_account,
                    error=str(exc),
                )
                continue
