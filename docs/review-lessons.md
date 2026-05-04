# Review lessons

Patterns the review system has caught. Each lesson encodes a watch-out point that reviewers should now check explicitly going forward. Reviewers consult this file at start of every invocation.

Format:
```
## L-NNN (T-XXX, <reviewer> <verdict>, <date>)
Pattern: <one-sentence description>
Active control: <what to check>
```

---

## L-001 (T-105, brief-reviewer SHIP nice-to-have, 2026-04-26)
Pattern: Polling intervals, pause durations, and similar hardcoded numbers in business logic violate §N9 (configurability invariant) even if they are sensible defaults.
Active control: At plan-reviewer stage, scan the plan for any numeric literals controlling timing, pacing, or periodic behavior. Require Settings/env exposure unless the value is mathematically fixed (e.g., "alpha = 2/(period+1)" formula constants).

## L-002 (T-107b, plan-reviewer REVISE, 2026-04-26)
Pattern: `name_template` literals in indicator implementations may drift from §B.2 spec when the indicator's `__init__` exposes a parameter that is hardcoded in the spec literal (e.g., `vwap_session` — session is fixed in spec, parameterizing it in the template breaks naming convention).
Active control: At plan-reviewer stage for any task touching `packages/features/builtins/`, explicitly check each indicator's `name_template` literal against §B.2 verbatim. Parametrized substitutions (`{symbol}`, `{interval}`) are universal; instance-baked parameters in the template (period, std_dev, etc.) are only valid where §B.2 shows them.

## L-003 (T-107b, plan-reviewer CONCERN, 2026-04-26)
Pattern: Helper functions extracted from indicator code (e.g., `_sma_seeded_ema_series`) need explicit alignment documentation showing they produce the same values as the standalone indicator class on the same input. Without this, a math drift between two implementations of the "same" computation can silently break golden tests.
Active control: At plan-reviewer stage, when a plan introduces a helper used by multiple indicators or by an indicator-internal computation, require a "golden cross-check" test in the plan that asserts helper output equals standalone indicator output on identical input.

## L-004 (T-107b, plan-reviewer CONCERN, 2026-04-26)
Pattern: `value_json` in FeatureValue is `Mapping[str, object]` (per T-106 contract) — this means dict literals work but the resulting FeatureValue is **not** hashable since dict isn't hashable. Plans involving `value_json` outputs should explicitly note this in the test strategy so a hash-check test exists.
Active control: At plan-reviewer stage, for any indicator producing `value_json`, require a test verifying that FeatureValue with `value_json` payload behaves correctly with respect to the documented `Mapping[str, object]` contract.

## L-005 (T-108, plan-reviewer REVISE, 2026-04-26)
Pattern: `sa.Float()` in SQLAlchemy/Alembic produces PostgreSQL `real` (4-byte single precision), NOT `DOUBLE PRECISION` (8-byte). Use `sa.Double()` for `DOUBLE PRECISION` columns. Spec literal `DOUBLE PRECISION` (e.g., §7.2 line 907 for features.value_num) requires `sa.Double()`, otherwise the migration silently degrades precision and integration tests of column type will fail.
Active control: At plan-reviewer stage, for any migration whose plan specifies `DOUBLE PRECISION`, verify the SQLAlchemy type is `sa.Double()`, not `sa.Float()`. At brief-reviewer stage, grep staged migration code for `sa.Float()` and flag if any `DOUBLE PRECISION` columns exist in the same migration.

## L-006 (general, observed across T-105/T-107a/T-107b, 2026-04-26)
Pattern: LOC estimates from Claude Code's plan drafts are systematically 20-40% optimistic for integration-heavy tasks (T-105 backfill: 240 → 344 = +43%; T-107b indicators: 216 → 280 = +30%). Skeleton or scaffold tasks (T-106, T-107a after split) match estimates more closely.
Active control: At plan-reviewer stage, if a plan's LOC estimate exceeds 280 LOC for an integration-heavy task, recommend pre-emptive split. If a plan's LOC estimate is 200-280 LOC, note it as "watch for cap pressure mid-write" but allow.

## L-007 (T-107, operator decision pre-plan-reviewer, 2026-04-26)
Pattern: Mid-write splits (Claude Code reaching 400-LOC cap mid-implementation and forced to split) are operationally expensive — they require git history rewrite, branch creation, and disrupt the implementation flow. Pre-emptive splits at planning time are far cheaper.
Active control: At plan draft stage, if the plan describes 5+ distinct components (e.g., 6 indicators) or has explicit "if I exceed cap mid-write I'll split" contingency language, recommend pre-emptive split into two tasks before invoking plan-reviewer.

## L-008 (T-218a, plan-reviewer REVISE BLOCKER #1, 2026-05-01)
Pattern: Plan-doc SQL literals using Python type names (`Decimal '0'`, `Datetime '...'`, `Bool 'true'`) are NOT valid PostgreSQL syntax — PG knows `numeric` / `timestamp` / `boolean` (lowercase, optionally with explicit cast `::numeric` or typed-literal `NUMERIC '0'`). The Python `decimal.Decimal` class name is not a PG type identifier and the SQL parser will raise `syntax error at or near "Decimal"` at runtime. Mock-level tests don't catch this because they intercept `conn.execute` before SQL is parsed.
Active control: At plan-reviewer stage, grep plan-doc + WG section for SQL literals containing Python type names (`Decimal '`, `Datetime '`, `Bool '`, etc.) — flag as BLOCKER. Require either implicit cast (bare `0` for NUMERIC, bare `true` for boolean), explicit cast (`0::numeric`, `'2026-01-01'::timestamptz`), or typed literal (`NUMERIC '0'`, `TIMESTAMPTZ '...'`). Additionally require a DB-level integration test pin against testcontainer PG when the helper exercises a non-trivial SQL expression (COALESCE/CASE/CAST), gated on `POSTGRES_TEST_DSN` env var per existing F1 integration pattern, otherwise mock-only tests pass while real path explodes.

## L-009 (T-409, ci-full pip-audit failure post-merge, 2026-05-04)
Pattern: Local pre-commit hooks do NOT run pip-audit; only ci-full does. New deps pinned at plan-time may carry unfixed CVEs (jinja2==3.1.4 had 3 CVEs with patches in 3.1.5 + 3.1.6 published before T-409 plan was drafted). Plan-reviewer Gate 1 + brief-reviewer Gate 3 + math-validator Gate 4 don't audit dep CVEs — gap surfaces only on master after CI-full run (~2 min latency post-push), requiring follow-up `fix(T-NNN)` commit on master.
Active control: At plan-reviewer stage, when a plan adds a NEW external dep (per §0.9), require explicit "verified latest patch version with no open CVEs at plan time" sentence in §0.9 justification — Claude Code can run `uv pip install --dry-run "<pkg>"` + `pip-audit --dry-run` locally before APPROVE. At brief-reviewer stage, run `uv run pip-audit --skip-editable` on staged uv.lock if any new dep added; reject SHIP if any unresolved CVE reported. Local pre-commit can't add pip-audit hook (~5s slow + network) without operator opt-in; gate is reviewer-side.
