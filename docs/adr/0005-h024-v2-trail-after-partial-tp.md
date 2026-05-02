# ADR-0005: H-024 v2 semantic — partial_tp promotes sl_type to 'trail'; subsequent close fill labeled 'trail' (not 'sl')

Status: accepted
Date: 2026-05-02
Deciders: operator, Claude Code
Records: brief-vs-implementation deviation flagged by plan-reviewer during T-218b Gate 1

## Context

Brief §20 H-024 (lines 2790-2796) specifies:

> **Context.** Partial-TP followed by SL: first SL fill inherited TP orderLinkId, was mislabeled.
> **Policy.** `exec_type` is assigned based on matching `execId → order_id` in our DB. Order-link fields from exchange are informational, not authoritative.
> **Test.** `test_sl_fill_after_partial_tp_labeled_sl_not_tp`.

The brief test name (`labeled_sl_not_tp`) reflects a v1 incident: in v1, after a partial-TP fill followed by a stop-loss fill, the SL fill carried the TP's `orderLinkId` from Bybit's response, and the v1 dispatcher mislabeled the SL fill as `'tp'`. The hazard policy fixed the mislabel by re-deriving `exec_type` from our DB state (orders-lookup matching `execId → order_id`), independent of exchange order-link fields.

T-218a (commit `fa46399`, 2026-05-01) consolidated F2 execution dispatcher design and at OQ-5 the operator approved a v2 semantic enhancement: **on `partial_tp` fills, the dispatcher promotes `position_state.sl_type` from `'protective'` (or `'be'`) to `'trail'`**. This activates trailing-stop semantics on the remaining qty, per the v2 trade lifecycle (§9.5:1591 "marks trailing on TP hit").

The downstream consequence — surfaced during T-218b Gate 1 plan-review (2026-05-02) — is that after a partial-TP fill the next opposite-side full-close fill is labeled `'trail'` (not `'sl'`), because the dispatcher's exec_type derivation reads `ps.sl_type='trail'` and selects the `'trail'` branch. The brief test name `test_sl_fill_after_partial_tp_labeled_sl_not_tp` therefore no longer reflects the v2 expected outcome; the v2 outcome under the same scenario is `'trail'`.

This ADR records the deviation so future readers of the brief understand why the implemented test asserts `'trail'` and not `'sl'`, and so the H-024 hazard binding remains traceable to its v2 form.

## Decision

**v2 H-024 hazard binding:** `exec_type` is derived from our DB state, not from exchange order-link fields (unchanged from brief). The DB state value used at the comparison point — specifically `position_state.sl_type` — reflects v2's trailing-on-TP-hit semantic per T-218a OQ-5. Concretely:

1. Partial-TP fill (`event.qty < remaining_qty`, opposite side, no orders row for synthetic SL/TP fill) → `exec_type='partial_tp'` AND `position_state.sl_type ← 'trail'` (write-back).
2. Subsequent full-close fill (`event.qty == remaining_qty`, opposite side, no orders row) reading `position_state.sl_type='trail'` → `exec_type='trail'` (NOT `'sl'`).

The brief test name `test_sl_fill_after_partial_tp_labeled_sl_not_tp` is **superseded** in v2 by the v2-aligned name:

`test_post_tp_close_fill_labeled_per_db_sl_type_not_exchange_orderlink`

The new test name preserves the hazard binding's load-bearing claim — *label derives from our DB state, not from exchange order-link inheritance* — while reflecting the v2 sl_type promotion semantic. The v2 test asserts the second-fill label is `'trail'`.

## Rationale

- **T-218a OQ-5 already approved the v2 semantic**: the operator explicitly approved `partial_tp → sl_type='trail'` at T-218a plan-time (2026-05-01). T-218b's plan-reviewer (2026-05-02) flagged that this approval implies a brief deviation that needs an ADR to be formally recorded; this ADR is the recording, not a re-litigation.
- **Hazard load-bearing claim is unchanged**: H-024's invariant — *exec_type derives from our DB, not from exchange order-link fields* — is satisfied by both v1 and v2 semantics. The deviation is narrow: only the value read out of DB at the comparison point differs (v1: sl_type unchanged after partial_tp; v2: sl_type promoted to 'trail'). The hazard's purpose (preventing exchange-side-orderlink mislabel) is preserved by the v2 derivation algorithm.
- **§0.6 architectural-decision discipline**: deviating from a verbatim brief construct (test name + asserted label) without an ADR would leave a silent gap between the brief and the codebase. Future readers consulting §20 H-024 would see `_sl_not_tp` and not understand why the test asserts `'trail'`. ADR-0005 closes that loop.
- **v2 trailing-on-TP-hit is core lifecycle**: §9.5:1591 explicitly documents "marks trailing on TP hit"; v2's design committed to trailing being the canonical post-partial_tp state. Writing `'sl'` after a partial_tp fill (per v1 test name) would actively contradict §9.5:1591. The v2 deviation is thus brief-internally-consistent (it aligns with §9.5) and only conflicts with the H-024 test-name literal.

## Consequences

Positive:
- T-218b plan can land cleanly without sneaking around the brief test name.
- Future tasks consulting H-024 (T-219 close flow, T-220 audit loop, T-221 reconciliation) see ADR-0005 in the recent-3-ADR context per §6.6, so the v2 semantic is foreground knowledge.
- Brief-divergence is now documented; auditors comparing brief to code have a paper trail.

Negative / trade-offs:
- Brief §20 H-024 verbatim text is now stale relative to the v2 implementation. Per §0.6 ADRs are the recorded path for spec deviations, so this is an accepted trade-off (briefs do not get rewritten; ADRs accrete).
- Two test-name conventions exist in the codebase (brief literal vs. v2-aligned). T-218b uses v2-aligned + cross-references the brief literal in the test docstring so readers searching by either name can navigate.

## Alternatives considered

- **Keep brief test name verbatim, assert `'trail'`**: rejected. Test name `_sl_not_tp` would semantically misrepresent the assertion. A reader skimming test names would expect the body to assert `'sl'`, then be confused when the body asserts `'trail'`. Test names are documentation; making them lie is a regression.
- **Revert v2 partial_tp → sl_type='trail' to keep brief literal**: rejected. Trailing-on-TP-hit is a core v2 feature per §9.5:1591 and §21 glossary entry "Trail". Removing it would gut a §9-named lifecycle behavior to preserve a §20 hazard test name. Wrong trade-off.
- **Add a separate v1-style scenario test where partial_tp does NOT promote sl_type, then assert `'sl'` on the close fill**: rejected. Such a scenario does not exist in v2 (we always promote post-partial_tp). Adding a synthetic test for a non-existent state would be dead-test code that drifts.

## Cross-references

- Brief §20 H-024 lines 2790-2796 (the hazard whose test name is superseded by this ADR).
- Brief §9.5:1591 ("marks trailing on TP hit") — the v2 lifecycle root.
- T-218a `docs/plans/T-218a.md` OQ-5 — operator approval of `partial_tp → sl_type='trail'` (2026-05-01).
- T-218b plan (forthcoming) `docs/plans/T-218b.md` — applies this ADR's decision in the `_derive_exec_type` algorithm.

## Follow-up

- T-218b plan revise must cite this ADR in the H-024 hazard binding row of its hazards table.
- No brief edit. Briefs are append-only-by-ADR per §0.6.
