# ADR-0015: Phase F6 — Post-MVP Hardening (BRIEF §19 amendment)

**Status:** Accepted (2026-05-17; operator decision "full new F6 phase" + "use defaults"; T-541 plan-stage pass-5)

## Context

F5 closed as Live-ready MVP (§A+§B operator-signed `de4d1ad`). Several items were operator-acknowledged residual carve-outs at that sign-off, explicitly **NOT** F5 blockers:

- **H-005** — deferred opposite-side guard; §20 DEFERRED, meta-test `_KNOWN_DEFERRED={5}`, tracked only as the T-F5+ "opposite_side_open scoring condition + H-005 test" backlog ticket.
- **D9** — native analytics-api logs `service=signal-gateway` (SERVICE_NAME mislabel).
- **strategy-engine-smoke** — a `strategy-engine-smoke` compose service needed for a full local F5_E2 deployment smoke (residual from the T-522 close-out RUN).
- 8 F5+ opportunistic polish items.

`TASKS.md:3` recorded "no next phase unlocked — operator's call". The pass-2 plan-reviewer phase-gate BLOCKER established that none of these can be implemented without (a) a numbered task and (b) an explicit phase unlock.

## Relationship to ADR-0011 (ADR-0011 rejected F6 — read this)

ADR-0011:72/75 explicitly **REJECTED** a "separate F6 phase" / "extend §19 with a new F6 block", rationale-then: it would create a 2-phase-MVP narrative contradicting BRIEF's single-MVP-sign-off model — but **F5 was pre-sign-off then**. ADR-0015 deliberately differs because the context inverted: F5 is now **signed-closed** (Live-ready MVP §A+§B, `de4d1ad`); F6 is purely **post-MVP additive** hardening, NOT a second MVP phase — the 2-phase-MVP concern ADR-0011 guarded against does not arise post-sign-off. ADR-0015 does **NOT supersede** ADR-0011 (orthogonal: pre- vs post-MVP-sign-off context); it extends §19. ADR-0011:107 itself anticipated *"if a separate F7 (post-Live-ready) phase emerges … revisit via new ADR"* — ADR-0015 is consistent with that foreseen post-Live-ready-phase path (F6 = the next sequential phase id).

## Decision

Open **Phase F6 — Post-MVP Hardening** as a BRIEF §19 addendum (§6.7 ADR mechanism; sibling-mechanism of ADR-0011's §19 amendment, not its decision). F6 scope = resolve the deferred hazards + ops/doc debt accumulated through F5 close-out, beyond the signed Live-ready MVP.

- **F6 numbered** = 3 committed work tasks: **T-542** (H-005 opposite-side-position guard + H-005 test — its own full Gate-1 cycle; an H-005 architecture ADR is created at T-542 plan-stage IF warranted, per §0.8 anti-hypothetical — none exists yet), **T-543** (D9 analytics-api SERVICE_NAME mislabel fix), **T-544** (strategy-engine-smoke compose service).
- **F6+ opportunistic** = the 8 carried-over polish items, relabelled `T-F5+`→`T-F6+` (NOT force-numbered; mirrors the F5/F5+ split).
- **F6 exit-criteria:** H-005 resolved (E4 36/36, no §20 DEFERRED) + D9 fixed + strategy-engine-smoke shipped (+ optional: a full local F5_E2 deployment smoke green).
- **F6 counter = N/3** over T-542/T-543/T-544; the **T-541 opener is the phase-populator meta-task** (mirrors T-500/T-523/T-200 — recorded `[x]` at its own close, NOT part of the /3).

## Consequences

- BRIEF §19 gets a `### Phase F6 — Post-MVP Hardening` block (mirror the F5 block: Goal + Tasks + Exit criteria + this ADR footnote).
- `TASKS.md:3` "no next phase unlocked" → "F6 unlocked 2026-05-17 (ADR-0015)"; F5 ✅ COMPLETE marker preserved.
- New `### F6 numbered` (T-541 opener + T-542/543/544) + `### F6+ opportunistic` TASKS.md sections.
- **F5+→F6+ migration = exactly the 8 OPEN items** (1 ReplayBus pause/resume/seek; 2 Backtest HTML report; 3 Shadow-variants Bonferroni significance; 4 Feature-backfill dashboard tile; 5 Multi-bot backtest; 6 docs(exchange-protocol-groupdoc); 7 §B.1 sizing.tier_promotion/tier_demotion; 8 §17.2 per-module coverage gate), relabelled `T-F5+`→`T-F6+`. The `[x] T-540 (was T-F5+)` DONE record (TASKS.md:353, commit `7b8c460`) **STAYS in-place** in `### F5+ opportunistic` (immutable point-in-time DONE record — never moved/rewritten); the F5+ header is annotated.
- **The F5 §A+§B Live-ready sign-off is NOT reopened/invalidated** — F6 is purely additive post-MVP; H-005/D9/etc were operator-acknowledged carve-outs at F5 sign-off, now scheduled, not regressions. Point-in-time F5 records (status.md historical entries, T-519/T-539 audit reports, `docs/plans/T-*`) unchanged.

### L-026 deliberate boundary (the T-F5+ ID footprint — asymmetric, per the "substantive-rewrite-owner exists?" discriminator)

_(Line numbers in this ADR are indicative as-of-2026-05-17 pre-this-commit; the F6 §19 block insertion in this same commit shifts BRIEF §20/§22 down ~18 lines. The stable anchors are the **§-section + distinctive content**, not the volatile line numbers — repo convention, mirrors ADR-0011/0013.)_

A repo-wide `T-F5+` sweep classifies every occurrence:

- **(a)** the 8 OPEN F5+ backlog items (`TASKS.md:345-352`) → relabel `T-F5+`→`T-F6+` + move to `### F6+ opportunistic` (T-541).
- **(a-xref, relabelled in-commit)** `docs/CLAUDE_CODE_BRIEF.md` §22 `tier_promotion`/`tier_demotion` comment block ("the T-F5+ backlog ticket" — markdown) = a pure ID cross-ref to backlog **item 7** (no substantive-rewrite owner; item 7 stays opportunistic) ⇒ T-541 relabels the ID string **in the same commit** (trivial swap, no state/semantic change).
- **(a-xref, deliberately-bound — NOT relabelled in T-541)** `packages/bus/payloads.py` (`not modeled (separate T-F5+)`) + `packages/sizing/compute.py` (`OQ-2=A deferred (separate T-F5+ backlog)`) carry the SAME tier_promotion "separate T-F5+" code-comment cross-ref (surfaced by the T-541 WG1 repo-wide sweep — the pass-5 review was `.md`-focused and did not enumerate these `.py` comments). They are the identical a-xref class but **deliberately NOT relabelled by T-541**: T-541 is a 0-src markdown/governance phase-opener (approved Gate-1 scope = ADR + §19 + §22-md + TASKS only); editing `packages/*.py` comments would drift the approved plan + make a 0-src governance task touch source files. They are **stale-but-harmless ID strings** (comment-only, zero behaviour impact) — bound to the tier_promotion `T-F6+` task (it rewrites them when picked up) OR a trivial future `docs(...)` comment-fix. Recorded here per L-026 "enumerate ALL sites; fix-in-same-commit OR deliberately-bound-with-rationale" — this is the deliberately-bound arm, with the boundary + rationale explicit (NOT a silent miss).
- **(b)** the H-005 "T-F5+ ticket" prose-refs (`BRIEF` §20 H-005, §9.4 "Known hazards addressed" line, §22 opposite-side comments; `README.md`; `docs/status.md` forward-line; `docs/runbooks/F5_close_out.md` E4-residual; **and `services/strategy_engine/app/consumer.py:29-34`** — the module-docstring H-005-DEFERRED prose-ref, the source file L-026 itself originated from [original BLOCKER #1], surfaced by the T-541 WG1 `services/`+`packages/` sweep) **HAVE** a substantive-rewrite owner = **T-542** (rewrites them DEFERRED→implemented + the new test) ⇒ deliberately **NOT touched** by T-541 (pre-editing the ID = double-edit/scope-creep). Their line numbers shift with every doc/code edit — anchor by §-section / file + content, not the volatile line numbers.
- **(c)** immutable point-in-time (`docs/plans/T-*`, `status.md` historical entries, T-519/T-539 audit reports, `tests/test_hazard_catalog_coverage.py`) → never touched.

The asymmetry (fix §22-tier in-commit vs deliberate-bind H-005 to T-542) is the L-026 "fix-in-same-commit OR deliberately-bound-with-rationale" discipline applied via the substantive-owner discriminator — not arbitrary. `tests/test_hazard_catalog_coverage.py:139` asserts the §20 block contains literal `T-F5+`; because §20 H-005:2664 is bucket (b) (untouched by T-541), the meta-test stays green — no §N10/H-005-whitelist regression. When **T-542** un-DEFERs H-005 it must concurrently rewrite ALL bucket-(b) sites DEFERRED→implemented in one commit (per L-026): §20 H-005 + §9.4 line + §22 opposite-side comments + `README.md` + `docs/status.md` forward-line + `docs/runbooks/F5_close_out.md` E4-residual + the `services/strategy_engine/app/consumer.py:29-34` module-docstring, AND the `tests/test_hazard_catalog_coverage.py` literal-`T-F5+`/`_KNOWN_DEFERRED` coupling (T-542's scope — the H-005 plan's own L-026 site-set).

## Rejected alternatives

- Keep H-005/D9/etc as permanent carve-outs — operator chose to resolve them.
- A lightweight "post-F5 unlock marker" without a phase — operator explicitly chose a full phase for structure across multiple post-MVP items.
- Fold into F5 — F5 is signed-off-closed; reopening would invalidate the MVP attestation.
- (ADR-0011's own F6-rejection — reconciled in "Relationship to ADR-0011": context inverted post-sign-off.)

## Cross-references

- BRIEF §19 (phase plan); §6.7:825-827 (ADR brief-amendment mechanism); §0.10 (phase-gate)
- ADR-0011 (relationship; :72/:75 F6-rejection-in-pre-sign-off-context; :107 anticipated post-Live-ready phase)
- T-523 / T-500 / T-200 (phase-populator task precedent)
- pass-2 plan-reviewer phase-gate BLOCKER (the trigger)
- §20 H-005; L-026 (the deliberate-boundary discipline)

## Supersedes / superseded by

None. Does **NOT** supersede ADR-0011 (orthogonal pre/post-MVP-sign-off context); extends §19 per the post-Live-ready-phase path ADR-0011:107 anticipated.

## Follow-up — F6 scope clarification (2026-05-17, operator decision C)

At the T-545 plan-stage (the `SignalsSection.source_filter` dead-schema-field fix), plan-reviewer Gate-1 flagged per **L-029** that F6 as opened by this ADR named only the **3 residual carve-outs** (T-542 H-005 / T-543 D9 / T-544 strategy-engine-smoke); a new operator-directed fix (T-545) had no phase home — a §0.10 phase-gate near-miss in a signed-off-MVP repo (the identical pattern L-029 was born from at T-541; the main session-flow should have caught it at session-start per L-029's active control, the gate caught it).

**Operator decision C (2026-05-17):** F6 scope-intent is **extended**. F6 admits not only the 3 original F5-sign-off residual carve-outs but also **operator-directed post-MVP bug-fixes / improvements** surfaced after F5 sign-off. Governance for each such item:

- Promote to a **numbered F6 task** in `TASKS.md` `### F6 numbered` (the F6 `/N` denominator grows; the T-541 opener stays excluded — mirror T-500/T-523/T-200).
- **No per-item ADR** — ADR-0015 already unlocked F6 via the §6.7 mechanism; this follow-up is the **standing scope-intent basis** for the whole post-MVP batch (distinct from T-541, which had to *open* F6 itself).
- Each item still runs its own full Gate-1..4 cycle (this clarification governs *phase admissibility*, not the per-task review discipline).
- **Does NOT reopen the F5 §A+§B Live-ready sign-off** — F6 remains strictly additive post-MVP; no §N10 regression of the signed MVP.

First item under this clarification: **T-545** (`source_filter`). This follow-up is the governance basis for the upcoming operator-directed post-MVP batch (CI-minute-batched per the operator's deferred-push mode — see the session memory).
