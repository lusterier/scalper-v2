"""Pydantic request + response models for ``/api/configs/*`` (T-405).

5 models: ``BotConfigResponse`` (full row) + ``BotConfigVersionsListResponse``
(paginated envelope) + ``ConfigValidateRequest`` + ``ConfigValidateResponse``
+ ``ConfigApplyRequest``.

Apply endpoint follow-up T-401b's `H-022/§16.8` atomic-tx contract — see
``routers/configs.py`` 5-helper same-conn pin.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type

from pydantic import BaseModel, Field

__all__ = [
    "BotConfigResponse",
    "BotConfigVersionsListResponse",
    "ConfigApplyRequest",
    "ConfigValidateRequest",
    "ConfigValidateResponse",
]


class BotConfigResponse(BaseModel):
    """Single ``bot_configs`` row projected to JSON."""

    id: int
    bot_id: str
    version: int
    applied_at: datetime
    applied_by: str
    config_yaml: str
    config_hash: str
    notes: str | None


class BotConfigVersionsListResponse(BaseModel):
    """Paginated envelope for ``GET /api/configs/{bot_id}/versions``."""

    versions: list[BotConfigResponse]
    total: int
    limit: int
    offset: int


class ConfigValidateRequest(BaseModel):
    """POST /api/configs/validate request body."""

    bot_id: str = Field(..., min_length=1, max_length=64)
    # max_length=200_000 — based on observed configs/bots/alpha.yaml ~5 KB;
    # 40x headroom for future complex bots; FastAPI default body limit not
    # hit. Hardcoded per §N9 acceptable trade-off vs env-tunable (no current
    # operator demand for tuning) per WG#13.
    yaml_text: str = Field(..., min_length=1, max_length=200_000)


class ConfigValidateResponse(BaseModel):
    """POST /api/configs/validate response.

    `valid=True` → `parsed_version` populated from BotConfig.version, `errors=[]`.
    `valid=False` → `parsed_version=None` ALWAYS (no partial parse exposed
    per WG#7), `errors=[str(e)]` from caught ValueError/ValidationError.
    """

    valid: bool
    bot_id: str
    parsed_version: int | None
    errors: list[str]


class ConfigApplyRequest(BaseModel):
    """POST /api/configs/{bot_id}/apply request body."""

    # max_length=200_000 — see ConfigValidateRequest rationale (WG#13).
    yaml_text: str = Field(..., min_length=1, max_length=200_000)
    applied_by: str = Field(..., min_length=1, max_length=128)
    notes: str | None = Field(None, max_length=500)
