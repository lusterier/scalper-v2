# Module: signal-gateway

Status: T-015a (skeleton) + T-015b1 (primitives, schemas, query module) — this doc.
Next revision: T-015b2 flips `*future (T-015b2)*` markers to present tense when the
`/webhook` pipeline wires the T-015b1 primitives into the handler.

## Purpose

Public HTTP ingress for TradingView webhooks (brief §2.1, §9.1). T-015a ships a
hello-world skeleton — process lifecycle, liveness/readiness probes, and the
Prometheus scrape target — so the §19 F0 exit criterion "Prometheus scrapes
signal-gateway /metrics" can flip from DOWN to UP. T-015b1 adds the pure
primitives + outbound schema + query module that the T-015b2 handler
orchestrates: HMAC verify, rate limiter, dedup ring, symbol-map cache,
`SignalValidated` schema, and `fetch_symbol_mapping` + `insert_signal`
queries. The full §9.1 `/webhook` surface lands in T-015b2; `POST /webhook`
returns 404 until then.

## Public interface

HTTP endpoints (FastAPI, listens on `:8000`):

- `GET /health` — liveness. Returns `200 {}` when the process is up. No
  dependencies; never fails while the service is running.
- `GET /ready` — readiness. Returns `200 {"ready": true}` when
  `NatsClient.state == CONNECTED` **and** `asyncpg.Pool.acquire(timeout=1.0)`
  succeeds. Returns `503 {"ready": false, "reason": "bus"|"db"}` otherwise.
- `GET /metrics` — Prometheus text exposition (per-service
  `CollectorRegistry`). T-015a exposes only `ProcessCollector` /
  `PlatformCollector` / `GCCollector` defaults; this is enough for the
  `infra/prometheus/prometheus.yml` scrape target to flip from DOWN to UP.
  The §15.3 service-level counters (`signals_received_total`,
  `signals_validated_total`, `errors_total`, `webhook_processing_seconds`)
  are declared and incremented together in T-015b2.
- `POST /webhook` — *future (T-015b2)*. §9.1 9-step validation pipeline.

Consumed NATS subjects: none. Publisher-only role.

Published NATS subjects (*future, T-015b2*):

- `signals.raw` — every inbound webhook body (post HMAC verify, pre-parse).
  Hazard H-010 fan-out; payload is
  `{"body_text", "source_ip", "received_at", "user_agent"}`.
- `signals.validated` — `packages.bus.schemas.signals.SignalValidated`
  envelopes (§8.4), post-dedup and post-symbol-mapping. Schema landed
  T-015b1; `Nats-Msg-Id` derived via
  `packages.bus.schemas.signals.message_id_for(idempotency_key)` (namespace
  UUID `_SIGNALS_VALIDATED_NS`) for server-side dedup alignment with the
  in-process ring across signal-gateway restarts.

DB tables read (*future, T-015b2*): `symbol_map` via T-015b1's
`SymbolMapCache` wrapping
`packages.db.queries.signal_gateway.fetch_symbol_mapping`, 60 s in-process TTL.
DB tables written (*future, T-015b2*): `signals` via T-015b1's
`packages.db.queries.signal_gateway.insert_signal` (hypertable, migration 0002).

Configuration (Pydantic Settings, env-sourced per §5.11):

| Name                          | Purpose                                     | Constraint / use                                                                                                    |
|-------------------------------|---------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| `SERVICE_NAME`                | Structured-log `service` field (§15.1).     | Default `"signal-gateway"`.                                                                                         |
| `LOG_LEVEL`                   | structlog level.                            | `Literal["DEBUG","INFO","WARNING","ERROR","CRITICAL"]`; default `"INFO"`.                                           |
| `HTTP_PORT`                   | Uvicorn bind port.                          | Default `8000` (contract frozen by T-013a).                                                                         |
| `NATS_URL`                    | JetStream endpoint.                         | Default `nats://nats:4222`.                                                                                         |
| `DATABASE_URL`                | asyncpg DSN.                                | Required; scheme-validated at pool creation.                                                                        |
| `SIGNAL_GATEWAY_HMAC_SECRET`  | Shared HMAC-SHA256 secret (`SecretStr`).    | Required; `min_length=32` (§16.3 HMAC-SHA256 floor). Wired to `verify_hmac` in T-015b1; consumed by `/webhook` in T-015b2. |

## Dependencies

- `packages.core` — `now_utc`, `ScalperError`, marker decorators, domain types.
- `packages.observability` — `configure`, `get_logger`, `make_registry`,
  `make_metrics_asgi_app`, `trace_scope`, `new_trace_id`, `bind_correlation`,
  `add_redacted_keys`.
- `packages.db` — `create_pool` (T-015a); `packages.db.queries.signal_gateway`
  exports `fetch_symbol_mapping` + `insert_signal` (T-015b1, wired in T-015b2).
- `packages.bus` — `NatsClient`, `ConnectionState` (T-015a);
  `packages.bus.schemas.SignalValidated` + `message_id_for` (T-015b1).
- External runtime: `fastapi`, `uvicorn[standard]`, `uvloop`,
  `pydantic-settings` (T-015a). T-015b1 adds no new runtime deps.
- Dev: `hypothesis~=6.152` (T-015b1, Hypothesis property test for `DedupRing`).

Justification for the four external runtime additions (§0.9): FastAPI is the
brief §3.1 HTTP-server mandate; Uvicorn is its standard ASGI server; uvloop
is the brief §3.1 event-loop choice; pydantic-settings is the idiomatic shape
for §5.11. Hypothesis is the brief §17.1 property-testing library.

## Lifecycle

**Startup (FastAPI lifespan, single composition root per §N6):**

1. `observability.configure(level=settings.log_level)` — root stdlib +
   structlog handlers, JSON stdout. Idempotent.
2. `observability.add_redacted_keys("signal_gateway_hmac_secret",
   "x_signature")` — forward-compatible with T-015b2 handlers.
3. `create_pool(settings.database_url, application_name="signal-gateway")`
   — project defaults from `packages.db` (pool sizing + command timeout).
4. `NatsClient(servers=[settings.nats_url], name="signal-gateway",
   logger=get_logger("signal-gateway", "system")); await nats.connect()`.
5. Attach `pool`, `nats`, `logger`, `registry` to `app.state`. FastAPI
   `Depends` providers in `app/deps.py` read from there; no module globals.
6. *T-015b2*: instantiate `RateLimiter`, `DedupRing`, `SymbolMapCache`
   (primitives from T-015b1) with their respective defaults and attach to
   `app.state`; register the rate-limit middleware and include the
   `/webhook` router.

**Per-request (ASGI middleware):** bind a `trace_id` (fresh `new_trace_id()`
or from inbound `X-Request-ID` header injected by nginx) via `trace_scope()`
for the duration of the request (§15.2). T-015b2 additionally binds
`correlation_id = idempotency_key` after successful parse.

**Shutdown (lifespan teardown):** `await nats.close()` first (drains tracked
subscriptions), then `await pool.close()`.

**Restart recovery.** The service holds no durable state. In T-015a and
T-015b1 the in-memory surface is the metric registry plus the (unwired)
pure primitives. In T-015b2 the dedup ring (10 s TTL), symbol-map cache
(60 s TTL), and rate-limit window (20 req/60 s per IP) are acceptable to
lose at restart because NATS `SIGNALS.duplicate_window=2 m` provides
server-side dedup across the window and 60 s of rate-limit state never
breaches the documented limit.

## Edge cases

- **NATS unreachable at startup.** `NatsClient.connect()` runs with
  `max_reconnect_attempts=-1`; the TCP connect retries until success or
  process termination. `/ready` returns `503 reason="bus"` until
  `state == CONNECTED`. `/health` stays `200` — liveness is independent of
  readiness (§18.4).
- **PG unreachable at startup.** `create_pool()` raises; lifespan fails;
  uvicorn exits non-zero; Docker `restart: unless-stopped` retries.
- **PG slow (pool exhaustion).** `/ready` calls `pool.acquire(timeout=1.0)`
  and returns `503 reason="db"` on timeout. Webhook timeouts are a T-015b2
  decision (likely bounded by `asyncpg` `command_timeout=30 s`).
- **NATS disconnects mid-flight.** `NatsClient` enters `DISCONNECTED`,
  auto-reconnect kicks in (`reconnect_time_wait=2 s`); `/ready` flips to
  `503` until reconnected. T-015b2 publish semantics during disconnect land
  in the next doc revision.
- **Slow downstream consumer.** Irrelevant. JetStream decouples publish
  latency from consumer speed; publish returns when the stream has durably
  stored the message.
- **`/metrics` scraped before lifespan completes.** The `/metrics` ASGI
  sub-app is mounted at factory time with the registry; default collectors
  return process gauges even before service-specific metrics are touched.

## Testing strategy

**T-015a unit tests** (no real PG / no real NATS, `TestClient(app)` + mocked
`app.state` via `conftest.py` monkey-patches on `create_pool` + `NatsClient`):

- `test_config.py` — Settings parses env correctly, rejects missing required
  vars, `min_length=32` on HMAC secret, `SecretStr` redaction in `repr(Settings())`.
- `test_health.py` — `/health` returns `200 {}`.
- `test_ready.py` — `200` when bus `CONNECTED` + pool acquires; `503
  reason="bus"` when bus `DISCONNECTED`; `503 reason="db"` on pool
  `TimeoutError` or `asyncpg.InterfaceError`.
- `test_app_factory.py` — `create_app()` returns a `FastAPI`, exposes
  `/health` / `/ready` / `/metrics`, no `/webhook` route in T-015a.

**T-015b1 primitive tests** (pure-class level, no FastAPI coupling):

- `test_security.py` — HMAC verify edges: valid signature, invalid, empty,
  tampered body, wrong secret, uppercase-hex rejection, mismatched-length
  (the constant-time `compare_digest` contract).
- `test_rate_limit.py` — sliding-window boundary (strict-less-than cutoff),
  over-limit rejection, post-eviction acceptance, distinct-key isolation,
  `limit=0` degenerate case.
- `test_dedup.py` — TTL rules, unique-key burst sweep bound, plus a
  Hypothesis property test oracle-ing `DedupRing` against a naive reference
  over arbitrary `(time, key)` event sequences (H-006 companion).
- `test_symbol_map.py` — cache hit / miss / post-TTL re-query / negative
  cache (unknown symbol cached as `None`) / distinct-key independence
  against a mocked `asyncpg.Pool`.

**T-015b2 future tests:**

- Unit: webhook handler orchestration across the §9.1 9 steps,
  `SignalEnvelope` Pydantic edges (unknown-top-level-fields migrated into
  `payload` via `model_validator(mode="before")`, required-field rejections),
  response-code mapping per condition.
- Integration: full `/webhook` → NATS → PG round-trip via testcontainers,
  skip unless `POSTGRES_TEST_DSN` + a NATS test URL are set. Mirrors
  `tests/integration/migrations/conftest.py` harness; activated under
  CI-full by T-016.

## Open questions

1. **HMAC timestamp + replay protection (§16.3) vs TradingView v3 alert
   format.** v3 alerts do not carry a timestamp in the body. Options: require
   a client-supplied header as part of the webhook contract, or rely on the
   dedup ring + NATS `duplicate_window` to bound replay exposure. ADR-blocked
   and deferred to F1 (owner: operator, decision point before T-F1 signal
   ingest hardening).
2. **Per-bot HMAC routing via `X-Bot-Signal-Source`.** §9.1 names the header;
   brief also permits a single shared secret for legacy. The per-bot path
   interacts with the F1 bot registry (migrations 0001 + future bot-config
   apply flow) that does not yet carry webhook-secret material. Deferred to
   F1 (owner: operator) with an ADR at that time.
3. **`signals.rejected.<bot_id>` fan-out.** §9.1 step 8 plus H-010 suggest
   per-bot rejection emission for scoring-engine visibility. Deferred to F3
   — depends on the bot registry existing.
4. **Symbol-map cache invalidation on admin CRUD.** The T-015b1
   `SymbolMapCache` bounds post-edit staleness to its 60 s TTL. F4+
   analytics-api will want event-bus-driven invalidation when operators
   edit `symbol_map` live.
5. **HMAC secret rotation protocol.** Today's single-secret rotation requires
   a restart; multi-secret + hot rotation lands with Open Question 2.
