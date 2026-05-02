# ADR-0007: APScheduler operational pattern (supervision + UTC + test fixtures)

Status: accepted
Date: 2026-05-02
Deciders: operator, Claude Code
Prerequisite for: T-220 (P&L audit loop + Migration 0009 trade_pnl_deltas).

## Context

Brief §3.1 line 302 mandates **APScheduler (in-process), no system cron** as the v2 scheduler. H-021 (line 2765-2771) tightens: "All scheduled jobs run via APScheduler in the service process, UTC only. No system cron." Test pin `test_daily_report_runs_at_configured_utc_time`.

T-220 is the first F2 task to actually use APScheduler — driving the periodic P&L audit loop (§9.5:1601-1605: every 5min, fetch Bybit closed-pnl last 3h per sub-account, compare to `trades.realized_pnl`, write corrections to `trade_pnl_deltas` if delta > $0.50). Backlog T-F2+ ADR-Q-C scope: "operational pattern (supervision + UTC enforcement + test fixtures), prerequisite for T-220. Brief mandates APScheduler library per §3.1 + H-021. ADR scope is operational pattern, NOT library selection."

The brief leaves open:

- **Scheduler instance scope.** One `AsyncIOScheduler` per service process, or one per scheduled job? Lifespan-owned vs. ad-hoc?
- **Crash supervision.** What happens if a scheduled job raises? APScheduler default behavior is to log + reschedule; T-220's audit loop must NOT silently fail on (e.g.) Bybit API outage — operator visibility required per §N2 audit log invariant.
- **UTC enforcement audit.** H-021 mandates UTC; how do we structurally prevent a future job from drifting to local-time? Single audit point at scheduler config? Job-time function clamp? Test pin asserting `tzinfo == UTC` at every job-add site?
- **Test patterns.** Two paths: (a) frozen-time via `freezegun` / monkey-patched `datetime.now`; (b) FakeScheduler that pumps jobs synchronously. Which is the canonical T-220 pattern?
- **Dep-footprint accounting.** APScheduler is not yet in the dep set — first task to add it (T-220) needs §0.9 dep justification. ADR scope: pin the dep + version + transitive surface (does APScheduler bring in `tzlocal` / `pytz` / etc.?).
- **Lifespan integration.** APScheduler `AsyncIOScheduler` requires explicit `start()` + `shutdown()`; where does it sit relative to existing pool / bus / adapters / dispatchers in execution-service lifespan?
- **Job replay safety.** APScheduler has `misfire_grace_time` for jobs that miss their scheduled time (e.g., service restart during a run window). T-220 audit loop is idempotent (writes `trade_pnl_deltas` rows keyed by trade_id; rerunning won't double-correct because divergence threshold filters), but the pattern needs to be locked.

This ADR resolves these so T-220's plan-reviewer at Gate 1 has a binding contract.

## Decision

### D1 — One `AsyncIOScheduler` per service process; lifespan-owned

A single `AsyncIOScheduler` instance is created at lifespan startup (after pool/bus/adapter setup, before dispatcher tasks), owned in `app.state.scheduler`, started via `scheduler.start()` and shut down via `scheduler.shutdown(wait=True)` in reverse-shutdown order (BEFORE pool.close so any in-flight job query against the pool finishes against an open pool).

Rationale: APScheduler's `AsyncIOScheduler` is designed to run as a singleton co-resident with the asyncio event loop. Per-job schedulers add boilerplate without isolation benefit (jobs in the same process share the loop anyway). Lifespan ownership matches the existing composition-root pattern (pool/bus/rate_limiter/adapters/dispatcher_tasks/position_lifecycle_tasks all in `app.state`).

### D2 — Single audit point for UTC enforcement at scheduler config

The scheduler is constructed with `timezone=UTC` at instantiation:

```python
from datetime import UTC
from apscheduler.schedulers.asyncio import AsyncIOScheduler
scheduler = AsyncIOScheduler(timezone=UTC)
```

This makes UTC the **default for every job's trigger** added to this scheduler. Per-job triggers may pass `timezone=` to override, but the project convention is: NEVER override at the job-add site. **Implementation in T-220**: AST-based regression test via `ast.parse()` + `ast.NodeVisitor` walking scheduler-using modules under `services/execution/app/`, asserting no `keyword(arg='timezone')` on calls to `add_job` / `scheduled_job`. Plain `grep` would miss multi-line formatted calls; AST scan is structurally robust.

Rationale: H-021 says "UTC only". A single audit point at scheduler ctor is structurally simpler than per-job UTC checks. Tests pin the contract.

### D3 — Crash supervision: per-job exception listener logs ERROR + alerts; does not stop the scheduler

Add a default exception listener via `scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)`:

```python
def _on_job_error(event: JobExecutionEvent) -> None:
    bound_logger.error(
        "scheduler.job_failed",
        job_id=event.job_id,
        scheduled_run_time=event.scheduled_run_time.isoformat(),
        traceback=event.traceback,
    )
```

The scheduler keeps running; failed job is rescheduled at next interval per its trigger. Operator visibility comes from the ERROR log + alerting-svc forwarding (per §9.7 alerting service alert rule on `scheduler.job_failed` log key). Critical jobs (T-220 audit) MUST also log structured business-error keys (e.g., `audit.bybit_api_failed`) inside the job body to give context-specific alerting.

Rationale: APScheduler default behavior is to swallow + reschedule, which violates §N2 audit log invariant (silent failure). The exception listener gives single-point visibility without halting the scheduler (which would degrade to no audits). Critical-job business-error logging is the consumer's responsibility.

### D4 — Test pattern: monkey-patched scheduler fixture (FakeScheduler) + injected `now_fn` callable

Two complementary patterns, both using existing F2 conventions — **no new dev-dep**:

- **FakeScheduler** (primary): a test fixture that monkey-patches `services.execution.app.main.AsyncIOScheduler` with a `_FakeScheduler` class capturing `add_job(func, trigger, **kwargs)` calls without actually scheduling. Tests assert: (a) job-add invocation count + signatures; (b) trigger types (`'interval'` vs `'cron'`) + intervals; (c) UTC enforcement by asserting no `timezone=` kwarg at add_job sites. The actual job function is invoked **directly** in tests via `await captured_jobs[0].func()` — bypasses the scheduler's loop machinery.

- **Injected `now_fn` callable** (secondary): for tests that exercise the JOB FUNCTION's time-dependent logic (e.g., "fetch closed-pnl for last 3 hours" → job body computes `now_fn() - timedelta(hours=3)` deterministically). Mirrors existing F2 vzor — T-216a/T-218a/T-219 already inject `now_fn=lambda: datetime.now(UTC)` at lifespan composition; tests pass `lambda: _FIXED_NOW` instead.

The two patterns compose: test the SCHEDULING (via FakeScheduler) + the JOB FUNCTION (via injected `now_fn`) independently. T-220 audit job's body receives `now_fn` per existing pattern; FakeScheduler captures the bound coroutine for direct invocation in tests.

**Rejected alternative — freezegun**: would have monkey-patched `datetime.now(UTC)` globally during a test. Rejected because (a) `freezegun` is NOT a transitive dev-dep — adding it would require new §0.9 justification + dep-footprint accounting; (b) `now_fn` injection is the established F2 pattern (already shipped in T-216a/T-218a/T-219) and works without globals; (c) globals-monkey-patching tests are flakier under asyncio than callable-injection tests.

### D5 — Dep-footprint accounting (per §0.9)

**Add `apscheduler==3.10.4`** (latest stable as of 2026-05) to execution-service runtime deps. Transitive footprint:

- `tzlocal` (timezone resolution helper; AsyncIOScheduler uses it for `timezone='local'` default).
- `python-dateutil` (already transitive via Pydantic / asyncpg).
- No `pytz` dependency in 3.10+ (replaced by stdlib `zoneinfo`).

Justification per §0.9:
- **Need**: H-021 mandates APScheduler verbatim; brief §3.1 line 302 confirms.
- **Maturity**: APScheduler 3.x is stable since 2014; production-deployed in scalper v1.
- **Alternatives considered**: stdlib `asyncio.create_task` + `asyncio.sleep` loop for 5-min cadence — rejected because (i) brief mandates APScheduler verbatim, (ii) cron-style triggers (T-220 daily report at configured UTC time per H-021 test pin) would require manual cron expression parsing.
- **Vulnerabilities**: no known CVEs as of 2026-05; `bandit` lint passes.

Pin scope: `apscheduler` only added to `services/execution/pyproject.toml` runtime deps (NOT workspace-level). Other services that need scheduling (none in F2) re-pin per their own task plan.

### D6 — Lifespan integration order

Insertion in `services/execution/app/main.py` lifespan:

```
1. pool = await create_pool(...)
2. bus = NatsClient(...); await bus.connect()
3. rate_limiter = SharedRateLimiter(...)
4. adapter_pool = await build_adapter_pool(...)
5. per-bot orders.requests subscriptions (T-216a)
6. per-bot ExecutionDispatcher tasks (T-218a + T-219)
7. NEW: scheduler = AsyncIOScheduler(timezone=UTC); scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR); add_job (T-220 audit); scheduler.start()
8. State attach (incl. app.state.scheduler)
9. yield
Reverse:
10. await bus.close()
11. position_lifecycle_tasks cancel + gather
12. dispatcher_tasks cancel + gather
13. ws_tasks + paper_consumer_tasks cancel + gather
14. NEW: await scheduler.shutdown(wait=True) — BEFORE adapter.close so any in-flight job's adapter REST call can finish; BEFORE pool.close so any in-flight DB query finishes
15. adapter.close per bot
16. pool.close
```

Rationale for shutdown placement (step 14): scheduler jobs may be running mid-tick at shutdown signal. `shutdown(wait=True)` blocks until all running jobs complete. Placing this BEFORE adapter.close + pool.close ensures jobs that depend on adapter/pool can finish cleanly. Placing AFTER bus.close is fine because audit jobs don't publish to bus (write-only DB writes); a future scheduled job that publishes would need a re-evaluation (out-of-scope per §0.8).

### D7 — Job replay safety: `misfire_grace_time` + idempotent job semantics

For T-220 audit (5-min interval), set `misfire_grace_time=120` (2min): if the service restarts during a scheduled tick window, APScheduler will fire the missed job IF restart completes within 2 minutes; otherwise the missed tick is dropped (next tick fires normally).

Idempotency contract: every scheduled job MUST be safe to run multiple times for the same time window. T-220 audit is idempotent across two distinct rerun scenarios:

- **(a) Sub-threshold rerun** (no-op): the divergence between Bybit `closed-pnl` and `trades.realized_pnl` is < $0.50; the threshold filter writes 0 rows. A subsequent rerun re-reads the same Bybit ledger, recomputes the same delta against the unchanged `trades.realized_pnl`, and again writes 0 rows. Idempotent without any DB-side mechanism.
- **(b) Supra-threshold rerun** (post-correction): the first run wrote a `trade_pnl_deltas` row + UPDATEd `trades.realized_pnl` to the corrected value. A subsequent rerun re-reads Bybit (same value) + the now-corrected `trades.realized_pnl` (zero divergence) → falls into case (a). Migration 0009 SHOULD add a `UNIQUE (trade_id, audit_run_at)` constraint as belt-and-suspenders against operator-driven concurrent runs (e.g., manual ad-hoc audit job invocation overlapping with scheduled tick). T-220 plan-reviewer at Gate 1 verifies the schema has this guard.

T-220 plan must explicitly call out: (i) divergence threshold filter as primary idempotency mechanism; (ii) Migration 0009 UNIQUE constraint as secondary; (iii) SELECT-before-INSERT pattern is NOT used (let DB raise on UNIQUE violation if the threshold filter logic ever regresses). This is a job-author contract, not enforced by APScheduler.

**T-220a addendum (2026-05-02)**: UNIQUE constraint scope is `(sub_account, audit_run_at)` per T-220a Migration 0009 OQ-A — D7 narrative pre-T-220a "UNIQUE (trade_id, audit_run_at)" was a placeholder; sub_account-window granularity per H-017 cumulative-attribution-only rules out per-trade columns (no `trade_id` in `trade_pnl_deltas` schema). Idempotency contract holds at the sub_account-window level: same `(sub_account, audit_run_at)` collision → UNIQUE violation → caught + WARN per T-220b job body.

## Rationale

- **D1 lifespan-owned singleton**: matches existing F2 composition pattern (pool/bus/adapter_pool/dispatcher_tasks all in `app.state`); APScheduler design assumes singleton-per-loop.
- **D2 single UTC audit point**: H-021 invariant is structural at scheduler ctor; per-job overrides are explicitly NOT allowed; test enforcement closes the loop.
- **D3 exception listener pattern**: APScheduler default would silently swallow + reschedule failures, violating §N2 audit log invariant. Listener emits a structured ERROR per failure; scheduler keeps running so audit cadence isn't lost.
- **D4 dual test pattern**: SCHEDULING tests (via FakeScheduler) verify job-add semantics WITHOUT running the loop; JOB BODY tests (via injected `now_fn` callable) verify time-dependent business logic. Composability prevents coupling test infra to APScheduler version.
- **D5 dep justification**: brief mandates APScheduler verbatim; no plausible alternative within §3.1 contract; transitive footprint is minimal (no `pytz`).
- **D6 lifespan order**: `shutdown(wait=True)` BEFORE adapter+pool close so jobs depending on those can finish; AFTER dispatcher_tasks cancel + position_lifecycle_tasks cancel because scheduler doesn't depend on them.
- **D7 misfire grace + idempotent jobs**: 2min misfire window is short enough that operator-driven restarts don't accidentally fire stale ticks; idempotent job contract ensures replay safety.

## Consequences

Positive:
- T-220 plan-reviewer has a concrete contract: 7 decisions D1-D7 give scheduler instantiation, UTC enforcement, error handling, test patterns, dep pinning, lifespan placement, replay safety.
- Future scheduled jobs (e.g., F5+ daily reports per H-021 test pin name) inherit the same pattern.
- Operator visibility into job failures via single `scheduler.job_failed` ERROR log key.
- Restart-resilience: `misfire_grace_time` covers operator-driven restarts; idempotent jobs cover content correctness.

Negative / trade-offs:
- APScheduler runtime dep adds tzlocal + python-dateutil transitive footprint. Not minimal but justified per D5.
- D2's single audit point relies on the convention "never pass `timezone=` to add_job". A future maintainer could violate this without being caught by mypy. Mitigation: regression test in T-220 explicitly scans for `timezone=` kwarg at add_job sites.
- D3's exception listener prints traceback to log; no Sentry / external alerting integration in F2. Operator-monitored via log aggregation per §16. F5+ may add Sentry.
- D7's misfire_grace_time=120 is a tradeoff: too short = legitimate restarts drop ticks; too long = stale-tick risk on extended outages. 2min is the existing scalper v1 default; operationally validated.

## Alternatives considered

- **Per-job AsyncIOScheduler instances** (rejected): boilerplate without benefit; APScheduler is designed for singleton-per-loop usage.
- **Stdlib `asyncio.create_task` + `asyncio.sleep` loop** (rejected): brief §3.1 mandates APScheduler; no flexibility for cron-style triggers (T-220 daily report) without re-implementing cron parser.
- **Pass `timezone=UTC` per add_job call site** (rejected per D2): structural enforcement is harder; single ctor audit point is the H-021-correct path.
- **Job error → halt scheduler** (rejected per D3): degrades operator's audit cadence to zero on first failure; H-021 expects continuous operation with errors visible.
- **Frozen-time (freezegun) as test pattern** (rejected per D4): `now_fn` callable injection is the established F2 pattern (T-216a/T-218a/T-219) and works without globals-patching; freezegun would require new dev-dep + globals-monkey-patching is asyncio-flakier than callable injection.
- **`misfire_grace_time=None` (always fire missed)** (rejected per D7): dangerous for daily report job — restart 23h after scheduled time would still fire, producing stale report.
- **Distributed scheduler (cross-process)** (rejected for F2): single-process execution-service per §3.1; F5+ sharding would revisit (e.g., RQ / Celery / Temporal).

## Cross-references

- Brief §3.1 line 302 (APScheduler library mandate).
- Brief §20 H-021 lines 2765-2771 (UTC-only scheduled jobs; test name `test_daily_report_runs_at_configured_utc_time`).
- Brief §9.5 lines 1601-1605 (P&L audit loop spec — T-220 consumer).
- Brief §16 (logging / alerting infrastructure).
- ADR-0006 (cumulative-delta close flow — T-219 close write that T-220 audit cross-checks).
- T-219 `services/execution/app/reconcile.py` (writes `trades.realized_pnl` from snapshot pair; T-220 audits these writes).
- T-216b1 `packages/db/queries/execution.py:287` (`update_trade_close` PK-only invariant; T-220 correction writes follow same pattern).
- T-220 (this ADR's primary consumer): `services/execution/app/audit.py` + Migration 0009 `trade_pnl_deltas`.

## Follow-up

- **T-220** (consumer): replaces TASKS.md backlog text "TODO" with full plan per D1-D7; adds `apscheduler` dep per D5; lifespan integration per D6; job idempotency contract per D7; FakeScheduler + injected `now_fn` tests per D4; H-021 verbatim test `test_daily_report_runs_at_configured_utc_time` per D2.
- **F5+ revisit triggers**: (i) execution-service sharding (D1 singleton becomes distributed; replace AsyncIOScheduler with distributed scheduler); (ii) Sentry/external alerting integration (D3 listener forwards to Sentry).
- **Future scheduled-job tasks**: any new task adding `scheduler.add_job(...)` cites this ADR; uses D2 (no `timezone=` kwarg) + D7 (idempotent body + misfire_grace_time set).
