"""``/api/configs/*`` read+write endpoints (T-405, BRIEF §9.6:1630 + §14.3:2064).

5 endpoints:

* ``GET /api/configs/{bot_id}`` — current latest version; 404 if no versions
* ``GET /api/configs/{bot_id}/versions`` — paginated history list
* ``GET /api/configs/{bot_id}/versions/{version}`` — specific version detail; 404 if missing
* ``POST /api/configs/validate`` — validate raw YAML body (no DB write);
  200 always (returns valid: bool + errors)
* ``POST /api/configs/{bot_id}/apply`` — second admin-write surface in repo
  (after T-401b symbol_map CRUD). Mirrors T-401b atomic-tx contract per
  §16.8/§15.6 — 5-helper same-conn pin.

Apply endpoint flow (per WG#1 validate-before-tx):

1. Parse YAML via ``load_bot_config_from_string`` MIMO tx — ValueError → 422.
2. Check ``parsed.bot_id == url_bot_id`` — mismatch → 409 BEFORE tx (per WG#11).
3. Compute ``config_hash = sha256(yaml_text.encode("utf-8")).hexdigest()``
   (raw bytes, no .strip() per WG#8).
4. Resolve ``actor`` + ``correlation_id`` (mirror T-401b WG#5 + WG#6).
5. Open ``async with pool.acquire() as conn, conn.transaction():`` and run
   ALL 5 helpers on same conn: ``select_bot_config_current`` →
   ``select_max_bot_config_version`` → ``insert_bot_config`` →
   ``update_bot_config_applied`` → ``insert_audit_event``.
6. ``insert_bot_config`` raises asyncpg.UniqueViolationError on (bot_id,
   version) collision (concurrent race per WG#3 — DETECTED, NOT prevented)
   → router returns 409 Conflict.
7. ``update_bot_config_applied`` returning False → ``RuntimeError`` →
   tx rollback → 5xx (per WG#9).
8. Audit ``before_state`` / ``after_state`` exclude ``config_yaml`` field
   (size discipline; 7-key shape) per WG#10. Full YAML retrievable from
   ``bot_configs`` table by version.
9. AFTER tx commits: ``logger.info("bot_config.applied", ...)``
   (per T-401b WG#9 — log post-commit only).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable  # noqa: TC003 — FastAPI inspects Annotated[Callable[...], ...]
from dataclasses import asdict
from datetime import (
    datetime,  # noqa: TC003 — FastAPI inspects Annotated[Callable[[], datetime], ...] at runtime
)
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from packages.db.queries.analytics import (
    count_bot_config_versions,
    insert_bot_config,
    select_bot_config_by_version,
    select_bot_config_current,
    select_bot_config_versions,
    select_max_bot_config_version,
    update_bot_config_applied,
)
from packages.db.queries.audit import insert_audit_event
from packages.scoring import load_bot_config_from_string

from ..deps import get_now_fn, get_pool
from ..models.configs import (
    BotConfigResponse,
    BotConfigVersionsListResponse,
    ConfigApplyRequest,
    ConfigValidateRequest,
    ConfigValidateResponse,
)

__all__ = ["router"]


router = APIRouter(prefix="/api/configs", tags=["configs"])


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

_ENTITY_TYPE = "bot_config"
_ACTION_APPLY = "bot_config.apply"


def _resolve_actor(request: Request) -> str:
    """Mirror T-401b ``_resolve_actor`` per WG#5."""
    if request.client is None:
        return "lan:unknown"
    return f"lan:{request.client.host}"


def _resolve_correlation_id(request: Request) -> str | None:
    """Mirror T-401b ``_resolve_correlation_id`` per WG#6."""
    header_value = request.headers.get("X-Correlation-ID")
    if header_value and header_value.strip():
        return header_value
    return None


def _row_to_response(row: Any) -> BotConfigResponse:
    return BotConfigResponse(**asdict(row))


def _audit_state_dict(row: Any) -> dict[str, Any]:
    """Project bot_configs row to audit JSONB EXCLUDING config_yaml (WG#10).

    Reduces audit_events.{before,after}_state JSONB bloat. Full YAML is
    retrievable from bot_configs table by version.
    """
    full = asdict(row)
    full.pop("config_yaml", None)
    return full


@router.get("/{bot_id}", response_model=BotConfigResponse)
async def get_current_bot_config(
    bot_id: str,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> BotConfigResponse:
    """Return current (latest) bot_config for bot_id; 404 if no versions yet."""
    async with pool.acquire() as conn:
        row = await select_bot_config_current(conn, bot_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no bot_config for {bot_id!r}",
        )
    return _row_to_response(row)


@router.get("/{bot_id}/versions", response_model=BotConfigVersionsListResponse)
async def list_bot_config_versions(
    bot_id: str,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BotConfigVersionsListResponse:
    """Paginated bot_config history (ORDER BY version DESC)."""
    async with pool.acquire() as conn:
        rows = await select_bot_config_versions(
            conn,
            bot_id=bot_id,
            limit=limit,
            offset=offset,
        )
        total = await count_bot_config_versions(conn, bot_id)
    return BotConfigVersionsListResponse(
        versions=[_row_to_response(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{bot_id}/versions/{version}",
    response_model=BotConfigResponse,
)
async def get_bot_config_version(
    bot_id: str,
    version: int,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> BotConfigResponse:
    """Return specific bot_config version detail; 404 if missing."""
    async with pool.acquire() as conn:
        row = await select_bot_config_by_version(
            conn,
            bot_id=bot_id,
            version=version,
        )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"bot_config {bot_id!r} version {version} not found",
        )
    return _row_to_response(row)


@router.post("/validate", response_model=ConfigValidateResponse)
async def validate_bot_config(
    body: ConfigValidateRequest,
) -> ConfigValidateResponse:
    """Validate raw YAML against BRIEF §B.1 schema; no DB writes.

    200 always (NOT 422 on validation failure) — UI live-as-typing
    consumes valid: bool + errors per T-416 Strategy editor flow.
    Reserves 422 only for malformed Pydantic request body shape.
    Per WG#7: parsed_version is ALWAYS None when valid=False (no
    partial parse exposed); errors=[str(e)] from caught Exception;
    empty list when valid=True.
    """
    try:
        parsed = load_bot_config_from_string(body.yaml_text)
    # Validate path catches ALL parse failures (ValueError + Pydantic ValidationError + ...).
    except Exception as exc:
        return ConfigValidateResponse(
            valid=False,
            bot_id=body.bot_id,
            parsed_version=None,
            errors=[str(exc)],
        )
    return ConfigValidateResponse(
        valid=True,
        bot_id=body.bot_id,
        parsed_version=parsed.version,
        errors=[],
    )


@router.post(
    "/{bot_id}/apply",
    response_model=BotConfigResponse,
    status_code=status.HTTP_201_CREATED,
)
async def apply_bot_config(
    bot_id: str,
    body: ConfigApplyRequest,
    request: Request,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    now_fn: Annotated[Callable[[], datetime], Depends(get_now_fn)],
) -> BotConfigResponse:
    """Apply new bot_config version: validate + insert + audit (atomic tx)."""
    # WG#1: validate-before-tx (steps 1-3 MIMO conn.transaction()).
    try:
        parsed = load_bot_config_from_string(body.yaml_text)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"YAML validation failed: {exc}",
        ) from exc

    # WG#11: bot_id URL-vs-body mismatch → 409 BEFORE tx.
    if parsed.bot_id != bot_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"bot_id mismatch: URL={bot_id!r} vs YAML body={parsed.bot_id!r}"),
        )

    # WG#8: raw-bytes hash (no .strip()) so trailing newlines change the hash.
    config_hash = hashlib.sha256(body.yaml_text.encode("utf-8")).hexdigest()
    occurred_at = now_fn()
    actor = _resolve_actor(request)
    correlation_id = _resolve_correlation_id(request)

    # WG#2 5-helper same-conn pin: select_current + select_max + insert + update + audit
    # all on one conn inside one tx. WG#3: race detected, NOT prevented (UniqueViolation → 409).
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                before_row = await select_bot_config_current(conn, bot_id)
                max_version = await select_max_bot_config_version(conn, bot_id)
                next_version = max_version + 1
                inserted_row = await insert_bot_config(
                    conn,
                    bot_id=bot_id,
                    version=next_version,
                    applied_at=occurred_at,
                    applied_by=body.applied_by,
                    config_yaml=body.yaml_text,
                    config_hash=config_hash,
                    notes=body.notes,
                )
                # WG#9: False return from update_bot_config_applied → tx rollback → 5xx.
                updated = await update_bot_config_applied(
                    conn,
                    bot_id=bot_id,
                    config_hash=config_hash,
                    config_applied_at=occurred_at,
                )
                if not updated:
                    msg = f"bot row missing during apply for bot_id={bot_id!r}"
                    raise RuntimeError(msg)
                # WG#10: audit before/after states exclude config_yaml (size discipline).
                await insert_audit_event(
                    conn,
                    occurred_at=occurred_at,
                    actor=actor,
                    action=_ACTION_APPLY,
                    entity_type=_ENTITY_TYPE,
                    entity_id=bot_id,
                    before_state=(
                        _audit_state_dict(before_row) if before_row is not None else None
                    ),
                    after_state=_audit_state_dict(inserted_row),
                    correlation_id=correlation_id,
                )
        except asyncpg.UniqueViolationError as exc:
            # WG#3: concurrent apply race — second writer hits (bot_id, version)
            # collision; respond 409, no audit row written (tx rolled back).
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(f"concurrent apply race for bot_id={bot_id!r}; version collision — retry"),
            ) from exc

    # WG#9 / T-401b WG#9: log AFTER tx commit (rollback path skips this).
    request.app.state.logger.info(
        _ACTION_APPLY,
        bot_id=bot_id,
        version=inserted_row.version,
        actor=actor,
        correlation_id=correlation_id,
    )
    return _row_to_response(inserted_row)
