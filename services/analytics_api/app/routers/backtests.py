"""``/api/backtests/*`` endpoint group (T-407, BRIEF §9.6:1629 + §14.3:2063).

Three endpoints — list, trigger, detail — with the trigger endpoint
atomic-coupled to one ``audit_events`` row inside the same
``conn.transaction()`` per BRIEF §16.8:2261-2264 + §15.6:2182-2184
(mirror T-401b symbol_map.create + T-405 bot_config.apply 5-helper
patterns; T-407 uses 2-helper since there is no version-bump or
update-applied step).

Audit row mapping:

* POST → ``action='backtest_run.queued'``, ``before_state=None``,
         ``after_state=<dict of inserted row excluding config_yaml>``
         (size discipline mirror T-405 WG#10).

POST flow (verbatim 10-step per T-407 plan WG#1):

1. Pydantic validate body (max_lengths + date range order).
2. Parse YAML via ``load_bot_config_from_string`` MIMO tx
   — ValueError → 422.
3. Check ``parsed.bot_id == body.bot_id`` MIMO tx — mismatch → 422.
4. ``config_hash = sha256(body.config_yaml.encode("utf-8")).hexdigest()``
   (raw bytes, NO ``.strip()``, NO normalization — per WG#7 + T-405
   WG#8 mirror).
5. Resolve ``actor`` via ``_resolve_actor(request)`` + ``correlation_id``
   via ``_resolve_correlation_id(request)``.
6. Pre-check bot existence via :func:`select_bot_by_id` (CONCERN #4
   default — fail-fast 404 before opening tx).
7. ``started_at = now_fn()`` — single call, capture into local; same
   value used for both ``backtest_runs.started_at`` AND
   ``audit_events.occurred_at`` (atomic time-ordering invariant).
8. Open ``async with pool.acquire() as conn, conn.transaction():`` and
   run BOTH ``insert_backtest_run`` + ``insert_audit_event`` on the
   SAME conn handle.
9. AFTER tx commits: ``logger.info("backtest_run.queued", ...)`` —
   rollback path skips per WG#9 / T-401b WG#9 / T-405 WG#9.
10. Return 202 + BacktestRunResponse.
"""

from __future__ import annotations

import hashlib
from collections.abc import (
    Callable,  # noqa: TC003 — FastAPI inspects Annotated[Callable[...], Depends(get_now_fn)]
)
from dataclasses import asdict
from datetime import (
    datetime,  # noqa: TC003 — FastAPI inspects Annotated[datetime, ...] at runtime
)
from typing import Annotated, Any
from uuid import UUID  # noqa: TC003 — FastAPI inspects Path[UUID] at runtime

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from packages.core.types import (
    BacktestStatus,  # noqa: TC001 — FastAPI inspects Query[BacktestStatus]
)
from packages.db.queries.analytics import (
    BacktestRunRow,
    count_backtest_runs,
    insert_backtest_run,
    select_backtest_run_by_id,
    select_backtest_runs_paginated,
    select_bot_by_id,
)
from packages.db.queries.audit import insert_audit_event
from packages.scoring import load_bot_config_from_string

from ..deps import get_now_fn, get_pool
from ..models.backtests import (
    BacktestRunCreateRequest,
    BacktestRunListResponse,
    BacktestRunResponse,
)

__all__ = ["router"]


router = APIRouter(prefix="/api/backtests", tags=["backtests"])


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

_ENTITY_TYPE = "backtest_run"
_ACTION_QUEUED = "backtest_run.queued"


def _resolve_actor(request: Request) -> str:
    """Mirror T-401b ``_resolve_actor`` per WG#5 — ``lan:<source_ip>``."""
    if request.client is None:
        return "lan:unknown"
    return f"lan:{request.client.host}"


def _resolve_correlation_id(request: Request) -> str | None:
    """Mirror T-401b ``_resolve_correlation_id`` per WG#6 — empty / whitespace → None."""
    header_value = request.headers.get("X-Correlation-ID")
    if header_value and header_value.strip():
        return header_value
    return None


def _row_to_response(row: BacktestRunRow) -> BacktestRunResponse:
    return BacktestRunResponse(**asdict(row))


def _audit_state_dict(row: BacktestRunRow) -> dict[str, Any]:
    """Project backtest_runs row to audit JSONB EXCLUDING ``config_yaml`` (WG#10 mirror).

    Reduces audit_events.{before,after}_state JSONB bloat. Full YAML is
    retrievable from ``backtest_runs`` table by id. Resulting shape has
    11 keys (12 columns minus ``config_yaml``).
    """
    full = asdict(row)
    full.pop("config_yaml", None)
    return full


@router.get("/", response_model=BacktestRunListResponse)
async def list_backtest_runs(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    bot_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[
        BacktestStatus | None,
        Query(
            alias="status",
            description="Filter by run status (queued/running/completed/failed).",
        ),
    ] = None,
    from_at: Annotated[
        datetime | None,
        Query(alias="from", description="started_at >= from (ISO-8601, inclusive)."),
    ] = None,
    to_at: Annotated[
        datetime | None,
        Query(alias="to", description="started_at < to (ISO-8601, exclusive)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BacktestRunListResponse:
    """Paginated + filtered backtest_runs list (ORDER BY started_at DESC)."""
    async with pool.acquire() as conn:
        rows = await select_backtest_runs_paginated(
            conn,
            bot_id=bot_id,
            status=status_filter,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            offset=offset,
        )
        total = await count_backtest_runs(
            conn,
            bot_id=bot_id,
            status=status_filter,
            from_at=from_at,
            to_at=to_at,
        )
    return BacktestRunListResponse(
        runs=[_row_to_response(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/",
    response_model=BacktestRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_backtest_run(
    body: BacktestRunCreateRequest,
    request: Request,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    now_fn: Annotated[Callable[[], datetime], Depends(get_now_fn)],
) -> BacktestRunResponse:
    """Queue a new backtest run + atomic audit row write.

    F4: status='queued' at insert; F5+ worker compute + status transitions.
    Returns 202 Accepted (queued, no synchronous compute).
    """
    # Step 2: parse YAML MIMO tx — any parse/schema failure → 422.
    # Mirror T-405 configs.py apply path: yaml_loader raises ValueError on
    # schema problems but `yaml.safe_load` itself raises yaml.YAMLError on
    # tokenizer faults; KeyError fires when required `bot_id` key is absent.
    # Bare `Exception` catch keeps behaviour aligned with T-405 precedent.
    try:
        parsed = load_bot_config_from_string(body.config_yaml, plugin_registry=None)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"config_yaml parse failed: {exc!s}",
        ) from exc

    # Step 3: bot_id mismatch check MIMO tx → 422.
    if parsed.bot_id != body.bot_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(f"config_yaml bot_id={parsed.bot_id!r} != request bot_id={body.bot_id!r}"),
        )

    # Step 4: config_hash — raw bytes, NO strip, NO normalization (WG#7).
    config_hash = hashlib.sha256(body.config_yaml.encode("utf-8")).hexdigest()

    # Step 5: actor + correlation_id resolution.
    actor = _resolve_actor(request)
    correlation_id = _resolve_correlation_id(request)

    # Step 7: now_fn() — single call, captured into local; same value
    # used for both backtest_runs.started_at AND audit_events.occurred_at
    # (atomic time-ordering invariant per WG#8).
    started_at = now_fn()

    async with pool.acquire() as conn:
        # Step 6: WG#3 — bot existence pre-check inside acquire() but
        # OUTSIDE conn.transaction(). bots.id has no FK from backtest_runs
        # (intentional, mirrors audit_events no-FK convention); bot
        # existence check is fail-fast UX, not invariant — bots table has
        # no F4 DELETE path so the check-then-insert race window is
        # unreachable.
        bot_row = await select_bot_by_id(conn, body.bot_id)
        if bot_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"bot {body.bot_id!r} not found",
            )

        # Step 8: BOTH inserts on the SAME conn inside one tx.
        async with conn.transaction():
            inserted_row = await insert_backtest_run(
                conn,
                name=body.name,
                bot_id=body.bot_id,
                config_yaml=body.config_yaml,
                config_hash=config_hash,
                date_range_start=body.date_range_start,
                date_range_end=body.date_range_end,
                started_at=started_at,
                notes=body.notes,
            )
            await insert_audit_event(
                conn,
                occurred_at=started_at,
                actor=actor,
                action=_ACTION_QUEUED,
                entity_type=_ENTITY_TYPE,
                entity_id=str(inserted_row.id),
                before_state=None,
                after_state=_audit_state_dict(inserted_row),
                correlation_id=correlation_id,
            )

    # Step 9: log AFTER tx commits (rollback path skips per WG#9).
    request.app.state.logger.info(
        _ACTION_QUEUED,
        run_id=str(inserted_row.id),
        bot_id=body.bot_id,
        actor=actor,
        correlation_id=correlation_id,
    )

    # Step 10: 202 + response body.
    return _row_to_response(inserted_row)


@router.get("/{run_id}", response_model=BacktestRunResponse)
async def get_backtest_run(
    run_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> BacktestRunResponse:
    """Return one backtest_run row by UUID PK; 404 if missing.

    UUID Path coercion auto-422 on garbage (e.g. ``/api/backtests/not-a-uuid``).
    """
    async with pool.acquire() as conn:
        row = await select_backtest_run_by_id(conn, run_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"backtest run {run_id!s} not found",
        )
    return _row_to_response(row)
