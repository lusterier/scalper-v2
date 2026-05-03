"""``/api/symbol-map/*`` admin CRUD endpoints (T-401b, BRIEF §9.6:1632).

Five endpoints — list, get, create, update, delete — with each write
mutation atomic-coupled to one ``audit_events`` row inside the same
``conn.transaction()`` per BRIEF §16.8:2261-2264 + §15.6:2182-2184.

Audit row mapping:
- POST  → ``action='symbol_map.create'``, ``before_state=None``,
          ``after_state=<dict of inserted row>``
- PUT   → ``action='symbol_map.update'``, ``before_state=<pre-update>``,
          ``after_state=<post-update>``
- DELETE→ ``action='symbol_map.delete'``, ``before_state=<pre-delete>``,
          ``after_state=None``

T-401b is the **first FastAPI router in the repo to use** ``pool.acquire()
+ conn.transaction()`` for multi-step writes (mirror
``services/execution/app/placement.py:302-317`` shape verbatim).

WG#1 (T-401b plan-reviewer 2026-05-03): ``ExchangeSource`` StrEnum
(canonical from ``packages.core.types``) used end-to-end — no Literal
duplication.
WG#3: JSONB codec for audit_events round-trip already shipped in
T-401a's :func:`services.analytics_api.app.main._register_jsonb_codec`;
this module assumes it's active.
WG#4: PUT/DELETE pre-read happens BEFORE ``conn.transaction()`` opens —
404 short-circuits without entering an empty tx.
WG#9: ``symbol_map.created`` / ``.updated`` / ``.deleted`` structured
log events emit AFTER tx commits (outside the ``async with`` block) so
rollback paths don't log a non-event.
WG#10: ``entity_id`` for PUT/DELETE derives from the URL **path**
parameter (since :class:`SymbolMapEntryUpdate` body does NOT include
``input_symbol``).
"""

from __future__ import annotations

from collections.abc import (
    Callable,  # noqa: TC003 — FastAPI inspects Annotated[Callable[...], Depends(get_now_fn)]
)
from dataclasses import asdict
from datetime import (
    datetime,  # noqa: TC003 — FastAPI inspects Annotated[Callable[[], datetime], ...] at runtime
)
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from packages.db.queries.analytics import (
    SymbolMapRow,
    delete_symbol_map_entry,
    insert_symbol_map_entry,
    select_all_symbol_map_entries,
    select_symbol_map_entry,
    update_symbol_map_entry,
)
from packages.db.queries.audit import insert_audit_event

from ..deps import get_now_fn, get_pool
from ..models.symbol_map import (
    SymbolMapEntryCreate,
    SymbolMapEntryResponse,
    SymbolMapEntryUpdate,
    SymbolMapListResponse,
)

__all__ = ["router"]


router = APIRouter(prefix="/api/symbol-map", tags=["symbol-map"])


_ENTITY_TYPE = "symbol_map"
_ACTION_CREATE = "symbol_map.create"
_ACTION_UPDATE = "symbol_map.update"
_ACTION_DELETE = "symbol_map.delete"


def _resolve_actor(request: Request) -> str:
    """Derive ``actor`` per §16.8:2227 — ``lan:<source_ip>``.

    Fallback ``lan:unknown`` when ``request.client is None`` (TestClient
    edge case) per WG#5 / plan §"Edge cases".
    """
    if request.client is None:
        return "lan:unknown"
    return f"lan:{request.client.host}"


def _resolve_correlation_id(request: Request) -> str | None:
    """Read ``X-Correlation-ID`` request header per §15.2 trace propagation.

    Empty / whitespace-only / missing → ``None`` (per WG#6 + OQ-7
    default 2026-05-03; F5+ middleware will fill via UUIDv4 fallback).
    """
    header_value = request.headers.get("X-Correlation-ID")
    if header_value and header_value.strip():
        return header_value
    return None


def _row_to_response(row: SymbolMapRow) -> SymbolMapEntryResponse:
    return SymbolMapEntryResponse(**asdict(row))


@router.get("/", response_model=SymbolMapListResponse)
async def list_symbol_map(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> SymbolMapListResponse:
    """List all symbol_map rows ordered by input_symbol ASC."""
    async with pool.acquire() as conn:
        rows = await select_all_symbol_map_entries(conn)
    return SymbolMapListResponse(entries=[_row_to_response(r) for r in rows])


@router.get("/{input_symbol}", response_model=SymbolMapEntryResponse)
async def get_symbol_map_entry_endpoint(
    input_symbol: str,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> SymbolMapEntryResponse:
    """Return one symbol_map entry; 404 if not found."""
    async with pool.acquire() as conn:
        row = await select_symbol_map_entry(conn, input_symbol)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"symbol_map entry {input_symbol!r} not found",
        )
    return _row_to_response(row)


@router.post(
    "/",
    response_model=SymbolMapEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_symbol_map_entry_endpoint(
    body: SymbolMapEntryCreate,
    request: Request,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    now_fn: Annotated[Callable[[], datetime], Depends(get_now_fn)],
) -> SymbolMapEntryResponse:
    """Create one symbol_map entry. 409 on duplicate PK; atomic audit row write."""
    actor = _resolve_actor(request)
    correlation_id = _resolve_correlation_id(request)
    occurred_at = now_fn()

    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                inserted_row = await insert_symbol_map_entry(
                    conn,
                    input_symbol=body.input_symbol,
                    canonical_symbol=body.canonical_symbol,
                    exchange_source=str(body.exchange_source),
                    notes=body.notes,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
                await insert_audit_event(
                    conn,
                    occurred_at=occurred_at,
                    actor=actor,
                    action=_ACTION_CREATE,
                    entity_type=_ENTITY_TYPE,
                    entity_id=body.input_symbol,
                    before_state=None,
                    after_state=asdict(inserted_row),
                    correlation_id=correlation_id,
                )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"symbol_map entry {body.input_symbol!r} already exists",
            ) from exc

    request.app.state.logger.info(
        _ACTION_CREATE,
        input_symbol=body.input_symbol,
        actor=actor,
        correlation_id=correlation_id,
    )
    return _row_to_response(inserted_row)


@router.put("/{input_symbol}", response_model=SymbolMapEntryResponse)
async def update_symbol_map_entry_endpoint(
    input_symbol: str,
    body: SymbolMapEntryUpdate,
    request: Request,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    now_fn: Annotated[Callable[[], datetime], Depends(get_now_fn)],
) -> SymbolMapEntryResponse:
    """Full PUT — overwrites all mutable fields. 404 if missing; atomic audit."""
    actor = _resolve_actor(request)
    correlation_id = _resolve_correlation_id(request)
    occurred_at = now_fn()

    async with pool.acquire() as conn:
        # WG#4: pre-read BEFORE conn.transaction() so 404 short-circuits
        # without entering a tx that immediately rolls back.
        before_row = await select_symbol_map_entry(conn, input_symbol)
        if before_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"symbol_map entry {input_symbol!r} not found",
            )

        async with conn.transaction():
            updated_row = await update_symbol_map_entry(
                conn,
                input_symbol=input_symbol,
                canonical_symbol=body.canonical_symbol,
                exchange_source=str(body.exchange_source),
                notes=body.notes,
                updated_at=occurred_at,
            )
            if updated_row is None:
                # Race condition: row deleted between pre-read and update.
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"symbol_map entry {input_symbol!r} not found",
                )
            await insert_audit_event(
                conn,
                occurred_at=occurred_at,
                actor=actor,
                action=_ACTION_UPDATE,
                entity_type=_ENTITY_TYPE,
                entity_id=input_symbol,
                before_state=asdict(before_row),
                after_state=asdict(updated_row),
                correlation_id=correlation_id,
            )

    request.app.state.logger.info(
        _ACTION_UPDATE,
        input_symbol=input_symbol,
        actor=actor,
        correlation_id=correlation_id,
    )
    return _row_to_response(updated_row)


@router.delete("/{input_symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_symbol_map_entry_endpoint(
    input_symbol: str,
    request: Request,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    now_fn: Annotated[Callable[[], datetime], Depends(get_now_fn)],
) -> None:
    """Delete one symbol_map entry. 404 if missing; atomic audit row write."""
    actor = _resolve_actor(request)
    correlation_id = _resolve_correlation_id(request)
    occurred_at = now_fn()

    async with pool.acquire() as conn:
        # WG#4: pre-read BEFORE conn.transaction() per same rationale as PUT.
        before_row = await select_symbol_map_entry(conn, input_symbol)
        if before_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"symbol_map entry {input_symbol!r} not found",
            )

        async with conn.transaction():
            deleted = await delete_symbol_map_entry(conn, input_symbol)
            if not deleted:
                # Race condition: row already deleted between pre-read and DELETE.
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"symbol_map entry {input_symbol!r} not found",
                )
            await insert_audit_event(
                conn,
                occurred_at=occurred_at,
                actor=actor,
                action=_ACTION_DELETE,
                entity_type=_ENTITY_TYPE,
                entity_id=input_symbol,
                before_state=asdict(before_row),
                after_state=None,
                correlation_id=correlation_id,
            )

    request.app.state.logger.info(
        _ACTION_DELETE,
        input_symbol=input_symbol,
        actor=actor,
        correlation_id=correlation_id,
    )
