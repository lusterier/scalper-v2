"""Inbound webhook model + response shapes for signal-gateway (§9.1 step 5, §N7).

:class:`SignalEnvelope` is the service-local wire schema for the
TradingView webhook body (§9.1 four required fields + free-form
``payload``). Kept hexagonal per §N7: this inbound shape is distinct
from :class:`packages.bus.schemas.signals.SignalValidated`, the
outbound NATS payload — the handler maps between them after symbol
resolution.

The ``model_validator(mode="before")`` extras migration solves TV v3
alert ergonomics: TradingView alert messages are flat JSON, so
strategy-specific indicators (``rsi``, ``sl_pct``, …) arrive as
top-level keys, not nested under ``payload``. The migrator moves any
non-envelope key into ``payload`` before field validation runs, letting
us keep ``extra="forbid"`` and still accept arbitrary TV signal
payloads.

Response models (:class:`WebhookValidatedResponse`,
:class:`WebhookDuplicateResponse`, :class:`WebhookErrorResponse`) are
typed explicitly per status code so FastAPI autogenerates an accurate
OpenAPI schema for each branch of the status map.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "SignalEnvelope",
    "WebhookDuplicateResponse",
    "WebhookErrorResponse",
    "WebhookValidatedResponse",
]


# Declared top-level keys of :class:`SignalEnvelope`. Anything else at
# top level is migrated into ``payload`` by the mode="before" validator
# before ``extra="forbid"`` runs.
_ENVELOPE_FIELDS: frozenset[str] = frozenset(
    {
        "symbol",
        "action",
        "source",
        "idempotency_key",
        "payload",
    }
)


class SignalEnvelope(BaseModel):
    """Inbound webhook body (§9.1 step 5).

    Required: ``symbol`` (non-empty), ``action`` ∈ {LONG, SHORT, CLOSE},
    ``source`` (non-empty), ``idempotency_key`` (non-empty). Optional:
    ``payload`` (free-form dict, auto-populated by the extras migrator).

    Field validation runs after the extras-migration validator below,
    so ``extra="forbid"`` never sees legit TV-strategy top-level keys —
    those are already inside ``payload`` by then.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1)
    action: Literal["LONG", "SHORT", "CLOSE"]
    source: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_unknown_keys(cls, data: Any) -> Any:
        """Move non-envelope top-level keys into ``payload``.

        TV v3 alerts deliver indicators as flat top-level keys
        (``{"symbol": ..., "rsi": 14.2, "sl_pct": 0.01}``). This hook
        rewrites the input so those keys land inside ``payload``
        before ``extra="forbid"`` trips. Non-dict inputs pass through
        unchanged — Pydantic raises a clear type error downstream.

        Collision policy: an explicit ``payload`` dict is the
        authoritative carrier and its keys win over any migrated
        top-level duplicate. Top-level extras are TV-shape legacy
        spillover (lower-trust source); when a client sends both
        ``payload: {"x": 1}`` and top-level ``x: 2`` simultaneously,
        the explicit nested value (``1``) is what lands in the merged
        payload. An explicit ``payload`` with a non-dict value passes
        through unchanged so the downstream field validator produces
        the authoritative error message.
        """
        if not isinstance(data, dict):
            return data
        extras = {k: v for k, v in data.items() if k not in _ENVELOPE_FIELDS}
        if not extras:
            return data
        existing = data.get("payload")
        if existing is not None and not isinstance(existing, dict):
            return data
        merged_payload = {**extras, **(existing or {})}
        return {
            **{k: v for k, v in data.items() if k in _ENVELOPE_FIELDS},
            "payload": merged_payload,
        }


class WebhookValidatedResponse(BaseModel):
    """200 response body — signal ingested successfully."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: int


class WebhookDuplicateResponse(BaseModel):
    """202 response body — ``idempotency_key`` seen within dedup TTL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["duplicate"] = "duplicate"


class WebhookErrorResponse(BaseModel):
    """4xx / 5xx response body.

    ``reason`` is the machine-readable condition; the closed set
    mirrors the §9.1 status map. ``detail`` is the human-readable
    summary — safe for log aggregation but not for leaking internals
    to the caller, so :mod:`.webhook` populates it with short, generic
    strings.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: str
    reason: Literal[
        "invalid_json",
        "validation_failed",
        "hmac_invalid",
        "symbol_unknown",
        "rate_limit",
        "internal",
    ]
