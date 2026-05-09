"""``POST /webhook`` handler — TradingView signal ingestion (§9.1).

Orchestrates the §9.1 9-step validation pipeline using T-015b1 primitives
(``verify_hmac``, :class:`DedupRing`, :class:`SymbolMapCache`,
:class:`SignalValidated` schema) plus :class:`SignalEnvelope` from
:mod:`.models` and the service :class:`Metrics` declared in
:mod:`.metrics`. Rate-limit and ``webhook_processing_seconds``
observation live one layer up in :class:`RateLimitMiddleware`.

Wire order (canonical) — see ``docs/modules/signal_gateway.md``
"Pipeline wire order" for the full diff against §9.1:

    middleware: trace_scope → RateLimitMiddleware → handler
    1. read raw body bytes (must precede any other body access)
    2. verify HMAC over raw bytes  → 401 hmac_invalid
    3. publish signals.raw          (best-effort, fall through on fail)
    4. JSON parse                   → 400 invalid_json
    5. peek idempotency_key         (decides validation_unkeyed split)
    6. SignalEnvelope.model_validate
                                    → 400 validation_failed (DB row 'invalid')
                                    → 400 validation_failed (no DB row, unkeyed)
                                    → 500 internal (DB fail in 'invalid' write)
    7. signals_received{source}.inc
    8. bind_correlation(idempotency_key)
    9. dedup.check_and_record       → 202 duplicate (DB row 'duplicate')
    10. symbol_cache.resolve        → 422 symbol_unknown (DB row 'invalid')
    11. insert_signal(validated) + insert_outbox_event in single tx
                                    → 500 internal (tx rollback)
    12. 200 {"signal_id": int}; outbox relay handles NATS publish post-commit (T-537b)

Dual-status semantic for validation_failed: caller sees 400 if the
audit row was written (DB up), 500 if the DB write failed (caller
re-tries). Post-mortem reader joins logs by trace_id +
correlation_id (when bound) to disambiguate.

``signals.raw`` correlation_id: a fresh UUID4, not the request
trace_id and not the eventual ``idempotency_key`` correlation. Raw is
audit-only (§8.1); no consumer joins on it, and reusing trace_id
would mix per-HTTP-request and per-signal-lineage ID spaces (§15.2).
"""

from __future__ import annotations

# TC001/TC002 disabled file-wide: FastAPI route decoration calls
# ``typing.get_type_hints()`` to resolve ``Annotated[X, Depends(...)]``
# parameters, which requires every ``X`` to be importable at
# hint-resolution time. Several DI types below are only referenced as
# annotations (the symbols are not method-called), so ruff would
# otherwise route them into ``TYPE_CHECKING``; that breaks FastAPI.
# ruff: noqa: TC001, TC002
import json
import uuid
from datetime import timedelta
from typing import Annotated, Any, Final, Literal

import asyncpg
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from structlog.stdlib import BoundLogger

from packages.bus import MessageEnvelope, NatsClient
from packages.bus.schemas.signals import SignalValidated
from packages.core import CorrelationId, now_utc
from packages.db.queries.signal_gateway import insert_signal
from packages.observability import bind_correlation
from packages.outbox import insert_outbox_event

from .config import Settings
from .dedup import DedupRing
from .deps import (
    get_bus,
    get_dedup,
    get_logger_dep,
    get_metrics,
    get_pool,
    get_settings,
    get_symbol_map_cache,
    get_trading_logger,
)
from .metrics import Metrics
from .models import (
    SignalEnvelope,
    WebhookDuplicateResponse,
    WebhookErrorResponse,
    WebhookValidatedResponse,
)
from .security import verify_hmac
from .symbol_map import SymbolMapCache

type _ErrorReason = Literal[
    "invalid_json",
    "validation_failed",
    "hmac_invalid",
    "symbol_unknown",
    "rate_limit",
    "internal",
]

# H-008: signal expiry default. Strategy-engine will share this when it
# lands — F1 may centralize in packages/core/constants.py.
# TODO(F1 H-008): centralize once strategy-engine needs it too.
_SIGNAL_TTL_SECONDS: Final[int] = 120


__all__ = ["router"]


router = APIRouter(tags=["webhook"])


def _client_ip(request: Request) -> str:
    """Best-effort client IP for log fields (mirror middleware extraction)."""
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def _err_response(
    status_code: int,
    *,
    detail: str,
    reason: _ErrorReason,
) -> JSONResponse:
    """Build a typed-validated 4xx/5xx response.

    Constructs :class:`WebhookErrorResponse` first so the
    ``Literal`` constraint on ``reason`` runs at handler time —
    out-of-set values fail at construction, never reach the wire.
    """
    body = WebhookErrorResponse(detail=detail, reason=reason)
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _serialize_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Reduce Pydantic errors to a flat ``[{loc, msg, type}, ...]`` for log fields."""
    return [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]


@router.post(
    "/webhook",
    responses={
        status.HTTP_200_OK: {"model": WebhookValidatedResponse},
        status.HTTP_202_ACCEPTED: {"model": WebhookDuplicateResponse},
        status.HTTP_400_BAD_REQUEST: {"model": WebhookErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": WebhookErrorResponse},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": WebhookErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": WebhookErrorResponse},
    },
)
async def webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    bus: Annotated[NatsClient, Depends(get_bus)],
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    dedup: Annotated[DedupRing, Depends(get_dedup)],
    symbol_cache: Annotated[SymbolMapCache, Depends(get_symbol_map_cache)],
    metrics: Annotated[Metrics, Depends(get_metrics)],
    trading_log: Annotated[BoundLogger, Depends(get_trading_logger)],
    system_log: Annotated[BoundLogger, Depends(get_logger_dep)],
) -> JSONResponse:
    """§9.1 ``/webhook`` orchestrator. See module docstring for wire order."""
    source_ip = _client_ip(request)

    # Step 1 — raw body (must precede any other body access; FastAPI
    # would consume the stream if a Pydantic body parameter were
    # declared, breaking HMAC verify).
    body = await request.body()
    signature = request.headers.get("X-Signature", "")

    # Step 2 — HMAC verify.
    secret = settings.signal_gateway_hmac_secret.get_secret_value()
    if not verify_hmac(body, signature, secret):
        metrics.errors.labels(
            service="signal-gateway",
            error_class="hmac_invalid",
        ).inc()
        trading_log.info("signal_rejected", reason="hmac_invalid", source_ip=source_ip)
        return _err_response(
            status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            reason="hmac_invalid",
        )

    # Step 3 — signals.raw publish (H-010 fan-out, best-effort audit).
    # correlation_id: fresh UUID4. Not trace_id (per-HTTP-request, §15.2)
    # and not the eventual idempotency_key (we don't have it yet, and
    # raw is audit-only — no consumer joins on this ID).
    try:
        raw_envelope = MessageEnvelope(
            correlation_id=CorrelationId(str(uuid.uuid4())),
            publisher="signal-gateway",
            payload={
                "body_text": body.decode("utf-8", errors="replace"),
                "source_ip": source_ip,
                "received_at": now_utc().isoformat(),
                "user_agent": request.headers.get("User-Agent", ""),
            },
        )
        await bus.publish("signals.raw", raw_envelope)
    except Exception as exc:
        metrics.errors.labels(
            service="signal-gateway",
            error_class="publish_raw_failed",
        ).inc()
        system_log.warning(
            "webhook_error",
            error_class="publish_raw_failed",
            error=str(exc),
        )

    trading_log.info("signal_received", source_ip=source_ip)

    # Step 4 — JSON parse.
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        metrics.errors.labels(
            service="signal-gateway",
            error_class="invalid_json",
        ).inc()
        trading_log.info("signal_rejected", reason="invalid_json", source_ip=source_ip)
        return _err_response(
            status.HTTP_400_BAD_REQUEST,
            detail="invalid JSON body",
            reason="invalid_json",
        )

    # Step 5 — peek idempotency_key. Treat None / non-str / empty /
    # whitespace-only as "no key" so the validation_unkeyed branch fires
    # consistently regardless of how the key is malformed.
    peeked_raw = parsed.get("idempotency_key") if isinstance(parsed, dict) else None
    peeked_key = peeked_raw.strip() if isinstance(peeked_raw, str) else ""

    # Step 6 — Pydantic validate.
    try:
        envelope = SignalEnvelope.model_validate(parsed)
    except ValidationError as exc:
        validation_errors = _serialize_validation_errors(exc)
        if peeked_key:
            # Keyed validation_failed: write DB audit row.
            try:
                async with pool.acquire() as conn:
                    await insert_signal(
                        conn,
                        received_at=now_utc(),
                        schema_version="1.0",
                        source=str(parsed.get("source", "")) if isinstance(parsed, dict) else "",
                        idempotency_key=peeked_key,
                        symbol=str(parsed.get("symbol", "")) if isinstance(parsed, dict) else "",
                        original_symbol=None,
                        action=str(parsed.get("action", "")) if isinstance(parsed, dict) else "",
                        payload=parsed if isinstance(parsed, dict) else {},
                        ingestion_status="invalid",
                        correlation_id=peeked_key,
                    )
            except Exception as db_exc:
                # DB fail in audit-write returns 500 (consistent with happy-path
                # DB-fail semantic). Caller re-tries; module doc notes the
                # dual-status validation_failed → 400/500 outcome.
                metrics.errors.labels(
                    service="signal-gateway",
                    error_class="db_insert_failed",
                ).inc()
                system_log.error(
                    "webhook_error",
                    error_class="db_insert_failed",
                    error=str(db_exc),
                )
                return _err_response(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="internal error",
                    reason="internal",
                )
            metrics.signals_validated.labels(status="invalid").inc()
            trading_log.info(
                "signal_rejected",
                reason="validation_error",
                idempotency_key=peeked_key,
                validation_errors=validation_errors,
            )
            return _err_response(
                status.HTTP_400_BAD_REQUEST,
                detail="validation failed",
                reason="validation_failed",
            )
        # Unkeyed validation_failed: no DB row.
        metrics.errors.labels(
            service="signal-gateway",
            error_class="validation_unkeyed",
        ).inc()
        trading_log.info(
            "signal_rejected",
            reason="validation_error",
            validation_errors=validation_errors,
        )
        return _err_response(
            status.HTTP_400_BAD_REQUEST,
            detail="validation failed",
            reason="validation_failed",
        )

    # Step 7 — receipt counter (post-Pydantic; source label safe to read).
    metrics.signals_received.labels(source=envelope.source).inc()

    # Step 8 — bind correlation_id BEFORE dedup so duplicate-branch
    # log/metric also carry the lineage ID.
    bind_correlation(CorrelationId(envelope.idempotency_key))

    received_at = now_utc()

    # Step 9 — dedup.
    if not await dedup.check_and_record(envelope.idempotency_key):
        try:
            async with pool.acquire() as conn:
                await insert_signal(
                    conn,
                    received_at=received_at,
                    schema_version="1.0",
                    source=envelope.source,
                    idempotency_key=envelope.idempotency_key,
                    symbol=envelope.symbol,
                    original_symbol=None,
                    action=envelope.action,
                    payload=envelope.payload,
                    ingestion_status="duplicate",
                    correlation_id=envelope.idempotency_key,
                )
        except Exception as db_exc:
            metrics.errors.labels(
                service="signal-gateway",
                error_class="db_insert_failed",
            ).inc()
            system_log.error(
                "webhook_error",
                error_class="db_insert_failed",
                error=str(db_exc),
            )
            return _err_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="internal error",
                reason="internal",
            )
        metrics.signals_validated.labels(status="duplicate").inc()
        trading_log.info(
            "signal_rejected",
            reason="duplicate",
            idempotency_key=envelope.idempotency_key,
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=WebhookDuplicateResponse().model_dump(),
        )

    # Step 10 — symbol resolve.
    canonical = await symbol_cache.resolve(envelope.symbol)
    if canonical is None:
        try:
            async with pool.acquire() as conn:
                await insert_signal(
                    conn,
                    received_at=received_at,
                    schema_version="1.0",
                    source=envelope.source,
                    idempotency_key=envelope.idempotency_key,
                    symbol=envelope.symbol,
                    original_symbol=None,
                    action=envelope.action,
                    payload=envelope.payload,
                    ingestion_status="invalid",
                    correlation_id=envelope.idempotency_key,
                )
        except Exception as db_exc:
            metrics.errors.labels(
                service="signal-gateway",
                error_class="db_insert_failed",
            ).inc()
            system_log.error(
                "webhook_error",
                error_class="db_insert_failed",
                error=str(db_exc),
            )
            return _err_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="internal error",
                reason="internal",
            )
        metrics.signals_validated.labels(status="invalid").inc()
        trading_log.info(
            "signal_rejected",
            reason="symbol_unknown",
            symbol=envelope.symbol,
            idempotency_key=envelope.idempotency_key,
        )
        return _err_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="symbol not in symbol_map",
            reason="symbol_unknown",
        )

    # Step 11 — DB write (validated) + outbox row (publish-intent) in single tx.
    # T-537b: insert_signal + insert_outbox_event commit atomically (audit Items
    # 2 + 7 close). NATS publish handled by OutboxRelayWorker post-commit; if
    # NATS is down the row stays in outbox_events and the relay retries
    # exponentially until success or max_attempts exhaustion.
    expires_at = received_at + timedelta(seconds=_SIGNAL_TTL_SECONDS)
    validated_payload = SignalValidated(
        source=envelope.source,
        idempotency_key=envelope.idempotency_key,
        received_at=received_at,
        symbol=canonical,
        original_symbol=envelope.symbol,
        action=envelope.action,
        expires_at=expires_at,
        payload=envelope.payload,
    )
    try:
        async with pool.acquire() as conn, conn.transaction():
            signal_id = await insert_signal(
                conn,
                received_at=received_at,
                schema_version="1.0",
                source=envelope.source,
                idempotency_key=envelope.idempotency_key,
                symbol=canonical,
                original_symbol=envelope.symbol,
                action=envelope.action,
                payload=envelope.payload,
                ingestion_status="validated",
                correlation_id=envelope.idempotency_key,
            )
            await insert_outbox_event(
                conn,
                service="signal-gateway",
                subject="signals.validated",
                correlation_id=envelope.idempotency_key,
                payload=validated_payload.model_dump(mode="json"),
                created_at=received_at,
            )
    except Exception as db_exc:
        metrics.errors.labels(
            service="signal-gateway",
            error_class="db_insert_failed",
        ).inc()
        system_log.error(
            "webhook_error",
            error_class="db_insert_failed",
            error=str(db_exc),
        )
        return _err_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
            reason="internal",
        )

    # Step 12 — 200 OK (outbox relay handles NATS publish post-commit per T-537b).
    metrics.signals_validated.labels(status="validated").inc()
    trading_log.info(
        "signal_validated",
        signal_id=signal_id,
        symbol=canonical,
        original_symbol=envelope.symbol,
        action=envelope.action,
        source=envelope.source,
        idempotency_key=envelope.idempotency_key,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=WebhookValidatedResponse(signal_id=signal_id).model_dump(),
    )
