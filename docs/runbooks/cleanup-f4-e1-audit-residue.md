# Cleanup: F4 E1 audit_events smoke residue (`c241c15` → `67e8c5f` window)

**Status:** Operator-runnable defensive cleanup runbook (T-520 sub-commit #4).

## Scope

F4 E1 dashboard smoke (2026-05-07) deployed `c241c15` (intermediate `default=str` fix for `audit_events.{before,after}_state` JSONB encoding) for ~2 hours before `67e8c5f` shipped the proper double-encode fix. `audit_events` rows written in that window have **corrupted before_state / after_state JSONB columns** — escaped JSON-string scalars instead of objects.

**Affected rows:** Typically `id IN (1, 2)` on the dev DB at the time. Production deploys post-`67e8c5f` are NOT affected (corruption only happens under the `c241c15` transitional state). Operator-side dev DB was already cleaned manually pre-runbook 2026-05-08; this doc captures the procedure for fresh-deploy operators or anyone hitting the same artifact.

## Detection

Connect to the target DB (replace `<DSN>` with your asyncpg-style DSN, e.g. `postgresql://user:pass@host:5432/scalper`):

```bash
psql "<DSN>" -c "
  SELECT id, table_name, op, occurred_at,
         pg_typeof(before_state) AS before_type,
         pg_typeof(after_state) AS after_type,
         (before_state::text)[1:60] AS before_head
  FROM audit_events
  WHERE id IN (1, 2)
     OR (before_state::text LIKE '\"%' AND before_state IS NOT NULL)
     OR (after_state::text LIKE '\"%' AND after_state IS NOT NULL);
"
```

The smoke residue rows show:

* `before_state::text` / `after_state::text` starting with a **literal escaped quote** (`"\"`) — the string-scalar JSONB representation of double-encoded JSON. Healthy rows store JSONB objects, so their `::text` projection starts with `{`.
* `pg_typeof(before_state)` is `jsonb` in both cases (column type unchanged); the corruption is at the **value level**, not the column level.

Expected smoke-residue match: 0–2 rows on dev DB; 0 rows on production deploys post-`67e8c5f`.

## Cleanup

If the detection query returns rows, DELETE them in a transaction:

```bash
psql "<DSN>" <<'EOF'
BEGIN;
-- Verify count before commit (should match detection-query output).
SELECT COUNT(*) AS smoke_residue_count
  FROM audit_events
  WHERE id IN (1, 2)
    AND ((before_state::text LIKE '"%' AND before_state IS NOT NULL)
         OR (after_state::text LIKE '"%' AND after_state IS NOT NULL));

-- Delete only the matching rows. The id-restriction is conservative — the
-- LIKE-pattern alone could match legitimate string-typed JSONB values
-- that happen to be quoted strings (rare but possible in audit payloads
-- written by future tasks). Combining id IN (1, 2) with the pattern
-- restricts the blast radius to the known smoke window artifact.
DELETE FROM audit_events
  WHERE id IN (1, 2)
    AND ((before_state::text LIKE '"%' AND before_state IS NOT NULL)
         OR (after_state::text LIKE '"%' AND after_state IS NOT NULL));

-- Confirm deletion.
SELECT COUNT(*) AS remaining_residue
  FROM audit_events
  WHERE id IN (1, 2)
    AND ((before_state::text LIKE '"%' AND before_state IS NOT NULL)
         OR (after_state::text LIKE '"%' AND after_state IS NOT NULL));

-- If remaining_residue == 0, COMMIT. Otherwise ROLLBACK and inspect.
COMMIT;
EOF
```

## Verification

Re-run the detection query — it should return zero rows. Healthy `audit_events` rows are unaffected.

```bash
psql "<DSN>" -c "
  SELECT id, table_name, op,
         (before_state::text)[1:30] AS before_head,
         (after_state::text)[1:30] AS after_head
  FROM audit_events
  ORDER BY id ASC
  LIMIT 10;
"
```

All `before_head` / `after_head` values should start with `{` (object), `null`, or be empty — never `"\"`.

## Background

The corruption mechanism: `audit_events.{before,after}_state` columns are typed `JSONB`. Per L-011 lesson (`docs/review-lessons.md`):

* When a connection has a registered JSONB codec (`conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads)`), passing a **pre-`json.dumps`-encoded string** to asyncpg as a `$N::jsonb` parameter triggers double encoding: codec re-runs `json.dumps` on the already-stringified value, storing a JSON string scalar (escaped `"{\"id\":1,...}"`) instead of a JSONB object.

`c241c15` shipped the `default=str` fix on the `json.dumps` call site (so UUID/datetime/Decimal values would stringify correctly), but did NOT remove the `json.dumps` wrapper itself. Under analytics-api's registered codec, the doubly-encoded value made it into `audit_events`. `67e8c5f` shipped the proper fix (drop the `json.dumps` wrapper at the call site; let the codec encoder handle JSONB encoding once).

## When to skip this runbook

Skip if **all three** are true:

1. Your deploy is fresh (post-`67e8c5f` `2026-05-07T15:26:32+00:00` master HEAD).
2. The detection query returns zero matching rows.
3. You're not migrating from a backup that includes the affected window.

In that case the table is clean by construction; this runbook is a no-op.

## Related

* `c241c15` — fix(audit) intermediate `default=str` (corruption-introducing).
* `67e8c5f` — fix(audit) proper double-encode fix.
* `docs/review-lessons.md` L-011 — JSONB double-encode under registered codec.
* T-520 sub-commit #4 — this runbook.
