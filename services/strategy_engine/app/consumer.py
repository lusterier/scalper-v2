"""§9.4 main loop steps 3a-3h — per-bot signal consumer (T-310b).

Subscribed at lifespan step 6 to ``signals.validated``. Per-signal handler
performs the eight sub-steps from BRIEF §9.4:1532-1543:

3a TTL check        — ``signal.expires_at < now`` strict per §9.4:1536 verbatim
3b symbol filter    — ``signal.symbol in bot_config.symbols``
3c+3d resolve+evaluate — DB lookup signal_id (T-310a) + T-307 ``evaluate(...)``
3e execute publish  — Decision==execute → OrderRequest → ``orders.requests.<bot_id>``
3f reject publish   — Decision==reject → SignalRejected → ``signals.rejected.<bot_id>``
3g passthrough      — same as execute per §9.4:1542 verbatim
3h scoring write    — ``insert_scoring_evaluation`` BEFORE publish (publish-after-persist)

Single ``now = now_fn()`` snapshot at handler entry per WG#1; reused by 3a TTL,
3c ``received_at_lower_bound``, 3f ``rejected_at``. Determinism + microsecond-
drift avoidance.

§N2 log-stream routing per WG#4:

* per-signal lifecycle (info)         → trading.log:
    ``signal_expired``, ``signal_outside_universe``, ``signal_id_unresolved``,
    ``consumer.close_action_unsupported_v1``
* service-internal errors             → system.log:
    ``scoring_evaluator_crashed``, ``scoring_evaluations_insert_failed``,
    ``consumer.signal_validated_validation_failed``
* post-persist publish miss (dual)    → audit.log + system.log:
    ``orders_request_publish_failed``, ``signals_rejected_publish_failed``

H-005 (opposite-signal guard) is implemented as a scoring rule per BRIEF
§9.4:1555 (``opposite_side_open`` condition type — scoring catalog extension,
not consumer concern). H-008 (signal TTL) bound by step 3a verbatim.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from packages.bus import MessageEnvelope
from packages.bus.errors import NotConnectedError, PublishError
from packages.bus.payloads import (
    ShadowRejectedStartPayload,
    VariantSpec,
    subject_for_shadow_rejected_start,
)
from packages.bus.schemas import (
    OrderRequest,
    SignalRejected,
    SignalValidated,
    subject_for_orders_request,
    subject_for_signals_rejected,
)
from packages.core import CorrelationId
from packages.db.queries.market_data import select_latest_close
from packages.db.queries.scoring import insert_scoring_evaluation
from packages.db.queries.signal_gateway import select_signal_id_by_idempotency_key
from packages.scoring import evaluate

from .cooldown_gate import check_cooldown

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from typing import Literal

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import BusProtocol
    from packages.core import BotId
    from packages.scoring import BotConfig, FeatureResolver

    from .metrics import Metrics

__all__ = ["make_signal_handler"]


_ACTION_TO_SIDE: dict[str, Literal["buy", "sell"]] = {
    "LONG": "buy",
    "SHORT": "sell",
}


def make_signal_handler(
    *,
    bot_id: BotId,
    bot_config: BotConfig,
    resolver: FeatureResolver,
    pool: asyncpg.Pool,
    bus: BusProtocol,
    trading_logger: BoundLogger,
    system_logger: BoundLogger,
    audit_logger: BoundLogger,
    now_fn: Callable[[], datetime],
    max_signal_age_seconds: int,
    metrics: Metrics,
) -> Callable[[MessageEnvelope], Awaitable[None]]:
    """Closure factory returning the ``signals.validated`` handler.

    Bot identity + config + resolver + pool/bus/logger trio + metrics bound in
    the closure; inner ``_handle`` performs §9.4 steps 3a-3h per signal plus
    the T-526 pre-scoring cooldown gate inserted between 3b and 3c.
    """

    async def _handle(envelope: MessageEnvelope) -> None:
        # Step 0 — envelope payload validation (programmer/upstream bug if invalid).
        try:
            signal = SignalValidated.model_validate(envelope.payload)
        except Exception as exc:
            system_logger.error(
                "consumer.signal_validated_validation_failed",
                bot_id=bot_id,
                correlation_id=envelope.correlation_id,
                error=str(exc),
            )
            return

        # WG#1 — single now snapshot reused across 3a / 3c / 3f.
        now = now_fn()

        # 3a TTL check (brief §9.4:1536 verbatim — strict <).
        if signal.expires_at < now:
            trading_logger.info(
                "signal_expired",
                bot_id=bot_id,
                idempotency_key=signal.idempotency_key,
                expires_at=signal.expires_at.isoformat(),
                now=now.isoformat(),
            )
            return

        # 3b symbol filter.
        if signal.symbol not in bot_config.symbols:
            trading_logger.info(
                "signal_outside_universe",
                bot_id=bot_id,
                idempotency_key=signal.idempotency_key,
                symbol=signal.symbol,
                universe=bot_config.symbols,
            )
            return

        # CLOSE action handling (v1 limitation per §0.8 — own-bot position
        # lookup deferred to F4+; T-310b only handles LONG/SHORT new entries).
        if signal.action == "CLOSE":
            trading_logger.info(
                "consumer.close_action_unsupported_v1",
                bot_id=bot_id,
                idempotency_key=signal.idempotency_key,
                symbol=signal.symbol,
            )
            return

        # T-526 pre-scoring cooldown gate (between 3b symbol filter + 3c signal_id
        # resolve). Reuses ``now`` snapshot per WG#1. Skip pattern mirrors
        # ``signal_expired`` / ``signal_outside_universe`` above: trading.log info +
        # Prom counter inc + return BEFORE scoring_evaluations / orders.requests /
        # signals.rejected. Short-circuits before DB hit when both knobs disabled.
        cooldown = await check_cooldown(
            pool=pool,
            bot_id=bot_id,
            exchange_mode=bot_config.exchange.mode,
            now=now,
            risk_config=bot_config.risk,
        )
        if cooldown.active:
            trading_logger.info(
                "signal_blocked_cooldown",
                bot_id=bot_id,
                idempotency_key=signal.idempotency_key,
                symbol=signal.symbol,
                reason=cooldown.reason,
                cooldown_until=(
                    cooldown.cooldown_until.isoformat()
                    if cooldown.cooldown_until is not None
                    else None
                ),
                streak_count=cooldown.streak_count,
                last_loss_at=(
                    cooldown.last_loss_at.isoformat() if cooldown.last_loss_at is not None else None
                ),
            )
            metrics.signals_blocked_cooldown.labels(
                bot_id=str(bot_id),
                reason=cooldown.reason or "unknown",
            ).inc()
            return

        # 3c resolve signal_id (T-310a; received_at_lower_bound from now snapshot).
        received_at_lower_bound = now - timedelta(seconds=max_signal_age_seconds)
        async with pool.acquire() as conn:
            signal_id = await select_signal_id_by_idempotency_key(
                conn,
                idempotency_key=signal.idempotency_key,
                received_at_lower_bound=received_at_lower_bound,
            )
        if signal_id is None:
            trading_logger.info(
                "signal_id_unresolved",
                bot_id=bot_id,
                idempotency_key=signal.idempotency_key,
                lower_bound=received_at_lower_bound.isoformat(),
            )
            return

        # 3d evaluate scoring rules (T-307; bound_logger forwards into evaluator).
        try:
            result = await evaluate(
                bot_config=bot_config,
                signal=signal,
                resolver=resolver,
                bound_logger=trading_logger,
            )
        except Exception as exc:
            system_logger.error(
                "scoring_evaluator_crashed",
                bot_id=bot_id,
                signal_id=signal_id,
                idempotency_key=signal.idempotency_key,
                error=str(exc),
            )
            return

        # 3h scoring_evaluations write — BEFORE publish (publish-after-persist
        # per T-200 Q2). Audit row exists even if downstream publish misses.
        try:
            async with pool.acquire() as conn:
                await insert_scoring_evaluation(
                    conn,
                    bot_id=bot_id,
                    signal_id=signal_id,
                    evaluated_at=now,
                    trigger_threshold=result.threshold,
                    total_score=result.total_score,
                    decision=result.decision,
                    config_version=bot_config.version,
                    rule_results=[rr.model_dump(mode="json") for rr in result.rule_results],
                    feature_snapshot=result.feature_snapshot,
                    correlation_id=envelope.correlation_id,
                )
        except Exception as exc:
            system_logger.error(
                "scoring_evaluations_insert_failed",
                bot_id=bot_id,
                signal_id=signal_id,
                idempotency_key=signal.idempotency_key,
                error=str(exc),
            )
            return

        # 3e+3f+3g decision dispatch.
        if result.decision in ("execute", "passthrough"):
            await _publish_order_request(
                bot_id=bot_id,
                bot_config=bot_config,
                signal=signal,
                signal_id=signal_id,
                envelope=envelope,
                bus=bus,
                audit_logger=audit_logger,
                system_logger=system_logger,
            )
        elif result.decision == "reject":
            await _publish_signal_rejected(
                bot_id=bot_id,
                signal=signal,
                signal_id=signal_id,
                result_reason=result.reason or "score_below_threshold",
                total_score=result.total_score,
                threshold=result.threshold,
                rejected_at=now,
                envelope=envelope,
                bus=bus,
                audit_logger=audit_logger,
                system_logger=system_logger,
            )
            # T-513a / BRIEF §13.5 — parallel publish ShadowRejectedStartPayload.
            # Always-on per BRIEF §13.5; execution-service-side gate via
            # Settings.shadow_rejected_enabled. virtual_entry_price from
            # latest closed-candle ohlc_1m row (best-effort; PG error → Decimal("0")
            # fallback; worker-side classifies entry==0 as NO_TRIGGER immediately).
            virtual_entry_price = await _resolve_virtual_entry(
                pool=pool,
                symbol=signal.symbol,
                bound_logger=system_logger,
            )
            await _publish_shadow_rejected_start(
                bot_id=bot_id,
                bot_config=bot_config,
                signal=signal,
                signal_id=signal_id,
                rejected_at=now,
                virtual_entry_price=virtual_entry_price,
                envelope=envelope,
                bus=bus,
                audit_logger=audit_logger,
                system_logger=system_logger,
            )

    return _handle


async def _publish_order_request(
    *,
    bot_id: BotId,
    bot_config: BotConfig,
    signal: SignalValidated,
    signal_id: int,
    envelope: MessageEnvelope,
    bus: BusProtocol,
    audit_logger: BoundLogger,
    system_logger: BoundLogger,
) -> None:
    """Build OrderRequest from BotConfig.execution + signal; publish to orders.requests.<bot_id>."""
    side = _ACTION_TO_SIDE[signal.action]
    # T-511b2 / ADR-0010: populate shadow runtime config when bot_config.shadow
    # is enabled. Maps ShadowVariant (packages.scoring.types) → VariantSpec
    # (packages.bus.payloads) — different types, identical structure.
    # Float→Decimal cast on max_duration_hours via Decimal(str(value)) avoids
    # binary-float roundoff per §5.3 / §N1. parent_kind mapping (BotConfig.exchange.mode
    # → "live"/"paper") happens at execution-service emit site (placement_persist.py
    # for live, placement.py paper-fork for paper) — NOT here; OrderRequest does
    # not carry parent_kind.
    shadow_variants_payload: list[VariantSpec] = []
    shadow_max_duration_hours: Decimal | None = None
    if bot_config.shadow is not None and bot_config.shadow.enabled:
        shadow_variants_payload = [
            VariantSpec(name=v.name, overrides=dict(v.overrides))
            for v in bot_config.shadow.variants
        ]
        shadow_max_duration_hours = Decimal(str(bot_config.shadow.max_duration_hours))
    request = OrderRequest(
        bot_id=bot_id,
        signal_id=signal_id,
        symbol=signal.symbol,
        side=side,
        qty=bot_config.execution.qty,
        leverage=bot_config.execution.leverage,
        sl_pct=bot_config.execution.sl_pct,
        tp_pct=bot_config.execution.tp_pct,
        tp_qty_pct=bot_config.execution.tp_qty_pct,
        be_trigger=bot_config.execution.be_trigger,
        be_sl_level=bot_config.execution.be_sl_level,
        trail_pct=bot_config.execution.trail_pct,
        exchange_mode=bot_config.exchange.mode,
        shadow_variants=shadow_variants_payload,
        shadow_max_duration_hours=shadow_max_duration_hours,
    )
    out_envelope = MessageEnvelope(
        correlation_id=CorrelationId(envelope.correlation_id),
        publisher="strategy-engine",
        payload=request.model_dump(mode="json"),
    )
    try:
        await bus.publish(subject_for_orders_request(bot_id), out_envelope)
    except (PublishError, NotConnectedError) as exc:
        # Audit row already written (3h ran before this publish). Dual-write to
        # audit.log + system.log per WG#4 so operator can see "decision was
        # made but downstream missed" and reconcile via scoring_evaluations.
        audit_logger.error(
            "orders_request_publish_failed",
            bot_id=bot_id,
            signal_id=signal_id,
            error=str(exc),
        )
        system_logger.error(
            "orders_request_publish_failed",
            bot_id=bot_id,
            signal_id=signal_id,
            error=str(exc),
        )


async def _publish_signal_rejected(
    *,
    bot_id: BotId,
    signal: SignalValidated,
    signal_id: int,
    result_reason: str,
    total_score: float,
    threshold: float,
    rejected_at: datetime,
    envelope: MessageEnvelope,
    bus: BusProtocol,
    audit_logger: BoundLogger,
    system_logger: BoundLogger,
) -> None:
    """Build SignalRejected (T-310a schema); publish to signals.rejected.<bot_id>."""
    rejection = SignalRejected(
        bot_id=bot_id,
        signal_id=signal_id,
        idempotency_key=signal.idempotency_key,
        symbol=signal.symbol,
        action=signal.action,
        reason=result_reason,
        total_score=total_score,
        threshold=threshold,
        rejected_at=rejected_at,
    )
    out_envelope = MessageEnvelope(
        correlation_id=CorrelationId(envelope.correlation_id),
        publisher="strategy-engine",
        payload=rejection.model_dump(mode="json"),
    )
    try:
        await bus.publish(subject_for_signals_rejected(bot_id), out_envelope)
    except (PublishError, NotConnectedError) as exc:
        audit_logger.error(
            "signals_rejected_publish_failed",
            bot_id=bot_id,
            signal_id=signal_id,
            error=str(exc),
        )
        system_logger.error(
            "signals_rejected_publish_failed",
            bot_id=bot_id,
            signal_id=signal_id,
            error=str(exc),
        )


async def _resolve_virtual_entry(
    *,
    pool: asyncpg.Pool,
    symbol: str,
    bound_logger: BoundLogger,
) -> Decimal:
    """T-513a / BRIEF §13.5: resolve virtual_entry_price for rejected-signal observation.

    Best-effort: queries latest closed-candle close from ``ohlc_1m`` via
    :func:`packages.db.queries.market_data.select_latest_close` with
    ``source="binance"`` (live market data filter). On any PG transient
    error returns ``Decimal("0")`` fallback + warn-log; consumer task
    survives. Worker-side classifies entry==0 as NO_TRIGGER immediately
    (early-return; no candle subscribe + no 60-min wait).
    """
    import asyncpg as _asyncpg

    try:
        async with pool.acquire() as conn:
            latest_close = await select_latest_close(
                conn,
                symbol=symbol,
                source="binance",
            )
        return latest_close if latest_close is not None else Decimal("0")
    except (_asyncpg.PostgresError, OSError, TimeoutError) as exc:
        bound_logger.warning(
            "shadow_rejected_resolve_virtual_entry_failed",
            symbol=symbol,
            error=str(exc),
        )
        return Decimal("0")


async def _publish_shadow_rejected_start(
    *,
    bot_id: BotId,
    bot_config: BotConfig,
    signal: SignalValidated,
    signal_id: int,
    rejected_at: datetime,
    virtual_entry_price: Decimal,
    envelope: MessageEnvelope,
    bus: BusProtocol,
    audit_logger: BoundLogger,
    system_logger: BoundLogger,
) -> None:
    """T-513a / BRIEF §13.5: publish ShadowRejectedStartPayload per signal rejection.

    Always-on per BRIEF §13.5 — independent of ``bot_config.shadow.enabled``
    (operator gates via ``Settings.shadow_rejected_enabled`` execution-service-
    side). Best-effort: publish failure logged + scoring_evaluations row already
    persisted via parent ``_handle`` path. Mirror :func:`_publish_signal_rejected`
    (T-310b shipped) per-publish error envelope.
    """
    payload = ShadowRejectedStartPayload(
        signal_id=signal_id,
        bot_id=str(bot_id),
        symbol=signal.symbol,
        action=signal.action,
        virtual_entry_price=virtual_entry_price,
        sl_pct=bot_config.execution.sl_pct,
        tp_pct=bot_config.execution.tp_pct,
        be_trigger=bot_config.execution.be_trigger,
        be_sl_level=bot_config.execution.be_sl_level,
        rejected_at=rejected_at,
    )
    out_envelope = MessageEnvelope(
        correlation_id=CorrelationId(envelope.correlation_id),
        publisher="strategy-engine",
        payload=payload.model_dump(mode="json"),
    )
    try:
        await bus.publish(subject_for_shadow_rejected_start(str(bot_id)), out_envelope)
    except (PublishError, NotConnectedError) as exc:
        audit_logger.error(
            "shadow_rejected_start_publish_failed",
            bot_id=bot_id,
            signal_id=signal_id,
            error=str(exc),
        )
        system_logger.error(
            "shadow_rejected_start_publish_failed",
            bot_id=bot_id,
            signal_id=signal_id,
            error=str(exc),
        )
