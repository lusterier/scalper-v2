# §20 Hazard Test-Coverage Audit (F5 E4)

**Task:** T-519 — §20 hazard test audit.
**Date:** 2026-05-16.
**Exit criterion:** E4 (BRIEF §19): *"All hazards in §20 have an associated test that passes."*
**Verdict:** **E4 SATISFIED — 35/36 hazards have a resolvable, pytest-collected, passing test; H-005 explicitly DEFERRED (operator-acknowledged residual-risk carve-out, T-F5+ backlog).**

## Mechanism (test-NAME-pin reverse-map — NOT `pytest -m hazard`)

The shipped §20 convention is the **test-NAME pin**: each `### H-NNN` entry's
`**Test.**` block cites its controlling test(s) by name in backticks. The
original T-519 task-def's `pytest -m hazard` / `@pytest.mark.hazard`
mechanism was a **stale pre-ADR-0011 artefact** — code-verified
non-existent (0 tree-wide `@pytest.mark.hazard` usages; `pyproject.toml`
`[tool.pytest.ini_options]` has `--strict-markers` but no `markers=[...]`
block — any `@pytest.mark.hazard` would fail collection). Dropped per
operator OQ-1=A.

The audit + permanent E4 regression guard is
**`tests/test_hazard_catalog_coverage.py`**: it parses the live §20 section
of `docs/CLAUDE_CODE_BRIEF.md`, extracts every `**Test.**` citation
(3 shapes: bare `` `test_x` ``, `` `path::test_x` ``, file-level
`` `path/test_y.py` ``; `+`-joined; code-ref backticks like `bus.close`
ignored), and asserts every citation resolves to a test in a subprocess
`pytest --collect-only -q` node-id set. A future §20 entry citing a
non-existent test **fails CI here** — the audit cannot silently rot.

**Re-run (the live E4 check):**
```
uv run pytest tests/test_hazard_catalog_coverage.py -q
```
plus the full suite for the *passing* half of E4 (the guard proves the
citation resolves to a collected test; the normal CI run proves it passes).

## Scope correction (H-026 → H-036)

The T-519 task-def said "H-001..H-026" — **doubly stale**. The actual §20
catalog is **H-001..H-036, 36 entries, no gaps** (verified contiguous by
`test_section20_catalog_is_contiguous_h001_to_max`). H-027/028/029 were
allocated 2026-05-15/16 at T-525a1/T-534b2/T-535; H-030..H-036 via the
operator audit 2026-05-08/09 — all postdate the task-def. Note: H-032/H-033
are physically positioned after H-036 in the file (deliberate audit-cluster
grouping); the contiguity check is on the **sorted integer set**, not
file-appearance order.

## Findings — 7 unresolved citations (6 corrected, 1 deferred)

The audit surfaced 7 §20 entries whose cited test name was not in the
collected suite. 6 were **stale v1-brief intended names** — the control IS
implemented and tested, F0..F4 just shipped the test under a different
(often deliberately cross-referenced) name. Citations corrected in §20 to
the actually-collected tests (the test docstrings independently confirm the
H-NNN binding):

| H-NNN | §20 cited (v1-brief intended) | Actual shipped test (corrected) |
|-------|-------------------------------|----------------------------------|
| H-006 | `test_webhook_rate_limit_rejects_above_threshold` | `services/signal_gateway/tests/test_rate_limit.py::test_over_limit_rejected` |
| H-009 | `test_duplicate_exec_event_is_ignored` | `services/execution/tests/test_dispatcher.py::test_execution_dispatcher_dedup_ring_drops_duplicate_exchange_exec_id` (docstring = explicit "§20 H-009 verbatim test pin") |
| H-010 | `test_signal_fanout_preserves_raw_before_dedup` | `services/signal_gateway/tests/test_webhook.py::test_invalid_json_returns_400` (asserts `subjects == ["signals.raw"]` — raw captured before validation) |
| H-015 | `test_orphan_close_uses_exchange_qty_string_not_float` | `packages/exchange/bybit_v5/tests/test_adapter.py::test_place_market_order_serializes_qty_as_decimal_string_not_float` + `::test_get_positions_preserves_qty_string_through_decimal_round_trip` (docstrings = explicit H-015 W#5/round-trip pins) |
| H-018 | `test_close_trade_updates_exactly_one_row_by_pk` | `packages/db/tests/test_queries_execution.py::test_update_trade_close_uses_where_id_pk_only_per_H_018` (name + `assert "WHERE id = $7"` = explicit H-018 pin) |
| H-024 | `test_sl_fill_after_partial_tp_labeled_sl_not_tp` | `services/execution/tests/test_dispatcher.py::test_post_tp_close_fill_labeled_per_db_sl_type_not_exchange_orderlink` — the v1 "sl_not_tp" semantic was **superseded by ADR-0005** (v2: partial_tp promotes `sl_type='trail'`, fill labels 'trail' not 'sl'); the H-024 invariant (exec_type from our DB execId→order_id, exchange order-link informational) is pinned by the v2 test |

The 7th — **H-005 (opposite-side guard)** — is a **genuine TOTAL coverage
gap**, NOT a stale-name rename:

- The §20 H-005 Policy claimed *"Implemented as a scoring rule
  (`block_opposite_position_open`)"* — **false**. The `opposite_side_open`
  scoring condition does NOT exist (`packages/scoring/conditions/` registry
  has plugin/rising/falling/ema_stack/… — zero `opposite`); no execution
  -side equivalent; `services/strategy_engine/app/consumer.py:30`
  explicitly defers it to a not-yet-existing "scoring-catalog extension".
- H-005 is a v1 production-earned capital hazard ("Live LONG BTCUSDT; SHORT
  signal arrives; v1 blocked"). v2 ships **without** this guard.
- **Operator decision (2026-05-16): DEFER + carve-out + backlog.** §20 H-005
  Policy + Test rewritten to reflect reality (DEFERRED, not "implemented");
  a NEW `T-F5+` backlog ticket tracks the implementation; E4 reported
  **35/36 with H-005 explicitly DEFERRED** (operator-acknowledged residual
  -risk, paper-feature-complete MVP scope). The meta-test whitelists H-005
  via the **tight** `_KNOWN_DEFERRED = {5}` set — a NEW deferral not in the
  set, OR H-005 losing its `DEFERRED` marker, still fails CI (the carve-out
  cannot silently widen or regress; §0.8 — implementing the rule is a
  separate feature task, NOT absorbed into the audit).
- **Brief self-consistency (§N10):** the same false "implemented as a
  scoring rule" claim was also corrected at the two other source-of-truth
  sites that directly contradicted the corrected §20 — `docs/CLAUDE_CODE_
  BRIEF.md:1565` (§9.5 strategy-engine "Known hazards addressed") and
  `services/strategy_engine/app/consumer.py:30` (module docstring) — both
  rewritten to "DEFERRED … see §20 H-005 / T-F5+" (doc-only, zero behavior
  change). Deliberate scope boundary (§0.8): the §B.1 alpha.yaml *example*
  config (`block_opposite_position` / `opposite_side_open` rule) and the
  immutable historical plan-docs (T-200/T-500/T-310b) are NOT edited by
  T-519 — they are forward-looking config examples / point-in-time records,
  not "hazard addressed" claims; the §B.1 example becomes valid when the
  T-F5+ ticket ships the `opposite_side_open` condition.

## E4 verdict

35/36 §20 hazards have a resolvable, pytest-collected test that passes in
CI; H-005 is explicitly DEFERRED with operator sign-off + a `T-F5+` backlog
ticket + a tight CI-guarded whitelist. **E4 is SATISFIED** for the
paper-feature-complete + Live-ready-hardening MVP, with the H-005 carve-out
documented here and in §20. The permanent guard
(`tests/test_hazard_catalog_coverage.py`) prevents future §20-citation rot.
