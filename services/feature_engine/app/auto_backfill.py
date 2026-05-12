"""Feature auto-backfill scheduler (T-518, BRIEF §9.3:1525-1528, ADR-0012).

On feature-engine lifespan startup, diff current ``indicators.yaml`` x symbols
registry against the ``feature_registry_seen`` NATS KV bucket (ADR-0012);
for each NEW ``feature_name``, fire-and-forget an async backfill task over
the configurable historical OHLC window (default 30d back per OQ-3=A
2026-05-12). On successful completion, mark the ``feature_name`` as seen
in the KV bucket so subsequent restarts skip it.

Per ADR-0012: NATS KV bucket ``feature_registry_seen`` is pre-provisioned
by ``infra/nats/bootstrap.sh`` (4th bucket per BRIEF §8.2 amendment).
NatsClient ``kv_get`` / ``kv_put`` API per
:mod:`packages.bus.client.NatsClient`.

Idempotency: backfill body uses ``INSERT ON CONFLICT DO UPDATE`` per
:func:`packages.db.queries.feature_engine.insert_feature` (T-110b).
Re-running yields identical rows. Marker write is delayed until full
success — partial completion (crash mid-window) leaves marker unset, so
next startup retries.

Math-validator scope (per CLAUDE.md gate-4): ``services/feature_engine/``
IS in the math-binding list. T-518 adds NO new Decimal arithmetic — it
delegates to existing :meth:`Feature.compute` + the verbatim Decimal→float
seam from :mod:`scripts.backfill_features._backfill_one_feature`. Per WG#1
plan-stage: math-validator is INVOKED post-brief-reviewer-SHIP and
expected to return ``VERIFIED — out of scope, no Decimal arithmetic added``.

L-013 codec note: ``kv_put(feature_registry_seen, key, value=now.isoformat().encode())``
is a pure-bytes KV write, NOT a JSONB write — L-013 ``_to_jsonable`` wrapper
convention does NOT apply. ``insert_feature`` writes via the verbatim
existing decimal-stringify seam (mirror :func:`scripts.backfill_features._backfill_one_feature`
lines 186-193) since feature-engine has the JSONB codec registered (lifespan
step 4 in :mod:`services.feature_engine.app.main`).
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from packages.db.queries.feature_engine import fetch_ohlc_range, insert_feature
from packages.features.intervals import INTERVAL_DELTA
from packages.features.types import OhlcCandle

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus.client import NatsClient
    from packages.features.protocols import Feature


__all__ = ["KV_BUCKET", "schedule_auto_backfills"]


# ADR-0012: 4th NATS KV bucket; pre-provisioned by infra/nats/bootstrap.sh.
KV_BUCKET = "feature_registry_seen"


async def schedule_auto_backfills(
    *,
    pool: asyncpg.Pool,
    bus: NatsClient,
    features_by_key: Mapping[tuple[str, str], list[tuple[str, Feature]]],
    window_days: int,
    source: str,
    logger: BoundLogger,
    background_tasks: set[asyncio.Task[None]],
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Detect NEW features (current registry minus seen-set) + schedule backfills.

    Returns the count of NEW ``feature_names`` scheduled (0 if all already
    seen). Each scheduled task is added to ``background_tasks`` for
    lifespan-shutdown cancellation; tasks self-remove on completion via
    ``add_done_callback``.

    Detection algorithm:

    1. Flatten ``features_by_key`` to ``[(symbol, interval, feature_name, feature), ...]``
       (each ``(symbol, interval)`` key contributes N entries).
    2. For each entry: ``kv_get(KV_BUCKET, feature_name)``.
       If ``None`` → NEW; else → already seen, skip.
    3. For each NEW: ``asyncio.create_task(_backfill_and_mark(...))``.

    Errors during detection (e.g., NATS bucket missing) propagate per
    BRIEF §0.4 fail-loud — caller (lifespan) terminates startup; operator
    reads ``infra/nats/bootstrap.sh`` log for guidance. Errors INSIDE
    individual backfill tasks are swallowed (per fire-and-forget contract;
    see :func:`_backfill_and_mark`).
    """
    new_count = 0
    for (symbol, interval), entries in features_by_key.items():
        for feature_name, feature in entries:
            seen = await bus.kv_get(KV_BUCKET, feature_name)
            if seen is not None:
                continue
            task = asyncio.create_task(
                _backfill_and_mark(
                    pool=pool,
                    bus=bus,
                    feature=feature,
                    feature_name=feature_name,
                    symbol=symbol,
                    interval=interval,
                    source=source,
                    window_days=window_days,
                    logger=logger,
                    now_fn=now_fn,
                ),
            )
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
            new_count += 1
    logger.info("auto_backfill_scheduled", new_features=new_count)
    return new_count


async def _backfill_and_mark(
    *,
    pool: asyncpg.Pool,
    bus: NatsClient,
    feature: Feature,
    feature_name: str,
    symbol: str,
    interval: str,
    source: str,
    window_days: int,
    logger: BoundLogger,
    now_fn: Callable[[], datetime],
) -> None:
    """Execute backfill for one feature; on success, mark seen in KV.

    Body mirrors :func:`scripts.backfill_features._backfill_one_feature`
    verbatim per WG#3 (Decimal→float seam preserves §5.3 + §N1). Empty
    OHLC range → marks seen anyway (per edge case #5: feature has been
    processed; no data is acceptable).

    Exception path (any: fetch error, compute error, insert error, KV
    failure during marker write) → log ``feature_auto_backfill_failed``
    at ERROR; do NOT call ``kv_put`` (marker stays unset → next startup
    retries); do NOT re-raise (fire-and-forget contract).
    """
    now = now_fn()
    from_dt = now - timedelta(days=window_days)
    to_dt = now
    try:
        async with pool.acquire() as conn:
            rows = await fetch_ohlc_range(
                conn,
                symbol=symbol,
                interval=interval,
                source=source,
                from_dt=from_dt,
                to_dt=to_dt,
            )
            buf: deque[OhlcCandle] = deque(maxlen=feature.warmup_candles)
            inserted = 0
            for row in rows:
                candle = OhlcCandle(
                    symbol=row[0],
                    interval=interval,
                    bucket_start=row[1],
                    open=row[2],
                    high=row[3],
                    low=row[4],
                    close=row[5],
                    volume=row[6],
                    source=row[7],
                )
                buf.append(candle)
                if len(buf) < feature.warmup_candles:
                    continue
                fv = feature.compute(tuple(buf))
                # Decimal→float seam (verbatim mirror scripts/backfill_features.py:186-193
                # per WG#3; preserves §5.3 + §N1; insert_feature codec dict-passthrough).
                value_num_wire = float(fv.value_num) if fv.value_num is not None else None
                value_bool_wire = fv.value_bool
                value_json_wire = (
                    {k: float(v) if isinstance(v, Decimal) else v for k, v in fv.value_json.items()}
                    if fv.value_json is not None
                    else None
                )
                computed_at = candle.bucket_start + INTERVAL_DELTA[interval]
                await insert_feature(
                    conn,
                    feature_name=feature_name,
                    symbol=symbol,
                    computed_at=computed_at,
                    value_num=value_num_wire,
                    value_bool=value_bool_wire,
                    value_json=value_json_wire,
                    source_version=feature.source_version,
                )
                inserted += 1
        if not rows:
            logger.info(
                "auto_backfill_no_data",
                feature=feature_name,
                symbol=symbol,
                interval=interval,
                from_dt=from_dt.isoformat(),
                to_dt=to_dt.isoformat(),
            )
        else:
            logger.info(
                "auto_backfill_complete",
                feature=feature_name,
                symbol=symbol,
                interval=interval,
                from_dt=from_dt.isoformat(),
                to_dt=to_dt.isoformat(),
                rows_total=len(rows),
                rows_inserted=inserted,
            )
        # Mark seen ONLY after full success (per edge cases #5 + #6).
        await bus.kv_put(KV_BUCKET, feature_name, now.isoformat().encode())
    except Exception as exc:
        logger.error(
            "feature_auto_backfill_failed",
            feature=feature_name,
            symbol=symbol,
            interval=interval,
            error=str(exc),
            error_type=type(exc).__name__,
        )
