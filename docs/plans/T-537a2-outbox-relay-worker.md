# T-537a2 — outbox relay worker: NEW packages/outbox/relay.py OutboxRelayWorker

**Type**: F5 numbered task (NOT a fix; counts toward F5 phase counter).
**Phase**: F5 (unlocked).
**Origin**: derived from T-537 cluster L-007 split per operator decision 2026-05-09. T-537a → T-537a1 (queries + types + migration; SHIPPED 2026-05-09 commit `6008ea0`/`d06aaee`) + T-537a2 (this; relay worker) + T-537b (signal-gateway integration).
**Date**: 2026-05-09.

## Background

T-537a1 delivered the durable storage half:

* `outbox_events` table (migration 0016) with partial pending index.
* `OutboxEvent` projection + `OutboxRelaySettings` (5 env-configurable knobs).
* 4 SQL helpers: `insert_outbox_event` / `select_pending_outbox_events` (FOR UPDATE SKIP LOCKED + SQL backoff math) / `mark_outbox_event_published` / `mark_outbox_event_failed`.

T-537a2 adds the **relay worker** — the consumer that polls pending events, publishes to NATS, and marks them as published or failed. T-537b will then wire the worker into signal-gateway lifespan + refactor `webhook.py:411-474` to write event-intent via `insert_outbox_event` inside the same tx as `insert_signal`.

## Operator decisions (2026-05-09 OQ session)

- **OQ-1 (T-537a2 round 1) = Serial publish**: per-event in batch — `await bus.publish` → `mark_published` / `mark_failed` → next event. Preserves FIFO order per service.
- **OQ-2 (T-537a2 round 1) = Silent cancel**: cancellation = shutdown, not failure. In-flight publish cancelled mid-tx → rollback, no `mark_failed`, `attempt_count` not incremented. Next poll picks the row up again. Mirror existing `dispatcher` / `shadow_worker` precedents.

Carried forward from T-537a parent + T-537a1 plan:
- Per-service relay in lifespan (OQ-2 round 1 of parent T-537a; T-537b will host it).
- Exponential backoff cap (OQ-4 round 1 of parent T-537a; backoff math is already in SQL via T-537a1's `select_pending_outbox_events`).
- Failed events kept forever (OQ-3 round 2 of parent T-537a).

## Transaction & lock semantics (BLOCKER #1 from plan-reviewer 2026-05-09 — explicit before AC)

The relay design picks **Variant B = batch-level tx** to combine FOR UPDATE SKIP LOCKED protection with serial per-event publish + mark semantics. Concrete shape:

```python
async with self._pool.acquire() as conn, conn.transaction():
    events = await select_pending_outbox_events(conn, ...)  # FOR UPDATE SKIP LOCKED
    if not events:
        # release tx + sleep + retry
        ...
        continue
    for event in events:
        try:
            # Reviewer-fix BLOCKER #2: relay CONSTRUCTS envelope from outbox row
            # fields; payload column stores BUSINESS event dict, NOT serialised
            # envelope. correlation_id is a separate column. publisher = service.
            envelope = MessageEnvelope(
                correlation_id=CorrelationId(event.correlation_id),
                publisher=self._service,
                payload=event.payload,
            )
            await self._bus.publish(event.subject, envelope)
            await mark_outbox_event_published(conn, event_id=event.id, published_at=now)
            # logger: outbox.relay.publish_succeeded
        except Exception as exc:  # NOT BaseException — CancelledError propagates uncaught
            await mark_outbox_event_failed(
                conn, event_id=event.id, last_attempt_at=now,
                last_error=str(exc), max_attempts=settings.max_attempts, failed_at=now,
            )
            # logger: outbox.relay.publish_failed (+ outbox.relay.exhausted if attempt_count+1 >= max)
    # tx commits at __aexit__ → all mark_* persist together; locks released.
```

Implications:

- **Locks held for entire batch**: FOR UPDATE SKIP LOCKED keeps the batch's rows locked from `select_pending` through to `tx.commit()`. Other replicas SKIP_LOCKED these rows during this window. Single-replica today is unaffected (one worker per service).
- **Atomic batch mark commit**: all `mark_published` + `mark_failed` calls in the iteration go to the SAME tx; one COMMIT at `async with` exit. Partial-batch failures are NOT a contradiction — failures generate `mark_failed` writes (not rollbacks); successes generate `mark_published` writes. Both commit together at batch end.
- **Per-event isolation via mark semantics, NOT via per-event tx**: AC#5's earlier wording ("per-event tx isolation") was misleading. Correct contract: failures isolated via `mark_failed` (logical isolation; row state). NO per-event tx scope.
- **Cancellation = clean rollback** (OQ-2 silent cancel + CONCERN #5 fix): `asyncio.CancelledError` raised inside `bus.publish` propagates UP UNCAUGHT (because except clause is `except Exception`, not `except BaseException`). The propagating exception causes `conn.transaction()` `__aexit__` to call `ROLLBACK`. ALL `mark_*` writes from the current batch are rolled back. Locks released. Rows return to `published_at IS NULL AND failed_at IS NULL` state with original `attempt_count`. Next poll picks them up again (after backoff window if they had prior `last_attempt_at`).
- **Concurrency / future scale-out**: when a second replica is deployed, FOR UPDATE SKIP LOCKED + batch-tx ensures both replicas see disjoint batches; T-537a1 testcontainer test `test_select_pending_for_update_skip_locked_disjoint_replicas` already pins this property.

This design closes BLOCKER #1 by picking Variant B verbatim. Tests #1-9 reflect this shape; integration test mirror is `tests/integration/queries/test_outbox.py:test_select_pending_for_update_skip_locked_disjoint_replicas` (T-537a1 already pins lock semantics; T-537a2 doesn't need a duplicate testcontainer test — the worker is a thin orchestrator over already-pinned helpers).

## T-537a2 scope (split from parent T-537a)

### `packages/outbox/relay.py` — NEW module

`OutboxRelayWorker` class. **No `run_relay_for_service` adapter** in T-537a2 — T-537b will own its lifespan integration shape (`asyncio.create_task(worker.run())` directly + `worker.stop()` in shutdown ordering). Reduces public surface ambiguity per BLOCKER #2 fix.

```python
class OutboxRelayWorker:
    """Per-service async relay: poll outbox_events → publish to NATS → mark.

    Hosted in service lifespan as ``asyncio.create_task(worker.run())``.
    Stop via ``await worker.stop()`` in shutdown ordering BEFORE bus.close()
    (per WG#6 contract from parent T-537a APPROVE — see __init__ docstring).

    Concurrency: ``select_pending_outbox_events`` uses FOR UPDATE SKIP LOCKED
    (T-537a1) → multiple replicas of the same service can run relays in
    parallel (future horizontal scale-out); single-replica today is unaffected.

    Per-event publish is SERIAL within a batch (OQ-1 2026-05-09 — preserves
    FIFO order per service in NATS subject-ordering consumer perspective).
    Per-event isolation via mark semantics: failures generate ``mark_failed``
    writes within the batch tx; successes generate ``mark_published`` writes;
    both commit together at batch tx exit. NO per-event tx scope (Variant B
    per Transaction & lock semantics).

    Failure handling:
    - bus.publish raises → ``mark_outbox_event_failed`` (attempt_count++,
      last_attempt_at, last_error; failed_at flipped if attempts exhausted).
    - bus.publish succeeds → ``mark_outbox_event_published``.
    - asyncio.CancelledError mid-publish (operator stop()) → silent cancel
      (OQ-2 2026-05-09): in-flight tx rolls back; row stays pending; next
      poll picks it up. attempt_count NOT incremented for cancellation.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        bus: BusProtocol,
        service: str,
        settings: OutboxRelaySettings,
        bound_logger: BoundLogger,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        ...

    async def run(self) -> None:
        """Main loop: poll → publish → mark; sleep poll_interval_s when batch empty.

        Returns cleanly on stop() via cancellation propagation.
        """
        ...

    async def stop(self) -> None:
        """Cancel + await termination. Idempotent."""
        ...
```

### Logger keys (5; per WG#7 from parent T-537a APPROVE)

**Definitive design (CONCERN #3 fix from plan-reviewer 2026-05-09)**: module-level `Final[str]` constants + `Final[frozenset[str]]` registry for test pinning. Mirror `packages/db/queries/audit.py` logger-event pattern.

```python
# packages/outbox/relay.py — module-level constants (top of file)
LOG_POLL_STARTED: Final[str] = "outbox.relay.poll_started"
LOG_PUBLISH_SUCCEEDED: Final[str] = "outbox.relay.publish_succeeded"
LOG_PUBLISH_FAILED: Final[str] = "outbox.relay.publish_failed"
LOG_EXHAUSTED: Final[str] = "outbox.relay.exhausted"
LOG_STOPPED: Final[str] = "outbox.relay.stopped"

_LOG_KEYS: Final[frozenset[str]] = frozenset({
    LOG_POLL_STARTED, LOG_PUBLISH_SUCCEEDED, LOG_PUBLISH_FAILED,
    LOG_EXHAUSTED, LOG_STOPPED,
})
```

Verbatim string literals — tests pin via `assert relay.LOG_POLL_STARTED == "outbox.relay.poll_started"` etc. NO f-string concatenation. NO class-level constants (worker is instantiated per service; constants are class-agnostic).

- `outbox.relay.poll_started` — emitted when a poll begins; kwargs `service` + `batch_size`.
- `outbox.relay.publish_succeeded` — emitted per successfully-published event; kwargs `service` + `event_id` + `subject` + `correlation_id` + `attempt_count` (the count BEFORE this success — useful for "succeeded after N retries" telemetry).
- `outbox.relay.publish_failed` — emitted per publish exception; kwargs `service` + `event_id` + `subject` + `correlation_id` + `attempt_count` (the count BEFORE this failed attempt) + `error`.
- `outbox.relay.exhausted` — emitted when an event hits `max_attempts` and `failed_at` is flipped; kwargs `service` + `event_id` + `subject` + `correlation_id` + `attempt_count` (final value = max_attempts).
- `outbox.relay.stopped` — emitted on graceful stop completion; kwargs `service`.

### Shutdown ordering contract (per WG#6 from parent T-537a APPROVE)

Documented in `OutboxRelayWorker.__init__` docstring:

```
Shutdown ordering for service lifespan (T-537b will follow):
  1. await worker.stop()    # cancel relay task + drain in-flight tx
  2. await bus.close()      # NATS unsubscribe + connection close
  3. await pool.close()     # asyncpg pool drain

Steps 2-3 run AFTER worker.stop() so any in-flight bus.publish call
inside the relay completes (or rolls back via cancel) BEFORE bus
disappears; pool.close() is last because the relay's tx wrapping the
publish needs the pool alive until cancellation propagates.
```

### Tests (`packages/outbox/tests/test_relay.py`)

Mock-based — `pool` + `bus` are AsyncMock fixtures; `bound_logger` is MagicMock. Tests pin behavior of:

1. **happy path with envelope construction round-trip** (CONCERN #4 fix → BLOCKER #2 corrected) — 2 events queued, each row has distinct `correlation_id: str` + `subject: str` + `payload: dict[str, Any]` (with stringified UUID/datetime/Decimal values inside payload from T-537a1 `_to_jsonable`; values stay as `str` after relay reconstruction since `MessageEnvelope.payload` is `dict[str, Any]` passthrough). `select_pending` returns both → for each: relay constructs `MessageEnvelope(correlation_id=event.correlation_id, publisher=service, payload=event.payload)`; `bus.publish` called with `(event.subject, envelope)`. Assertion captures `bus.publish.call_args` per event + verifies `envelope.correlation_id == event.correlation_id` + `envelope.publisher == "signal-gateway"` + `envelope.payload == event.payload` (dict equality on round-tripped business payload — stringified inner values stay as str; no Pydantic coercion claimed). `mark_published` called with correct id + published_at; logger keys `poll_started` + `publish_succeeded` × 2.
2. **publish failure (single event)** — 1 event queued; `bus.publish` raises `RuntimeError("nats unreachable")` → `mark_failed` called with correct args (event_id, last_attempt_at, last_error str, max_attempts, failed_at); logger key `publish_failed`.
3. **partial-batch failure isolation** — batch of 3 events; middle one's `bus.publish` raises → first event marked published, second marked failed, third event marked published. No event blocks the others.
4. **max-attempts exhaustion** — 1 event already at `attempt_count=99`, `max_attempts=100`; `bus.publish` raises → `mark_failed` called → logger key `exhausted` emitted (mock `select_pending` returns the event; mock `mark_failed` succeeds; verify both `publish_failed` AND `exhausted` log keys present, not just `publish_failed`).
5. **empty-batch sleep** — `select_pending` returns `[]` → `asyncio.sleep` patched to count calls; verify worker sleeps `poll_interval_s` then polls again. Stop after 2 sleep cycles.
6. **stop() during sleep** — worker is in the empty-batch sleep when `stop()` is called → `run()` returns cleanly; `stopped` log key emitted.
7. **stop() during in-flight publish** — `bus.publish` is a slow AsyncMock that awaits an `asyncio.Event`; `stop()` is called while the publish is in-flight → `CancelledError` propagates → silent cancel (no `mark_failed`); on next hypothetical re-run the row would re-poll. Verify `mark_failed.assert_not_called()` for that event ID; `stopped` log key emitted.
8. **§N3 marker assertions + logger key constants** — `OutboxRelayWorker.run` is NOT marked (it's a worker loop, not a single side-effect; CLAUDE.md §N3 applies to single external-write functions). Logger keys are module-level `Final[str]` constants + `_LOG_KEYS: Final[frozenset[str]]` registry per CONCERN #3 fix; test imports `packages.outbox.relay` and asserts `relay.LOG_POLL_STARTED == "outbox.relay.poll_started"` + 4 sibling assertions + `relay._LOG_KEYS == frozenset({"outbox.relay.poll_started", ...})` (5-element membership pin).

Plus 1 sanity test:

9. **Worker constructor + stop() before run()** — calling `stop()` before `run()` is idempotent + no-op (logger key `stopped` still emitted); covers lifespan-cleanup-on-startup-failure path.

Total: 9 unit tests in `test_relay.py`.

### `packages/outbox/__init__.py` — extend exports

Add `OutboxRelayWorker` to public exports (T-537b will import from there). `run_relay_for_service` adapter is OUT of T-537a2 scope per BLOCKER #2 fix from plan-reviewer pass-1.

## Out of scope (deferred to T-537b + T-537c/d)

- **T-537b**: signal-gateway integration. webhook.py refactor + lifespan wire-up + Settings composition + integration test exercising full pipeline.
- **T-537c (deferred indefinitely)**: execution-service migration to outbox.
- **T-537d (deferred indefinitely)**: strategy-engine migration to outbox.

## Files touched

### Source (2 files)

1. NEW `packages/outbox/relay.py` — `OutboxRelayWorker` class only. `run_relay_for_service` adapter DROPPED per BLOCKER #2 fix.
2. UPDATED `packages/outbox/__init__.py` — extend exports (+`OutboxRelayWorker`).

### Tests (1 file)

3. NEW `packages/outbox/tests/test_relay.py` — 9 mock-based unit tests.

### Documentation (4 files; chore commit)

4. `TASKS.md` — T-537a2 DONE entry; F5 phase counter advances `27/48 → 28/49`.
5. `docs/CLAUDE_CODE_BRIEF.md` — § 8.7 sub-section may gain a brief reference to `OutboxRelayWorker` (optional; if §8.7 already references the worker as future, no change needed).
6. `docs/status.md` — late-night XV section.
7. `docs/plans/T-537a2-outbox-relay-worker.md` — this plan doc (chore-staged per CLAUDE.md gate-1 contract).

## LOC budget

- `relay.py`: ~250-350 LOC (worker class + 5 module-level Final[str] logger key constants + `_LOG_KEYS` frozenset registry + run/stop lifecycle + per-event try/except + max-attempts exhaustion logic + module docstring with shutdown contract + Transaction & lock semantics block reference).
- `__init__.py`: +3 LOC (extend exports with `OutboxRelayWorker` only — `run_relay_for_service` adapter dropped per BLOCKER #2 fix).
- Tests: ~400-450 LOC across 9 tests in `test_relay.py`.
- Total feat commit: ~650-800 LOC; src ~256-350 LOC.

**LOC calibration note per CONCERN #6 from plan-reviewer 2026-05-09**: 256 src LOC is nominal estimate; L-014 active control flags worker-style tasks as 350-450 realistic ceiling. Per-event tx isolation contract + 5 logger emission paths + 3 cancel paths + max-attempts exhaustion path push toward upper end. **Pre-authorized §0.3 over-cap waiver per L-014 active control if drift-checker pass-1 flags >400 LOC src** — mirror T-511b1 (627 LOC) / T-512a (570 LOC) / T-513b1 (491 LOC) precedents. If overshoot crosses +50% (~600 LOC src), pause and escalate to operator for further split (no obvious split available — T-537a1 + T-537b cluster is already at 3-task split).

## Acceptance criteria (AC)

1. NEW `packages/outbox/relay.py` exports `OutboxRelayWorker` class. (`run_relay_for_service` adapter DROPPED from T-537a2 scope per BLOCKER #2 fix; T-537b owns its lifespan integration shape.)
2. `OutboxRelayWorker.__init__` accepts `pool` + `bus` + `service` + `settings: OutboxRelaySettings` + `bound_logger` + optional `clock` (`Callable[[], datetime]` per §N1; default `lambda: datetime.now(UTC)`).
3. `OutboxRelayWorker.__init__` docstring documents the 3-step shutdown ordering contract (`stop()` → `bus.close()` → `pool.close()`) per WG#6.
4. `OutboxRelayWorker.run()` main loop per "Transaction & lock semantics" Variant B: `async with self._pool.acquire() as conn, conn.transaction():` wraps the entire batch. Inside tx: `select_pending_outbox_events` (FOR UPDATE SKIP LOCKED + SQL backoff window) → for each event SERIALLY: relay constructs `MessageEnvelope(correlation_id=CorrelationId(event.correlation_id), publisher=self._service, payload=event.payload)` (BLOCKER #2 fix: payload column stores BUSINESS event dict; correlation_id is separate column; publisher = service name) → `await bus.publish(event.subject, envelope)` → `mark_outbox_event_published` on success; `mark_outbox_event_failed` on exception. Empty batch → close tx + `asyncio.sleep(settings.poll_interval_s)` + retry; non-empty → tx commits at `__aexit__`, then re-poll immediately (no sleep between active batches).
5. Partial-batch failure isolation via mark semantics (NOT per-event tx): failures generate `mark_failed` writes to the same batch tx; successes generate `mark_published` writes; one COMMIT covers all marks at batch tx exit. Failed event does NOT block successful peers' marks.
6. `mark_outbox_event_failed` is called with `now=clock()` for both `last_attempt_at` and `failed_at`. `max_attempts` sourced from `settings.max_attempts`.
7. Cancellation handling per OQ-2 = silent cancel: per-event try/except wraps `bus.publish` + `mark_*` calls with `except Exception` (NOT `except BaseException`) per CONCERN #5 fix from plan-reviewer 2026-05-09. `asyncio.CancelledError` propagates UP UNCAUGHT → batch-level `conn.transaction()` `__aexit__` rolls back ALL pending `mark_*` writes from the current batch → rows return to original `published_at IS NULL AND failed_at IS NULL` state with original `attempt_count`. NO `mark_failed` invocation. Verified by test #7.

8. Logger key constants per CONCERN #3 fix: module-level `Final[str]` constants (`LOG_POLL_STARTED`, `LOG_PUBLISH_SUCCEEDED`, `LOG_PUBLISH_FAILED`, `LOG_EXHAUSTED`, `LOG_STOPPED`) + `Final[frozenset[str]]` `_LOG_KEYS` registry at top of `relay.py`. NO class-level constants. NO f-string concatenation at log emission sites. Verified by test #8.
9. 5 logger keys emitted at the right places per WG#7 verbatim: `outbox.relay.poll_started` + `publish_succeeded` + `publish_failed` + `exhausted` + `stopped`. NO f-string concatenation at emission sites; reference module-level Final constants from AC#8.
10. `OutboxRelayWorker.stop()` cancels the running task + awaits termination + emits `outbox.relay.stopped` log; idempotent (multiple stop() calls = same result).
11. `OutboxRelayWorker` instance can be `stop()`ed before `run()` is ever called (lifespan-startup-failure path); `stopped` log emitted; no exception.
12. `packages/outbox/__init__.py` extended with `OutboxRelayWorker` export only. `run_relay_for_service` adapter NOT in T-537a2 scope per BLOCKER #2 fix.
13. Tests: 9 NEW mock-based unit tests in `test_relay.py` covering: happy path with envelope construction + single failure + partial-batch isolation + max-attempts exhaustion + empty-batch sleep + stop during sleep + stop during in-flight publish + logger keys constants + stop-before-run idempotency.
14. Repo regression: pytest 2117 → ~2126 expected (+9 net new unit tests). F5 phase counter advances `27/48 → 28/49` (numerator+1 for shipped, denominator+1 for new T-537a2 numbered task). Branch `feat/T-537a2-outbox-relay-worker` per CLAUDE.md branching policy.

## Hand verification

N/A — no financial math. The "math" in this task is integer counting (attempt_count) and float retry-policy (poll_interval_s sleep). Math-validator Gate 4 expected `VERIFIED — out of scope, math-validator skipped` (mirror T-537a1 precedent: `packages/outbox/` not in math-validator scope).

## Test plan ordering (§N4 TDD)

1. Write `OutboxRelayWorker.__init__` skeleton + `stop()` minimal (idempotent no-op variant) + logger keys as module-private frozenset + `__init__.py` exports update.
2. Write `test_relay.py` test #1 (happy path 2 events). Verify FAIL (run() not implemented yet).
3. Implement `run()` happy path serial loop. Verify test #1 PASS.
4. Add tests #2-3 (single failure + partial-batch isolation). Implement try/except per-event + `mark_failed`. Verify all 3 PASS.
5. Add test #4 (max-attempts exhaustion + `exhausted` log). Verify PASS.
6. Add test #5 (empty-batch sleep). Implement sleep branch. Verify PASS.
7. Add tests #6-7 (stop during sleep + stop during in-flight publish; CancelledError silent cancel). Verify PASS.
8. Add test #8 (logger key constants verbatim). Verify PASS.
9. Add test #9 (stop-before-run idempotency). Verify PASS.
10. Run repo-wide pytest. 2117 → ~2126 expected.
11. Drift-checker Gate 2 → brief-reviewer Gate 3 → math-validator Gate 4 (out-of-scope).

## Open questions

None — both T-537a2-specific OQs baked at plan time per operator session 2026-05-09; carried-forward decisions from parent T-537a + T-537a1 unchanged.

## Cross-references

- BRIEF §8.7 — outbox pattern (T-537a1 sub-section already documents the 3-step pattern).
- BRIEF §N1 UTC; §N3 idempotency (no new external writes — relay re-publishes via existing helpers); §N5 80% coverage; §N6 no globals (DI throughout); §N7 hexagonal (relay is messaging adapter).
- BRIEF §20 — no NEW hazard for T-537a2; H-### catalog entry deferred to T-537b when concrete dispatch surface is built.
- packages/bus/protocol.py — `BusProtocol.publish(subject, envelope)`.
- packages/bus/envelope.py — `MessageEnvelope` (frozen Pydantic; constructor accepts `correlation_id` + `publisher` + `payload`; relay constructs from outbox row fields per BLOCKER #2 fix).
- packages/outbox/queries.py — `select_pending_outbox_events` + `mark_outbox_event_published` + `mark_outbox_event_failed`.
- packages/outbox/types.py — `OutboxEvent` projection + `OutboxRelaySettings`.
- T-537a1 plan-doc — design decisions + 13-item Write-time guidance verbatim.
- TASKS.md `## Done` T-537a1 — origin; F5 counter narrative `27/48` after T-537a1 ship.
- docs/status.md late-night XIV — 7-bug audit progress tracker (Items 2 + 7 IN PROGRESS via T-537 cluster).
- L-007 split-watch active control: this plan IS the second task of the split (T-537a → T-537a1 + T-537a2).
- L-014 LOC calibration: ~256 src LOC under cap; smaller than typical new-infra cohort (T-511b1 627, T-512a 570, T-513b1 491, T-537a1 365).

## Mirror precedents

- `services/execution/app/shadow_worker.py` `ShadowWorker` class — `start()` / `stop()` lifecycle + bus subscription + active-tasks registry. Closer match for class shape (worker hosted in lifespan). T-537a2 worker is simpler (no per-event tasks; serial loop in single task).
- `services/execution/app/dispatcher.py` `ExecutionDispatcher` + `run_dispatcher_for_bot` — per-bot lifespan task with cancellation-safe shutdown.
- T-537a1 plan-doc — directly preceding plan in the cluster; provides the type + queries surface this task consumes.

## Write-time guidance

(Plan-reviewer pass-3 APPROVE 2026-05-09 verbatim 5-item active control list; binding for drift-checker + brief-reviewer Gate 2/3.)

1. Cross-reference v "Cross-references" sekcii (line 273) opraviť: `packages/bus/payloads.py — MessageEnvelope.model_validate for round-trip from JSONB` → `packages/bus/envelope.py — MessageEnvelope (frozen Pydantic; constructor accepts correlation_id + publisher + payload)`. Pass-3 fix odstránil `model_validate` pattern; cross-ref je residual stale string. 1-riadková úprava pri prvom edite plan-docu / na začiatku implementácie. [Already applied at plan time pre-implementation.]

2. Test #1 assertion `envelope.publisher == "signal-gateway"` predpokladá `service` kwarg do worker constructor sa volá `"signal-gateway"`. Test fixture musí explicitne pin-núť `service=signal-gateway` parametrický kwarg + asserciu — nie hardcoded literál v inom mieste.

3. Per AC#9 + WG#7 5-key registry: pri každom log-emit site v `relay.py` použiť modul-level Final konštantu (LOG_POLL_STARTED, LOG_PUBLISH_SUCCEEDED, LOG_PUBLISH_FAILED, LOG_EXHAUSTED, LOG_STOPPED). Žiadny f-string concat (`f"outbox.relay.{event}"`). Test #8 pinuje verbatim string match.

4. Per AC#7 cancellation contract: try/except v per-event loop MUSÍ byť `except Exception`, NIKDY `except BaseException`. CancelledError MUSÍ propagovať uncaught aby `conn.transaction()` __aexit__ vykonal ROLLBACK. Test #7 toto pinuje cez `mark_failed.assert_not_called()`.

5. Per L-014 LOC waiver framework: ak drift-checker Gate 2 pass-1 flagne >400 LOC src, commit msg pri SHIP musí obsahovať explicit operator §0.3 over-cap waiver (mirror T-511b1 / T-512a / T-513b1 precedent). Brief-reviewer Gate 3 reject SHIP bez waiveru.

## Branch step

Per CLAUDE.md branching policy:

1. `git checkout -b feat/T-537a2-outbox-relay-worker` BEFORE staging any changes.
2. Feat commit on branch (Source files 1-2 + Tests file 3).
3. Chore commit on branch (Documentation files 4-7).
4. `git checkout master && git merge --ff-only feat/T-537a2-outbox-relay-worker`.
5. `git push origin master`.
6. `git branch -d feat/T-537a2-outbox-relay-worker`.
