"""NATS handler closure: parse → dedup → render → telegram send (T-409).

Pure use-case orchestration per §N7 hexagonal split (WG#6). Accepts
:class:`AlertsConfig` + :class:`DedupTracker` + :class:`TelegramClient` +
``jinja_env`` + ``logger`` + ``now_fn`` as kwargs (no module globals,
no I/O at import time).

Handler flow per plan:
1. Parse envelope.payload — extract ``event`` field.
2. Find rule (exact match > wildcard ``*`` per OQ-5=A).
3. Threshold gate (optional per BRIEF §B.5 example pnl_audit_correction).
4. Dedup check (5-min window per OQ-4=A).
5. Render template via jinja2.
6. Telegram send (with retry escalation if rule.severity == 'critical').

NATS handler exceptions are swallowed by :class:`NatsClient._dispatch`
per its contract (logs ``bus_handler_failed``); this handler catches its
own predictable failure modes (missing payload field) and returns gracefully
to avoid relying on the bus swallow path.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from jinja2 import Environment
    from structlog.stdlib import BoundLogger

    from packages.bus.envelope import MessageEnvelope

    from .config import AlertsConfig, RuleConfig
    from .dedup import DedupTracker
    from .telegram import TelegramClient

__all__ = ["make_alert_handler"]


def _passes_threshold(payload: dict[str, Any], threshold_field: str, threshold_min: float) -> bool:
    """Return True if ``payload[threshold_field] >= threshold_min``.

    Missing field or non-numeric value → False (alert dropped). Per BRIEF
    §B.5 pnl_audit_correction example: only fire alert when |delta| >= $10.
    """
    value = payload.get(threshold_field)
    if value is None:
        return False
    try:
        return float(value) >= threshold_min
    except (TypeError, ValueError):
        return False


def _compute_dedup_key(rule: RuleConfig, payload: dict[str, Any]) -> str:
    """sha256 over event_name + canonical JSON of payload (per OQ-4=A).

    Two identical alerts produce same key (dedup); two distinct alerts of
    same event_type with different payload fields produce different keys
    (both fire, e.g. two ``pnl_audit_correction`` for different sub_accounts).
    """
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(f"{rule.event}:{canonical}".encode()).hexdigest()


def make_alert_handler(
    *,
    alerts_config: AlertsConfig,
    dedup: DedupTracker,
    telegram_client: TelegramClient,
    jinja_env: Environment,
    logger: BoundLogger,
    now_fn: Callable[[], datetime],
) -> Callable[[MessageEnvelope], Any]:
    """Build the NATS handler closure for ``system.alerts`` subscription.

    Closure-bound dependencies (alerts_config / dedup / telegram_client /
    jinja_env / logger / now_fn) are injected per §N6 — no module globals,
    no singletons.
    """

    async def _handler(envelope: MessageEnvelope) -> None:
        payload = envelope.payload
        # envelope.payload is typed `dict[str, Any]`; defensive in case
        # asyncpg / NATS deserialization ever returns something else.
        event_name_raw = payload.get("event") if isinstance(payload, dict) else None
        if not event_name_raw or not isinstance(event_name_raw, str):
            logger.warning(
                "alert_payload_missing_event",
                payload_keys=sorted(payload) if isinstance(payload, dict) else [],
            )
            return
        event_name: str = event_name_raw

        rule = alerts_config.find_rule(event_name)
        if rule is None:
            # Use `event_name=` (NOT `event=`) — `event` is structlog's
            # reserved kwarg for the message body.
            logger.info("alert_no_rule_match", event_name=event_name)
            return

        # Optional threshold gate (per BRIEF §B.5 pnl_audit_correction example).
        if rule.threshold is not None and not _passes_threshold(
            payload,
            rule.threshold.field,
            rule.threshold.min,
        ):
            logger.debug(
                "alert_below_threshold",
                event_name=event_name,
                threshold_field=rule.threshold.field,
                threshold_min=rule.threshold.min,
            )
            return

        dedup_key = _compute_dedup_key(rule, payload)
        if dedup.is_duplicate(dedup_key, now=now_fn()):
            logger.debug(
                "alert_deduped",
                event_name=event_name,
                dedup_key=dedup_key,
            )
            return
        dedup.mark(dedup_key, now=now_fn())

        # Render Jinja2 template — caller (lifespan) loads templates from
        # FileSystemLoader rooted at configs/alerts/.
        template = jinja_env.get_template(rule.template)
        rendered = template.render(
            event=event_name,
            severity=rule.severity,
            channel=rule.channel,
            payload=payload,
            correlation_id=envelope.correlation_id,
            published_at=envelope.published_at.isoformat(),
        )

        is_critical = rule.severity == "critical"
        await telegram_client.send(
            channel=rule.channel,
            text=rendered,
            is_critical=is_critical,
        )

    return _handler
