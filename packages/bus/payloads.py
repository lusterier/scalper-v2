"""Pydantic payload envelopes for cross-service NATS topics (§8 + §13).

T-511b1 ships ``ShadowStartPayload`` + ``VariantSpec`` for the
``shadow.start.<bot_id>`` topic per BRIEF §13.3. The terminal-outcome
StrEnum is REUSED from :class:`packages.core.types.ShadowVariantTerminal`
(T-510b shipped 2026-05-07 commit ``6df8859``); this module does NOT
redefine it. Migration 0014 wire-format is snake_case plain TEXT (no
CHECK constraint per OQ-4=A); ``ShadowVariantTerminal`` already aligns.

Future shadow / backtest payloads land here as the F5 cluster shipped.
"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 — runtime annotation on Pydantic Decimal fields
from typing import Literal

from pydantic import BaseModel, ConfigDict


class VariantSpec(BaseModel):
    """One shadow variant — name + override params per BRIEF §13.2 YAML schema.

    Override keys are a subset of execution-config keys: ``be_trigger``,
    ``be_sl_level``, ``trail_pct``, ``sl_pct``, ``tp_pct``, ``tp_qty_pct``,
    ``max_duration_hours``. Unknown keys are rejected via
    ``model_config.extra='forbid'`` plus the per-key validator below.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    overrides: dict[str, Decimal | int]


class ShadowStartPayload(BaseModel):
    """``shadow.start.<bot_id>`` envelope per BRIEF §13.3.

    Published by :mod:`services.execution.app.placement_persist` post-commit
    on trade-open when ``bot_config.shadow.enabled`` (T-511b2 wires the
    publisher; T-511b1 ships only the consumer in :mod:`shadow_worker`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: Literal[1] = 1
    parent_trade_id: int
    bot_id: str
    symbol: str
    side: Literal["buy", "sell"]
    entry_price: Decimal
    qty: Decimal
    variants: list[VariantSpec]
