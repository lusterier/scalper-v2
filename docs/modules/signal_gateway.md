# Module: signal-gateway

Status: T-015a (skeleton) — this doc.
Next revision: T-015b updates sections marked *future* to present tense when the
`/webhook` pipeline lands.

## Purpose

Public HTTP ingress for TradingView webhooks (brief §2.1, §9.1). T-015a ships a
hello-world skeleton — process lifecycle, liveness/readiness probes, and the
Prometheus scrape target — so the §19 F0 exit criterion "Prometheus scrapes
signal-gateway /metrics" can flip from DOWN to UP. The validation pipeline
(HMAC, rate limit, dedup ring, Pydantic validation, symbol-map resolution, DB
insert, NATS publish) is T-015b; the full `/webhook` surface from §9.1 does not
exist in T-015a and `POST /webhook` returns 404.

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
  are declared and incremented together in T-015b.
- `POST /webhook` — *future (T-015b)*. §9.1 9-step validation pipeline.

Consumed NATS subjects: none. Publisher-only role.

Published NATS subjects (*future, T-015b*):
- `signals.raw` — every inbound webhook body (post HMAC + parse), pre-dedup.
  Hazard H-010 fan-out.
- `signals.validated` — `SignalValidated` envelopes (§8.4), post-dedup and
  post-symbol-mapping.

DB tables read (*future, T-015b*): `symbol_map` (cached 60 s in-process).
DB tables written (*future, T-015b*): `signals` (hypertable, migration 0002).

Configuration (Pydantic Settings, env-sourced per §5.11):

| Name                          | Purpose                                   | T-015a use                           |
|-------------------------------|-------------------------------------------|--------------------------------------|
| `SERVICE_NAME`                | Structured-log `service` field (§15.1).   | `"signal-gateway"`.                  |
| `LOG_LEVEL`                   | structlog level.                          | `INFO`.                              |
| `HTTP_PORT`                   | Uvicorn bind port.                        | `8000` (contract frozen by T-013a).  |
| `NATS_URL`                    | JetStream endpoint.                       | `nats://nats:4222`.                  |
| `DATABASE_URL`                | asyncpg DSN.                              | `postgresql://…@postgres:5432/…`.    |
| `SIGNAL_GATEWAY_HMAC_SECRET`  | Shared HMAC secret (SecretStr).           | Loaded but unused; activated T-015b. |

## Dependencies

- `packages.core` — `now_utc`, `ScalperError`, marker decorators, domain types.
- `packages.observability` — `configure`, `get_logger`, `make_registry`,
  `make_metrics_asgi_app`, `trace_scope`, `new_trace_id`, `bind_correlation`,
  `add_redacted_keys`.
- `packages.db` — `create_pool`.
- `packages.bus` — `NatsClient`, `ConnectionState`. T-015b adds
  `packages.bus.schemas.SignalValidated`.
- External runtime: `fastapi`, `uvicorn[standard]`, `uvloop`,
  `pydantic-settings`.

Justification for the four external additions (§0.9): FastAPI is the brief §3.1
HTTP-server mandate; Uvicorn is its standard ASGI server; uvloop is the brief
§3.1 event-loop choice; pydantic-settings is the idiomatic shape for §5.11
("typed Pydantic settings model at startup").

## Lifecycle

**Startup (FastAPI lifespan, single composition root per §N6):**

1. `observability.configure(level=settings.log_level)` — root stdlib +
   structlog handlers, JSON stdout. Idempotent.
2. `observability.add_redacted_keys("signal_gateway_hmac_secret",
   "x_signature")` — forward-compatible with T-015b handlers.
3. `create_pool(settings.database_url, application_name="signal-gateway")`
   — project defaults from `packages.db` (pool sizing + command timeout).
4. `NatsClient(servers=[settings.nats_url], name="signal-gateway",
   logger=get_logger("signal-gateway", "system")); await nats.connect()`.
5. Attach `pool`, `nats`, `logger`, `registry` to `app.state`. FastAPI
   `Depends` providers in `app/deps.py` read from there; no module globals.

**Per-request (ASGI middleware):** bind a `trace_id` (fresh `new_trace_id()`
or from inbound `X-Request-ID` header injected by nginx) and optional
`correlation_id` via `trace_scope()` for the duration of the request, so every
log line emitted inside the handler carries both (§15.2).

**Shutdown (lifespan teardown):** `await nats.close()` first (drains tracked
subscriptions), then `await pool.close()`.

**Restart recovery.** The service holds no durable state. In T-015a there is
no in-memory state beyond the metric registry. In T-015b the dedup ring
(10 s TTL), symbol-map cache (60 s TTL), and rate-limit window (20 req/60 s per
IP) are all in-process — acceptable because NATS `SIGNALS.duplicate_window=2 m`
provides server-side dedup across a restart window, and a 60-second restart
against 20 req/60 s per IP does not exceed the documented limit.

## Edge cases

- **NATS unreachable at startup.** `NatsClient.connect()` runs with
  `max_reconnect_attempts=-1`; the TCP connect retries until success or
  process termination. `/ready` returns `503 reason="bus"` until
  `state == CONNECTED`. `/health` stays `200` — liveness is independent of
  readiness (§18.4).
- **PG unreachable at startup.** `create_pool()` raises; lifespan fails;
  uvicorn exits non-zero; Docker `restart: unless-stopped` retries.
- **PG slow (pool exhaustion).** `/ready` calls `pool.acquire(timeout=1.0)`
  and returns `503 reason="db"` on timeout. Webhook timeouts are a T-015b
  decision (likely bounded by `asyncpg` `command_timeout=30 s`).
- **NATS disconnects mid-flight.** `NatsClient` enters `DISCONNECTED`,
  auto-reconnect kicks in (`reconnect_time_wait=2 s`); `/ready` flips to
  `503` until reconnected. T-015b publish semantics during disconnect land in
  the next doc revision.
- **Slow downstream consumer.** Irrelevant. JetStream decouples publish
  latency from consumer speed; publish returns when the stream has durably
  stored the message.
- **`/metrics` scraped before lifespan completes.** The `/metrics` ASGI
  sub-app is mounted at factory time with the registry; default collectors
  return process gauges even before service-specific metrics are touched.

## Testing strategy

**T-015a unit tests** (no real PG / no real NATS, `TestClient(app,
lifespan="off")` + mocked `app.state`):

- `test_config.py` — Settings parses env correctly, rejects missing required
  vars, `SecretStr` redaction round-trip.
- `test_health.py` — `/health` returns `200 {}`.
- `test_ready.py` — `200` when bus `CONNECTED` + pool acquires; `503
  reason="bus"` when bus `DISCONNECTED`; `503 reason="db"` when pool acquire
  times out.
- `test_app_factory.py` — `create_app()` returns a `FastAPI`, exposes
  `/health`, `/ready`, `/metrics`, and has no `/webhook` route.

Fixtures: `AsyncMock`-wrapped `asyncpg.Pool` and a `NatsClient` stub
exposing a mutable `.state` property. No testcontainers — those land in
ci-full (T-016).

**T-015b future tests:**

- Unit: HMAC `compare_digest` behaviour, rate-limit sliding-window boundary,
  dedup ring TTL + concurrency, `SignalEnvelope` Pydantic edges, symbol-map
  cache hit/miss/expiry.
- Integration: full webhook → NATS → PG round-trip via testcontainers, skip
  unless `POSTGRES_TEST_DSN` + a NATS test URL are set. Mirrors
  `tests/integration/migrations/conftest.py` harness.
- Property (Hypothesis): dedup ring idempotency under concurrent inputs
  (H-006 test companion).

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
