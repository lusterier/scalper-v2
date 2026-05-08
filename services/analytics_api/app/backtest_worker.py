"""T-509: backtest worker — polls backtest_runs queue + dispatches to T-507b CLI.

Per backlog OQ-1=A: in-process in analytics-api lifespan (NOT separate
``services/backtest-worker/`` service). Per T-509-plan OQ-1=A: import-call
dispatch into refactored T-507b ``main()`` (NOT subprocess invocation).
Per T-509-plan OQ-2=A: atomic ``UPDATE...RETURNING + SKIP LOCKED`` claim
semantic (race-safe for future multi-worker; SKIP LOCKED is no-op for
single-worker today).

Refactored T-507b ``main()`` accepts optional ``args.run_id`` — if set,
skips ``insert_backtest_run`` + uses existing row (worker already
CLAIMED + transitioned to 'running' atomically at claim time).

L-013 BLOCKER #1 fix: worker outer ``dispatch_failed`` handler passes
``codec_registered=True`` to ``update_backtest_run_completion`` so helper
binds dict directly (analytics-api pool registers
``_register_jsonb_codec`` at ``services/analytics_api/app/main.py:121``;
text-mode + codec would double-encode per L-011 regression class).
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packages.core.types import BacktestStatus
from packages.db.queries.analytics import (
    claim_next_backtest_run,
    update_backtest_run_completion,
)

if TYPE_CHECKING:
    import asyncpg
    from structlog.stdlib import BoundLogger

    from services.analytics_api.app.config import Settings


__all__ = ["run_backtest_worker_loop"]


async def run_backtest_worker_loop(
    *,
    pool: asyncpg.Pool,
    settings: Settings,
    logger: BoundLogger,
) -> None:
    """T-509 worker loop: poll queue → claim → dispatch → loop.

    Loop body:

    1. Acquire pool conn → ``claim_next_backtest_run`` (atomic
       ``UPDATE...RETURNING + SKIP LOCKED``).
    2. If row claimed: dispatch to T-507b ``main()`` with claimed
       ``run_id``.
    3. If no row: sleep ``settings.backtest_worker_poll_interval_s``.
    4. On ``asyncio.CancelledError`` (lifespan shutdown): break out of
       loop.

    Worker's outer ``except`` is defensive ONLY for dispatch-layer bugs
    (e.g., bad args mapping). T-507b internally writes status='failed' on
    its own exception path through ITS OWN codec-clean CLI pool; the
    worker outer handler passes ``codec_registered=True`` to honor L-013
    codec-state-immune convention against analytics-api pool's registered
    JSONB codec.
    """
    logger.info(
        "backtest.worker.started",
        poll_interval_s=settings.backtest_worker_poll_interval_s,
    )
    try:
        while True:
            run_row = None
            async with pool.acquire() as conn:
                run_row = await claim_next_backtest_run(
                    conn,
                    started_at=datetime.now(UTC),
                )
            if run_row is None:
                await asyncio.sleep(settings.backtest_worker_poll_interval_s)
                continue
            logger.info(
                "backtest.worker.claimed",
                run_id=str(run_row.id),
                bot_id=run_row.bot_id,
            )
            try:
                await _dispatch_to_t507b(run_row, settings, logger)
            except Exception as exc:
                logger.error(
                    "backtest.worker.dispatch_failed",
                    run_id=str(run_row.id),
                    error=str(exc),
                )
                async with pool.acquire() as conn:
                    await update_backtest_run_completion(
                        conn,
                        run_id=run_row.id,
                        status=BacktestStatus.FAILED,
                        summary={
                            "error": f"worker dispatch failed: {exc!s}"[:500],
                        },
                        finished_at=datetime.now(UTC),
                        codec_registered=True,
                    )
    except asyncio.CancelledError:
        logger.info("backtest.worker.cancelled")
        raise


async def _dispatch_to_t507b(
    run_row: Any,
    settings: Settings,
    logger: BoundLogger,
) -> None:
    """T-509 OQ-1=A: import-call dispatch to T-507b main() with external run_id.

    Constructs argparse.Namespace from ``run_row`` fields. Writes
    ``run_row.config_yaml`` to a tmpfile because T-507b
    ``_load_bot_config_with_overrides`` expects ``config_path: Path``.
    Tmpfile cleaned up in ``finally`` regardless of dispatch outcome.

    ``plugin_registry_path=None`` mirrors T-407 POST endpoint at
    ``services/analytics_api/app/routers/backtests.py:191``; plugin-using
    YAMLs fail fast in T-507b with operator-actionable error.
    """
    from scripts.backtest import main as t507b_main

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        delete=False,
    ) as tmp:
        tmp.write(run_row.config_yaml)
        tmp_path = Path(tmp.name)
    try:
        args = argparse.Namespace(
            bot=run_row.bot_id,
            from_at=run_row.date_range_start,
            to_at=run_row.date_range_end,
            config_path=tmp_path,
            overrides=[],
            pace="max",
            source="binance",
            plugin_registry_path=None,
            db_url=settings.database_url,
            name=None,
            notes=None,
            compare=None,
            run_id=run_row.id,
        )
        rc = await t507b_main(args)
        logger.info(
            "backtest.worker.completed",
            run_id=str(run_row.id),
            return_code=rc,
        )
    finally:
        tmp_path.unlink(missing_ok=True)  # noqa: ASYNC240 — sync I/O on tmpfile cleanup is fine; no event-loop blocking risk
