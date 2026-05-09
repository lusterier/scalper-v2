# T-537a1 — outbox base: NEW packages/outbox/{types,queries} + migration 0016_outbox_events

**Type**: F5 numbered task (NOT a fix; counts toward F5 phase counter).
**Phase**: F5 (unlocked).
**Origin**: derived from operator audit Items 2 + 7 → operator hybrid scope decision 2026-05-09 (T-537a + T-537b only) → **further split per L-007 split-watch + plan-reviewer WG**: T-537a → T-537a1 (this; queries + types + migration) + T-537a2 (relay worker; separate task) + T-537b (signal-gateway integration; separate task).
**Date**: 2026-05-09.

## Background

The audit Items 2 + 7 expose the same root cause: state-and-publish are not atomic. Outbox pattern decouples by writing event intent to durable `outbox_events` table inside the same DB transaction as business state; a separate relay worker eventually publishes to NATS.

T-537a1 delivers the **base infrastructure** — durable storage + write-side helpers + read-side helpers + retry-state helpers. NO relay worker logic in this task. T-537a2 will add the relay worker in a separate plan-stage cycle.

This sub-split was operator-chosen 2026-05-09 to keep each task individually small (≤400 src LOC) and to let the relay-worker design (lifecycle / retry math / shutdown ordering) iterate in isolation against an already-merged base.

## Operator decisions (2026-05-09 OQ session, carried forward from T-537a parent plan)

- **OQ-1 round 1 = Hybrid**: T-537a + T-537b (now further split: T-537a1 + T-537a2 + T-537b).
- **OQ-2 round 1 = Per-service relay in lifespan**: applies to T-537a2; relevant here only as forward-compat shape for the queries' `service` discriminator column.
- **OQ-3 round 1 = NEW packages/outbox/**: applies to this task (creates the package).
- **OQ-4 round 1 = Exponential backoff cap**: applies to this task indirectly via the `select_pending` SQL backoff-window filter (T-537a2 worker calls this helper but the math lives in SQL).
- **OQ-1 round 2 = Single generic `outbox_events` table** with `service` column discriminator.
- **OQ-2 round 2 = `FOR UPDATE SKIP LOCKED`** in `select_pending` query.
- **OQ-3 round 2 = Failed events kept forever**: `failed_at NOT NULL` rows persist for admin replay.

## T-537a1 scope (split from parent T-537a)

### Migration 0016_outbox_events

NEW Alembic migration creating the single generic `outbox_events` table:

```sql
CREATE TABLE outbox_events (
    id BIGSERIAL PRIMARY KEY,
    service TEXT NOT NULL,                      -- 'signal_gateway' | 'execution' | 'strategy_engine'
    subject TEXT NOT NULL,                      -- NATS subject
    correlation_id TEXT,                        -- ties back to webhook idempotency_key / order correlation
    payload JSONB NOT NULL,                     -- MessageEnvelope serialized via _to_jsonable per L-013
    created_at TIMESTAMPTZ NOT NULL,            -- when business tx committed (UTC per §N1)
    published_at TIMESTAMPTZ,                   -- NULL until relay publishes
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    last_error TEXT,
    failed_at TIMESTAMPTZ                       -- set when attempt_count >= max_attempts
);

-- Partial index for the relay's hot path (scan only pending rows).
CREATE INDEX outbox_events_pending_idx
    ON outbox_events (service, created_at)
    WHERE published_at IS NULL AND failed_at IS NULL;

-- Companion index for admin/audit queries by correlation_id (NULL-safe; non-unique).
CREATE INDEX outbox_events_correlation_idx
    ON outbox_events (correlation_id)
    WHERE correlation_id IS NOT NULL;
```

Migration revision = 0016, down_revision = 0015. Forward-only per §N8; downgrade drops the table + indexes.

### `OutboxEvent` dataclass + `OutboxRelaySettings` (`packages/outbox/types.py`)

```python
@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """Domain projection of an `outbox_events` row.

    Read-only; queries.py builds rows from this dataclass at insert time
    (well, builds the INSERT bind args from kwargs and returns the new id);
    select_pending returns lists of these for T-537a2 relay worker consumption.
    """
    id: int
    service: str
    subject: str
    correlation_id: str | None
    payload: dict[str, Any]
    created_at: datetime
    published_at: datetime | None
    attempt_count: int
    last_attempt_at: datetime | None
    last_error: str | None
    failed_at: datetime | None


class OutboxRelaySettings(BaseSettings):
    """Settings model for OutboxRelayWorker (consumed by T-537a2).

    Lives in T-537a1 because T-537a2 worker tests will use it; placing it in
    types.py keeps the shape declarable + settable from env without the relay
    module existing yet. Env prefix convention: `OUTBOX_RELAY_*` flat
    (mirror existing service Settings flat-prefix convention; e.g. signal-gateway
    `RATE_LIMIT_*` rather than nested model `RATE_LIMIT__WINDOW_S`).
    """
    model_config = SettingsConfigDict(env_prefix="OUTBOX_RELAY_", extra="ignore")
    poll_interval_s: float = 1.0          # base poll cadence between relay batches
    batch_size: int = 100                  # max rows per relay loop iteration
    max_attempts: int = 100                # before failed_at is set
    backoff_base_s: float = 2.0            # min delay between attempts (multiplied by 2^N)
    backoff_cap_s: float = 60.0            # max delay between attempts
```

### SQL helpers (`packages/outbox/queries.py`)

4 helpers, all parameterized + thin (no business logic).

```python
@non_idempotent
async def insert_outbox_event(
    conn: _DbExecutor,
    *,
    service: str,
    subject: str,
    correlation_id: str | None,
    payload: dict[str, Any],
    created_at: datetime,
) -> int:
    """INSERT into outbox_events; returns new id (BIGSERIAL).

    L-013 codec-immune wrapper: payload is wrapped via `_to_jsonable(payload)` +
    `json.dumps(...)` before binding to `$5::jsonb` — works regardless of whether
    the calling service registered the asyncpg JSONB codec. Matches the
    `packages/db/queries/audit.py` convention (insert_audit_state precedent).
    """
    ...


async def select_pending_outbox_events(
    conn: _DbExecutor,
    *,
    service: str,
    batch_size: int,
    now: datetime,
    backoff_base_s: float,
    backoff_cap_s: float,
) -> list[OutboxEvent]:
    """SELECT pending rows for service, ordered by created_at, FOR UPDATE SKIP LOCKED.

    Filter:
        published_at IS NULL
        AND failed_at IS NULL
        AND (
            last_attempt_at IS NULL
            OR last_attempt_at <= $now - make_interval(secs => least(
                $backoff_base_s * power(2.0, attempt_count),
                $backoff_cap_s
            ))
        )
    ORDER BY created_at ASC
    LIMIT $batch_size
    FOR UPDATE SKIP LOCKED.

    Backoff math is single-source-of-truth in SQL via PG `power` function;
    Python-side never duplicates this calculation per WG#5. PG returns
    `double precision`; cross-cast to interval via `make_interval`.

    Returns list of `OutboxEvent` projections.
    """
    ...


async def mark_outbox_event_published(
    conn: _DbExecutor,
    *,
    event_id: int,
    published_at: datetime,
) -> None:
    """UPDATE outbox_events SET published_at = $2 WHERE id = $1."""
    ...


async def mark_outbox_event_failed(
    conn: _DbExecutor,
    *,
    event_id: int,
    last_attempt_at: datetime,
    last_error: str,
    max_attempts: int,
    failed_at: datetime,
) -> None:
    """Increment attempt_count + record error; set failed_at if attempts exhausted.

    SQL:
        UPDATE outbox_events
        SET attempt_count = attempt_count + 1,
            last_attempt_at = $1,
            last_error = $2,
            failed_at = CASE WHEN attempt_count + 1 >= $3 THEN $4 ELSE NULL END
        WHERE id = $5
    """
    ...
```

### Tests

1. `packages/outbox/tests/__init__.py` — empty package marker.
2. `packages/outbox/tests/test_types.py` — `OutboxEvent` dataclass field order + frozen/slots compliance + `OutboxRelaySettings` env round-trip.
3. `packages/outbox/tests/test_queries.py` — mock-based SQL string + bind ordering for all 4 helpers (mirror `test_queries_execution.py` convention).
4. `tests/integration/queries/test_outbox.py` — testcontainer-gated round-trip per L-008:
   - insert + select_pending returns the row.
   - mark_published flips `published_at`; subsequent select_pending excludes it.
   - mark_failed increments `attempt_count`; `failed_at` set when exhausted.
   - FOR UPDATE SKIP LOCKED behavior: parallel SELECT in two transactions returns disjoint sets.
   - Backoff window: row with `last_attempt_at = now - 0.5s` and `attempt_count=1` (next delay = 4s with default base=2.0) is NOT returned; same row with `last_attempt_at = now - 5s` IS returned. Test fixture values pre-computed against PG `power(2.0, ...)` semantics per WG#5 — hardcoded, NOT computed in Python.
5. `tests/integration/migrations/test_0016_migration.py` — table + indexes + types per §N8. Uses `alembic downgrade 0015` (explicit revision target per L-012 / WG#2), NOT `downgrade -1`.

## Out of scope (deferred to T-537a2 + T-537b)

- **T-537a2** — `packages/outbox/relay.py`: `OutboxRelayWorker` class with `run` + `stop` lifecycle, retry math wiring, shutdown ordering contract, logger keys (`outbox.relay.*`), 5 logger key constants. Plus `packages/outbox/tests/test_relay.py`. Plus `packages/outbox/__init__.py` exports for relay.
- **T-537b** — signal-gateway integration: `webhook.py` refactor (replace `bus.publish("signals.validated")` with `insert_outbox_event` inside same tx as `insert_signal`); wire `OutboxRelayWorker` into `services/signal_gateway/app/main.py` lifespan; Settings composition; integration test exercising full pipeline.
- **T-537c (deferred indefinitely)** — execution-service migration to outbox.
- **T-537d (deferred indefinitely)** — strategy-engine migration to outbox.

## Files touched

### Source (4 files)

1. NEW `packages/outbox/__init__.py` — public exports for `OutboxEvent` + `OutboxRelaySettings` + 4 query helpers. (Relay exports added in T-537a2.)
2. NEW `packages/outbox/types.py` — `OutboxEvent` + `OutboxRelaySettings`.
3. NEW `packages/outbox/queries.py` — 4 SQL helpers + module-private `_to_jsonable` mirror of `packages/db/queries/audit.py:55` per WG#1 + module-private `_DbExecutor` type alias per WG#3.
4. NEW `migrations/versions/0016_outbox_events.py` — Alembic migration.

### Tests (5 files)

5. NEW `packages/outbox/tests/__init__.py`
6. NEW `packages/outbox/tests/test_types.py`
7. NEW `packages/outbox/tests/test_queries.py`
8. NEW `tests/integration/queries/test_outbox.py`
9. NEW `tests/integration/migrations/test_0016_migration.py`

### Documentation (5 files; chore commit)

10. `docs/CLAUDE_CODE_BRIEF.md` — §8 outbox pattern reference (whichever sub-section number fits at chore commit time per WG#6).
11. `TASKS.md` — T-537a1 DONE entry; F5 phase counter advances `26/47 → 27/48` (numerator+1, denominator+1) per WG#7.
12. `docs/status.md` — late-night XIV section.
13. `docs/plans/T-537a1-outbox-queries-types-migration.md` — this plan doc (chore-staged per CLAUDE.md gate-1 contract).
14. `docs/review-lessons.md` — no new lesson for T-537a1 (no generalizable catch).

## LOC budget

- Migration `0016_outbox_events.py`: ~50 LOC.
- `packages/outbox/__init__.py`: ~25 LOC.
- `packages/outbox/types.py`: ~80 LOC (dataclass + Pydantic settings + module docstring).
- `packages/outbox/queries.py`: ~210 LOC (4 helpers + `_to_jsonable` mirror + `_DbExecutor` alias + module docstring + type imports).
- Tests: ~400-450 LOC across 5 test files.
- Total feat commit: ~770-820 LOC; src ~365 LOC; **under §0.3 400 src cap** (no waiver needed).

If drift-checker bounces with overshoot beyond +25%, no further split available — T-537a2 is already the relay isolation. At that point, escalate to operator for scope renegotiation.

## Acceptance criteria (AC)

1. NEW migration `migrations/versions/0016_outbox_events.py` with revision='0016', down_revision='0015', creates `outbox_events` table + 2 indexes per schema above.
2. Migration test `tests/integration/migrations/test_0016_migration.py` verifies columns + indexes per §N8; uses `alembic downgrade 0015` (explicit revision; L-012 / WG#2).
3. NEW `packages/outbox/types.py` exports `OutboxEvent` (frozen dataclass, slots=True; 11 fields per spec) and `OutboxRelaySettings` (Pydantic `BaseSettings` with `env_prefix="OUTBOX_RELAY_"`; 5 fields per spec).
4. NEW `packages/outbox/queries.py` exports 4 SQL helpers: `insert_outbox_event` (returns BIGSERIAL id), `select_pending_outbox_events` (FOR UPDATE SKIP LOCKED + backoff-window filter in SQL via PG `power`), `mark_outbox_event_published`, `mark_outbox_event_failed` (CASE-when-attempt-count-exceeds-max sets failed_at).
5. `insert_outbox_event` annotated `@non_idempotent` per §N3 (existing decorator at `packages/core/markers.py`; verified by plan-reviewer parent T-537a).
6. `insert_outbox_event` payload write uses `json.dumps(_to_jsonable(payload))` codec-immune wrapper per L-013 / WG#1 — module-private `_to_jsonable` mirror of `packages/db/queries/audit.py:55`. Module docstring documents the codec-state-immunity contract.
7. `select_pending_outbox_events` SQL backoff math is `least(backoff_base_s * power(2.0, attempt_count), backoff_cap_s)` per WG#5. Test fixtures in `test_outbox.py` use hardcoded values pre-computed against PG semantics, NOT Python-side recomputation.
8. `_DbExecutor` type alias declared module-locally in `queries.py` per WG#3 (mirror `packages/db/queries/feature_engine.py:40` convention; NOT imported from another queries module).
9. NEW `packages/outbox/__init__.py` exports `OutboxEvent`, `OutboxRelaySettings`, `insert_outbox_event`, `select_pending_outbox_events`, `mark_outbox_event_published`, `mark_outbox_event_failed`. (Relay exports deferred to T-537a2.)
10. Tests: 5 NEW test files per §Files touched. Mock-based unit tests run in CI without testcontainer. Testcontainer-gated tests skip without `POSTGRES_TEST_DSN`.
11. Repo regression: pytest 2097 → ~2110-2115 expected (+~13-18 net new mock-based tests; testcontainer-gated test functions skip without DSN).
12. F5 phase counter advances `26/47 → 27/48` per WG#7 (numerator+1 for shipped, denominator+1 for new T-537a1 numbered task). T-537a2 + T-537b denominator increments happen at THEIR plan-stage time per existing TASKS.md narrative pattern.
13. Docs/CLAUDE_CODE_BRIEF.md gains §8 outbox sub-section reference per WG#6 (verify section number uniqueness at chore commit time).
14. Branch `feat/T-537a1-outbox-queries-types-migration` per CLAUDE.md branching policy (T-537a1 is feature work). FF-merge to master + push + branch delete.

## Hand verification

N/A — no financial math. Backoff math is integer/float retry-policy in SQL (`least(base * power(2.0, attempts), cap)`); not P&L / Decimal. Math-validator Gate 4 expected `VERIFIED — out of scope, math-validator skipped` (`packages/outbox/` is messaging infra, not in math-validator's scope of `packages/features/builtins/`, `packages/features/protocols.py`, `packages/features/types.py`, `packages/pnl/`, `services/feature-engine/`, `services/execution/`, `services/scoring/`).

## Test plan ordering (§N4 TDD)

1. Write migration `0016_outbox_events.py` + `test_0016_migration.py` FIRST. Run `alembic upgrade head` against testcontainer; test passes (table created).
2. Run `alembic downgrade 0015` (explicit revision per WG#2); verify table dropped; re-upgrade to 0016; verify table back. Sandwich pattern.
3. Write `packages/outbox/types.py` + `test_types.py`. Both pass.
4. Write `packages/outbox/queries.py` + `test_queries.py` mock-based. SQL strings + bind ordering verified at mock level.
5. Write `tests/integration/queries/test_outbox.py` testcontainer round-trip. Verifies real PG behavior (FOR UPDATE SKIP LOCKED, backoff window in SQL with PG-computed `power(2.0, ...)`, JSONB codec-immune payload round-trip).
6. Re-run full repo pytest — 2097 → ~2110-2115 expected (testcontainer-gated tests skip without POSTGRES_TEST_DSN per F1 pattern).
7. Drift-checker Gate 2 → brief-reviewer Gate 3 → math-validator Gate 4 (out-of-scope expected).

## Open questions

None — all 7 OQs (4 in round 1 + 3 in round 2) baked at plan time per operator session 2026-05-09 + the further L-007 split decision baked at this plan's timestamp.

## Cross-references

- BRIEF §N1 UTC; §N3 idempotency; §N5 80% coverage; §N6 no globals; §N7 hexagonal; §N8 forward-only Alembic + per-migration test; §N9 env-configurable knobs.
- BRIEF §8 NATS messaging contract.
- BRIEF §9.1 signal-gateway publish flow (Item 2 origin; addressed by T-537b).
- BRIEF §20 H-009 dedup ring (companion); H-018 PK-only invariant (orthogonal — outbox is an INSERT-only durable buffer; UPDATE only on `published_at` / `failed_at` flip uses PK already).
- packages/bus/payloads.py — `MessageEnvelope` shape used in payload column.
- packages/db/queries/audit.py:55 — `_to_jsonable` reference convention (WG#1).
- packages/db/queries/feature_engine.py:40 — `_DbExecutor` type alias convention (WG#3).
- migrations/versions/0015_shadow_variants_relax_parent_fk.py — current head; 0016 next.
- TASKS.md `## Done` fix(T-218c) + fix(T-216c) + fix(T-217c) — execution-service operational hardening cluster precedents (different category but same audit origin).
- docs/status.md late-night XIII — 7-bug audit progress tracker (Items 2 + 7 → T-537 origin).
- L-007 split-watch active control: this plan IS the L-007 split (T-537a → T-537a1 + T-537a2).
- L-008 SQL syntax + testcontainer integration test pattern (applied to test_outbox.py).
- L-012 explicit `alembic downgrade <revision>` target (applied to test_0016_migration.py).
- L-013 codec-immune `_to_jsonable` JSONB writer convention (applied to insert_outbox_event).
- L-014 LOC calibration: ~365 src LOC under cap (no waiver needed for T-537a1; T-537a2 may need waiver for relay worker scope).
- L-015 sibling migration test: N/A (NEW table, no sibling integration tests touch `outbox_events`).
- L-018 / L-019 / L-020: N/A (no "dormant in mode" claim, no retry loop without exception handling, no composite-PK helper).

## Mirror precedents

- `packages/bus/` shape (mirror layout: types.py + queries-equivalent + tests).
- `packages/db/queries/audit.py` (`_to_jsonable` convention).
- `packages/db/queries/feature_engine.py` (`_DbExecutor` type alias convention).
- T-217c integration test in `tests/integration/queries/test_execution.py` (testcontainer-gated round-trip pattern; verifies row-state round-trip on UPDATE/INSERT).

## Branch step

Per CLAUDE.md branching policy + status.md late-night XIII branch step RESTORED note:

1. `git checkout -b feat/T-537a1-outbox-queries-types-migration` BEFORE staging any changes.
2. Feat commit on branch (Source files 1-4 + Tests files 5-9).
3. Chore commit on branch (Documentation files 10-13).
4. `git checkout master && git merge --ff-only feat/T-537a1-outbox-queries-types-migration`.
5. `git push origin master`.
6. `git branch -d feat/T-537a1-outbox-queries-types-migration`.

## Write-time guidance

(Carried verbatim from plan-reviewer T-537a parent APPROVE 2026-05-09 10-item list; pruned to T-537a1 scope. WG#6 worker shutdown ordering + WG#7 logger keys are deferred to T-537a2 plan since they apply to relay worker. Remaining 8 items renumbered for T-537a1.)

1. **JSONB codec-immune payload write convention (L-013)**: `insert_outbox_event` zapisuje `payload` ako `$N::jsonb`. Plán to nespomína explicitne. Signal-gateway / execution-service / strategy-engine NIE registrujú `_register_jsonb_codec` (pre tieto services je `json.dumps(_to_jsonable(payload))` správna forma); analytics-api / feature-engine codec registrujú (passing dict directly). Keďže `packages/outbox/queries.py` je shared cross-service infra ktorý môže byť volaný z OBOCH typov služieb, MUSÍŠ vyriešiť toto v jednom mieste — buď: (a) volajúci kontrakt: caller passing `dict` a queries.py routuje cez `_to_jsonable` + service-aware encode (nie); alebo realisticky (b) wrap payload via `_to_jsonable(payload)` v queries.py PRED bind a parameter type column = `jsonb` — codec-state-immune wrapper standard per L-013 active control. Module docstring MUSÍ zdokumentovať switch trigger keď consumer service flips codec-registration state. Brief-reviewer odmietne raw `json.dumps(payload)` bez `_to_jsonable`. Reference: `packages/db/queries/audit.py:55` `_to_jsonable` definition + L-013 lesson.

2. **Migration `downgrade()` explicit revision target (L-012)**: ak plán pre `tests/integration/migrations/test_0016_migration.py` testuje rollback, MUSÍ použiť `alembic downgrade 0015` (explicit), NIE `downgrade -1`. Plán to neuvádza explicitne. Plus tento isty pattern aj pre upgrade-and-downgrade-and-upgrade-again sandwich ak je súčasťou test_0016.

3. **`_DbExecutor` typ alias konvencia**: existujúce `packages/db/queries/*.py` deklarujú `_DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]` lokálne v každom module. `packages/outbox/queries.py` má replikovať túto konvenciu (NIE importovať z iného queries modulu). Mirror `packages/db/queries/feature_engine.py:40`.

4. **`OutboxRelaySettings` env prefix konvencia (§N9)**: zvol JEDNU konvenciu deterministicky pre T-537a1 a doc-strinuj — flat field-prefix `OUTBOX_RELAY_*` (mirror existing service Settings flat-prefix convention; e.g. signal-gateway `RATE_LIMIT_*` rather than nested model `RATE_LIMIT__WINDOW_S`). Bez konvencie T-537b bude musieť reverzne tvoriť rozhodnutie. Set `model_config = SettingsConfigDict(env_prefix="OUTBOX_RELAY_", extra="ignore")` na `OutboxRelaySettings`.

5. **Backoff math single source of truth (AC#7)**: SQL formula `least(backoff_base_s * power(2.0, attempt_count), backoff_cap_s)`. Pri písaní integration testu (`test_outbox.py`, backoff window assertion) MUSÍŠ použiť identicky tú istú PG funkciu (`power(2.0, ...)`) — Python `**` alebo `math.pow` na floatoch v tom istom test bode by skryl precision drift. Test fixture konštrukcia (`last_attempt_at = now - X seconds`) má použiť hardcoded values, ktoré boli pre-vypočítané z PG semantík (`SELECT power(2.0, 1) * 2.0;` = 4.0 — hardcode 4-second window). Plán už spomína integer/float arithmetic; explicitne zachovaj že PG `power(double precision, double precision)` returnuje `double precision` — `least(numeric, double precision)` cross-cast má byť testovaný real PG roundtripom (NIE asercia na presných floatoch).

6. **Brief §8 outbox pattern reference (AC#13)**: pri chore commit time over že nový sekčný číselný klúč nezasahuje do existujúcich H-### references; ak §8 už má pod-sekcie 8.1/8.2/8.3 a plán pridáva 8.4, skontroluj že žiadny existujúci ADR / lesson / hazard note neodkazuje na §8.4 ako rezervovaný pre niečo iné. Sub-section number is determined at chore commit by reading existing §8 TOC.

7. **F5 phase counter `26/47 → 27/48` denominator update**: TASKS.md head riadok 5 musí byť update-nutý zo `**26/47 tasks done...**` na `**27/48 tasks done...**` after T-537a1 ships, NIE iba numerator. Operator T-537a1/a2/b enumerate ako 3 nové numbered tasks (per L-007 split + OQ-1 hybrid), takže denominator narastie postupne (+1 pri každom plan-time; +1 pri každom shipped-time at NUMERATOR). T-537a1 ship → 27/48; T-537a2 ship → 28/49; T-537b ship → 29/50. Skontrolovať či brief-reviewer Gate 3 chore-stage cards uvidí oba inkrementy konzistentne.

8. **Branch policy `feat/T-537a1-outbox-queries-types-migration`**: T-537a1 je feature task, nie bug fix. Branch step §1 použiť `git checkout -b feat/T-537a1-outbox-queries-types-migration`.

(Items 9–13 NEW from T-537a1 re-review plan-reviewer APPROVE 2026-05-09 post-split.)

9. **§N3 markers na všetky 4 query helpers explicit.** Plán pomenuje `@non_idempotent` len na `insert_outbox_event` (AC#5). Doplň markery aj na zvyšné: `mark_outbox_event_published` → `@idempotent` (UPDATE WHERE id=$1 SET published_at=$2 — same id + same payload = same result; safe to retry); `mark_outbox_event_failed` → `@non_idempotent` (`attempt_count = attempt_count + 1` + CASE-driven `failed_at` flip — opakované volanie zmení stav; T-537a2 worker MUSÍ zaručiť mark_failed sa volá raz per attempt). `select_pending_outbox_events` je read — bez markera. Module docstring `queries.py` zdokumentuje ktorý helper je v ktorej kategórii a prečo (signál pre T-537a2 worker autora).

10. **`_to_jsonable` import path.** Použi explicit private-import `from packages.db.queries.audit import _to_jsonable  # noqa: PLC2701` per L-013 active control option (c) (T-510b operator default A precedent). Alternatívy (extracted-shared-module / promoted-public) flaguj v module docstring `queries.py` ako future-decision; aktuálne použiť option (c) verbatim.

11. **Module docstring `queries.py` codec-state-immune contract.** Explicitne zdokumentuj že JSONB write využíva `json.dumps(_to_jsonable(payload))` formu pre signal-gateway / execution-service / strategy-engine (services NEregistrujú codec); ak ktorýkoľvek z týchto services niekedy `_register_jsonb_codec` zavedie, MUSÍ sa pattern preklopiť na `_to_jsonable(payload)` pass-as-dict (analytics-api / feature-engine režim). Per L-013 active control bod (4).

12. **AC#3 `OutboxRelaySettings` field-level doc + validators.** Plán uvádza 5 polí s defaultmi v code-bloku. Pri implementácii pridaj per-field `Field(..., description="...")` zhodne s plánom (semantika field-u zostáva traceable z env do dokumentácie). Tiež pridaj field validators ak default range má hranice (`max_attempts >= 1`, `backoff_cap_s >= backoff_base_s`) — bez validátorov mis-konfigurácia env premennej v T-537a2 lifespan zlyhá až runtime.

13. **`select_pending_outbox_events` SQL `make_interval` cross-cast verification.** Plán píše `make_interval(secs => least(...))` kde `least(double precision, double precision)` vráti `double precision`. PG `make_interval(secs)` accepts `double precision` (per pg docs `make_interval(years int default 0, ..., secs double precision default 0.0)`). Test fixture `last_attempt_at = now - 5s` so `attempt_count=1, base=2.0, cap=60.0` → expected window = `least(2.0 * power(2.0, 1), 60.0)` = `4.0s`; row IS returned (5s > 4s). **Hardcode 4.0s v test fixture per WG#5** (NIE Python `2.0 ** 1 * 2.0` výraz v test code). Integration test asercia musí uvádzať expected window VERBATIM ako pre-vypočítanú PG hodnotu.
