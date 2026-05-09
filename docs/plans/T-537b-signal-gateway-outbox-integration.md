# T-537b — signal-gateway outbox integration: atomic state-and-publish-intent + relay lifespan + closes audit Items 2+7

**Type**: F5 numbered task (NOT a fix; counts toward F5 phase counter).
**Phase**: F5 (unlocked).
**Origin**: derived from T-537 cluster L-007 split per operator decision 2026-05-09. T-537a → T-537a1 (queries + types + migration; SHIPPED commit `6008ea0` + `d06aaee`) + T-537a2 (relay worker; SHIPPED commit `d30f36a` + `b97b8bd`) + T-537b (this; signal-gateway integration). T-537c (execution-service) + T-537d (strategy-engine) deferred indefinitely. Companion `fix(T-537a1-sql-typecast)` shipped 2026-05-09 commit `d1b531d` + `051b5a2` unblocked ci-full.
**Date**: 2026-05-09.

## Background

T-537a1 + T-537a2 delivered the outbox infrastructure half:

* `outbox_events` table + indexes (migration 0016).
* `OutboxEvent` projection + `OutboxRelaySettings` (5 env-configurable knobs).
* 4 SQL helpers (insert_outbox_event @non_idempotent + select_pending FOR UPDATE SKIP LOCKED + mark_published @idempotent + mark_failed @non_idempotent).
* `OutboxRelayWorker` class with run/stop lifecycle, batch-tx Variant B semantics, envelope construction from row fields, silent-cancel propagation, 5 module-level Final[str] log key constants.

T-537b wires this into **signal-gateway** — the first concrete consumer of the outbox pattern. After this task, the audit gap (Items 2 + 7) is structurally closed:

* **Item 2 (signal-loss between dedup-record and publish)**: dedup ring + `insert_signal` + `insert_outbox_event` all commit in a single tx. NATS publish failure no longer loses signals — relay retries until success (or until `max_attempts` exhaustion, where the row stays for admin replay).
* **Item 7 (outbox-publish reliability gap)**: same mechanism — outbox is the durable buffer between business state and NATS. The "swallowed publish exception" path in old `webhook.py:460-474` is removed entirely.

## Operator decisions (2026-05-09 OQ session)

- **OQ-1 = Single tx**: `async with pool.acquire() as conn, conn.transaction(): insert_signal + insert_outbox_event` — atomic state-and-publish-intent. tx rollback on either failure → no signals row + no outbox row. Splnenie Item 2 + 7.
- **OQ-2 = Full removal of direct bus.publish**: every "signals.validated" event flows through outbox. Latency: ~poll_interval_s (default 1.0s). Consistent with outbox semantics.
- **OQ-3 = NEW H-034 hazard**: outbox relay shutdown ordering pinned in BRIEF §20 catalog: `worker.stop()` → `bus.close()` → `pool.close()`.
- **OQ-4 = Testcontainer PG + mocked NATS**: integration test mirrors T-537a1 + T-537a2 pattern. `POSTGRES_TEST_DSN` gating; no new NATS server dep. Test verifies full pipeline via mock bus.publish call assertions.

## Scope

### `services/signal_gateway/app/webhook.py` — Step 11 + 12 collapse into single tx

Current shape (lines 408-474):

```python
# Step 11 — DB write (validated).
try:
    async with pool.acquire() as conn:
        signal_id = await insert_signal(conn, ...)
except Exception as db_exc:
    return _err_response(500, "internal", reason="internal")

# Step 12 — signals.validated publish.
expires_at = received_at + timedelta(seconds=_SIGNAL_TTL_SECONDS)
validated_payload = SignalValidated(...)
try:
    validated_envelope = MessageEnvelope(
        message_id=message_id_for(envelope.idempotency_key),
        correlation_id=CorrelationId(envelope.idempotency_key),
        publisher="signal-gateway",
        payload=validated_payload.model_dump(mode="json"),
    )
    await bus.publish("signals.validated", validated_envelope)
except Exception as pub_exc:
    return _err_response(500, "internal", reason="internal")

# Step 13 — 200 OK.
return JSONResponse(status_code=200, content=...)
```

New shape:

```python
# Step 11 — DB write (validated) + outbox row (publish-intent) in one tx.
expires_at = received_at + timedelta(seconds=_SIGNAL_TTL_SECONDS)
validated_payload = SignalValidated(
    source=envelope.source,
    idempotency_key=envelope.idempotency_key,
    received_at=received_at,
    symbol=canonical,
    original_symbol=envelope.symbol,
    action=envelope.action,
    expires_at=expires_at,
    payload=envelope.payload,
)
try:
    async with pool.acquire() as conn, conn.transaction():
        signal_id = await insert_signal(
            conn,
            received_at=received_at,
            schema_version="1.0",
            source=envelope.source,
            idempotency_key=envelope.idempotency_key,
            symbol=canonical,
            original_symbol=envelope.symbol,
            action=envelope.action,
            payload=envelope.payload,
            ingestion_status="validated",
            correlation_id=envelope.idempotency_key,
        )
        await insert_outbox_event(
            conn,
            service="signal-gateway",
            subject="signals.validated",
            correlation_id=envelope.idempotency_key,
            payload=validated_payload.model_dump(mode="json"),
            created_at=received_at,
        )
except Exception as db_exc:
    metrics.errors.labels(
        service="signal-gateway",
        error_class="db_insert_failed",
    ).inc()
    system_log.error(
        "webhook_error",
        error_class="db_insert_failed",
        error=str(db_exc),
    )
    return _err_response(500, detail="internal error", reason="internal")

# Step 12 — 200 OK (outbox relay handles NATS publish post-commit).
metrics.signals_validated.labels(status="validated").inc()
trading_log.info(
    "signal_validated",
    signal_id=signal_id,
    symbol=canonical,
    original_symbol=envelope.symbol,
    action=envelope.action,
    source=envelope.source,
    idempotency_key=envelope.idempotency_key,
)
return JSONResponse(status_code=200, content=WebhookValidatedResponse(signal_id=signal_id).model_dump())
```

Key deltas:

- **Step 12 publish path REMOVED entirely** (lines 440-474 in original). Lines 440 step number renumbered to "Step 12 — 200 OK".
- **`pool.acquire()` + `conn.transaction()` block** wraps both helpers (insert_signal + insert_outbox_event); single COMMIT atomic.
- **`SignalValidated` payload constructed BEFORE the tx** so its `model_dump` cost doesn't extend the tx window (read-only outside tx; safe to compute eagerly).
- **Error log key** `webhook_error` with `error_class="db_insert_failed"` covers both helpers (single error path now); no separate `publish_validated_failed` error class.
- **Metric `errors` increment** on tx failure; no separate `publish_validated_failed` metric.
- **Log `signal_validated` AFTER** tx commit + 200 response — order unchanged.

`MessageEnvelope` import + construction REMOVED from webhook.py (it's now constructed inside `OutboxRelayWorker._run_one_batch` per T-537a2). `message_id_for` import preserved if used elsewhere; otherwise drop.

Step 9 dedup branch (lines 322-356) and Step 10 invalid-symbol branch (lines 365-406) — these write to `signals` with status `duplicate` / `invalid` and do NOT publish to NATS in current code. They DO NOT need outbox integration: they're dead-end audit rows, not events for downstream consumers. **OUT OF T-537b scope** for outbox integration; their `pool.acquire()` blocks stay unchanged.

### `services/signal_gateway/app/main.py` — lifespan integration

Current shape (lines 106-130):

```python
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = await create_pool(...)
    bus = NatsClient(...)
    await bus.connect()
    symbol_cache = SymbolMapCache(pool)
    app.state.pool = pool
    app.state.bus = bus
    app.state.symbol_cache = symbol_cache
    logger.info("service_started", http_port=settings.http_port)
    try:
        yield
    finally:
        await bus.close()
        await pool.close()
        logger.info("service_stopped")
```

New shape:

```python
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = await create_pool(...)
    bus = NatsClient(...)
    await bus.connect()
    symbol_cache = SymbolMapCache(pool)

    # T-537b: outbox relay worker. Hosted in lifespan as asyncio.create_task.
    # Shutdown ordering per H-034: worker.stop() → bus.close() → pool.close().
    outbox_relay = OutboxRelayWorker(
        pool=pool,
        bus=bus,
        service="signal-gateway",
        settings=settings.outbox_relay,
        bound_logger=logger,
    )
    relay_task = asyncio.create_task(outbox_relay.run(), name="outbox-relay-signal-gateway")

    app.state.pool = pool
    app.state.bus = bus
    app.state.symbol_cache = symbol_cache
    app.state.outbox_relay = outbox_relay  # exposed for tests + ops introspection

    logger.info("service_started", http_port=settings.http_port)
    try:
        yield
    finally:
        # H-034 shutdown ordering.
        await outbox_relay.stop()
        await bus.close()
        await pool.close()
        logger.info("service_stopped")
    # relay_task is awaited via stop(); reference held to satisfy Ruff RUF006.
    _ = relay_task
```

`relay_task` reference held (not GC'd by holding the bound name until stop() awaits it). Final `_ = relay_task` line satisfies Ruff RUF006 ("Store a reference to the return value of asyncio.create_task").

### `services/signal_gateway/app/config.py` — Settings composition

Current `Settings` has flat fields (database_url, nats_url, http_port, etc.). Add `outbox_relay` as nested field:

```python
from packages.outbox import OutboxRelaySettings

class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    # ... existing fields ...

    # T-537b — outbox relay worker config (env_prefix=OUTBOX_RELAY_*).
    outbox_relay: OutboxRelaySettings = Field(default_factory=OutboxRelaySettings)
```

`OutboxRelaySettings.model_config = SettingsConfigDict(env_prefix="OUTBOX_RELAY_")` — defaults work without `.env` overrides; env vars `OUTBOX_RELAY_POLL_INTERVAL_S`, `OUTBOX_RELAY_BATCH_SIZE`, etc. flow through.

`Field(default_factory=OutboxRelaySettings)` ensures the nested settings model is constructed lazily so its env_prefix-driven loading happens on first access.

### Tests

#### Unit tests (`services/signal_gateway/tests/test_webhook.py` extension)

Existing test_webhook.py covers happy + duplicate + invalid + DB-fail paths. Updates needed:

1. **Existing happy path test** — assertion that `bus.publish` was awaited with subject `"signals.validated"` REMOVED. Replace with assertion that `insert_outbox_event` was called with correct kwargs (service="signal-gateway", subject="signals.validated", correlation_id=idempotency_key, payload=validated_payload.model_dump, created_at=received_at).
2. **Existing publish-failure test** — REMOVED (no longer relevant; bus.publish path is gone from webhook).
3. **NEW test** — `test_validated_path_calls_insert_signal_and_insert_outbox_event_in_same_tx`: verify both helpers invoked inside a single `conn.transaction()` context manager (mock pool.acquire returning a single shared connection; mock conn.transaction tracking enter/exit).
4. **NEW test** — `test_validated_path_does_not_call_bus_publish`: assert `bus.publish.assert_not_called()` for the validated path (proves outbox routing is exclusive).
5. **NEW test** — `test_validated_path_tx_rollback_on_outbox_insert_failure`: mock `insert_outbox_event` to raise; verify `insert_signal` write NOT visible after tx rollback (mock conn.transaction __aexit__ tracks exception type); assert 500 response + log error_class="db_insert_failed".
6. **Existing duplicate + invalid path tests** — UNCHANGED (those paths don't go through outbox per scope).

#### Integration test (`services/signal_gateway/tests/integration/test_webhook_outbox_e2e.py` NEW)

Testcontainer-gated (POSTGRES_TEST_DSN); mocked NATS bus. Mirror existing `test_webhook_e2e.py` shape.

Test flow:

1. Start app (testcontainer-migrated DB, mocked NatsClient).
2. POST `/webhook` with valid signal envelope (HMAC-signed).
3. Assert 200 response + `signal_id` returned.
4. Query `signals` table: row exists with `ingestion_status="validated"`.
5. Query `outbox_events` table: row exists with `service="signal-gateway"`, `subject="signals.validated"`, `correlation_id=idempotency_key`, `published_at IS NULL` (relay hasn't polled yet).
6. Wait for relay poll cycle (or trigger directly via `app.state.outbox_relay._run_one_batch()`); assert `bus.publish.assert_awaited_once_with("signals.validated", <envelope>)`.
7. Query `outbox_events` again: `published_at IS NOT NULL` (relay marked).

Skip-gated on POSTGRES_TEST_DSN per F1 integration pattern (mirrors T-537a1 + T-537a2 testcontainer convention).

### Documentation

- `docs/CLAUDE_CODE_BRIEF.md` — NEW H-034 hazard entry in §20 (after H-033, before §21 Glossary). Plus §9.1 signal-gateway pipeline step 12 reword (remove publish step; reference outbox).
- `TASKS.md` — T-537b DONE entry; F5 phase counter advances `28/49 → 29/50`.
- `docs/status.md` — late-night XVII section.
- `docs/plans/T-537b-signal-gateway-outbox-integration.md` — this plan doc (chore-staged per CLAUDE.md gate-1 contract).
- `docs/review-lessons.md` — no new lesson for T-537b (no generalizable catch beyond existing L-007/L-008/L-013/L-021 active controls re-applied).

## NEW H-034 hazard text (for BRIEF §20)

```
### H-034 — Outbox relay shutdown ordering: stop() → bus.close() → pool.close()

**Context.** `OutboxRelayWorker` (T-537a2) holds an open DB tx during a batch
publish (Variant B per T-537a2 plan §"Transaction & lock semantics"). The tx
contains `select_pending_outbox_events` (FOR UPDATE SKIP LOCKED locks held
through the publish + mark cycle), one or more `bus.publish` calls, and the
mark_published / mark_failed writes that commit at batch tx exit. If the
service lifespan tears down `bus` BEFORE `worker.stop()` cancels the relay
task, an in-flight `bus.publish` raises `ConnectionClosedError` mid-batch
→ the per-event try/except catches it, marks the event failed → on next
relay poll the event would be retried. Worse: if `pool` closes before
`worker.stop()`, the in-flight tx loses its connection mid-mark → asyncpg
`InterfaceError: connection is closed` propagates uncaught → tx is left
in an indeterminate state (PG aborts the connection's tx automatically but
the worker's context-manager exit is in an exception path). T-537b first
shipped the wiring; precedent enforced via `services/signal_gateway/app/main.py`
lifespan.

**Policy.** Service lifespan teardown order MUST be:

  1. `await worker.stop()` — cancels relay task; awaits termination; in-flight
     tx rolls back via CancelledError propagation.
  2. `await bus.close()` — NATS unsubscribe + connection close. Safe AFTER
     stop because relay's bus.publish calls have completed (or rolled back).
  3. `await pool.close()` — asyncpg pool drain. Safe AFTER bus.close because
     relay no longer holds a conn.

For services hosting multiple outbox relays (future T-537c execution + T-537d
strategy-engine), all relay `worker.stop()` calls run BEFORE bus.close.

**Test.** `test_lifespan_shutdown_order_stops_relay_before_bus_close` (NEW
in `services/signal_gateway/tests/test_app_factory.py`) — patches `bus.close`
+ `pool.close` + `OutboxRelayWorker.stop` to record call order; asserts
`stop` < `bus.close` < `pool.close`.

H-034 numbering note: companion to H-030 (open-fill remaining_qty) + H-031
(paper adapter must not feed live ExecutionDispatcher) + H-032 (retry loop
exception coverage) + H-033 (composite-PK position_state UPDATE trade_id
guard). H-030..H-034 all derive from operator audit 2026-05-08/05-09; H-034
specifically closes audit Items 2 + 7 (signal-loss publish-after-dedup +
outbox-publish reliability gap) by enforcing relay-host shutdown contract.
```

## Out of scope (deferred)

- **T-537c (deferred indefinitely)**: execution-service migration to outbox. Currently `placement_persist.py` + `reconcile.py` use `emit_post_commit_*` pattern with try/except swallow on publish failure. Outbox migration follows same shape as T-537b but for execution-service.
- **T-537d (deferred indefinitely)**: strategy-engine migration to outbox. Currently `consumer.py` has 3 `bus.publish` call sites with no DB writes between consume and publish.
- Integration test in **full E2E mode** (real NATS) — deferred. Testcontainer-gated mocked-NATS suffices for T-537b per OQ-4.

## Files touched

### Source (3 files)

1. `services/signal_gateway/app/webhook.py` — Step 11+12 collapse into single tx + remove direct bus.publish path. Imports cleanup (`MessageEnvelope`, `message_id_for`, `CorrelationId` may no longer be needed; verify at edit time). NEW import `from packages.outbox import insert_outbox_event`.
2. `services/signal_gateway/app/main.py` — lifespan: construct `OutboxRelayWorker` + `asyncio.create_task(worker.run())` + shutdown ordering per H-034. NEW import `from packages.outbox import OutboxRelayWorker`.
3. `services/signal_gateway/app/config.py` — `Settings.outbox_relay: OutboxRelaySettings = Field(default_factory=OutboxRelaySettings)`. NEW import `from packages.outbox import OutboxRelaySettings`.

### Tests (3 files)

4. `services/signal_gateway/tests/test_webhook.py` — UPDATE existing happy path + REMOVE publish-failure test + ADD 3 NEW tests per scope.
5. `services/signal_gateway/tests/test_app_factory.py` — ADD NEW `test_lifespan_shutdown_order_stops_relay_before_bus_close` per H-034 test pin.
6. `services/signal_gateway/tests/integration/test_webhook_e2e.py` (UPDATED per plan-reviewer pass-1 CONCERN #1) — existing testcontainer-gated test `test_webhook_full_round_trip` + `test_webhook_duplicate_round_trip` use `_NATS_DELIVERY_TIMEOUT_SECONDS = 5.0` waiting for NATS-delivered envelope. Post-T-537b that delivery flows through outbox relay (default `poll_interval_s=1.0s` + publish + mark = ~1.5s typical), so the 5s timeout REMAINS sufficient — but assertions must change since payload now arrives via relay-published envelope NOT direct webhook publish. Choice (b) per pass-1 CONCERN #1: KEEP both tests, EXTEND `_NATS_DELIVERY_TIMEOUT_SECONDS` to `10.0` (defensive against test-runner CI slowness — 2x typical relay cycle + jitter), and UPDATE test docstrings to reflect "post-relay delivery" assertion path. Both tests verify pipeline end-to-end which is MORE valuable post-T-537b (covers the new relay hop).
7. NEW `services/signal_gateway/tests/integration/test_webhook_outbox_e2e.py` — testcontainer-gated mocked-NATS pipeline test (per OQ-4): verifies `signals` row + `outbox_events` row creation in single tx + relay poll + bus.publish call args + mark_published flip. Complements (does not duplicate) `test_webhook_e2e.py` which uses real NATS.

### Documentation (6 files; chore commit)

8. `docs/CLAUDE_CODE_BRIEF.md` — NEW H-034 entry between H-033 and §21 Glossary; §9.1 step 12 reword.
9. `docs/modules/signal_gateway.md` (per plan-reviewer pass-1 CONCERN #2) — UPDATE 4 sites: (i) line 127 step 12 reword to mirror BRIEF §9.1 reword (publish via outbox relay; signal-gateway no longer publishes directly); (ii) line 186 error_class enum: REMOVE `publish_validated_failed` (path no longer exists); (iii) line 267 "500 internal on signals.validated" → reword as tx-fail single error path covering both insert_signal AND insert_outbox_event; (iv) line 352 e2e assertion description: align with extended timeout + post-relay delivery semantics from `test_webhook_e2e.py` per Test plan §6 above.
10. `TASKS.md` — T-537b DONE entry; F5 counter `28/49 → 29/50`.
11. `docs/status.md` — late-night XVII section.
12. `docs/plans/T-537b-signal-gateway-outbox-integration.md` — this plan doc.
13. `services/signal_gateway/pyproject.toml` — add `scalper-v2-outbox` workspace dep.

## LOC budget

- `webhook.py`: -50 LOC (remove publish path) + 15 LOC (insert_outbox_event call inside tx) = net **-35 LOC**.
- `main.py`: +15 LOC (relay construction + shutdown ordering).
- `config.py`: +5 LOC (1 import + 1 field).
- `pyproject.toml`: +1 LOC (dep).
- Tests: +200-250 LOC (~3 NEW unit tests in test_webhook.py + 1 NEW lifespan test in test_app_factory.py + ~150 LOC integration test).
- Total feat commit: ~200-250 LOC; src **net -14 LOC** (refactor reduces webhook complexity); under §0.3 400 src cap.

## Acceptance criteria (AC)

1. `services/signal_gateway/app/webhook.py` Step 11 + 12 collapsed into single `async with pool.acquire() as conn, conn.transaction():` block wrapping both `insert_signal` and `insert_outbox_event` calls. Single error path on tx failure (500 response + log key `webhook_error` with error_class="db_insert_failed").
2. Direct `bus.publish("signals.validated", ...)` call REMOVED from validated path. Outbox relay handles all NATS publishes.
3. Imports cleaned up: `MessageEnvelope` import removed if no longer used; `message_id_for` removed if no longer used; `CorrelationId` import preserved if used elsewhere (check). NEW import `from packages.outbox import insert_outbox_event`.
4. `services/signal_gateway/app/main.py` lifespan constructs `OutboxRelayWorker(pool=pool, bus=bus, service="signal-gateway", settings=settings.outbox_relay, bound_logger=logger)` AFTER `bus.connect()` AND BEFORE `app.state` attaches. Wraps `worker.run()` in `asyncio.create_task` named `"outbox-relay-signal-gateway"`.
4a. Lifespan shutdown ordering per H-034: `await outbox_relay.stop()` → `await bus.close()` → `await pool.close()`. Verified by test_lifespan_shutdown_order_stops_relay_before_bus_close.
5. `app.state.outbox_relay` set so tests + ops introspection can reach the worker instance.
6. `services/signal_gateway/app/config.py` `Settings.outbox_relay: OutboxRelaySettings = Field(default_factory=OutboxRelaySettings)` per OQ env-prefix routing.
7. NEW dependency `scalper-v2-outbox = { workspace = true }` in `services/signal_gateway/pyproject.toml`.
8. Tests: existing happy path test in `test_webhook.py` updated to assert `insert_outbox_event` invocation kwargs (NOT `bus.publish`); existing publish-failure test REMOVED; 3 NEW tests added (insert_signal + insert_outbox_event in same tx + bus.publish.assert_not_called + tx-rollback-on-outbox-failure).
9. NEW integration test `test_webhook_outbox_e2e.py` exercising full pipeline (POST /webhook → DB insert + outbox row → relay polls → bus.publish + mark_published) using mocked NATS bus per OQ-4. Existing `test_webhook_e2e.py` (real-NATS round-trip) PRESERVED with `_NATS_DELIVERY_TIMEOUT_SECONDS = 5.0 → 10.0` extension + post-relay delivery docstring update per plan-reviewer pass-1 CONCERN #1 fix.
10. NEW H-034 entry in BRIEF §20 (after H-033). Cross-link to plan + L-021. Numbering note relating to H-030..H-034 cluster from operator audit.
11. BRIEF §9.1 Step 12 reworded: "Publish to `signals.raw` (audit) and `signals.validated` via outbox (T-537 cluster; relay worker polls outbox_events + publishes; signal-gateway no longer publishes directly)." `docs/modules/signal_gateway.md` synchronized per plan-reviewer pass-1 CONCERN #2 fix (4 sites updated).
12. TASKS.md fix(T-537b) DONE entry; F5 counter `28/49 → 29/50` advance per L-007 split convention.
13. docs/status.md late-night XVII section prepended above late-night XVI; 7-bug audit progress tracker updates Items 2 + 7 → DONE.
14. Repo regression: `POSTGRES_TEST_DSN=... uv run pytest -q` → ~2244 + ~5 NEW tests = ~2249 expected. 0 regressions.
15. Branch `feat/T-537b-signal-gateway-outbox-integration` per CLAUDE.md branching policy. FF-merge to master + push + branch delete + verify CI green (per L-021 — locally exercise testcontainer tests with POSTGRES_TEST_DSN BEFORE push).

## Hand verification

N/A — no financial math. Math-validator Gate 4 expected `VERIFIED — out of scope, math-validator skipped`.

## Test plan ordering (§N4 TDD)

1. Read existing `test_webhook.py` happy + publish-failure tests for shape reference.
2. Apply `webhook.py` refactor (Step 11+12 collapse). Run existing test_webhook.py — expect failures on the 2 affected tests (happy publish assertion + publish-failure test).
3. Update existing happy test to assert `insert_outbox_event` invocation; delete publish-failure test. Re-run; expect PASS.
4. Add 3 NEW unit tests per scope; run; expect PASS.
5. Apply `main.py` + `config.py` lifespan changes.
6. Add `test_lifespan_shutdown_order_stops_relay_before_bus_close` per H-034. Run; expect PASS.
7. Add testcontainer integration test `test_webhook_outbox_e2e.py`. Run with `POSTGRES_TEST_DSN=...` per L-021 active control; expect PASS.
8. Run repo-wide pytest with POSTGRES_TEST_DSN. Expect 2244 + ~5 = ~2249 passed. 0 regressions.
9. Drift-checker Gate 2 → brief-reviewer Gate 3 → math-validator Gate 4 (out-of-scope expected).

## Open questions

None — all 4 OQs baked at plan time per operator session 2026-05-09.

## Cross-references

- BRIEF §9.1 — signal-gateway pipeline; step 12 will be reworded.
- BRIEF §8.7 — outbox pattern (T-537a1 chore commit added the sub-section reference).
- BRIEF §20 — hazard catalog; H-034 NEW.
- BRIEF §N1 UTC, §N3 idempotency, §N6 no globals (all preserved).
- packages/outbox/{queries,relay,types}.py — T-537a1 + T-537a2 helpers consumed.
- packages/db/queries/signal_gateway.py — `insert_signal` helper (signature unchanged).
- TASKS.md `## Done` T-537a1 + T-537a2 + fix(T-537a1-sql-typecast) — direct precedents.
- docs/status.md late-night XVI — last session; ci-full unblocked.
- L-007 split-watch: T-537a → T-537a1 + T-537a2 + T-537b (this is the third + final task of the cluster for now).
- L-008 testcontainer integration test pattern.
- L-013 codec-immune `_to_jsonable` JSONB (T-537a1 helpers handle this internally; webhook caller passes plain dict).
- L-021 (NEW from this session): locally execute testcontainer tests with `POSTGRES_TEST_DSN` BEFORE push. Active control applied to T-537b integration test.

## Mirror precedents

- T-537a1 + T-537a2 plan-docs (L-007 split siblings).
- T-217c integration test (testcontainer + assertions on real PG state).
- existing `services/signal_gateway/tests/integration/test_webhook_e2e.py` (testcontainer + signal-gateway full pipeline shape).
- existing `services/execution/app/main.py` lifespan + shutdown ordering (mirror for OutboxRelayWorker placement; e.g. shadow_worker.stop() pattern).

## Write-time guidance

(Plan-reviewer pass-2 APPROVE 2026-05-09 verbatim 5-item active control list; binding for drift-checker + brief-reviewer Gate 2/3.)

1. **Verify Step 11+12 collapse leaves no stranded imports** — webhook.py audit at edit time: `MessageEnvelope`, `message_id_for`, `CorrelationId` imports each verified used elsewhere or removed. AC#3 already requires this; flag any deletion in commit message so brief-reviewer can spot-check.

2. **`test_lifespan_shutdown_order_stops_relay_before_bus_close` must record exact call ordering**, not just check that all three were called. Use a shared list + side_effect lambdas appending labels; assert list equals `["worker.stop", "bus.close", "pool.close"]`. Pure presence-check would silently regress under reorder.

3. **Integration test `test_webhook_outbox_e2e.py` must trigger relay deterministically** — relying on `await asyncio.sleep(poll_interval_s + epsilon)` invites flake. Either call `app.state.outbox_relay._run_one_batch()` directly (per plan §"Test flow" step 6 already mentions this) OR use a private `outbox_relay._poll_now()` if it exists; document the choice in the test docstring so future readers know why sleep wasn't used.

4. **`_NATS_DELIVERY_TIMEOUT_SECONDS = 10.0` extension in `test_webhook_e2e.py` must include an inline comment** referencing T-537b + the relay-cycle math (poll_interval_s=1.0 + publish + mark ≈ 1.5s typical, 2x cushion = 3s, 10s defensive against CI slowness). Future devs trimming "unused timeout" risk regression without this provenance.

5. **`signal_gateway.md` 4 update sites should be applied as a single docs edit, NOT spread across multiple commits** — chore commit per Files touched § currently bundles all 6 doc files; verify all 4 sites land in the same chore commit so module doc never has partial drift between feat-commit and chore-commit.

## Branch step

Per CLAUDE.md branching policy:

1. `git checkout -b feat/T-537b-signal-gateway-outbox-integration` BEFORE staging any changes.
2. Feat commit on branch (Source files 1-3 + Tests files 4-6 + dep 11).
3. Chore commit on branch (Documentation files 7-10).
4. Per L-021 active control: locally run `POSTGRES_TEST_DSN=postgresql://scalper:devpass@localhost:5432/postgres uv run pytest services/signal_gateway/tests/integration/test_webhook_outbox_e2e.py -v` BEFORE push to verify testcontainer tests pass.
5. `git checkout master && git merge --ff-only feat/T-537b-signal-gateway-outbox-integration`.
6. `git push origin master`.
7. `git branch -d feat/T-537b-signal-gateway-outbox-integration`.
8. Verify ci-full + ci-fast + e2e green via `gh run list --branch master --limit 5`.
