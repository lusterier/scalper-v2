# F5 Task-Completion Reconciliation Audit (pre-T-522 ledger gate)

**Task:** T-539 — F5 task-completion reconciliation audit.
**Date:** 2026-05-17.
**Purpose:** Verify every F5 task-slot is genuinely DONE by hard commit-evidence (not prose), reconcile the `64/66` counter to pinpoint exactly which slots are open, and fix stale display before T-522's E1..E6 sign-off. Sibling of T-519 (T-519 audited §20-hazard-test coverage before E4; T-539 audits task-completion before E5/E6).
**Verdict:** **F5 LEDGER VERIFIED.** The `64/66` counter is correct and internally consistent (gapless chore-trail chain, every numerator `+1` a documented leaf close, every denominator change a documented scope-extension or L-007 split). The 2 open slots are **T-522's two documented sign-off sections** (E5 paper-feature-complete + E6 Live-ready) — **NOT a forgotten task**. 6 TASKS.md checkboxes were display-stale (`[ ]` while counter-closed) → corrected. No forgotten or uncounted task. Counter line 5 byte-untouched (correct as-is).

## Methodology

A slot is **DONE** iff ALL of:
- (a) a `feat`/`fix` commit exists;
- (b) reachable from master HEAD — evidenced by appearing in `git log master --pretty` (⇔ ancestor of master); spot-confirmed with explicit `git merge-base --is-ancestor <sha> master` for the 4 family-completing leaves + T-538;
- (c) a `chore(tasks)` close commit with its counter bump exists in the trail;
- (d) TASKS.md checkbox `[x]` — OR, for a stale split parent, ALL its leaves satisfy (a)–(d);
- (e) the deliverable lands on master via the feat commit (clause-b establishes file presence on master).

Evidence sources, independent of checkboxes: the `chore(tasks)` counter trail is the bookkeeping authority (`git log master --grep='chore(tasks)' --reverse`); the feat→SHA map is `git log master | grep 'feat(T-5'`. Checkboxes are display only — the counter follows the chore trail, not the boxes (this is exactly why a checkbox can be stale while the slot is genuinely counter-closed).

## Scope boundary (deliberate — §0.8 + L-026, recorded verbatim)

- **F0–F4 NOT re-audited** — closed and audited at their own phase-closes; out of T-539 scope.
- **T-537a1 / T-537a2 / T-537b / T-538 are OUT of the 66-counter** — closed (feats `6008ea0`/`d30f36a`/`687ec82`/`e0ad247` merged; chores `d06aaee`/`b97b8bd`/`051b5a2`/`ae0c7a1`/`8497138`/`c46fad4`) but tracked as **audit-item / H-034 / H-035 closures** ("late-night" addenda), the same no-F5-counter-token convention as `fix(T-216c/217c/218c)`. They are complete; they are simply not numbered F5 phase-counter slots. **T-529 IS in-66** (chore `a422106` = `31/52 F5`, feat `f90c382` "closes audit Item 6; H-036" merged) — a normal closed hardening-cluster slot.
- **Backlog `T-F5+` items are post-F5**, excluded from the 66.
- **No permanent CI counter-invariant guard built** — operator one-time default; a guard is a deferrable `T-F5+` (noted, not built; distinct from T-519 which shipped a meta-test).

## Counter reconciliation (WG2 — step-by-step, self-checking)

The denominator counts **leaf-slots**; the numerator counts **closed leaves**. The chore-trail is a **gapless chain** — each close's resulting numerator equals the next close's starting numerator, `+1` per leaf:

- **Start:** `2/22` (T-500 `c11c767`) climbing one-per-close through the F5-numbered set to `22/22` (T-512b `387b67d`).
- **Scope extension:** at T-523 (`7875392`, chore body verbatim: *"13 mandatory pre-live operational hardening tasks T-524..T-536 added per ADR-0011 … T-522 close-out runbook scope expanded to 2-section runbook (paper feature-complete + Live-ready)"*) the denominator jumps `22 → 44`; numerator `22 → 23`.
- **Split growth:** denominator `44 → 66` purely via documented increments — each `+1` an L-007/recursive split foundation leaf (T-513b1, T-516*, T-517*, T-534a, T-534b1, T-533a, T-533b1, T-527a, T-527b1, T-527b2a, T-528a, T-532a), `+2` at the T-525 recursive split (`42/57`). Final denominator reached `66` at T-532a (`ab8e0b7`, `60/65 → 61/66`).
- **Tail:** `61/66` (T-532a) → `62/66` (T-532b `abfa775`) → `63/66` (T-519 `2d9adeb`) → **`64/66`** (T-521 `52d0dcf`).

No numerator step skips, repeats, or jumps; every `+1` maps to exactly one documented leaf close; every denominator change has a cited rationale. **`64/66` is internally consistent.**

### Why `66 − 64 = 2` (NOT a forgotten task)

The only genuinely not-counter-closed leaf is **T-522** (no `chore(tasks): T-522 done`, no numerator bump). Yet the counter shows 2 open. This is **not** a second forgotten task — it is **T-522 owning two sign-off sections**, documented by three independent authorities:

1. **T-522 def (TASKS.md:207):** *"Single runbook with 2 sign-off sections (paper feature-complete + Live-ready) per OQ-4 default … **(E5 + E6 owner; per WG#5 + ADR-0011).**"*
2. **F5 exit-criteria trace (TASKS.md §"F5 exit-criteria trace"):** **E5** owner = "T-522 close-out runbook + sign-off (paper feature-complete section)"; **E6** owner = "T-524..T-536 + T-522 close-out runbook (Live-ready section)".
3. **The denominator-setting commit itself** — T-523 chore `7875392` body: *"T-522 close-out runbook scope expanded to 2-section runbook (paper feature-complete + Live-ready)"*. The commit that set denominator 44 explicitly expanded T-522 to two sign-off sections at the same moment.

The 2 open counter-slots **ARE** these two T-522 sign-off sections. Closing T-522 closes both (`64 → 66`, F5 complete); if T-522 splits into T-522+T-537 at its own plan-stage (conditional "if scope trips §0.3 cap" — not yet done), one section closes each. **Counter correct → TASKS.md line 5 NOT edited (WG5).**

## Findings — display-staleness (the only T-539 mutations)

Six TASKS.md checkboxes were `[ ]` while the slot was counter-closed (counter follows the chore trail, not the box). Each corrected to `[x]` with a `[T-539 un-stale]` annotation citing completing-feat + final-chore + counter:

| TASKS.md | Slot | Kind | Completing feat | Final chore (counter) | Was | Now |
|----------|------|------|-----------------|-----------------------|-----|-----|
| L224 | T-527 | split parent | `96fae4f` "completes the T-527 tier-sizing system" | `2c01a9a` (`57/64→58/64`) | `[ ]` | `[x]` |
| L226 | T-527b | intermediate split parent | `96fae4f` | `2c01a9a` | `[ ]` | `[x]` |
| L228 | T-527b2 | intermediate split parent | `96fae4f` | `2c01a9a` | `[ ]` | `[x]` |
| L230 | **T-527b2b** | **leaf** (deepest, ind6) — counter-closed, box stale | `96fae4f` | `2c01a9a` (`num +1`) | `[ ]` | `[x]` |
| L231 | T-528 | split parent | `610c8b4` "completes the T-528 feature" | `548d351` (`59/65→60/65`) | `[ ]` | `[x]` |
| L237 | T-532 | split parent | `2bfc6a3` "(completes T-532)" | `abfa775` (`61/66→62/66`) | `[ ]` | `[x]` |

The whole T-527 family (parent + both intermediate roll-up parents + the deepest leaf) was un-staled together — fixing only the top `T-527` parent and leaving `T-527b`/`T-527b2`/`T-527b2b` stale is the exact L-026 single-site-stop failure mode.

**T-522 (TASKS.md:207) correctly remains `[ ]`** — genuinely pending (the E1..E6 sign-off; cannot close until everything signs off). Not stale.

## Verdict

- **F5 ledger is trustworthy for T-522 sign-off.** `64/66` correct; gapless, internally consistent counter chain; no forgotten or uncounted task; no double-count.
- **2 open slots = T-522's two sign-off sections** (E5 paper-feature-complete + E6 Live-ready) — a documented 1-task-owns-2-criteria allocation, not missing work.
- **6 display-stale checkboxes corrected**; counter line 5 untouched (correct as-is).
- **Out-of-66 audit-item tasks** (T-537*/T-538) verified complete (feats merged + chores) — deliberately outside the phase counter, recorded above.
- **Only genuinely pending F5 work: T-522.** F5 closes when T-522 ships (`64 → 66`).

## Re-runnable verification

```
# gapless counter chain (every chore close, oldest→newest)
git log master --grep='chore(tasks)' --reverse --pretty='%h %s'

# feat→SHA map (every F5 feat/fix on master ⇒ ancestor-of-master by construction)
git log master --pretty='%h %s' | grep -E '(feat|fix)\(T-5'

# the 3 family-completing leaves + T-538 are ancestors of master
for s in 96fae4f 610c8b4 2bfc6a3 e0ad247; do git merge-base --is-ancestor $s master && echo "$s OK"; done

# T-522 is the only genuinely-uncounted leaf (no 'T-522 done' chore)
git log master --grep='chore(tasks): T-522 done' --oneline   # → empty

# stale-vs-fixed checkbox sweep in the F5 ## Next region
awk 'NR>=182 && NR<=256 && /\[[ x]\][[:space:]]*T-/' TASKS.md
```
