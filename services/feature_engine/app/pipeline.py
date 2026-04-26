"""Feature pipeline (§9.3, T-110c).

:class:`FeaturePipeline` is the per-closed-candle dispatch consumer.
It subscribes once to the ``market.ohlc.1m.>`` wildcard on the NATS
bus and drives the persist → KV → publish loop for every registered
1m feature on each closed candle. Multi-interval live updates
(5m/15m/1h/4h/1d cagg-trigger on 1m close) are F1+ deferred (operator
D1) — higher-interval features in ``features_by_key`` get warmed up
at startup by T-110d but never live-updated until the F1+ extension
ships. The pipeline still holds a :class:`BufferHandle` for those
keys so dispatch can ``tail()`` against them when their live updates
finally land.

Composition: pure-ish use case with no I/O resources owned. Bus,
asyncpg pool, :class:`BufferRegistry`, ``features_by_key``, and the
logger are all DI'd via the constructor. Mirrors the T-104b
:class:`packages.market.OhlcPipeline` shape — same DI'd-resource
discipline, same per-callback error-isolation contract — but uses a
single bus subscription with handler-side routing rather than
per-symbol async tasks (NATS is the source here, not WS).

Ordering on each closed candle (per registered feature):

1. Push the domain :class:`OhlcCandle` into the
   ``(symbol, "1m")`` buffer.
2. ``tail(feature.warmup_candles)`` → ``feature.compute(candles)`` →
   :class:`FeatureValue`.
3. ``insert_feature(...)`` writes the canonical row (DB).
4. ``kv_put("feature_latest", "<feature_name>:<symbol>", payload_json)``
   refreshes the latest-value cache.
5. ``bus.publish(subject_for(feature_name, symbol), envelope)``
   publishes the live tap.

Error contract per feature (mirrors T-104b "publish-after-persist"):

* DB-fail → skip KV+publish (canonical store down → don't notify).
* KV-fail → still publish (NATS+DB stay consistent; KV is best-effort
  cache; downstream falls back to DB read).
* Publish-fail → DB+KV stand (downstream recovers via KV read or via
  the next compute on the next closed candle).

Per-feature ``except Exception`` (NOT bare ``except``) so one bad
feature does not kill the consumer or absorb
:class:`asyncio.CancelledError`. Mirrors T-104b ``_consume_symbol``
error semantics.

Template-substitution invariant (per Write-time guidance #1 in
``docs/plans/T-110c.md``): :mod:`features_registry` substitutes
``name_template`` once at registration time and packs the result into
the ``(feature_name, Feature)`` tuple. This module NEVER calls
``feature.name_template.format(...)`` itself — single source of truth
in the registry layer; T-111 YAML loader inherits the same contract.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.bus import MessageEnvelope
from packages.bus.schemas import FeatureUpdate, OhlcCandlePayload, subject_for
from packages.core import CorrelationId
from packages.db.queries.feature_engine import insert_feature
from packages.features.intervals import INTERVAL_DELTA
from packages.features.types import OhlcCandle

if TYPE_CHECKING:
    from collections.abc import Mapping

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient
    from packages.features.buffers import BufferHandle, BufferRegistry
    from packages.features.protocols import Feature
    from packages.features.types import FeatureValue


__all__ = ["FeaturePipeline"]


_PUBLISHER = "feature-engine"
_KV_BUCKET = "feature_latest"
_SUBJECT_WILDCARD = "market.ohlc.1m.>"

# Bucket-end offset per interval lives at packages.features.intervals
# (single source of truth per T-112 plan Concern #3 option B). T-110c
# pipeline subscribes 1m-only; F1+ multi-interval cagg-trigger live
# updates extend by adding keys to packages.features.intervals.INTERVAL_DELTA
# (no callsite change here). KeyError on unknown interval = fail-loud
# per §0.4 (misconfigured registry surfaces immediately).


class FeaturePipeline:
    """Per-closed-candle feature dispatch consumer (§9.3, 1m-only)."""

    def __init__(
        self,
        *,
        bus: NatsClient,
        pool: asyncpg.Pool[asyncpg.Record],
        buffer_registry: BufferRegistry,
        features_by_key: Mapping[tuple[str, str], list[tuple[str, Feature]]],
        logger: BoundLogger,
    ) -> None:
        self._bus = bus
        self._pool = pool
        self._buffer_registry = buffer_registry
        self._features_by_key = features_by_key
        self._logger = logger
        self._handles: dict[tuple[str, str], BufferHandle] = {}

    def acquire_handles(self) -> None:
        """Synchronously acquire one :class:`BufferHandle` per registered key.

        Split out of the prior monolithic ``start()`` (T-110d) so a
        composition root can sequence ``acquire_handles → warmup_load →
        start_consuming``. Without this split, warmup pushes silent-drop
        on un-allocated buffers OR race live frames and wedge
        out-of-order data into the deque.

        Sync because :meth:`BufferRegistry.acquire` is sync (T-110a is
        pure-domain). Empty registry → no-op; ``_handles`` stays empty.
        """
        if not self._features_by_key:
            return
        for key in self._features_by_key:
            symbol, interval = key
            self._handles[key] = self._buffer_registry.acquire(symbol, interval)

    async def start_consuming(self) -> None:
        """Subscribe to ``market.ohlc.1m.>`` so live frames begin arriving.

        Caller MUST invoke :meth:`acquire_handles` first; warmup_load
        (if any) must run between the two so the first live frame
        hits a populated tail. Empty registry → no subscription, no-op
        (matches T-104b empty-symbols precedent). The acquired handles
        are kept on ``self._handles`` keyed by ``(symbol, interval)``
        so :meth:`_dispatch` can call ``handle.tail(...)`` against the
        public API; the pipeline never ``_release``-s mid-operation,
        preserving the T-110a H-014 refcount contract.
        """
        if not self._features_by_key:
            self._logger.info("feature_pipeline_started_empty")
            return
        await self._bus.subscribe(_SUBJECT_WILDCARD, self._on_envelope)
        self._logger.info(
            "feature_pipeline_started",
            keys=sorted(self._features_by_key),
        )

    async def stop(self) -> None:
        """Release all acquired :class:`BufferHandle` instances.

        The NATS subscription itself is drained by
        :meth:`packages.bus.NatsClient.close`; the pipeline does not
        manually unsubscribe (mirrors T-104b convention).
        """
        for handle in self._handles.values():
            handle.__exit__(None, None, None)
        self._handles.clear()
        self._logger.info("feature_pipeline_stopped")

    async def _on_envelope(self, envelope: MessageEnvelope) -> None:
        """NATS handler — parse payload, route by symbol, dispatch closed candles."""
        try:
            payload = OhlcCandlePayload.model_validate(envelope.payload)
        except Exception as exc:
            # Malformed payload must not kill the subscription. Mirror
            # NatsClient._dispatch's swallow-and-log pattern.
            self._logger.error(
                "feature_pipeline_payload_parse_error",
                error=str(exc),
            )
            return
        if not payload.is_closed:
            # In-progress candles are normal traffic — no warning log.
            return
        candle = self._wire_to_domain(payload)
        await self._dispatch(candle)

    async def _dispatch(self, candle: OhlcCandle) -> None:
        """Run the dispatch loop for every registered 1m feature on ``candle``.

        Symbols not in ``features_by_key`` for the ``"1m"`` interval
        are silent-skipped with a debug log (defensive against NATS
        delivering candles for symbols outside the registered set).
        """
        key = (candle.symbol, "1m")
        registered = self._features_by_key.get(key, [])
        handle = self._handles.get(key)
        if not registered or handle is None:
            self._logger.debug("feature_pipeline_unregistered_symbol", symbol=candle.symbol)
            return
        self._buffer_registry.push(candle.symbol, "1m", candle)
        for feature_name, feature in registered:
            try:
                tail = handle.tail(feature.warmup_candles)
                feature_value = feature.compute(tail)
            except Exception as exc:
                self._logger.error(
                    "feature_pipeline_compute_error",
                    feature_name=feature_name,
                    symbol=candle.symbol,
                    error=str(exc),
                )
                continue
            await self._persist_kv_publish(feature_name, feature, candle, feature_value)

    async def _persist_kv_publish(
        self,
        feature_name: str,
        feature: Feature,
        candle: OhlcCandle,
        feature_value: FeatureValue,
    ) -> None:
        """DB → KV → publish; per error contract in module docstring."""
        update = self._build_update(feature_name, feature, feature_value, candle.symbol, candle)
        try:
            async with self._pool.acquire() as conn:
                await insert_feature(
                    conn,
                    feature_name=feature_name,
                    symbol=candle.symbol,
                    computed_at=update.computed_at,
                    value_num=update.value_num,
                    value_bool=update.value_bool,
                    value_json=update.value_json,
                    source_version=feature.source_version,
                )
        except Exception as exc:
            self._logger.error(
                "feature_pipeline_persist_error",
                feature_name=feature_name,
                symbol=candle.symbol,
                error=str(exc),
            )
            return
        kv_key = f"{feature_name}:{candle.symbol}"
        kv_value = update.model_dump_json().encode()
        try:
            await self._bus.kv_put(_KV_BUCKET, kv_key, kv_value)
        except Exception as exc:
            # KV is a best-effort cache — keep going to publish so the
            # NATS+DB stay consistent for downstream consumers.
            self._logger.error(
                "feature_pipeline_kv_error",
                feature_name=feature_name,
                symbol=candle.symbol,
                error=str(exc),
            )
        envelope = self._build_envelope(feature_name, candle.symbol, update)
        try:
            await self._bus.publish(subject_for(feature_name, candle.symbol), envelope)
        except Exception as exc:
            # Publish-fail leaves DB+KV intact; downstream recovers via
            # KV read or next compute. Log + continue.
            self._logger.error(
                "feature_pipeline_publish_error",
                feature_name=feature_name,
                symbol=candle.symbol,
                error=str(exc),
            )

    @staticmethod
    def _wire_to_domain(payload: OhlcCandlePayload) -> OhlcCandle:
        """Map §8.4 wire payload → §9.3 domain candle (Decimal preserved)."""
        return OhlcCandle(
            symbol=payload.symbol,
            interval=payload.interval,
            bucket_start=payload.bucket_start,
            open=payload.open,
            high=payload.high,
            low=payload.low,
            close=payload.close,
            volume=payload.volume,
            source=payload.source,
        )

    @staticmethod
    def _build_update(
        feature_name: str,
        feature: Feature,
        feature_value: FeatureValue,
        symbol: str,
        candle: OhlcCandle,
    ) -> FeatureUpdate:
        """Map domain :class:`FeatureValue` → wire :class:`FeatureUpdate`.

        Single Decimal→float conversion site:

        * ``value_num``: Decimal → ``float(...)``.
        * ``value_bool``: pass-through.
        * ``value_json``: ``{k: float(v) if isinstance(v, Decimal) else v}``;
          non-Decimal sub-values (str, bool, int, etc.) pass through
          per L-004 ``Mapping[str, object]`` openness (matches T-110b's
          ``dict[str, object] | None`` Pydantic schema).

        ``computed_at`` = ``candle.bucket_start + INTERVAL_DELTA[candle.interval]``
        (bucket end). KeyError on unknown interval is intentional
        fail-loud — see module docstring.
        """
        computed_at = candle.bucket_start + INTERVAL_DELTA[candle.interval]
        value_num: float | None = None
        value_bool: bool | None = None
        value_json: dict[str, object] | None = None
        if feature_value.value_num is not None:
            value_num = float(feature_value.value_num)
        elif feature_value.value_bool is not None:
            value_bool = feature_value.value_bool
        elif feature_value.value_json is not None:
            value_json = {
                k: float(v) if isinstance(v, Decimal) else v
                for k, v in feature_value.value_json.items()
            }
        return FeatureUpdate(
            feature_name=feature_name,
            symbol=symbol,
            computed_at=computed_at,
            value_num=value_num,
            value_bool=value_bool,
            value_json=value_json,
            source_version=feature.source_version,
        )

    @staticmethod
    def _build_envelope(
        feature_name: str,
        symbol: str,
        update: FeatureUpdate,
    ) -> MessageEnvelope:
        """Wrap a :class:`FeatureUpdate` in the §8.3 envelope.

        ``correlation_id = "feature:<feature_name>:<symbol>:<computed_at.isoformat()>"``
        — same bucket → same id intentionally (matches the
        ``insert_feature`` idempotency PK), so duplicate publishes for
        the same bucket carry a stable correlation-id.
        """
        correlation_id = CorrelationId(
            f"feature:{feature_name}:{symbol}:{update.computed_at.isoformat()}",
        )
        return MessageEnvelope(
            correlation_id=correlation_id,
            publisher=_PUBLISHER,
            payload=update.model_dump(mode="json"),
        )
