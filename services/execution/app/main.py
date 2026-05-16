"""FastAPI app factory + lifespan for execution-service (§9.5, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

T-214 shipped the skeleton: pool + bus + ``/health`` / ``/ready`` /
``/metrics``. T-215 wired the adapter pool composition root + rate
limiter. T-216a (this revision) registers per-bot
``orders.requests.<bot_id>`` subscriptions at lifespan step 6 (one per
bot in ``app.state.adapters``); T-216a handler stops at NotImplementedError
post-fill_price (T-216b owns SL+TP+persist+events). Subscribe failure
at lifespan = service crash (mirror Settings startup-validation
invariant per WG#7); silent partial-failure (1/N bots subscribed) is
NOT acceptable.

Remaining F2 work:

* T-216b — extend placement.py with SL+TP+persist+events post-fill_price.
* T-217 — :class:`PositionLifecycle` FSM monitor task per trade.
* T-218 — :class:`DedupingConsumer` execution dispatcher.
* T-219 — cumulative-delta P&L close flow.
* T-220 — APScheduler-driven P&L audit loop.
* T-221 — post-restart reconciliation (H-020).

Composition split (mirrors T-100 / T-109):

* **Synchronous primitives** (Settings, structlog logger, Prometheus
  registry) instantiated in :func:`create_app` body and attached to
  ``app.state`` immediately so dependency providers in :mod:`.deps`
  see them before lifespan runs.
* **Asynchronous resources** (asyncpg :class:`~asyncpg.Pool`,
  :class:`packages.bus.NatsClient`) live in the lifespan and attach
  inside the ``async with`` block.

Lifespan order (T-216a + T-218a — 8 steps):

1. ``pool = await create_pool(database_url, application_name="execution-service")``.
2. ``bus = NatsClient(...); await bus.connect()``.
3. ``rate_limiter = SharedRateLimiter(bus=bus, **rate_limit_kwargs)`` — per ADR-0003.
4. ``adapter_pool = await build_adapter_pool(...)`` — reads active bots,
   per-bot constructs adapter, spawns ws/paper tasks (T-215; H-022, ADR-0004).
5. **T-216a**: per-bot ``orders.requests.<bot_id>`` subscription loop —
   ``for bot_id, adapter in adapter_pool.adapters.items(): await
   bus.subscribe(subject_for_orders_request(bot_id), make_per_bot_handler(...))``.
   Subscribe failure at lifespan = service crash (WG#7 fail-fast).
6. **T-218a**: per-bot ``ExecutionDispatcher`` task — one
   ``asyncio.create_task(run_dispatcher_for_bot(...))`` per
   ``(bot_id, adapter)``. Each task pumps ``adapter.stream_executions()``
   into a ``DedupingConsumer[ExecutionEvent]`` keyed on ``exchange_exec_id``
   (H-009 ring; capacity from ``Settings.dispatch_dedup_capacity``).
7. State attach: pool / bus / rate_limiter / adapters / ws_tasks /
   paper_consumer_tasks / dispatcher_tasks → ``app.state``.

Shutdown order (reverse, load-bearing):

8. ``await bus.close()`` — drains tracked subscriptions (incl. per-bot
   ``orders.requests.<bot_id>``), closes NATS connection. **Must run BEFORE**
   ``adapter.close`` / ``pool.close`` so any in-flight bus publish that
   touches downstream components finishes against open infra.
9. Cancel + gather ``dispatcher_tasks`` — they consume from
   ``adapter.stream_executions()``; cancelling them BEFORE adapter
   prevents mid-iter raises (graceful stop signal via CancelledError).
10. Cancel + gather ws_tasks + paper_consumer_tasks (drain backgrounds).
11. ``await adapter.close()`` per bot (Bybit closes ws + httpx; Paper no-op).
12. ``await pool.close()`` — releases asyncpg connections.

T-216 publish-after-persist contract (T-200 Q2) inherits this order:
the pool stays open until the bus has fully drained, so the publish
side of `publish-after-persist` cannot race against pool teardown.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent  # type: ignore[import-untyped]
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from fastapi import FastAPI

from packages.bus import NatsClient
from packages.bus.schemas.orders import subject_for_orders_request
from packages.db import create_pool
from packages.exchange.rate_limiter import SharedRateLimiter
from packages.observability import (
    configure,
    get_logger,
    make_metrics_asgi_app,
    make_registry,
)

from .audit import run_pnl_audit_tick
from .config import Settings
from .dispatcher import ExecutionDispatcher, run_dispatcher_for_bot
from .equity_snapshot import run_equity_snapshot_tick
from .health import router as health_router
from .metrics import build_execution_metrics
from .placement import make_per_bot_handler
from .pool import build_adapter_pool
from .restart import reconcile_on_startup
from .shadow_rejected_replay import resume_active_observations_on_startup
from .shadow_rejected_worker import ShadowRejectedWorker
from .shadow_replay import resume_active_variants_on_startup
from .shadow_worker import ShadowWorker
from .sl_watchdog import run_sl_watchdog_tick
from .trail_audit import run_trail_audit_tick

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from packages.exchange.protocols import ExchangeClient

__all__ = ["create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` for execution-service."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    configure(level=settings.log_level)
    logger = get_logger(settings.service_name, "system")
    registry_metrics = make_registry()
    exec_metrics = build_execution_metrics(registry_metrics)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root for async resources. See module docstring."""
        # 1. asyncpg pool.
        pool = await create_pool(
            settings.database_url,
            application_name=settings.service_name,
        )
        # 2. NATS bus.
        bus = NatsClient(
            servers=[settings.nats_url],
            name=settings.service_name,
            logger=logger,
        )
        await bus.connect()

        # 3. SharedRateLimiter (per ADR-0003).
        rate_limiter = SharedRateLimiter(
            bus=bus,
            orders_rate=settings.rate_limit_orders_rate,
            orders_capacity=settings.rate_limit_orders_capacity,
            positions_rate=settings.rate_limit_positions_rate,
            positions_capacity=settings.rate_limit_positions_capacity,
            ip_global_rate=settings.rate_limit_ip_global_rate,
            ip_global_capacity=settings.rate_limit_ip_global_capacity,
            pause_ms=settings.rate_limit_pause_ms,
        )

        # 4. Adapter pool composition (active bots → per-bot adapter + tasks).
        adapter_pool = await build_adapter_pool(
            pool=pool,
            bus=bus,
            rate_limiter=rate_limiter,
            settings=settings,
            bound_logger=logger,
        )

        # T-217a — single shared registry across all bots; trade_id is BIGSERIAL global-unique.
        position_lifecycle_tasks: dict[int, asyncio.Task[None]] = {}

        # T-527b2b / OQ-7: resolve a bot adapter's sub_account (Bybit
        # ``_sub_account`` / paper ``_bot_id`` synonym, Decision #8). Defined
        # here (before the make_per_bot_handler loop — its first use) so the
        # T-219 dispatcher loop (below) + T-220/equity uses still resolve.
        def _resolve_sub_account(adapter_obj: object) -> str:
            sub_account = getattr(adapter_obj, "_sub_account", None)
            if sub_account is None:
                bot_id_attr = getattr(adapter_obj, "_bot_id", None)
                if bot_id_attr is None:
                    msg = "adapter has no _sub_account or _bot_id attribute"
                    raise RuntimeError(msg)
                return str(bot_id_attr)
            return str(sub_account)

        # 5. Per-bot orders.requests.<bot_id> subscription (T-216a + T-216b2 wrap).
        # The handler returned by make_per_bot_handler is the OrderRequestDedupConsumer.consume
        # bound method (H-009 per-bot ring; capacity from Settings). Subscribe failure
        # here crashes the lifespan (WG#7 fail-fast).
        for bot_id, adapter in adapter_pool.adapters.items():
            handler = make_per_bot_handler(
                bot_id=bot_id,
                sub_account=_resolve_sub_account(adapter),
                metrics=exec_metrics,
                adapter=adapter,
                bus=bus,
                logger=logger,
                pool=pool,
                dedup_capacity=settings.execution_orders_dedup_capacity,
                now_fn=lambda: datetime.now(UTC),
                fill_price_retry_attempts=settings.execution_fill_price_retry_attempts,
                fill_price_retry_backoff_s=settings.execution_fill_price_retry_backoff_s,
                position_lifecycle_tasks=position_lifecycle_tasks,
                position_poll_interval_s=settings.position_poll_interval_s,
                position_poll_stale_ticks=settings.position_poll_stale_ticks,
            )
            await bus.subscribe(subject_for_orders_request(bot_id), handler)

        # 5.5. T-221 — post-restart reconciliation per H-020 + H-026.
        # Runs BEFORE dispatchers start so monitor tasks for matching trades are
        # already in position_lifecycle_tasks registry by the time fills arrive.
        await reconcile_on_startup(
            pool=pool,
            bus=bus,
            adapters=adapter_pool.adapters,
            position_lifecycle_tasks=position_lifecycle_tasks,
            race_window_seconds=settings.execution_reconcile_race_window_seconds,
            position_poll_interval_s=settings.position_poll_interval_s,
            position_poll_stale_ticks=settings.position_poll_stale_ticks,
            bound_logger=logger,
            now_fn=lambda: datetime.now(UTC),
        )

        # T-219 — per-sub-account asyncio.Lock registry per ADR-0006 D4.
        # Multiple bots may share a sub_account (per ADR-0004 H-022 family);
        # they share the same Lock instance to serialize closed-pnl snapshot pairs.
        # Lock is keyed on sub_account string (NOT bot_id). Paper adapters use
        # bot_id-as-sub_account synonym per Decision #8.
        closed_pnl_locks: dict[str, asyncio.Lock] = {}

        # 6. Per-bot ExecutionDispatcher tasks (T-218a; H-009 per-bot dedup ring).
        # T-218c fix(paper-dispatcher-skip) / H-031: skip dispatcher for paper
        # bots — PaperExchange writes paper_* tables and emits ExecutionEvent
        # for both open + close (paper/adapter.py:820/930/1185); the LIVE
        # ExecutionDispatcher tries to look up events in live orders/trades/
        # position_state which paper bots don't write to → unattributable_fill
        # RuntimeError → run_dispatcher_for_bot re-raises → task dies silently.
        # Paper bots' orders.requests subscriber (line 166) STAYS — placement
        # handler routes paper orders via PaperExchange.place_market_order;
        # dispatcher's role is irrelevant for paper.
        dispatcher_tasks: list[asyncio.Task[None]] = []
        for bot_id, adapter in adapter_pool.adapters.items():
            if bot_id in adapter_pool.paper_bot_ids:
                continue
            sub_account = _resolve_sub_account(adapter)
            if sub_account not in closed_pnl_locks:
                closed_pnl_locks[sub_account] = asyncio.Lock()
            dispatcher = ExecutionDispatcher(
                bot_id=bot_id,
                pool=pool,
                bus=bus,
                bound_logger=logger,
                capacity=settings.dispatch_dedup_capacity,
                now_fn=lambda: datetime.now(UTC),
                adapter=adapter,
                sub_account=sub_account,
                closed_pnl_lock=closed_pnl_locks[sub_account],
                closed_pnl_post_close_sleep_s=settings.execution_closed_pnl_post_close_sleep_s,
            )
            task = asyncio.create_task(
                run_dispatcher_for_bot(
                    adapter=adapter,
                    dispatcher=dispatcher,
                    bound_logger=logger,
                ),
                name=f"dispatcher_{bot_id}",
            )
            dispatcher_tasks.append(task)

        # 6.5. T-511b2 / ADR-0010 — ShadowWorker construction + start.
        # Always-on (data-driven by per-bot bot_config.shadow.enabled YAML;
        # bots without shadow emit no shadow.start.<bot_id> events → worker
        # has zero work). Subscribes shadow.start.> + trade.closed.> wildcards.
        # WG#9 / config.py: shadow_seed_balance_usd + shadow_fee_rate are
        # service-wide (NOT per-bot) — shadow simulation isolated from paper-
        # bot fee config. fixed_pct/0 slippage minimizes variant-vs-parent
        # divergence (variant inherits parent's already-applied entry slippage
        # via seed_open_state per T-511a).
        shadow_worker = ShadowWorker(
            bus=bus,
            pool=pool,
            seed_balance=settings.shadow_seed_balance_usd,
            slippage_model="fixed_pct",
            slippage_params={"fixed_slippage_pct": Decimal("0")},
            fee_rate=settings.shadow_fee_rate,
            clock=lambda: datetime.now(UTC),
        )
        await shadow_worker.start()

        # 6.7. T-512a / BRIEF §13.4 / H-023 — shadow variant restart-recovery
        # via OHLC replay. AFTER shadow_worker.start() (cancel-hook subscription
        # `trade.closed.>` is live before resume tasks register into _active_tasks)
        # AND AFTER reconcile_on_startup (live trade re-hydration done; SELECT
        # trades.status returns post-reconcile state). BEFORE scheduler.start()
        # (audit doesn't fire mid-replay).
        await resume_active_variants_on_startup(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_worker=shadow_worker,
            clock=lambda: datetime.now(UTC),
        )

        # 6.8. T-513a / BRIEF §13.5 — rejected-signal 60-min observation FSM.
        # Always-on per BRIEF §13.5 ("Separate from variants"); operational
        # kill-switch via `Settings.shadow_rejected_enabled`. Constructed
        # AFTER shadow_worker + resume_active_variants so subscriptions
        # are live before NATS messages start consuming.
        shadow_rejected_worker: ShadowRejectedWorker | None = None
        if settings.shadow_rejected_enabled:
            shadow_rejected_worker = ShadowRejectedWorker(
                bus=bus,
                pool=pool,
                observation_minutes=settings.shadow_rejected_observation_minutes,
                clock=lambda: datetime.now(UTC),
            )
            await shadow_rejected_worker.start()

            # 6.9. T-513b1 / BRIEF §13.5 + §20 H-023 — rejected observation
            # restart-recovery via OHLC replay. Mirror T-512a wire pattern:
            # AFTER `shadow_rejected_worker.start()` (subscriptions ready before
            # resume tasks register; functionally agnostic since rejected obs
            # have no cancel-hook subscribe — operator-symmetry rationale per
            # plan OQ-4=A 2026-05-08). BEFORE `scheduler.start()` (audit
            # doesn't fire mid-replay).
            await resume_active_observations_on_startup(
                pool=pool,
                bus=bus,
                settings=settings,
                shadow_rejected_worker=shadow_rejected_worker,
                clock=lambda: datetime.now(UTC),
            )

        # 7. T-220b — APScheduler-driven P&L audit (per ADR-0007 D1-D7).
        # Sub-account → adapter + bot_ids reverse mapping for audit job composition.
        sub_account_to_adapter: dict[str, object] = {}
        sub_account_to_bot_ids: dict[str, list[str]] = {}
        for bot_id, adapter in adapter_pool.adapters.items():
            sub = _resolve_sub_account(adapter)
            sub_account_to_adapter.setdefault(sub, adapter)
            sub_account_to_bot_ids.setdefault(sub, []).append(str(bot_id))

        scheduler = AsyncIOScheduler(timezone=UTC)

        def _on_job_error(event: JobExecutionEvent) -> None:
            logger.error(
                "scheduler.job_failed",
                job_id=event.job_id,
                scheduled_run_time=event.scheduled_run_time.isoformat(),
                traceback=event.traceback,
            )

        scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

        async def _audit_job() -> None:
            audit_logger = logger.bind(component="audit")
            await run_pnl_audit_tick(
                pool=pool,
                sub_account_to_adapter=sub_account_to_adapter,  # type: ignore[arg-type]
                sub_account_to_bot_ids=sub_account_to_bot_ids,
                window_seconds=settings.execution_audit_window_seconds,
                divergence_threshold_usd=settings.execution_audit_divergence_threshold_usd,
                bound_logger=audit_logger,
                now_fn=lambda: datetime.now(UTC),
            )

        scheduler.add_job(
            _audit_job,
            trigger="interval",
            seconds=settings.execution_audit_tick_interval_seconds,
            id="pnl_audit",
            misfire_grace_time=120,
        )

        async def _equity_snapshot_job() -> None:
            equity_logger = logger.bind(component="equity_snapshot")
            await run_equity_snapshot_tick(
                pool=pool,
                # L-023: reflow-stable cast (string forward-ref, runtime
                # no-op) — NOT an inline `# type: ignore[arg-type]` like the
                # _audit_job site above; that form migrates off its kwarg
                # under ruff-format reflow (bit T-525a2/b).
                sub_account_to_adapter=cast(
                    "dict[str, ExchangeClient]",
                    sub_account_to_adapter,
                ),
                sub_account_to_bot_ids=sub_account_to_bot_ids,
                metrics=exec_metrics,
                bound_logger=equity_logger,
                now_fn=lambda: datetime.now(UTC),
            )

        scheduler.add_job(
            _equity_snapshot_job,
            trigger="interval",
            seconds=settings.execution_equity_snapshot_interval_seconds,
            id="equity_snapshot",
            misfire_grace_time=120,
        )

        # T-534b2 — SL watchdog (H-028). In-memory consecutive (bot,symbol)
        # miss-counter is lifespan-owned (§N6 — no global; DI into the
        # closure, on app.state for test introspection mirror exec_metrics).
        sl_miss_counters: dict[tuple[str, str], int] = {}

        async def _sl_watchdog_job() -> None:
            sl_watchdog_logger = logger.bind(component="sl_watchdog")
            await run_sl_watchdog_tick(
                pool=pool,
                # adapter_pool.adapters is already dict[BotId, ExchangeClient]
                # (pool.py:102) → no L-023 cast / no `# type: ignore` needed
                # here (contrast _equity_snapshot_job, whose
                # sub_account_to_adapter: dict[str, object] forced the cast).
                adapters=adapter_pool.adapters,
                paper_bot_ids=adapter_pool.paper_bot_ids,
                bus=bus,
                sl_miss_counters=sl_miss_counters,
                missing_threshold_ticks=settings.execution_sl_watchdog_missing_threshold_ticks,
                bound_logger=sl_watchdog_logger,
                now_fn=lambda: datetime.now(UTC),
            )

        scheduler.add_job(
            _sl_watchdog_job,
            trigger="interval",
            seconds=settings.execution_sl_watchdog_tick_interval_seconds,
            id="sl_watchdog",
            misfire_grace_time=120,
        )

        # T-536 — trailing SL audit (drift detection). Stateless emit-only
        # (no counter, no bus, no app.state — contrast _sl_watchdog_job);
        # adapter_pool.adapters already dict[BotId, ExchangeClient] → no cast.
        async def _trail_audit_job() -> None:
            trail_audit_logger = logger.bind(component="trail_audit")
            await run_trail_audit_tick(
                pool=pool,
                adapters=adapter_pool.adapters,
                paper_bot_ids=adapter_pool.paper_bot_ids,
                drift_tolerance_pct=settings.execution_trail_audit_drift_tolerance_pct,
                bound_logger=trail_audit_logger,
                now_fn=lambda: datetime.now(UTC),
            )

        scheduler.add_job(
            _trail_audit_job,
            trigger="interval",
            seconds=settings.execution_trail_audit_tick_interval_seconds,
            id="trail_audit",
            misfire_grace_time=120,
        )
        scheduler.start()

        # 8. State attach.
        app.state.pool = pool
        app.state.bus = bus
        app.state.rate_limiter = rate_limiter
        app.state.adapters = adapter_pool.adapters
        app.state.ws_tasks = adapter_pool.ws_tasks
        app.state.paper_consumer_tasks = adapter_pool.paper_consumer_tasks
        app.state.dispatcher_tasks = dispatcher_tasks
        app.state.position_lifecycle_tasks = position_lifecycle_tasks
        app.state.closed_pnl_locks = closed_pnl_locks
        app.state.scheduler = scheduler
        app.state.exec_metrics = exec_metrics
        app.state.sl_miss_counters = sl_miss_counters
        app.state.shadow_worker = shadow_worker
        app.state.shadow_rejected_worker = shadow_rejected_worker

        logger.info(
            "service_started",
            http_port=settings.http_port,
            bots_loaded=len(adapter_pool.adapters),
        )
        try:
            yield
        finally:
            # Reverse shutdown:
            #   bus.close → shadow_worker.stop (T-511b2 H-016 cancel hook
            #   alongside lifecycle tasks; subscriptions drained by bus.close
            #   so finalizer bus_unsubscribe is no-op) → position_lifecycle_tasks
            #   cancel (T-217a; monitors write monitor-only fields, dispatcher
            #   writes fill-flow fields; column-disjoint UPDATEs are MVCC-safe
            #   but ordering keeps shutdown's audit log monotonic) →
            #   dispatcher_tasks cancel (consume from adapter.stream_*; must
            #   drain before adapter.close pulls the WS) → ws_tasks cancel →
            #   adapter.close → pool.close.
            await bus.close()
            if shadow_rejected_worker is not None:
                await shadow_rejected_worker.stop()
            await shadow_worker.stop()
            for task in position_lifecycle_tasks.values():
                task.cancel()
            if position_lifecycle_tasks:
                await asyncio.gather(*position_lifecycle_tasks.values(), return_exceptions=True)
            for task in dispatcher_tasks:
                task.cancel()
            if dispatcher_tasks:
                await asyncio.gather(*dispatcher_tasks, return_exceptions=True)
            background_tasks = [
                *adapter_pool.ws_tasks,
                *adapter_pool.paper_consumer_tasks,
            ]
            for task in background_tasks:
                task.cancel()
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            # T-220b — scheduler shutdown(wait=True) BEFORE adapter.close + pool.close
            # so in-flight audit job's adapter REST call + DB query can finish per
            # ADR-0007 D6.
            scheduler.shutdown(wait=True)
            for adapter in adapter_pool.adapters.values():
                await adapter.close()
            await pool.close()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

    app.state.settings = settings
    app.state.logger = logger

    app.include_router(health_router)
    app.mount("/metrics", make_metrics_asgi_app(registry_metrics))
    return app
