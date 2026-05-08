# ADR-0011: F5 scope extension to "Live-ready MVP"

**Status:** Accepted (2026-05-08, T-523 plan-stage Gate 1 pass-2)
**Context window:** F5 close-out + pre-live deployment hardening
**Authors:** Operator + general-purpose research agent (audit 2026-05-08) + plan-reviewer (T-523 Gate 1 APPROVE)

## Decision

F5 scope is extended with **13 mandatory hardening tasks (T-524..T-536)** plus 1 meta-chore (T-523 itself) covering pre-live operational gaps surfaced by the operator in session 2026-05-08. The F5 exit criterion E5 is renamed from *"Operator signs off Plný MVP scope"* to *"Operator signs off Live-ready MVP scope"*. A new exit criterion E6 is added: *"All hardening tasks shipped + integration tests green + Live-ready deployment runbook executed"*.

Hardening clusters by category:

- **Risk management (3 tasks)**: T-524 bot-level concurrent-trades caps + T-525 daily loss limit / max drawdown stop + T-526 cooldown after loss / losing-streak cooldown.
- **Position sizing (3 tasks)**: T-527 §B.1 sizing block reified + T-528 risk-per-SL sizing + T-529 qty_step rounding / min order / available_balance pre-check.
- **Account balance / equity tracking (3 tasks)**: T-530 `ExchangeClient.get_account_balance()` protocol extension + T-531 equity snapshot table + APScheduler tick + Prometheus gauge + T-532 funding fee tracking.
- **Trade lifecycle FSM (1 task)**: T-533 named-state `TradeLifecycleState` enum refactor.
- **SL/TP verification (3 tasks)**: T-534 periodic SL watchdog APScheduler tick + T-535 SL overwrite protection + T-536 trailing SL audit pass.

## Context

### BRIEF §19 original F5 scope

BRIEF §19 originally defined F5 as "MVP feature-complete-on-paper": shadow variants + backtest harness + UI extensions + close-out polish. The audit-grade focus was on **trading correctness** (TDD financial math, hazard catalog, P&L audit loop) — operational safety for live deployment was implicit. F5 exit criteria E1..E5 covered backtest ✓, comparison ✓, shadow restart ✓, hazard tests ✓, MVP sign-off ✓ — but did NOT cover bot-level risk caps, balance-driven sizing, account equity tracking, named-state FSM, or periodic SL verification.

### Operator session 2026-05-08 audit

Operator surfaced 6 categories of pre-live operational gaps post-F5-feature-complete. General-purpose research agent audit results (commit `387b67d` ship of T-512b):

- **Bucket A (already shipped)**: 8/9 of bod-4 (PnL accounting) sub-items — cumulative-delta per ADR-0006, fee-per-fill, partial TP, reduce-only, realized/unrealized split, executions audit table, T-220 audit loop, restart reconciliation T-221. Bod-6 partially (SL confirmation 3× retry T-216b1, emergency_close H-004, restart rehydration).
- **Bucket B (planned but unshipped, no task ID)**: §B.1 sizing block (BRIEF-deferred to F4+) + qty_step rounding (`placement.py:153` WARN-log stub) + `virtual_balance{bot_id}` Prometheus gauge mention.
- **Bucket C (silent everywhere — BRIEF + TASKS + ADRs none)**: All bot-level risk caps (max_open_trades, daily_loss, max_drawdown halt, cooldowns, losing-streak), % / risk-per-SL sizing, altcoin cap, min order / available-balance pre-checks, `get_account_balance()` adapter protocol method, wallet/available/equity/margin tracking, funding fees, periodic SL existence check, SL overwrite protection, the named SIGNAL_RECEIVED..RECONCILED enum.

### Architectural tension

The shipped trade lifecycle is split across 4 columns (`trades.status` text + `orders.status` text + `position_state.tp_hit/trailing_active/sl_type` flags + `close_reason` enum). Operator's proposed named-state FSM enum (T-533) consolidates these. This is a refactor with sibling test impact (L-015) — surfaces at T-533 plan-stage.

## Rationale

- **"Plný MVP" semantic should match production-ready, not feature-complete-paper.** Going live without bot-level risk caps risks capital loss; without periodic SL watchdog risks naked positions; without available_balance pre-check risks rejected-order pile-up; without funding fee tracking the cumulative-delta P&L audit (T-220, ADR-0007) drifts from exchange truth.
- **Single sign-off per operator preference** — cleaner than two-stage (paper-MVP + live-ready) sign-off conceptually. Operator's session 2026-05-08 OQ-1=B chose extend-F5 over new-F6.
- **BRIEF original F5 definition was incomplete vs operator intent**; this ADR formalizes the scope correction without requiring a new phase. ADR is the canonical mechanism per §6.7 protocol — BRIEF amendments via ADR (not direct BRIEF edits without rationale trail).

## Consequences

### Accepted

- F5 close-out delayed by 13 hardening tasks (~3-4 weeks at current cadence).
- T-522 close-out runbook scope expanded to include Live-ready criteria. Default = single runbook with 2 sign-off sections (paper feature-complete + Live-ready). T-522 plan stage may split into T-522 + T-537 if scope trips §0.3 cap.
- BRIEF §19 F5 block updated: E5 renamed (Plný MVP → Live-ready MVP); E6 added; scope-expansion paragraph noting hardening sub-cluster + footnote pointer to this ADR.
- F5+ opportunistic items absorbed-and-promoted to mandatory:
  - F4+ "risk-based position sizing — replace `execution.qty: Decimal` with `sizing.tiers` block from §B.1:3006-3025" (TASKS.md line 265 verbatim) → **T-527**.
  - "T-F2+: Bybit V5 instruments-info step-size cache" (TASKS.md line 269 verbatim) → **T-529**.
  - BRIEF §15:2151 `virtual_balance{bot_id}` Prometheus gauge mention → **T-531**.
- 5 hardening tasks pre-flagged for L-007 split-watch at plan stage: T-525 (loss limit FSM + state persistence + reconcile pattern matches L-014 FSM-execution profile) + T-527 (§B.1 block large) + T-528 (sizing.method discriminator dispatcher) + T-533 (large refactor across modules + migration + state population) + T-534 (APScheduler tick + emergency_close on miss matches L-016 restart-recovery profile).

### Trade-offs

- Single sign-off bundles paper-feature-complete + production-ready — operator cannot mark MVP shipped at intermediate paper-only state.
- Hardening tasks land late in F5 (after existing tail T-513b + UI T-516+T-517 + T-518..T-521) — reduces parallelism within phase but maintains ordering coherence (UI surfaces real data first; then hardening; then close-out).
- ADR-0011 records BRIEF amendment via ADR mechanism rather than direct BRIEF rewrite — preserves audit trail at cost of 2-place coherence (BRIEF §19 + ADR-0011 must both be updated when scope changes).

### Anticipated hazards (NOT yet added to BRIEF §20 catalog per §0.8)

The following hazards are anticipated based on hardening task semantics but **NOT added to BRIEF §20 catalog at T-523 time** — each surfaces during the relevant task's plan stage and gets formal H-NNN allocation then (per §0.8 anti-hypothetical: don't add hazard catalog entries for unborn implementation).

- **H-027 (anticipated, T-525)**: Daily loss limit / drawdown stop must be persisted across restart and re-evaluated on startup (mirror T-221 reconcile pattern). Otherwise restart resets the kill-switch.
- **H-028 (anticipated, T-534)**: Periodic SL watchdog must distinguish "Bybit side dropped SL" from "Bybit side returned 'no positions' on transient error" — false-positive emergency_close would close real positions wrongly.
- **H-029 (anticipated, T-535)**: SL overwrite detection must NOT fire false-positive on legitimate trail SL updates (same trade_id + same direction) — only on out-of-FSM updates.

## Rejected alternatives

- **Separate F6 phase**: rejected per operator Q1=B 2026-05-08. Would have moved hardening cluster post-F5 sign-off; preserves F5 narrow scope but doubles sign-off ceremony. Operator preferred single sign-off semantic — "Plný MVP" should mean production-ready.
- **F5+ opportunistic only (no scope rename)**: rejected. These are mandatory before live, not nice-to-haves; opportunistic backlog is for genuinely-deferrable enhancements. Misclassifying capital-safety items as opportunistic risks operator skipping them under cadence pressure.
- **Defer indefinitely / ship as-is**: rejected. Capital-loss risk before bot-level caps are in place. Operator's live-deployment timeline depends on production-ready hardening.
- **Extend BRIEF §19 with new F6 phase block**: rejected per operator Q1=B. Would have added phase ceremony but didn't change actual work; new F6 creates 2-phase MVP narrative which contradicts BRIEF's existing single-MVP-sign-off model.
- **Direct BRIEF §19 rewrite without ADR**: rejected per §6.7 ADR discipline. Cross-cutting scope change (adds 13 mandatory tasks + renames exit criterion) requires ADR rationale trail. ADR-0011 is the canonical mechanism.

## Cross-references

- BRIEF §19:2575-2591 (F5 phase definition — addended by this ADR)
- BRIEF §B.1:3006-3025 (sizing block — promoted to T-527 mandatory)
- BRIEF §15:2151 (`virtual_balance` Prometheus mention — promoted to T-531 mandatory)
- BRIEF §15.2 (signal-acceptance gating — extended by T-524 + T-526)
- BRIEF §20 H-001 (cumulative-delta P&L per ADR-0006 — bod 4 already shipped, no task)
- ADR-0006 (cumulative-delta reconciliation — covers bod-4 PnL accounting majority)
- ADR-0007 (P&L audit loop — covers bod-4 internal vs exchange comparison sub-item)
- T-220 (P&L audit loop, ADR-0007 implementation)
- T-221 (restart reconciliation, H-020 + H-026)
- T-310a OQ-4 (§B.1 sizing block F4+ deferral — historical anchor; superseded by T-527)
- T-500 (F5 backlog populator — meta-task precedent for T-523 pattern)
- T-512b (F5 E3 partial sign-off — variant half; T-513b mirror remains for full E3)
- L-007 (pre-emptive split mechanic — applies to T-525 + T-527 + T-528 + T-533 + T-534 at plan stages)
- L-014 (FSM-style execution-service tasks LOC calibration — T-525 + T-528 watch)
- L-015 (sibling migration test impact — applies to T-531 + T-532 + T-533 plan-docs)
- L-016 (replay-recovery / restart-resume FSM tasks LOC calibration — T-534 watch)

## Follow-up

- **T-522 close-out plan stage** decides 2-section vs split-T-522/T-537 runbook structure.
- **T-525 + T-527 + T-528 + T-533 + T-534 plan stages** decide pre-emptive split per L-007 (5 tasks pre-flagged at T-523 time).
- **T-530 + T-532 plan stages** decide migration ordering (0016 + 0017 vs combined; sibling impact per L-015 if combined).
- **Per L-015 active control: T-531 (migration 0016) + T-532 (migration 0017) + T-533 (migration 0018) plan-docs MUST include "Sibling migration test impact" section** listing every earlier `tests/integration/migrations/test_NNNN_migration.py` whose assertions touch any modified table.
- **H-027 / H-028 / H-029 formal allocation** at T-525 / T-534 / T-535 plan stages respectively.

## Supersedes / superseded by

None. Future work: if a separate F7 (post-Live-ready) phase emerges (e.g., multi-account portfolio management, advanced strategies), revisit via new ADR.
