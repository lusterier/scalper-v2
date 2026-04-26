# CLAUDE.md — scalper-v2

This file is loaded automatically at the start of every Claude Code session. It encodes the operator's preferences and the rules that apply across every task. The full technical brief is `docs/CLAUDE_CODE_BRIEF.md`. Operator context is `docs/OPERATOR_CONTEXT.md`. Read both before starting work in a new session.

## Project

`scalper-v2` is a multi-bot crypto-derivatives trading platform. TradingView webhook signals → scoring engine (per-bot, YAML-configured) → Bybit (or paper exchange) → audit-grade JSON logs. Replaces a working v1 SQLite single-bot system. Rewrite, not migration.

## Operator

- Slovak speaker. **Respond in Slovak.** Keep technical terms (commit, branch, diff, scope, scaffold, CI, ADR, etc.) in English.
- Non-programmer. Don't dump code without context when asked a question. Don't lecture.
- Single developer. Fast-forward merges to master only. No merge commits.
- Wants minimum ceremony. No "Skvelá práca!", no "Výborne!", no emoji, no congratulations on completed tasks.
- Conventional Commits format: `feat(T-NNN): ...`, `fix(T-NNN): ...`, `chore(...): ...`, `docs(...): ...`, `test(...): ...`.

## Non-negotiables (BRIEF §1.2)

These are invariants. Violating them is a regression that must be called out and fixed.

- **N1.** UTC everywhere internal. ISO-8601 with explicit `+00:00`. Display CEST only in UI / Telegram / log viewer scripts. Never `CURRENT_TIMESTAMP` or `NOW()` in SQL.
- **N2.** Structured JSON logs from day zero. Three streams: `trading.log`, `audit.log`, `system.log`.
- **N3.** Every external write annotated `@idempotent` or `@non_idempotent`. Non-idempotent writes do not retry.
- **N4.** TDD for financial math and execution lifecycle (P&L, position sizing, order placement, reconciliation).
- **N5.** 80% line coverage on `execution/`, `scoring/`, `pnl/`, `feature_engine/`, `db/`, `exchange_adapters/`. Enforced in CI.
- **N6.** No globals, no singletons. DI via constructors. State composed at the edge in `main.py`.
- **N7.** Hexagonal architecture. Pure business logic; thin adapters for I/O.
- **N8.** Forward-only Alembic migrations with `test_migration.py` per migration.
- **N9.** Anything that is not an invariant is configurable in YAML or env. No hardcoded fees, intervals, percentages.
- **N10.** No regression of any §20 hazard.

## Operating rules (BRIEF §0)

- **One task at a time.** No starting T-N+1 before T-N is merged or explicitly parked.
- **Diff ≤400 LOC** excluding tests, generated code, migrations, vendoring. If approaching, stop and split.
- **No silent refactors.** Out-of-scope cruft → backlog ticket, not absorbed into current task.
- **No new dependencies** without a one-paragraph justification in the PR description. Security-critical libs need operator approval.
- **Ambiguity → ask, don't guess.** Cost of asking < cost of guessing wrong.
- **Phase gate:** do not work on tasks from a phase the operator hasn't unlocked in `TASKS.md`.

## Workflow

**Session start (BRIEF §6.6).** Before coding, read in this order:

1. `TASKS.md` — what is done, in progress, next.
2. The 3 most recent ADRs in `docs/adr/`.
3. `docs/status.md` if present.

Then post a short summary:

```
Session start.
Last session ended at: T-NNN (mmm-dd).
Current phase: FX
Open questions for me:
  1. ...
Proposed next task: T-NNN — <brief>.
Proceed?
```

Wait for "proceed" before starting work.

**Branching.** One branch per task: `feat/T-NNN-short-name`. Fast-forward merge to master. Delete branch after merge.

**End of session.** Update `TASKS.md`. Mark done items, note new tasks discovered, note blockers. Post a one-message summary with what was completed, what's in progress, what's next.

## Pre-implementation review — MANDATORY

**Before writing any code for a new task, invoke the `plan-reviewer` subagent on the consolidated plan.**

When the operator approves starting a new task T-NNN:

1. Read `TASKS.md` task entry, the spec reference (brief section or `docs/modules/<n>.md`), and the 3 most recent ADRs.
2. Draft an initial plan. For new modules, this is a `docs/modules/<n>.md` per the §6.2 template (Purpose / Public interface / Dependencies / Lifecycle / Edge cases / Testing strategy / Open questions). For changes to existing modules, an inline plan with: scope, files touched, new types/functions, hazards relevant from §20, test strategy, open questions.
3. **If the plan has open questions** (decisions not determinable from brief alone — defaults, library choices, scope boundaries, etc.): list them with proposed defaults and present to the operator. Wait for operator's answers ("use defaults" is a valid answer).
4. **Consolidate the plan** with operator's decisions baked in. The resulting plan must contain no unresolved questions — every decision is committed.
5. **Invoke `plan-reviewer` with the CONSOLIDATED plan as input.** Do not invoke it on the draft with open questions — it would flag them as blockers.

The reviewer returns one of three verdicts:

- **`APPROVE`** — plan is sound. **Write the consolidated plan to `docs/plans/T-NNN.md`** so the drift-checker subagent can read it during implementation. Then show one-line summary to operator: *"Plan approved: X. Proceed with implementation?"*. Wait for "proceed".
- **`REVISE`** — plan has issues. Apply the listed fixes (this may mean going back to the operator if the issue requires their input), then re-run `plan-reviewer`. Do not start coding until reviewer approves.
- **`NEEDS DISCUSSION`** — plan touches an architectural decision or brief gap. Show the reviewer's question to the operator. The result is typically an ADR draft (§0.6, §6.3) — write it, get it reviewed by `plan-reviewer` again, then proceed.

Do not start coding before `plan-reviewer` says `APPROVE`. The point is to catch architecture and process issues at the cheapest stage — the plan — rather than after 400 lines of code.

For trivial tasks (typo fixes, doc-only edits, single-file refactors with no architectural impact) the reviewer will return `APPROVE` quickly; this is not a bottleneck.

## Mid-implementation drift check — RECOMMENDED

**During implementation, invoke the `drift-checker` subagent at natural checkpoints to verify the work-in-progress matches the approved plan in `docs/plans/T-NNN.md`.**

Natural checkpoints are:

1. After completing any single file larger than ~50 LOC, before moving to the next file.
2. After the test suite first passes for the current change, before adding more functionality.
3. As a final self-check just before invoking `brief-reviewer` for pre-commit review.

The drift-checker compares uncommitted changes (`git diff HEAD`) against the approved plan and returns:

- **`ON TRACK`** — implementation matches plan. Continue.
- **`DRIFT`** — scope creep, premature abstraction, missing hazard implementation, or unauthorized additions detected. Either refactor back to the plan, or update the plan via ADR if the deviation is intentional.
- **`NEEDS DISCUSSION`** — drift-checker found something that needs operator input.

Skip drift-checker for trivial diffs (<30 LOC) — it adds no value at that scale. For substantive implementation work it catches the kind of mid-stream divergence the pre-commit reviewer cannot (because pre-commit reviewer doesn't know the plan, only the brief).

## Pre-commit review — MANDATORY

**Before every `git commit`, invoke the `brief-reviewer` subagent on staged changes.** This is the second of the two review gates (the first being `plan-reviewer` before coding starts).

The reviewer returns one of three verdicts:

- **`SHIP`** — clean. Proceed with commit. Show the reviewer's one-line summary in the terminal so the operator can see it ran.
- **`FIX FIRST`** — issues found. Fix them, then re-run the reviewer. Do not commit until the reviewer says SHIP.
- **`NEEDS DISCUSSION`** — reviewer is uncertain or sees a brief deviation that needs an operator decision. **Stop. Show the reviewer's question to the operator and wait.** Do not commit.

The reviewer is the quality gate that replaces the operator's manual desktop-side review. Do not skip it. If it fails for technical reasons (missing file, config error), stop and report — do not bypass.

For trivial commits (typo fix, doc-only, single log message) the reviewer will return `SHIP` quickly; this is not a delay.

## What NOT to do

- Don't address the operator by name (unknown).
- Don't comment on pace ("rýchlo postupujeme", "sme pozadu"). Project has no deadline.
- Don't redirect the operator to read the brief ("pozri si §X"). The operator won't. Summarize the relevant rule yourself.
- Don't propose teaching programming or recommending books. Out of scope.
- Don't write merge commits. Fast-forward only.
- Don't create files outside the working directory without an explicit task reason.
- Don't add CI/CD ceremony (extra workflows, docker variants, etc.) the brief doesn't ask for.

## Key file locations

- `docs/CLAUDE_CODE_BRIEF.md` — full technical brief (§0–§24, hazards H-001..H-026)
- `docs/OPERATOR_CONTEXT.md` — operator profile and dynamics
- `TASKS.md` — single source of truth for task state
- `docs/adr/NNNN-title.md` — Architecture Decision Records
- `docs/modules/{name}.md` — per-module design docs (BRIEF §6.2)
- `docs/plans/T-NNN.md` — consolidated approved plan per task (written after `plan-reviewer` APPROVE; read by `drift-checker` during implementation)
- `docs/status.md` — operator notes for next session (if present)
