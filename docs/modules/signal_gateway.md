# Module: signal-gateway

Status: T-015a (skeleton) + T-015b1 (primitives, schemas, query module) +
T-015b2a (metrics + models + middleware + DI wiring) + T-015b2b (`/webhook`
handler + integration tests) — full §9.1 ingestion path live.

## Purpose

Public HTTP ingress for TradingView webhooks (brief §2.1, §9.1). T-015a ships
the FastAPI skeleton — process lifecycle, liveness/readiness probes, and the
Prometheus scrape target. T-015b1 adds the pure primitives + outbound schema
+ query module: HMAC verify, rate limiter, dedup ring, symbol-map cache,
`SignalValidated` schema, and `fetch_symbol_mapping` + `insert_signal`
queries. T-015b2a wires service metrics (§15.3 four counters/histogram),
inbound `SignalEnvelope` + response models, the rate-limit ASGI middleware,
and DI providers into the composition root. T-015b2b lands the `/webhook`
handler that orchestrates the full §9.1 13-step pipeline (see "Pipeline
wire order" below) plus the per-branch unit suite and the env-gated
PG + NATS end-to-end test (`tests/integration/test_webhook_e2e.py`).

## Public interface

HTTP endpoints (FastAPI, listens on `:8000`):

- `GET /health` — liveness. Returns `200 {}` when the process is up. No
  dependencies; never fails while the service is running.
- `GET /ready` — readiness. Returns `200 {"ready": true}` when
  `NatsClient.state == CONNECTED` **and** `asyncpg.Pool.acquire(timeout=1.0)`
  succeeds. Returns `503 {"ready": false, "reason": "bus"|"db"}` otherwise.
- `GET /metrics` — Prometheus text exposition (per-service
  `CollectorRegistry`). Process / platform / GC defaults plus the §15.3
  service-level counters declared in T-015b2a:
  `signals_received_total{source}`, `signals_validated_total{status}`,
  `errors_total{service, error_class}`, and the
  `webhook_processing_seconds` histogram. Counters with labels emit
  `# HELP` / `# TYPE` lines unconditionally; sample lines materialise after
  the first label combination is touched. The histogram observation is
  bound to `RateLimitMiddleware` so it covers the full ``POST /webhook``
  request lifecycle, including 429 short-circuits.
- `POST /webhook` — §9.1 ingestion. HMAC-authenticated, rate-limited
  (sliding window 20 req/60 s per IP, H-006), dedup-gated
  (`DedupRing` 10 s TTL + SIGNALS `duplicate_window=2 m`),
  symbol-resolved against `symbol_map`, persisted to `signals`, and
  fanned out to `signals.raw` (audit) + `signals.validated` (work).
  See "Pipeline wire order" + "Response codes" + "Log event catalog"
  sections below.

Consumed NATS subjects: none. Publisher-only role.

Published NATS subjects:

- `signals.raw` — every inbound webhook body that passed HMAC + rate limit,
  published before JSON parse so a malformed body still lands in the audit
  stream. Hazard H-010 fan-out; payload is
  `{"body_text", "source_ip", "received_at", "user_agent"}`.
  `correlation_id` on the envelope is a fresh UUID4 (raw is audit-only;
  no consumer joins on this ID, and reusing the request `trace_id` would
  conflate per-HTTP-request and per-signal-lineage ID spaces, §15.2).
  Publish failure is best-effort — logs `webhook_error
  error_class=publish_raw_failed` and the pipeline continues.
- `signals.validated` — `packages.bus.schemas.signals.SignalValidated`
  envelopes (§8.4), post-dedup and post-symbol-mapping. `Nats-Msg-Id` is
  derived via `message_id_for(idempotency_key)` (namespace UUID
  `_SIGNALS_VALIDATED_NS`) for server-side dedup alignment with the
  in-process ring across signal-gateway restarts. `correlation_id` on the
  envelope equals the signal's `idempotency_key`. Publish failure → 500.

DB tables read: `symbol_map` via `SymbolMapCache` wrapping
`packages.db.queries.signal_gateway.fetch_symbol_mapping`, 60 s in-process TTL.
DB tables written: `signals` via
`packages.db.queries.signal_gateway.insert_signal` (hypertable, migration
0002), marked `@non_idempotent` per §N3 (T-015d). Caller (the handler)
surfaces DB write failure as 500 and does not retry; operator / TradingView
re-send is the recovery path.

Configuration (Pydantic Settings, env-sourced per §5.11):

| Name                          | Purpose                                     | Constraint / use                                                                                                    |
|-------------------------------|---------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| `SERVICE_NAME`                | Structured-log `service` field (§15.1).     | Default `"signal-gateway"`.                                                                                         |
| `LOG_LEVEL`                   | structlog level.                            | `Literal["DEBUG","INFO","WARNING","ERROR","CRITICAL"]`; default `"INFO"`.                                           |
| `HTTP_PORT`                   | Uvicorn bind port.                          | Default `8000` (contract frozen by T-013a).                                                                         |
| `NATS_URL`                    | JetStream endpoint.                         | Default `nats://nats:4222`.                                                                                         |
| `DATABASE_URL`                | asyncpg DSN.                                | Required; scheme-validated at pool creation.                                                                        |
| `SIGNAL_GATEWAY_HMAC_SECRET`  | Shared HMAC-SHA256 secret (`SecretStr`).    | Required; `min_length=32` (§16.3 HMAC-SHA256 floor). Read by the `/webhook` handler at every request (constant-time compare). Per-bot routing via `X-Bot-Signal-Source` is open question #2 (F1). |

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

## Pipeline wire order

The actual `POST /webhook` request flow. Numbering reflects what the code
does; brief §9.1 prescribes the same set of operations but in a different
listed order, and the diff is documented below the table.

```
middleware: trace_scope (T-015a) → RateLimitMiddleware (T-015b2a) → handler
  1.  read raw body bytes (must precede any other body access)
  2.  verify HMAC over raw bytes              → 401 hmac_invalid
  3.  publish signals.raw                     (best-effort, fall through on fail)
  4.  JSON parse                              → 400 invalid_json
  5.  peek idempotency_key from parsed dict   (decides validation_unkeyed split)
  6.  SignalEnvelope.model_validate           → 400 validation_failed (DB row 'invalid')
                                              → 400 validation_failed (no DB row, unkeyed)
                                              → 500 internal (DB fail in audit-write)
  7.  signals_received{source}.inc            (post-Pydantic; source label safe)
  8.  bind_correlation(idempotency_key)
  9.  dedup.check_and_record                  → 202 duplicate (DB row 'duplicate')
  10. symbol_cache.resolve                    → 422 symbol_unknown (DB row 'invalid')
  11. insert_signal(ingestion_status=validated)  → 500 internal on DB fail
  12. publish signals.validated               → 500 internal on publish fail
  13. 200 {"signal_id": int}
```

Diffs vs the §9.1 numbered list:

- **Rate limit before HMAC.** §9.1 lists step 1 (HMAC) before step 3 (rate
  limit). Wire order inverts this: HMAC is O(body_len), rate limit is O(1)
  dict lookup. A storm of unsigned traffic gets dropped without spending
  HMAC CPU. Hazard H-006 posture. Rate-limit lives in
  :class:`RateLimitMiddleware` outside the handler proper; the handler
  starts at step 1 (raw-body read) and the limiter has already accepted.
- **Dedup after Pydantic validate.** §9.1 lists step 4 (dedup) before
  step 5 (validate). Wire order swaps them: `idempotency_key` must be a
  validated string before we can look it up in the dedup ring. The
  step 5 / step 6 split (peek-then-validate) is a refinement: a manual
  string extraction of the key drives the keyed-vs-unkeyed branching at
  step 6 without re-parsing.
- **`signals.raw` publish position.** Brief §9.1 step 8 lists raw + validated
  publishes together at the end. Hazard H-010 ("fan-out before dedup")
  requires `signals.raw` to fire before any de-duplicating gate, so the
  audit stream sees every webhook that cleared HMAC + rate limit. Wire
  order at step 3, immediately after HMAC. **Not** published for HMAC-
  invalid (401) or rate-limited (429) — H-006 storm protection (audit
  stream must not fill with attack traffic).

**Validation_failed dual-status.** A Pydantic-fail with a parseable
`idempotency_key` writes a `signals` row with `ingestion_status='invalid'`
and returns 400. If that DB write itself fails, the response flips to 500
internal — same shape as the happy-path DB failure. Caller's view: a 400
means the audit row exists and the handler classified the body as invalid;
a 500 means the handler couldn't even record what was wrong, retry. Both
flows log `signal_rejected reason=validation_error` to the trading stream;
the 500 path additionally logs `webhook_error error_class=db_insert_failed`
to the system stream.

## Response codes

| Status | Reason (machine-readable)              | Body shape                                              |
|--------|----------------------------------------|---------------------------------------------------------|
| 200    | —                                      | `{"signal_id": int}`                                    |
| 202    | —                                      | `{"status": "duplicate"}`                               |
| 400    | `invalid_json`                         | `{"detail": "invalid JSON body", "reason": "invalid_json"}` |
| 400    | `validation_failed`                    | `{"detail": "validation failed", "reason": "validation_failed"}` |
| 401    | `hmac_invalid`                         | `{"detail": "unauthorized", "reason": "hmac_invalid"}`  |
| 422    | `symbol_unknown`                       | `{"detail": "symbol not in symbol_map", "reason": "symbol_unknown"}` |
| 429    | `rate_limit`                           | `{"detail": "rate limited", "reason": "rate_limit"}`    |
| 500    | `internal`                             | `{"detail": "internal error", "reason": "internal"}`    |

`reason` is a closed `Literal[...]` on `WebhookErrorResponse`; out-of-set
values fail at body-construction time, never reach the wire.

## Log event catalog

| Event              | Stream    | Bound IDs                              | Fields                                                                               |
|--------------------|-----------|----------------------------------------|--------------------------------------------------------------------------------------|
| `signal_received`  | `trading` | `trace_id`                             | `source_ip`                                                                          |
| `signal_rejected`  | `trading` | `trace_id` (+ `correlation_id` post-validate) | `reason ∈ {hmac_invalid, rate_limit, invalid_json, duplicate, validation_error, symbol_unknown}` + reason-specific (`source_ip`, `idempotency_key`, `symbol`, `validation_errors[]`) |
| `signal_validated` | `trading` | `trace_id` + `correlation_id`          | `signal_id`, `symbol`, `original_symbol`, `action`, `source`, `idempotency_key`      |
| `webhook_error`    | `system`  | `trace_id` (+ `correlation_id` if bound) | `error_class ∈ {publish_raw_failed, publish_validated_failed, db_insert_failed, unhandled}`, `error` (exc str) |

`trace_id` is bound by the T-015a `bind_trace` HTTP middleware on every
request; `correlation_id = idempotency_key` is bound by the handler
immediately after `SignalEnvelope.model_validate` succeeds (§15.2 — signal
ingest is the canonical correlation ID origin point), so log events emitted
from step 9 onwards carry both IDs.

## Lifecycle

**Startup — synchronous primitives in `create_app` body:**

1. `Settings()` reads env via pydantic-settings (fail-fast on missing /
   malformed values, before uvicorn binds the port).
2. `observability.configure(level=settings.log_level)` — root stdlib +
   structlog handlers, JSON stdout. Idempotent.
3. `observability.add_redacted_keys("signal_gateway_hmac_secret",
   "x_signature")`.
4. Two stream-distinct loggers — `logger = get_logger("signal-gateway",
   "system")` and `trading_logger = get_logger("signal-gateway",
   "trading")` (§15.1 — handler emits `signal_*` events to trading and
   `webhook_error` to system).
5. `registry = build_registry()`; `metrics =
   build_signal_gateway_metrics(registry)` declares the §15.3 four
   metric handles on the same registry.
6. `RateLimiter()` and `DedupRing()` instantiated with defaults
   (window 60 s / limit 20 / dedup TTL 10 s).
7. `app = FastAPI(lifespan=...)` then sync items attached to `app.state`
   (`settings`, `logger`, `trading_logger`, `metrics`, `rate_limiter`,
   `dedup`).
8. `app.add_middleware(RateLimitMiddleware, ...)` then
   `@app.middleware("http") bind_trace`. Starlette prepends, so the
   last-registered runs outermost — `bind_trace` outer, rate limit inner
   (verified by `tests/test_app_factory.py::test_trace_middleware_runs_before_rate_limit_middleware`).
9. `include_router(health_router)` (T-015a) and
   `include_router(webhook_router)` (T-015b2b);
   `mount("/metrics", make_metrics_asgi_app(registry))`.

**Startup — asynchronous resources in lifespan:**

10. `pool = await create_pool(settings.database_url, ...)` — project
    defaults (pool sizing + command timeout) from `packages.db`.
11. `bus = NatsClient(servers=[settings.nats_url], ..., logger=logger);
    await bus.connect()`.
12. `symbol_cache = SymbolMapCache(pool)`.
13. Attach `pool`, `bus`, `symbol_cache` to `app.state` inside the
    `async with` block.
14. `logger.info("service_started", http_port=settings.http_port)`,
    yield.

**Per-request (ASGI middleware):** `bind_trace` HTTP middleware binds a
`trace_id` for the request lifetime (§15.2; from inbound `X-Request-ID`
header — nginx injects via `proxy_set_header X-Request-ID $request_id` —
or fresh `new_trace_id()` if absent). After step 6 of "Pipeline wire
order" the handler additionally binds `correlation_id = idempotency_key`.

**Shutdown (lifespan teardown):** `await bus.close()` first (drains tracked
subscriptions), then `await pool.close()`.

**Restart recovery.** The service holds no durable state. The dedup ring
(10 s TTL), symbol-map cache (60 s TTL), and rate-limit window
(20 req/60 s per IP) are acceptable to lose at restart because NATS
`SIGNALS.duplicate_window=2 m` provides server-side dedup across the
window and 60 s of rate-limit state never breaches the documented limit.

## Edge cases

- **NATS unreachable at startup.** `NatsClient.connect()` runs with
  `max_reconnect_attempts=-1`; the TCP connect retries until success or
  process termination. `/ready` returns `503 reason="bus"` until
  `state == CONNECTED`. `/health` stays `200` — liveness is independent of
  readiness (§18.4).
- **PG unreachable at startup.** `create_pool()` raises; lifespan fails;
  uvicorn exits non-zero; Docker `restart: unless-stopped` retries.
- **PG slow (pool exhaustion).** `/ready` calls `pool.acquire(timeout=1.0)`
  and returns `503 reason="db"` on timeout. Webhook DB writes are bounded
  by the `packages.db.create_pool` `command_timeout` default (30 s); a
  blocked write surfaces to the handler as a 500 internal.
- **NATS disconnects mid-flight.** `NatsClient` enters `DISCONNECTED`,
  auto-reconnect kicks in (`reconnect_time_wait=2 s`); `/ready` flips to
  `503` until reconnected. Publish failures during the disconnect window
  surface as 500 internal on `signals.validated` (publishing failure is
  a partial-state condition — DB row already written; caller re-sends)
  or as a logged best-effort skip on `signals.raw`.
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
  `/health` / `/ready` / `/metrics`. T-015b2a/b extensions (state +
  middleware + webhook route) live in the same file (see "T-015b2a unit
  tests" below).

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

**T-015b2a unit tests** (extend `test_app_factory.py`):

- State attachment of sync primitives in `create_app` body
  (`settings`, `metrics`, `rate_limiter`, `dedup`, `logger`,
  `trading_logger`).
- Async-resource attachment after lifespan entry (`pool`, `bus`,
  `symbol_cache`).
- `RateLimitMiddleware` presence in `app.user_middleware`.
- Functional middleware-order: a forced 429 response carries the
  `X-Request-ID` header set by `bind_trace`, proving `bind_trace` ran
  outermost. If this test ever flips, swap the registration order in
  `main.py`.
- `/webhook` route registered post-T-015b2b `include_router`.

**T-015b2b unit tests:**

- `test_models.py` — `SignalEnvelope` happy + extras-migration semantics
  (TV v3 flat-alert shape, explicit-payload + extras merge, collision
  policy "explicit payload wins"), `model_validator(mode="before")` guards
  (non-dict payload pass-through preserves authoritative field error;
  list payload variant; non-dict input), field constraints
  (`action` enum, `min_length=1` on `idempotency_key` / `symbol` /
  `source`), response models (validated / duplicate construction +
  closed-Literal `reason` rejection on `WebhookErrorResponse`).
- `test_webhook.py` — per-branch handler orchestration (13 tests
  covering the full status code matrix), `insert_signal` patched at the
  webhook import site so the suite stays decoupled from
  `packages.db.queries` internals. Includes Flag A verification
  (`signals.raw` envelope correlation_id is a fresh UUID4) and Flag C
  verification (DB-fail in the validation_failed audit-write returns 500,
  not 400).

**T-015b2b integration tests** (`tests/integration/test_webhook_e2e.py`):

- Env-gated (`POSTGRES_TEST_DSN` + `NATS_TEST_URL`); module-skipped
  otherwise. Mirrors `tests/integration/migrations/conftest.py` harness for
  the throwaway-DB lifecycle.
- `test_webhook_full_round_trip`: signed POST → 200 + `signal_id`,
  `signals` row asserted (status `validated`, canonical/original symbol,
  payload migrated), `signals.validated` envelope received via a
  pre-lifespan `NatsClient.subscribe`, `expires_at - received_at ==
  timedelta(seconds=120)` exact.
- `test_webhook_duplicate_round_trip`: same `idempotency_key` twice →
  first 200, second 202 `{status: duplicate}`, two `signals` rows, only
  one `signals.validated` publish landed.
- T-016 will set the env vars under CI-full via testcontainers.

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
   `SymbolMapCache` bounds post-edit staleness to its 60 s TTL. F3+
   analytics-api will want event-bus-driven invalidation when operators
   edit `symbol_map` live.
5. **HMAC secret rotation protocol.** Today's single-secret rotation requires
   a restart; multi-secret + hot rotation lands with Open Question 2.
