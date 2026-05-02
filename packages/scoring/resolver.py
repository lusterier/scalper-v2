"""§10.3 feature reference resolver (T-306).

Substitute ``${signal.symbol}`` template, look up in NATS KV
``feature_latest`` (key ``<feature_name>:<symbol>``), fall through to DB
``features`` table on miss, apply staleness check
(``2 * interval_seconds`` default). Returns :class:`ResolverResult`
with status ``ok`` / ``data_missing`` / ``data_stale`` consumed by
T-307 evaluator to populate :attr:`RuleContext.feature_snapshot`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from packages.bus.errors import NotConnectedError, PublishError
from packages.bus.schemas import FeatureUpdate
from packages.core import now_utc
from packages.db.queries.feature_engine import LatestFeatureRow, select_latest_feature
from packages.features.intervals import INTERVAL_DELTA
from packages.features.types import FeatureValue

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient
    from packages.bus.schemas.signals import SignalValidated


__all__ = ["FeatureResolver", "ResolverResult", "ResolverStatus"]


type ResolverStatus = Literal["ok", "data_missing", "data_stale"]


_KV_BUCKET = "feature_latest"

_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")
_SUPPORTED_TEMPLATES: frozenset[str] = frozenset({"signal.symbol"})


@dataclass(frozen=True, slots=True)
class ResolverResult:
    """``status='ok'`` → value populated; ``data_missing`` / ``data_stale`` → None."""

    value: FeatureValue | None
    status: ResolverStatus
    error_info: dict[str, Any] | None = None


def substitute_template(template: str, signal: SignalValidated) -> str:
    """Replace ``${signal.symbol}`` with ``signal.symbol.lower()`` per §10.3:1722."""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token not in _SUPPORTED_TEMPLATES:
            msg = f"unsupported template: ${{{token}}}; supported: {sorted(_SUPPORTED_TEMPLATES)}"
            raise ValueError(msg)
        return signal.symbol.lower()

    return _TEMPLATE_RE.sub(_replace, template)


def parse_feature_ref(resolved_ref: str) -> tuple[str, str]:
    """Parse ``ind.<symbol>.<interval>.<rest...>`` → ``(symbol, interval)``."""
    parts = resolved_ref.split(".")
    if len(parts) < 4:
        msg = f"malformed feature-ref {resolved_ref!r}; expected at least 4 dot-segments"
        raise ValueError(msg)
    symbol = parts[1]
    interval = parts[2]
    if interval not in INTERVAL_DELTA:
        msg = (
            f"unknown interval {interval!r} in feature-ref {resolved_ref!r}; "
            f"supported: {sorted(INTERVAL_DELTA)}"
        )
        raise ValueError(msg)
    return symbol, interval


def _feature_update_to_feature_value(update: FeatureUpdate) -> FeatureValue:
    """Wire :class:`FeatureUpdate` (float) → domain :class:`FeatureValue` (Decimal)."""
    if update.value_num is not None:
        return FeatureValue(value_num=Decimal(str(update.value_num)))
    if update.value_bool is not None:
        return FeatureValue(value_bool=update.value_bool)
    return FeatureValue(value_json=update.value_json)


def _db_row_to_feature_value(row: LatestFeatureRow) -> FeatureValue:
    """§N7 seam: primitives :class:`LatestFeatureRow` → domain :class:`FeatureValue`."""
    if row.value_num is not None:
        return FeatureValue(value_num=Decimal(str(row.value_num)))
    if row.value_bool is not None:
        return FeatureValue(value_bool=row.value_bool)
    return FeatureValue(value_json=row.value_json)


class FeatureResolver:
    """§10.3 resolver — KV → DB fallback → staleness check."""

    def __init__(
        self,
        *,
        bus: NatsClient,
        pool: asyncpg.Pool,
        bound_logger: BoundLogger,
        now_fn: Callable[[], datetime] = now_utc,
    ) -> None:
        self._bus = bus
        self._pool = pool
        self._logger = bound_logger
        self._now_fn = now_fn

    async def resolve(
        self,
        *,
        rule_feature: str,
        signal: SignalValidated,
        max_staleness_sec: int | None = None,
    ) -> ResolverResult:
        """Substitute template + KV lookup + DB fallback + staleness check."""
        resolved_ref = substitute_template(rule_feature, signal)
        symbol, interval = parse_feature_ref(resolved_ref)
        if max_staleness_sec is None:
            max_staleness_sec = int(2 * INTERVAL_DELTA[interval].total_seconds())

        # KV key suffix uses UPPERCASE symbol per producer contract at
        # services/feature_engine/app/pipeline.py:239 (`f"{feature_name}:{candle.symbol}"`
        # where candle.symbol is canonical upper-case from wire).
        # `parts[1]` (post-substitute_template) is lowercased per §10.3:1722;
        # we reconstruct uppercase here to match producer-side write.
        kv_key = f"{resolved_ref}:{signal.symbol.upper()}"
        kv_value, kv_computed_at = await self._try_kv(resolved_ref, symbol, kv_key)
        if kv_value is not None and kv_computed_at is not None:
            return self._check_staleness(
                value=kv_value,
                computed_at=kv_computed_at,
                max_staleness_sec=max_staleness_sec,
            )

        db_value, db_computed_at = await self._try_db(resolved_ref, symbol)
        if db_value is None or db_computed_at is None:
            return ResolverResult(
                value=None,
                status="data_missing",
                error_info={"path": "kv+db missing", "feature_ref": resolved_ref},
            )
        return self._check_staleness(
            value=db_value,
            computed_at=db_computed_at,
            max_staleness_sec=max_staleness_sec,
        )

    async def _try_kv(
        self,
        feature_ref: str,
        symbol: str,
        kv_key: str,
    ) -> tuple[FeatureValue | None, datetime | None]:
        """Read KV; return (None, None) on miss / decode fail / bus error (logged WARN)."""
        try:
            kv_result = await self._bus.kv_get(_KV_BUCKET, kv_key)
        except (NotConnectedError, PublishError) as exc:
            self._logger.warning(
                "feature_resolver.kv_lookup_failed",
                feature_ref=feature_ref,
                symbol=symbol,
                path="kv",
                reason=str(exc),
            )
            return None, None
        if kv_result is None:
            return None, None
        payload, _revision = kv_result
        try:
            update = FeatureUpdate.model_validate_json(payload)
        except (ValidationError, ValueError) as exc:
            self._logger.warning(
                "feature_resolver.kv_decode_failed",
                feature_ref=feature_ref,
                symbol=symbol,
                path="kv",
                reason=str(exc),
            )
            return None, None
        return _feature_update_to_feature_value(update), update.computed_at

    async def _try_db(
        self,
        feature_ref: str,
        symbol: str,
    ) -> tuple[FeatureValue | None, datetime | None]:
        """Read DB; return (None, None) on miss."""
        async with self._pool.acquire() as conn:
            row = await select_latest_feature(conn, feature_name=feature_ref, symbol=symbol)
        if row is None:
            return None, None
        return _db_row_to_feature_value(row), row.computed_at

    def _check_staleness(
        self,
        *,
        value: FeatureValue,
        computed_at: datetime,
        max_staleness_sec: int,
    ) -> ResolverResult:
        """Return ok if fresh; data_stale with age + max in error_info if stale."""
        age_sec = int((self._now_fn() - computed_at).total_seconds())
        if age_sec > max_staleness_sec:
            return ResolverResult(
                value=None,
                status="data_stale",
                error_info={
                    "computed_at": computed_at.isoformat(),
                    "age_sec": age_sec,
                    "max_staleness_sec": max_staleness_sec,
                },
            )
        return ResolverResult(value=value, status="ok")
