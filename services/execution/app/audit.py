"""§9.5:1601-1605 + H-017 P&L audit loop (T-220b).

APScheduler-driven 5-min tick per ADR-0007 D1-D7. For each sub-account:

1. Snapshot ``cumulative_bybit`` via :meth:`ExchangeClient.get_closed_pnl_window`
   (T-220a; time-windowed Bybit ledger sum since ``now - window_seconds``).
2. Snapshot ``cumulative_db`` via :func:`select_realized_pnl_sum_for_bots_since`
   (T-220a; SUM trades.realized_pnl WHERE bot_id IN sub_account-bots AND closed_at >= since).
3. Compute ``delta = cumulative_bybit - cumulative_db``.
4. IF ``abs(delta) > divergence_threshold_usd`` → :func:`insert_trade_pnl_delta`
   (T-220a; UNIQUE constraint surfaces concurrent-run conflict per ADR-0007 D7).

Per H-017: cumulative-only attribution; NEVER back-correct trades.realized_pnl
(T-219 cumulative-delta close flow is source of truth per ADR-0006 + H-012).
Brief §9.5:1605 "updates trades.realized_pnl" superseded by H-017 (see ADR-0006
cross-reference addendum at this commit).

Job idempotency contract per ADR-0007 D7:

- Sub-threshold rerun: same delta < threshold → 0 rows written.
- Supra-threshold rerun: post-correction T-219 already wrote correct realized_pnl;
  next tick recomputes zero divergence → 0 rows.
- Concurrent-run protection: Migration 0009 UNIQUE (sub_account, audit_run_at)
  raises asyncpg.UniqueViolationError; caught + WARN + next tick.

Each sub-account failure (Bybit API error, SQL error, UNIQUE violation) is
logged + skipped; the loop continues to the next sub-account so one bad
sub-account does not drop audits for the others.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from asyncpg.exceptions import UniqueViolationError

from packages.db.queries.execution import (
    insert_trade_pnl_delta,
    select_realized_pnl_sum_for_bots_since,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.exchange.protocols import ExchangeClient


__all__ = ["run_pnl_audit_tick"]


async def run_pnl_audit_tick(
    *,
    pool: asyncpg.Pool,
    sub_account_to_adapter: dict[str, ExchangeClient],
    sub_account_to_bot_ids: dict[str, list[str]],
    window_seconds: int,
    divergence_threshold_usd: Decimal,
    bound_logger: BoundLogger,
    now_fn: Callable[[], datetime],
) -> None:
    """One audit tick — iterate all sub-accounts; flag divergences."""
    audit_run_at = now_fn()
    window_start = audit_run_at - timedelta(seconds=window_seconds)
    window_end = audit_run_at

    for sub_account, adapter in sub_account_to_adapter.items():
        try:
            cumulative_bybit = await adapter.get_closed_pnl_window(
                sub_account,
                since=window_start,
            )
        except Exception as exc:
            bound_logger.error(
                "audit.bybit_api_failed",
                sub_account=sub_account,
                error=str(exc),
            )
            continue

        bot_ids = sub_account_to_bot_ids.get(sub_account, [])
        try:
            async with pool.acquire() as conn:
                cumulative_db = await select_realized_pnl_sum_for_bots_since(
                    conn,
                    bot_ids=bot_ids,
                    since=window_start,
                )

                delta = cumulative_bybit - cumulative_db
                if abs(delta) <= divergence_threshold_usd:
                    bound_logger.debug(
                        "audit.no_divergence",
                        sub_account=sub_account,
                        cumulative_bybit=str(cumulative_bybit),
                        cumulative_db=str(cumulative_db),
                        delta=str(delta),
                    )
                    continue

                bound_logger.warning(
                    "audit.pnl_divergence",
                    sub_account=sub_account,
                    cumulative_bybit=str(cumulative_bybit),
                    cumulative_db=str(cumulative_db),
                    delta=str(delta),
                    threshold=str(divergence_threshold_usd),
                )
                try:
                    await insert_trade_pnl_delta(
                        conn,
                        sub_account=sub_account,
                        audit_run_at=audit_run_at,
                        window_start=window_start,
                        window_end=window_end,
                        cumulative_bybit=cumulative_bybit,
                        cumulative_db=cumulative_db,
                        delta=delta,
                    )
                except UniqueViolationError:
                    bound_logger.warning(
                        "audit.unique_violation_concurrent_run",
                        sub_account=sub_account,
                        audit_run_at=audit_run_at.isoformat(),
                    )
        except Exception as exc:
            bound_logger.error(
                "audit.db_failed",
                sub_account=sub_account,
                error=str(exc),
            )
            continue
