# ADR-0016: H-005 opposite-side guard implemented as a consumer pre-scoring gate (BRIEF §20/§22 amendment)

**Status:** Accepted (2026-05-17; operator architecture decision "Consumer gate" at the T-542 Gate-1 plan-stage; F6 numbered T-542, the original F6 driver)

## Context

§20 **H-005** (live LONG, opposite SHORT arrives — v1 blocked, v2 DEFERRED). BRIEF §20 Policy + §22 (`block_opposite_position` rule, `condition: type: opposite_side_open, bot: self`, weight -999, required) specified the control as a **scoring condition** `opposite_side_open` (per-bot enable/disable, default blocked). T-519 audit (2026-05-16) confirmed it was never built: no evaluator in `packages/scoring/conditions/`, no execution-side equivalent, `consumer.py:30` defers it. F5 signed-off with E4 35/36 + H-005 DEFERRED (operator-acknowledged residual). F6 opened (ADR-0015); T-542 is the numbered task resolving it.

The shipped F5 reality: **all 7 pre-scoring guards** (3a TTL, 3b symbol, 3b' source-filter [T-545], cooldown [T-526], caps [T-524], loss-limit [T-525a2], drawdown [T-525b]) are **consumer silent-skip gates** deriving state from the DB per signal. The `opposite_side_open` scoring-condition design would be the *only* position-state-dependent scoring condition and requires `ctx.bot.*` position-state context-plumbing into the scoring evaluator that does not exist (a novel subsystem).

## Decision

Implement H-005 as a **consumer pre-scoring silent-skip gate** `risk.block_opposite_side` (default `True` = blocked, per the BRIEF policy intent), mirroring the shipped T-526 cooldown gate: a per-signal DB read of the open-positions table (`position_state` / `paper_position_state` by `exchange_mode`), blocking a new entry whose mapped side (`_ACTION_TO_SIDE[action]`) is opposite the open position's side for `(bot_id, symbol)`. Silent-skip (trading.log info + Prom counter + return before scoring) — same class as cooldown/caps. **Retire** the BRIEF §20/§22 `opposite_side_open` scoring-condition design + the `block_opposite_position` YAML rule.

## Consequences

- BRIEF §20 H-005 Policy/Test rewritten (gate, not scoring rule); **both** §22 occurrences retired — site A (`block_opposite_position` rule, `condition: type: opposite_side_open`) and site B (`opposite_side_open` plugin `entry_point: packages.scoring.rules.opposite_side:OppositeSideOpenRule`) — each replaced by a `risk.block_opposite_side` pointer; the §9.4 strategy-engine "Known hazards addressed" H-005 line + the §19 F6 exit-criteria mechanism wording updated.
- NEW `services/strategy_engine/app/opposite_side_gate.py` + a focused `packages/db/queries` open-position-side helper + `RiskSection.block_opposite_side: bool=True` + consumer wiring + a `signals_blocked_opposite_side_total{bot_id,reason}` counter.
- E4 35/36 → **36/36**: production `_KNOWN_DEFERRED → frozenset()` + §20 H-005 `**Test.**` cites the collectable `test_blocks_opposite_open_side`; the parser self-test `test_deferred_carveout_detected_and_tightly_gated` is decoupled from the production constant (synthetic H-901/H-902 + local `_synthetic_ack`). The L-026 un-DEFER site-set is rewritten in one commit.
- **F5 §A+§B Live-ready sign-off NOT reopened** — F6 additive; H-005 was an operator-acknowledged carve-out, now resolved, not a regression. **Two enumerated operator-signed/point-in-time records stay byte-unchanged (ADR-0015:35 invariant over ADR-0015:46 site-list — the §35-vs-:46 reconciliation):** (1) `docs/runbooks/F5_close_out.md` :86/:90/:105 signed §A/§B "Result: PASS WITH 3 PARTIALS … E4 35/36 + H-005 DEFERRED" attestations (true as of `2026-05-17T05:17:35+00:00`) — non-mutating forward-annotation footnote only; (2) `docs/status.md` :13/:15/:19 T-541 historical entry — untouched, the un-DEFER satisfied by T-542's own new newest-first entry. Neither rewrite falsifies a signed/historical record.
- math-validator OUT-of-scope (no Gate-4 trigger path; boolean side check).

## Rejected alternatives

- **Scoring condition `opposite_side_open`** (BRIEF design-of-record): needs nonexistent `ctx.bot.*` position-state context-plumbing into the scoring evaluator — a novel subsystem, larger, riskier, deviates from the uniform shipped gate architecture; triggers math-validator (`packages/scoring/`). Operator rejected in favour of the gate.
- In-memory `orders.events.<bot_id>` own-position view (§9.4 step 4): unbuilt; large; the consumer CLOSE-action block is deferred for exactly this reason.

## Relationship to ADR-0015 / ADR-0011

Under F6 (ADR-0015) as numbered T-542 (an original F5-residual carve-out, distinct from the decision-C operator-directed batch — H-005 was named in ADR-0015's F6 numbered set from the start). Extends BRIEF §20/§22 via the §6.7 ADR brief-amendment mechanism (sibling-mechanism of ADR-0011/ADR-0015). Does NOT supersede any ADR.

## Cross-references

BRIEF §20 H-005 / §22 / §9.4 / §19 / §6.7 / §15.2; ADR-0015 (F6; L-026 un-DEFER site-set :46/:49); T-526 cooldown_gate (the mirrored precedent); T-545 (the sibling silent-skip gate pattern + structural-anchor lesson); L-026 / L-029.

## Relevant paths

- precedent to mirror: `services/strategy_engine/app/cooldown_gate.py` (full structure), its consumer wiring `consumer.py:183-217`, `metrics.py` `signals_blocked_cooldown`
- `services/strategy_engine/app/consumer.py:88` `_ACTION_TO_SIDE` (reuse), `:172` CLOSE block (insert after), `:29-34` docstring (un-DEFER)
- `packages/db/queries/execution.py:649` `select_recent_open_trade_exists` (focused-query precedent), `analytics.py:515` `select_open_positions` + `_row_to_open_position` (`position_state` schema, `.side`)
- `packages/scoring/types.py:409` `RiskSection`
- `packages/bus/schemas/signals.py:65` `SignalValidated.action`; side vocab `packages/exchange/protocols.py:95` `Literal["buy","sell"]`
- L-026 un-DEFER (token-anchored): BRIEF §20 `### H-005` / §9.4 strategy-engine `**Known hazards addressed:** H-005` / §22 site A `block_opposite_position` / §22 site B `opposite_side_open` entry_point / §19 F6 exit-criteria; EXCLUDE the H-024 `**Test.**` "opposite-side" prose (unrelated, ADR-0005). `README.md` F5-sign-off + E4-status lines. `tests/test_hazard_catalog_coverage.py`: `_KNOWN_DEFERRED → frozenset()` + doc, `_FIX_DEFERRED` relabel H-901/H-902 + `test_deferred_carveout_detected_and_tightly_gated` decouple → local `_synthetic_ack`. `docs/runbooks/F5_close_out.md` signed §A/§B lines (forward-annotation footnote only). `docs/status.md` T-541 entry (untouched; new entry at close).
