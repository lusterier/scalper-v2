# Session status

## 2026-05-17 (T-543 ✅ **D9 SERVICE_NAME mislabel fixed** — feat `826189a` LOCAL [batch-push mode]; F6 2/4 → 3/4; gates [plan APPROVE 1-pass / drift skipped trivial / brief SHIP 5/5 WG / math OOS])

**What**: native analytics-api (via `scripts/dev-up.sh`, not the container) logged `service=signal-gateway` — a §N2 misattribution (D9, flagged at the T-522 F5 close-out RUN `b16d41a`, carved out of T-540). Root cause: `dev-up.sh` sources `.env` with `set -a` (auto-export) → the shared `.env`'s `SERVICE_NAME=signal-gateway` leaks; the native analytics-api uvicorn launch pinned `DATABASE_URL`/`NATS_URL` but not `SERVICE_NAME` → analytics-api `Settings()` inherited it. Fix: pin `SERVICE_NAME=analytics-api` in that env prefix (mirrors `compose.yaml:452` per-service `SERVICE_NAME` — the containerized path was always correct). NEW `tests/scripts/test_dev_up_service_identity.py` line-continuation-robust parse regression pin (2 passed). `config.py`/`.env.example`/`compose` correctly untouched — the cross-service `SERVICE_NAME` env mapping is the correct pattern; the bug was only the native launch-script omission.

**Watch-outs for next session**:
- **F6 remaining: T-544 ONLY** (`strategy-engine-smoke` compose service — enables a full local F5_E2 deployment smoke; the last numbered F6 task; F6 3/4 → 4/4 on its close). F6+ opportunistic backlog (8 items) is separate.
- **⚠ BATCH-PUSH MODE STILL ACTIVE** (operator CI-minute budget). Unpushed `origin/master..HEAD` after the T-543 chore: `1ada2b6`, `80896e6`, `4a21b2e`, `b900a29`, `e057876`, `38b554d`, `826189a` (T-543 feat) + pending T-543 chore. **ONE `git push origin master` at batch end** — do NOT push per-task.

## 2026-05-17 (T-542 ✅ **H-005 opposite-side guard resolved** — feat `e057876` LOCAL [batch-push mode]; F6 1/4 → 2/4; the original F6 driver; ADR-0016; E4 35/36 → 36/36; gates [plan APPROVE pass-2 (1 REVISE) / drift ON TRACK / brief SHIP 8/8 WG / math OOS])

**What**: §20 H-005 (live LONG + opposite SHORT) was DEFERRED in v2 — the original driver that opened F6. Resolved as the per-bot `risk.block_opposite_side` **consumer pre-scoring silent-skip gate** (`services/strategy_engine/app/opposite_side_gate.py`), mirroring the shipped T-526 cooldown gate: per-signal DB read of `position_state`/`paper_position_state`, blocks a new entry whose `_ACTION_TO_SIDE`-mapped side is opposite the open position's side. `RiskSection.block_opposite_side: bool = True` (BRIEF "default blocked"). §N4 TDD (8-row truth table + CLOSE-ordering pin); 537 passed.

**ADR-0016**: the operator chose the **consumer-gate** architecture over the BRIEF §20/§22 design-of-record `opposite_side_open` **scoring condition** (all 7 shipped F5 pre-scoring guards are consumer gates; a scoring condition would need nonexistent position-state context-plumbing). ADR-0016 records this + the BRIEF §20/§22 amendment retiring the scoring-condition design (both §22 sites). L-026 un-DEFER in one commit (§20/§9.4/§22 A+B/§19/README/consumer-docstring + the meta-test 3-part decouple → E4 36/36). F5 §A+§B sign-off NOT reopened (F6 additive); `F5_close_out.md` signed lines byte-unchanged + forward-annotation only.

**Watch-outs for next session**:
- **NEW L-030** (the §N10 default-ON-gate blast-radius lesson — see `docs/review-lessons.md`): a config-default-ON guard with an unconditional per-signal DB read collides with the scoring-path mock pools of EVERY consumer-driving sibling test, not just same-hazard tests. Required test-isolation in `test_consumer.py` `_bot_config()` + 2 `packages/scoring/tests/` F3 files (real `configs/bots/*.yaml` NOT modified — production default stays `True`).
- **⚠ BATCH-PUSH MODE STILL ACTIVE** (operator CI-minute budget). Unpushed local `origin/master..HEAD`: `1ada2b6`, `80896e6`, `4a21b2e`, `b900a29`, `e057876` (T-542 feat) + the pending T-542 chore. **ONE `git push origin master` at batch end** — do NOT push per-task. F6 remaining: T-543 (D9 SERVICE_NAME), T-544 (strategy-engine-smoke).

## 2026-05-17 (T-545 ✅ **`source_filter` wired into the consumer** — feat `4a21b2e` LOCAL [batch-push mode]; F6 1/4; first operator-directed post-MVP fix under ADR-0015 decision-C; all gates [plan APPROVE pass-3/3 / drift ON TRACK / brief SHIP / math OOS])

**What**: `SignalsSection.source_filter` (schema since T-310a) was dead — never read at runtime, so a configured per-bot source allowlist was silently ignored. Wired into the consumer as a **3b' source-allowlist silent-skip gate** between the 3b symbol filter `return` and the CLOSE block — mirrors 3b EXACTLY (`trading_logger.info("signal_outside_source_filter")` + `return`; no Prometheus counter, no `metrics.py` change — same silent-skip class as `signal_outside_universe`). §N4 TDD: 4 cases incl. the CLOSE-ordering pin; 36 passed incl. sibling regression; ruff+mypy clean. `docs/runbooks/ops.md` new-bot YAML template now documents a commented-optional `signals:`/`source_filter` block (CLAUDE.md ops-runbook rule).

**Governance**: F6 admissibility per the **ADR-0015 2026-05-17 decision-C follow-up** (F6 scope extended to operator-directed post-MVP fixes; promoted to numbered T-545, no per-item ADR, no L-029 violation). F5 §A+§B Live-ready sign-off NOT reopened — F6 strictly additive.

**⚠ BATCH-PUSH MODE ACTIVE** (operator CI-minute budget 1912/2000): T-545 feat `4a21b2e` + the F6-governance commits (`1ada2b6`/`80896e6`) + prior batch are **committed LOCAL, NOT pushed**. More operator-directed F6 tasks follow in this batch; **ONE `git push origin master` at batch end** (the only CI-minute-consuming step). Do NOT push per-task.

## 2026-05-17 (T-541 ✅ **Phase F6 (Post-MVP Hardening) OPENED** — feat `7bdb459`; ADR-0015 §6.7 BRIEF §19 amendment; F5 §A+§B Live-ready sign-off NOT reopened, F6 strictly additive; all gates [plan-reviewer APPROVE pass-5/5 / drift ON TRACK / brief SHIP after 1 FIX FIRST / math OOS]; NEW L-029)

**Why F6 opened (the H-005→F6 arc):** the operator asked to resolve H-005 (the long-deferred `opposite_side_open` guard). The H-005 plan ran 2 plan-reviewer passes; **pass-2 BLOCKER-1** caught a §0.10 **phase-gate violation** — F5 was COMPLETE with "no next phase unlocked", H-005 was only a `T-F5+` backlog ticket, so pulling ~180-230 LOC trading-critical code in was not authorized. Surfaced to the operator; the operator chose a **full F6 phase**. T-541 is the §6.7 phase-opener meta-task (mirror T-523/T-500/T-200). **NEW L-029** records the generalizable lesson (a signed-off-MVP repo is phase-gated *harder*; "operator requested" ≠ phase unlock; backlog→numbered-task + explicit phase unlock is governance, not a plan-text fix — caught at plan-reviewer, the main session-start had wrongly treated the request as the unlock).

**F6 structure (TASKS.md):** `## Current Phase` line-3 = "F6 unlocked 2026-05-17 — ADR-0015" (F5 ✅ COMPLETE / 66/66 / §A+§B byte-preserved). NEW `### F6 numbered` — **F6 0/3** (T-541 opener excluded — phase-populator meta): **T-542** H-005 opposite-side-position guard + test (the original request, now properly phase-gated; own Gate-1 cycle), **T-543** D9 analytics-api `SERVICE_NAME` mislabel fix, **T-544** `strategy-engine-smoke` compose service. NEW `### F6+ opportunistic` = the 8 F5+ polish items relabelled `T-F5+`→`T-F6+` (text byte-verbatim); `### F5+ opportunistic` body removed + annotated; the `[x] T-540` DONE record retained byte-unchanged in-place (point-in-time). BRIEF §19 NEW `### Phase F6` block + §22 tier_promotion comment a-xref relabel. ADR-0015 records the **exhaustive L-026 T-F5+ classification** (a relabelled / a-xref-md relabelled in-commit / a-xref-py `payloads.py`+`compute.py` deliberately-bound / b H-005 prose-refs incl. `consumer.py:29-34` bound to T-542 / c immutable) — all 39 occurrences classified, 0 src touched.

**ADR-0011 reconciliation:** ADR-0011 had rejected an F6 phase (pre-sign-off, single-MVP-narrative protection); ADR-0015 deliberately differs because F5 is now signed-closed (F6 = post-MVP additive, not a 2-phase MVP), consistent with the post-Live-ready phase ADR-0011:107 anticipated. Non-supersede.

**Next:** T-542 — H-005 resolution (the operator's original request), now correctly phase-gated under F6. Its own Gate-1 cycle (the pre-scoring silent-skip gate architecture + the L-026 un-DEFER site-set, incl. the bucket-(b) `consumer.py:29-34` + meta-test coupling, are recorded in `docs/plans/T-541.md` forward-note + ADR-0015 for T-542's plan-stage). Also F6: T-543 (D9), T-544 (strategy-engine-smoke); F6+ opportunistic = the 8 carried polish items.

## 2026-05-17 (T-522 §B LIVE-READY operator sign-off RECORDED → ✅ **F5 PHASE COMPLETE** — operator `luster` @ `2026-05-17T05:24:14+00:00`; both §A+§B signed → F5 counter **64/66 → 66/66**; Result PASS WITH 3 PARTIALS; +2 authority `docs/audit/f5-task-completion.md` T-539 reconciliation)

**F5 is COMPLETE — Live-ready MVP signed off.** The operator explicitly attested §B (Live-ready, E6) and instructed the author to transcribe it (same pattern as §A — the operator's own informed attestation per explicit instruction, not author self-sign; L-027/WG2 bar *fabricating* a sign-off absent operator attestation, not transcribing an explicit one). With §A (paper-feature-complete, E1..E5) + §B (Live-ready, E6) **both** operator-signed, the shipped gate-approved `F5_close_out.md` "On sign-off complete" mechanic fires: F5 counter `64/66 → 66/66` + **F5 COMPLETE** (TASKS.md:3 phase header + line-5 counter). The `+2` is the documented E5+E6 sign-off-slot closure per `docs/audit/f5-task-completion.md` (T-539 reconciliation: `66−64=2` = T-522's two sign-off sections) — an authorized close-bump, NOT counter drift (WG4/L-027 precondition now met: both blocks FILLED, not blank).

**Recorded faithfully — NOT a clean PASS.** §B Result = PASS WITH 3 PARTIALS: hardening T-524..T-536 shipped **VERIFIED** (T-539 ledger); integration tests green **CI-grade** (per-task gates + CI-full; not re-run on the markdown-only HEAD); Live-ready deployment **PARTIAL** (close-out RUN built+ran the full stack healthy + live feed; full F5_E2 kill/restart stayed CI-grade per decision A; D1–D11 fixed by T-540). E4 35/36 + H-005 DEFERRED carve-out. The Live-ready sign-off is on the runbook's documented **informed known-residual basis**, which the operator accepted across the full session arc (decision A, H-005 acknowledgment, D1–D11 findings, L-028 honesty notes).

**Residual carve-outs remain tracked (NOT F5 blockers per the operator's informed sign-off, NOT erased by F5 COMPLETE):** H-005 → T-F5+ `opposite_side_open` ticket; D9 (analytics-api SERVICE_NAME mislabel) → separate `fix(...)`; a `strategy-engine-smoke` compose service for a full local F5_E2 deployment smoke. **No next phase unlocked** — that is the operator's call (phase-gate; the author does not presume F6). The full project MVP is now Live-ready-signed on the documented informed-residual basis.

## 2026-05-17 (T-522 §A PAPER-FEATURE-COMPLETE operator sign-off RECORDED — operator `luster` @ `2026-05-17T05:17:35+00:00`; Result **PASS WITH 3 PARTIALS** [E1 verified / E2 partial-depth empty-window / E3 CI-grade per T-522 decision A / E4 35/36 + H-005 DEFERRED]; **F5 counter UNCHANGED 64/66 — F5 NOT COMPLETE**)

**What happened**: the operator explicitly attested §A (paper-feature-complete, E1..E5) and instructed the author to transcribe the sign-off (`docs/runbooks/F5_close_out.md` §A block now filled: `operator: luster @ 2026-05-17T05:17:35+00:00`, Master HEAD `c024f21`). This is the operator's own informed attestation transcribed per their explicit instruction — **not** author self-sign (L-027/WG2 bar *fabricating* a sign-off without operator attestation; here the operator explicitly attested + directed it, on the fully-informed known-residual basis the runbook's "Deliberate residual" section frames). **§A only** — §B (Live-ready, E6) was NOT signed. Per the shipped F5_close_out "On sign-off complete" mechanic (gate-approved): the F5 counter bumps `64/66 → 66/66` + F5 marks COMPLETE **only when §A AND §B are both signed** → therefore the counter is **deliberately UNCHANGED 64/66** and F5 is **NOT COMPLETE** on §A alone (no false attestation; WG4/L-027 respected). §A's PASS WITH 3 PARTIALS is recorded faithfully (E2 empty-window partial-depth; E3 CI-grade per decision A — full F5_E2 deployment smoke not executed; E4 H-005 DEFERRED carve-out) — not a clean PASS. **Next (operator)**: E6/§B Live-ready sign-off when ready (its evidence basis: T-539 ledger VERIFIED + the hardening cluster shipped + a deployment-up check) → then the 1-line micro-chore bumps F5 `64→66` COMPLETE. Optional unrelated follow-ups remain: D9 separate `fix(...)`; a `strategy-engine-smoke` compose service for a full local F5_E2 deployment smoke.

## 2026-05-17 (T-540 F5 operator-runbook + BRIEF §12.2 accuracy fix CLOSED — D1–D11; feat `7b8c460`; **out-of-66, F5 counter UNCHANGED 64/66** [doc-accuracy + fixture; F5 sign-off stays the operator act per L-027/WG4]; all gates [plan APPROVE / drift ON TRACK / brief SHIP 5/5 WG / math OUT-OF-SCOPE]; NEW `configs/bots/smoke.yaml` runnable; no NEW review-lesson [L-026/L-028 applied not added])

**Outcome**: the F5 operator docs now run as written for the verified surface. NEW `configs/bots/smoke.yaml` (`scoring.mode: passthrough` + `rules: []` → deterministic accept, ZERO feature dependency — sidesteps the F4+-unimplemented `oi_change`; alpha/beta untouched) + a `shadow:` block. BRIEF §12.2 + F5_E1/F5_E2/F4_E1/F5_close_out corrected: D1 (alembic `-c migrations/alembic.ini` + `POSTGRES_URL`), D2/D3 (`python -m scripts.backtest` + `--config-path`), D4 (`127.0.0.1` `--db-url`), D5 (`execution.sl_pct` override), D6 (E3 = CI-full/testcontainer-gated, not ad-hoc), D7 (smoke.yaml shadow), D8 (`SIGNAL_GATEWAY_HMAC_SECRET`), D10 (per-bot `BOT_<ID>_PAPER_*` env), D11 (`BTCUSDT.P` payload). **EXECUTION-VERIFIED** (L-028 active control): the F5_E1 backtest path was re-run with smoke.yaml this session (completed, 5-aggregate summary, smoke.yaml schema-loaded incl. shadow). **AUTHORED-from-RUN-evidence, NOT re-run end-to-end**: the full F5_E2 live signal→kill→restart sequence — compose ships `strategy-engine-alpha`/`-beta` only; a `smoke` bot also needs a `strategy-engine-smoke` service (documented residual); E3 stays CI-grade per the T-522 close-out **decision A**. F5_E2 + F5_close_out carry explicit L-028 honesty notes. **Deliberately NOT touched (L-026 boundary)**: D9 (native analytics-api `SERVICE_NAME` mislabel → separate `fix(...)`, code class); `configs/bots/alpha.yaml`/`beta.yaml` (real bots; `oi_change` impl = separate F4+ ticket); closed-phase `F3_E1`/`F2_E1`. T-540 is **out-of-66** (no counter bump — mirror T-537/538/539). **F5 status unchanged: 64/66, NOT COMPLETE** — the formal Live-ready §A/§B sign-off remains the operator's act. **Next (operator)**: the F5 operator runbooks are now accurate for the verified path; remaining = the formal §A/§B sign-off (when ready) → 1-line micro-chore bumps F5 `64→66` COMPLETE; optionally the D9 separate `fix(...)` + a `strategy-engine-smoke` compose service if a full local F5_E2 deployment smoke is later wanted.

## 2026-05-17 (T-522 F5 close-out RUN — operator-delegated assisted execution; E1✅ E2✅partial E4✅ E6-deploy✅partial E3←CI-grade [operator decision A]; **11 discoveries D1–D11 → NEW T-F5+ follow-up**; **NEW L-028**; F5 counter UNCHANGED 64/66 — formal §A/§B operator sign-off still PENDING per L-027/WG2, NO false attestation)

**What was verified (assisted run; author gathered evidence, operator decides + signs):** **E1** ✅ 30-day backtest completes + persists 5-aggregate `backtest_runs.summary` (feat `e4723e8`/`db2d282`/`850b94a`; DB row `8c63bb36…` finished=t; empty-window 0-trades = valid per runbook). **E2** ✅ two-config `--compare` emits aggregate-diff + per-trade-diff sections (feat `fcdc453`; run_A `8c63bb36` vs run_B `e067c236`, `execution.sl_pct` override; partial-depth — empty window, mirror F4_E1 PASS-WITH-PARTIALS). **E4** ✅ `tests/test_hazard_catalog_coverage.py` **8 passed** (35/36 + H-005 DEFERRED). **E6-deploy** ✅partial — the FULL containerized stack (postgres/nats/market-data/feature-engine/strategy-engine-alpha/execution-service/signal-gateway) **builds + runs healthy** + live market-data feed (1445+ real BTCUSDT/ETHUSDT 1m candles) = genuine Live-ready deployment evidence. **E3** ← **CI-grade (operator decision A)**: 2 controlling tests shipped-green CI-full 2026-05-08 (T-512b/T-513b2) + T-519 §20 audit + the E4 meta-test pass (resolves H-016/H-023); the full F5_E2 kill→restart smoke was attempted but blocked by the D1–D11 prereq chain + alpha.yaml's `oi_change`-bound scoring (a known F4+-unimplemented feature → no signal accepts) — **environment/doc-prereq chain, NOT a shadow-restart product defect**; pushing further required faking the scoring config too → weak synthetic evidence, declined per operator A.

**Primary close-out finding — D1–D11**: the F5 operator runbooks (F5_E1/F5_E2/F4_E1/F5_close_out) + BRIEF §12.2 CLI + deployment prereqs **systematically do not run as written** (alembic `-c`+DSN, backtest `-m`/`--config-path`/host-DSN, CI-gated tests shown ad-hoc, missing shadow/paper-env/symbol-map prereqs, HMAC var name, analytics-api SERVICE_NAME mislabel, per-bot paper env for every `bots` row, `BTCUSDT.P` symbol-map key). Recorded as a NEW **T-F5+** backlog ticket (TASKS.md ### F5+ opportunistic) + **L-028** (runbook procedure-drift is uncaught by all 4 gates; never sign a phase on an unexecuted runbook). This is exactly what a close-out smoke is for — high-value output.

**Transients fully reverted**: `configs/bots/alpha.yaml` shadow block (git checkout), `compose.smoke.yaml` (rm), smoke containers stopped, analytics-api pidfile restored. **Operator restore**: `./scripts/dev-up.sh` (restarts native analytics-api; postgres/nats/vite were already up). Dev DB now holds ~24h backfilled real `ohlc_1m` (harmless — normal market-data-svc output; left as-is).

**F5 status: NOT COMPLETE — counter intentionally 64/66.** The formal Live-ready §A/§B sign-off in `docs/runbooks/F5_close_out.md` remains the operator's act (L-027/WG2 — no false attestation; the gates passed T-522's deliverable, not the phase sign-off). **Next (operator)**: when ready, formally sign §A/§B (evidence basis: E1/E2/E4 verified + E6-deploy partial + E3 CI-grade) → then the 1-line micro-chore bumps F5 `64→66` COMPLETE; and/or schedule the T-F5+ D1–D11 runbook-accuracy fix (recommended before relying on the F5 operator docs for an actual live deploy).

## 2026-05-17 (T-522 F5 close-out runbook DELIVERED — the final F5 close-out leaf; `docs/runbooks/F5_close_out.md` shipped + all 4 gates passed [plan-reviewer Gate-1 APPROVE 5 WG / drift ON TRACK / brief SHIP 5/5 WG / math-validator OUT-OF-SCOPE not-invoked]; **F5 counter INTENTIONALLY UNCHANGED 64/66** — T-522-the-TASK done but E5/E6 sign-off slots remain OPEN: operator must run the close-out smoke + sign §A/§B PASS; the 64→66 bump + "F5 COMPLETE" is a future operator-triggered micro-chore [operator decision B — no false attestation]; **NEW L-027**)

**Outcome**: T-522 the deliverable — the operator-runnable F5 close-out runbook with the commit/test-cited E1..E6 evidence trace + §A/§B blank operator sign-off blocks — is shipped + merged. **F5 is NOT yet complete**: E5 ("operator signs off Live-ready MVP") + E6 ("hardening shipped + integration green + Live-ready deployment runbook executed") are operator actions; the §A/§B blocks are deliberately blank (WG2 — author does not self-sign). Per operator decision (option B), the chore does **NOT** pre-bump the counter to 66/66 nor write "F5 COMPLETE" — that would falsely attest the MVP was signed off when it was not. The F5 counter intentionally stays **64/66** (T-539-verified: `66−64=2` = T-522 E5+E6, operator-gated). This is NOT a T-539-class stale checkbox: T-522 `[x]` = the TASK (author the runbook) is done; the 2 sign-off slots stay open until the operator executes `docs/runbooks/F5_close_out.md` and signs §A/§B PASS — which triggers the future micro-chore that bumps `64→66` + marks **F5 COMPLETE**. **NEW L-027** (plan-reviewer active control): a sign-off / operator-attestation task's plan must NOT pre-bump the phase counter or write a COMPLETE marker at the task-deliverable close — gate it on the actual operator action. feat `ed54563`; runbook `docs/runbooks/F5_close_out.md`; plan `docs/plans/T-522.md`. **Next (operator action — not a coding task)**: run `docs/runbooks/F5_close_out.md`, verify E1..E6, sign §A + §B; then a 1-line micro-chore bumps F5 `64→66` COMPLETE. No remaining F5 coding tasks.

## 2026-05-17 (T-539 F5 task-completion reconciliation audit CLOSED — pre-T-522 ledger gate, sibling of T-519; 0 src markdown-only; **F5 counter UNCHANGED 64/66** [T-539 out-of-66 audit-item — no num/denom bump, mirror T-537/T-538/T-529-audit convention; F5 closes at T-522 64→66]; all gates [plan-reviewer Gate-1 APPROVE 5 WG / drift ON TRACK / brief SHIP 5/5 WG / math-validator OUT-OF-SCOPE not-invoked — 0 .py, T-519/T-521/T-523 markdown-meta precedent]; ledger VERIFIED — counter gapless+consistent, no forgotten task, 6 display-stale checkboxes corrected; no NEW review-lesson [L-026 applied not added])

**Outcome**: the operator "nič nezabudnúť" concern resolved — the F5 ledger is trustworthy for T-522 sign-off. `64/66` is correct (gapless chore-trail `2/22` → T-523 ADR-0011 scope-extension `22→44` → documented L-007 split `+1`s → `64/66`; every numerator `+1` a documented leaf close, no skipped/double bump). The `66−64=2` open slots = **T-522 two documented sign-off sections** (E5 paper-feature-complete + E6 Live-ready) per T-522 def + F5 exit-criteria trace + the denominator-setting T-523 chore `7875392` body — NOT a forgotten task. 6 TASKS.md checkboxes were display-stale only (the counter follows the chore trail, not the box): the whole T-527 split family (T-527 / T-527b / T-527b2 / leaf T-527b2b) + T-528 + T-532 flipped `[ ]`→`[x]` with feat+chore+counter annotation (L-026 — no single-site stop). Counter line 5 byte-untouched (WG5: `64/66` proven correct). Out-of-66 audit-items T-537a1/a2/b + T-538 verified complete (feats merged + chores); T-529 in-66; F0–F4 not re-audited; backlog `T-F5+` excluded; no CI counter-guard built (operator one-time default). feat `39c0bfa`; report `docs/audit/f5-task-completion.md`; plan `docs/plans/T-539.md` (Gate-1 APPROVE + 5 WG). **Next**: T-522 — the final F5 close-out leaf (E1..E6 sign-off / Live-ready MVP); the ledger is now trustworthy for its sign-off. After T-522, F5 closes `64 → 66`.

## 2026-05-17 (T-521 final docs pass CLOSED — F5 close-out tail 2/3 [T-519→**T-521**→T-522]; 5 markdown deliverables, 0 src LOC; F5 counter 63/66 → 64/66 [numerator +1, denom unchanged — no L-007 split]; all gates [plan-reviewer Gate-1 APPROVE / drift ON TRACK / brief SHIP / math-validator OUT-OF-SCOPE not-invoked — markdown-only 0 .py, T-519/T-523/T-533a precedent]; **L-026 residual-sweep complete — the last H-005 brief self-contradiction closed**; no NEW review-lesson [L-026 applied not added]; session interrupted by Anthropic API 500 *after* `bc54cc3` feat commit landed — close-out ritual resumed 2026-05-17, feat commit verified intact NOT re-run)

**F5 counter 63/66 → 64/66**: T-521 (final docs pass) — numerator 63→64, denominator unchanged 66 (no L-007 split). T-521 is the **2nd of the F5 close-out tail** (T-519 §20 audit ✓ → **T-521 final docs ✓** → T-522 E1..E6 sign-off / Live-ready MVP = the final close-out leaf). 5 markdown deliverables, 0 src LOC (§0.3-exempt; T-500/T-519/T-523 markdown-only precedent).

### T-521 — final docs pass (F5 close-out tail 2/3)

- **Deliverables**: NEW `docs/runbooks/F5_E1_backtest_smoke.md` (operator-runnable backtest smoke — 30-day window completes + `backtest_runs.summary` aggregates + `--compare` two-run aggregate+per-trade diff; mirrors the F4_E1 skeleton). NEW `docs/runbooks/F5_E2_shadow_smoke.md` (shadow restart-recovery — trade w/ shadow.enabled → kill execution-service mid-variant → restart → `shadow_variants` finalize/resume via §13.4 OHLC replay, H-023 not lost). README full MVP-complete rewrite (stale 49-LOC "Phase F0" → status F5-complete/Live-ready, multi-service architecture, features, quick-start + F5_E1/E2 cross-refs, ADR-0011 hardening + §20-audit/H-005-DEFERRED; retains the "CLAUDE_CODE_BRIEF.md = source of truth" pointer). `docs/CLAUDE_CODE_BRIEF.md` deltas: §21 glossary +12 F5 terms (entry-format mirrored); §11 +3 F5 @idempotent methods (`get_account_balance`/`get_mark_price`/`get_funding_fees_window` — signatures verbatim from shipped `protocols.py:138/157/177`); §B.1 sizing example explicit `method: tier` + commented `risk_per_sl` alternative (matches shipped `SizingSection` `types.py:353-358`).
- **2 SOUND in-scope additions beyond the literal deliverable list** (drift-checker ON TRACK + brief-reviewer SHIP adjudicated, NOT scope-creep): (a) §B.1 `block_opposite_position` + §B.3 plugin_registry `opposite_side_open` + §B.1 `tier_promotion`/`tier_demotion` ALL stale-vs-shipped → ANNOTATE-DEFERRED — the T-519 **L-026** active-control ("claim-correction must grep ALL source-of-truth sites not just the targeted one") applied thoroughly; same stale-example-config class the plan-reviewer pass-1 BLOCKER'd; **closes the last H-005 brief self-contradiction** the T-519 L-026 lesson flagged. (b) `get_closed_pnl_window` deliberate-boundary documented.
- **Gates**: plan-reviewer Gate-1 APPROVE (5 WG) → drift-checker ON TRACK (5/5 WG; both additions SOUND in-scope L-026-completion) → brief-reviewer SHIP (5/5 WG; L-026 residual-sweep complete — every `opposite_side_open`/`OppositeSideOpenRule`/`tier_promotion` occurrence in BRIEF is DEFERRED-annotated, 0 residual un-annotated site; pre-empted a brief-reviewer FIX-FIRST) → math-validator OUT OF SCOPE not invoked (markdown-only, 0 .py, no Gate-4 trigger path; T-519/T-523/T-533a markdown-meta-task precedent; committed disposition). meta-test 8 passed (§20 untouched). L-021 N/A (no migration). §0.3 = 0 src (all 5 deliverables markdown-exempt). No NEW review-lesson — L-026 was *applied* (its first post-introduction exercise), not added. **Commit** `bc54cc3`; **Plan** `docs/plans/T-521.md` (APPROVED + 5 WG).
- **Session continuity**: yesterday's session was force-terminated by an Anthropic API 500 error that fired *after* `git commit` for `bc54cc3` had already succeeded (pre-commit hooks Passed, commit object written). The 2026-05-17 resume verified the feat commit intact (NOT re-run — no double-commit) and that the close-out ritual had not started; this `chore(tasks)` + ff-merge + push + branch-delete is that resumed tail.
- **Next**: T-522 (F5 close-out runbook + E1..E6 sign-off — Live-ready MVP per ADR-0011; the **final** F5 close-out leaf; blocked-by T-507/508/509/512/516/518/519/521 + T-524..T-536 all green — T-521 ✓ now satisfied). After T-522, F5 phase closes. **Open backlog** (unchanged): T-F5+ §17.2 per-module coverage gate (T-527b2a); T-F5+ `tier_promotion`/`tier_demotion` (T-527 OQ-2=A); T-F5+ `opposite_side_open` scoring condition + H-005 test (T-519 H-005 deferral; operator-acknowledged carve-out).

## 2026-05-16 (T-519 §20 hazard test audit CLOSED — F5 E4 exit-criterion owner; **ALL ADR-0011 hardening clusters shipped**, T-519 is the first of the F5 close-out tail [T-519→T-521→T-522]; F5 counter 62/66 → 63/66 [numerator +1, denom unchanged — no L-007 split]; all 4 gates [plan-reviewer Gate-1 single-pass APPROVE / drift ON TRACK / brief SHIP after 2 FIX FIRST / math-validator OUT-OF-SCOPE not-invoked T-523/T-533a markdown-meta precedent]; stale task-def H-026→H-036 + `pytest -m hazard`→test-NAME-pin corrected in-body L-018; H-005 genuine TOTAL gap → operator DEFER+carve-out+backlog; brief §N10 self-consistency [consumer.py:30 + BRIEF:1565 same false claim corrected]; NEW L-026 [claim-correction must grep ALL source-of-truth sites])

**F5 counter 62/66 → 63/66**: T-519 (§20 hazard test audit, E4 owner) — numerator 62→63, denominator unchanged 66 (no L-007 split). **ALL ADR-0011 hardening clusters are now shipped** (position-sizing T-527+T-528, account-balance/equity T-530+T-531+T-532, risk-mgmt T-524/525/526, trade-lifecycle FSM T-533, SL/TP verification T-534/535/536, T-529 qty); T-519 is the **first of the F5 close-out tail** → remaining: T-521 (final docs, blocked-by T-519 ✓ now unblocked) → T-522 (E1..E6 sign-off, Live-ready MVP).

### T-519 — §20 hazard test audit

- Stale task-def corrected in-body (L-018/T-532a-T-533a doubly-stale class): scope "H-001..H-026" → actual **H-001..H-036** (36, no gaps); mechanism `pytest -m hazard`/`@pytest.mark.hazard` → DROPPED (code-verified non-existent — pyproject `--strict-markers` + no `markers=[]` would reject it) → **test-NAME-pin reverse-map** (OQ-1=A; the shipped §20 `**Test.**` backtick-citation convention). NEW `tests/test_hazard_catalog_coverage.py` = permanent CI E4 guard: parses live §20 `**Test.**` citations (3 shapes: bare/`path::test`/file-level + `+`-join; code-ref backticks like `bus.close`/`Decimal(...)//`/`call_order==[...]` ignored), subprocess `pytest --collect-only -q` resolves each (collect-only never RUNS — no recursion), contiguity on the SORTED INTEGER SET not file-order (H-032/033 physically after H-036 by deliberate audit-cluster grouping; max derived from parsed entries — anti-L-025). 6 self-tests pin the parser.
- Audit findings: **6 stale v1-brief intended names** (control IS implemented+tested; F0..F4 named the test differently, docstrings independently confirm the H-NNN binding) corrected to the real collected tests (H-006→`test_over_limit_rejected`, H-009→`test_execution_dispatcher_dedup_ring_drops_duplicate_exchange_exec_id`, H-010→`test_invalid_json_returns_400`, H-015→2 qty-string tests, H-018→`test_update_trade_close_uses_where_id_pk_only_per_H_018`, H-024→`test_post_tp_close_fill_labeled_per_db_sl_type_not_exchange_orderlink` [v1 semantic superseded by ADR-0005]; stale names plain-prose not backticks — parser-safe). **H-005 = genuine TOTAL gap** (opposite_side_open scoring rule code-verified non-existent — zero in packages/scoring/conditions/, consumer.py:30 defers to a not-yet-existing extension; §20 Policy "Implemented as a scoring rule" was FALSE) → **operator decision DEFER + carve-out + backlog**: §20 H-005 Policy+Test rewritten DEFERRED, NEW T-F5+ backlog ticket, meta-test TIGHT `_KNOWN_DEFERRED={5}` whitelist (new deferral ∉ set OR H-005 un-DEFERRED still fails CI — §0.8 not absorbed; E4 = 35/36 + H-005 explicitly DEFERRED operator-acknowledged paper-feature-complete MVP residual-risk).
- Brief §N10 self-consistency (the 2 brief-reviewer FIX-FIRST catches → **NEW L-026**): the SAME false "implemented as a scoring rule" claim was duplicated at `services/strategy_engine/app/consumer.py:30` (docstring-only, 0 behavior change — 32 consumer tests pass) + `docs/CLAUDE_CODE_BRIEF.md:1565` (§9.5 "Known hazards addressed") — both corrected to DEFERRED. Deliberate scope boundary (§0.8): §B.1 alpha.yaml example config + immutable historical plan-docs (T-200/T-500/T-310b) NOT edited (forward-looking example owned by the T-F5+ ticket / point-in-time records). NEW `docs/audit/hazard-test-coverage.md` regenerable E4 report.
- 4 gates: plan-reviewer Gate-1 single-pass APPROVE (0 BLOCKER; 6 WG; L-018 every stale-claim code-cited) → drift ON TRACK (`_KNOWN_DEFERRED` SOUND mechanically-required operator-decision-driven NOT scope-creep; 6/6 WG; close-bookkeeping correctly close-time) → brief SHIP (after 2 FIX FIRST — consumer.py:30 then BRIEF:1565 residual stale-claim BLOCKERs both resolved; residual-claim sweep empty → L-026) → math-validator OUT OF SCOPE not-invoked (no Gate-4 trigger-list path; markdown + doc-only consumer.py; zero Decimal; T-523/T-533a precedent; committed disposition). meta-test 8 passed; ruff/format/mypy/bandit Passed (fixed 2 PT018 + 1 PT019 `_collected`→`collected_tests`); L-021 N/A. **Commit** `3d4fc38`; **Plan** `docs/plans/T-519.md` (APPROVED + 6 WG); `docs/review-lessons.md` +L-026.
- **Next**: T-521 (final docs — runbooks F5_E1/E2 smoke, glossary, README, BRIEF deltas; blocked-by T-519 ✓) → T-522 (F5 close-out runbook + E1..E6 sign-off, Live-ready MVP; blocked-by T-507/508/509/512/516/518/519/521 + T-524..T-536 all green). Operator picks order (T-521 then T-522 is the DAG order). **Open backlog**: T-F5+ §17.2 per-module coverage gate (T-527b2a); T-F5+ tier_promotion/tier_demotion (T-527 OQ-2=A); **T-F5+ opposite_side_open scoring condition + H-005 test (NEW — the T-519 H-005 deferral; operator-acknowledged carve-out, tracked in §20 H-005 + the audit report)**.

## 2026-05-16 (T-532b funding-fee poll tick + cumulative-funding emit CLOSED — L-007 split consumer leaf 2/2 of T-532 [FINAL leaf]; **T-532 funding-fee tracking COMPLETE** [T-532a read-path+storage + T-532b poll-tick+emit]; F5 counter 61/66 → 62/66 [numerator +1, denom unchanged — L-007 split +1 applied at T-532a close]; all 4 gates pass [plan-reviewer Gate-1 pass-2 APPROVE / drift ON TRACK / brief SHIP / math-validator VERIFIED Gate-4 COMMITTED IN-scope MODERATE — signed SUM -0.10 exact, no N-bot double-count, anti-L-025 runtime-identity window test]; pass-1 REVISE 0 BLOCKER/4 CONCERN [Bod A connection-model / Bod B L-013-citation / Bod C empty-window contradiction / Bod G T-531-LOC — all consolidation/precision, no operator input, resolved]; connection-model divergence from T-531 per-insert-acquire SOUND in-body; L-021 N/A [no migration]; L-022 point 35 ≈ 1.1× on-target tick)

**F5 counter 61/66 → 62/66**: T-532b (poll tick + cumulative-funding emit, blocked-by the shipped T-532a) — consumer leaf 2/2 of the L-007 split T-532→{a,b} (mirror T-528a→T-528b / T-527b1→T-527b2). Numerator 61→62; denominator unchanged 66 (the T-532 1-slot→2-leaf +1 was applied at T-532a close). **T-532 lineage FULLY CLOSED**: T-532 → {a [read-path+storage, done], b [poll-tick+cumulative-emit, done]}. **Funding-fee tracking is live end-to-end** (ADR-0011 account-balance/equity sub-cluster, sibling of shipped T-530/T-531): a separate `funding_fee_poll` APScheduler tick per sub-account stores `funding_fees` rows + emits one `funding_settlement_window` `trading_events` cumulative-funding term (H-017-clean — never folded into trades.realized_pnl; the T-220 cumulative-delta audit no longer drifts from exchange truth per ADR-0011:39).

### T-532b — funding-fee poll tick + cumulative-funding emit

- NEW `services/execution/app/funding_fee_poll.py:run_funding_fee_poll_tick` — SEPARATE APScheduler tick (OQ-4=A) verbatim-mirror shipped T-531 `run_equity_snapshot_tick` MINUS metrics; windowed pull. Per sub-account: `get_funding_fees_window` → (a) storage fan-out one funding_fees row per settlement PER BOT (T-531 equity per-bot fan-out precedent — monitoring-not-truth, truth is the T-220 audit per ADR-0006 H-017) + (b) **EXACTLY 1** `funding_settlement_window` `insert_trading_event` per sub-account (OQ-B1=A emit-only / OQ-B2=A self-contained — audit.py UNTOUCHED). `cumulative_funding = sum((f.funding for f in fees), start=Decimal("0"))` ONCE from the in-memory list (NOT SELECT-SUM over the N-fanned rows → structurally no N-bot double-count). `bot_id=None` sub-account attribution; H-017-clean (OQ-3=A — never folded into trades.realized_pnl). Empty window → skip BOTH (no empty-emit noise). +2 §N9 Settings (interval+window mirror execution_audit_*), main.py `_funding_fee_poll_job` mirror `_equity_snapshot_job`. NO migration / NO adapter/audit.py change (OQ-B2=A self-contained, L-007 boundary clean), 0 §20 regression, no new H-NNN.
- **Connection/tx model (mechanically-required divergence from T-531 per-insert-acquire mirror — drift+brief+math SOUND in-body, T-527b2b _resolve_sub_account-relocation class, NOT silent-refactor)**: ONE `pool.acquire()` per sub-account wrapping the entire NxM fan-out + 1 emit (T-531 = single write-kind/bot; T-532b = per-sub-account dual-write group → one acquire/sub-account avoids NxM+1 churn), NO `conn.transaction()` (partial failure = ACCEPTED monitoring degradation, self-heals via the overlapping windowed re-poll; each event a self-contained snapshot NOT additive — T-220 cumulative-snapshot semantic). §N3 both writers @non_idempotent no-retry.
- **L-013 pre-adjudicated N/A by construction** (insert_trading_event internal json.dumps, execution-service NO jsonb codec → single-encode; payload manually JSON-native, zero datetime/UUID/Decimal objects; reused UNMODIFIED — brief-reviewer confirmed no false-positive). **anti-L-025**: the window-boundary test asserts via the `now_fn − timedelta` runtime identity (NOT a hardcoded ms/iso literal — the T-532a L-025 copy-trap structurally avoided).
- 4 gates: plan-reviewer Gate-1 pass-2 APPROVE (pass-1 REVISE 0 BLOCKER/4 CONCERN — Bod A/B/C/G all consolidation/precision, no operator input, resolved + re-verified) → drift ON TRACK (5/5 WG, scope/L-007 boundary clean, ~210 src ≪400) → brief SHIP (5/5 WG, 0 BLOCKER/CONCERN, L-013 no false-positive) → math-validator VERIFIED (Gate-4 COMMITTED IN-scope MODERATE; signed SUM -0.10 exact + no N-bot double-count + window 12:00Z−10800s=09:00Z + no float/no accidental arithmetic; no findings). 38 targeted pass (+8; shipped siblings byte-preserved); mypy strict staged incl. tests clean (L-024 — scripts/ grep clean, backtest.py NOT impacted); ruff/format/mypy/bandit pre-commit Passed (6 RUF002/003 ×→x cosmetic); L-021 N/A. **Commit** `2bfc6a3`; **Plan** `docs/plans/T-532b.md` (APPROVED pass-2 + 5 WG); recursive-split-record `docs/plans/T-532.md`.
- **Next**: position-sizing cluster (T-527+T-528) + account-balance/equity sub-cluster (T-530+T-531+T-532) now ALL COMPLETE. The ADR-0011 hardening clusters are all shipped. Remaining F5 = the close-out tail: **T-519** (§20 hazard test audit) → **T-521** (final docs) → **T-522** (E1..E6 sign-off, Live-ready MVP). Operator picks order. Open backlog: T-F5+ §17.2 per-module coverage gate; T-F5+ tier_promotion/tier_demotion (T-527 OQ-2=A).

## 2026-05-16 (T-532a funding-settlement read path + storage CLOSED — L-007 split foundation leaf 1/2 of T-532 [T-532b poll-tick + cumulative-delta audit integration blocked-by, now unblocked]; F5 counter 60/65 → 61/66 [L-007 split denom +1, numerator +1]; all 4 gates pass [plan-reviewer Gate-1 single-pass APPROVE / drift ON TRACK / brief SHIP / math-validator VERIFIED Gate-4 COMMITTED IN-scope — signed-Decimal funding decode + Numeric(20,4)]; **L-021 GREEN** — test_0021_migration.py run locally vs real dev TimescaleDB [5 passed]; doubly-stale-doc 0017→0021/down_rev 0020 corrected; 2 plan-vs-reality corrections SOUND in-body [stdlib @dataclass not Pydantic; settled_at # UTC comment-convention not validator]; NEW L-025 [plan-doc Hand-verification numbers are not code-verified evidence — math-validator must independently recompute]; L-022 point 34 ≈ 1.3× migration/adapter test-heavy)

**F5 counter 60/65 → 61/66**: T-532 "funding-fee tracking" (ADR-0011 account-balance/equity sub-cluster — sibling of the shipped T-530/T-531) → **L-007 split** (operator OQ-2=A T-532 plan-stage; cap-proximate — T-531 [no audit integration] was ~297 src single, T-532 = T-531-shape + cumulative-delta audit integration + paginated pull + value type) → T-532a (funding read path + storage, done) + T-532b (poll tick + cumulative-delta audit integration + main wiring + §N9 poll Settings, blocked-by → now unblocked). Denominator 65→66 (+1, T-532 1-slot → 2 leaves — mirror T-528→{a,b}); T-532a done → numerator 60→61. **Structure mirrors T-528a→T-528b / T-527b1→T-527b2** (foundation read+storage → poll-tick+audit consumer). ADR-0011/0006/0007 govern → no new ADR.

### T-532a — funding-settlement read path + storage

- NEW `FundingFee` `@dataclass(frozen=True,slots=True)` (symbol/settled_at[UTC]/funding[signed Decimal]; types.py `__all__` only) + `ExchangeClient.get_funding_fees_window(sub_account,since)->list[FundingFee]` `@idempotent` (protocols + Bybit + paper). Bybit adapter verbatim-mirrors `get_closed_pnl_window` (`/v5/asset/transaction-log` `type=SETTLEMENT`, sub_account-pre-limiter, cursor cap `_MAX_FUNDING_PAGES`=10, `RateLimitError→_on_rate_limit_hit("positions")→raise`); funding `Decimal(str(...))` no-float signed, transactionTime→`datetime.fromtimestamp(int(...)/1000,tz=UTC)` (ws.py:123/149 convention), rate-bucket `"positions"` (`/v5/asset/*`≡`get_account_balance` `/v5/account/*` no-new-EndpointGroup, code-verified). Returns `list[FundingFee]` (deliberate divergence from `get_closed_pnl_window`'s `->Decimal` — storage+audit need per-settlement records, OQ-1/3=A); paper→`[]` (no perpetual-funding model, documented limitation). NEW migration `0021_funding_fees` hypertable verbatim-mirror 0019 + `test_0021_migration.py` mirror test_0019 + NEW `packages/db/queries/funding.py:insert_funding_fee` `@non_idempotent` mirror `insert_equity_snapshot`. NO consumer (T-532b first); zero behavior change; 0 §20 regression, no new H-NNN. §N9: NO Settings (poll-interval is T-532b — OQ-4=A). L-015: NEW isolated funding_fees → ZERO existing-migration-test churn (OQ-1=A clean-separation benefit).
- **Doubly-stale-doc correction (L-018, T-533a class)**: TASKS:237 "migration 0017" + ADR-0011 "0016+0017" BOTH stale (head was 0020) → migration **0021, down_revision='0020'**.
- **2 plan-vs-reality corrections (T-530/T-533a tooling-correction class, drift+brief+math SOUND in-body)**: (1) plan said "Pydantic mirror AccountBalance" — actual types.py precedent is stdlib `@dataclass(frozen,slots)`; (2) `settled_at: datetime # UTC` comment-convention (mirror `OrderPlaceResult.placed_at`) — no field_validator (UTC by adapter construction).
- **NEW L-025** (math-validator VERIFIED-with-concern): the plan-doc `## Hand verification` carried a wrong epoch literal (`1779897600000`, decodes 2026-05-27 not -05-16; correct `1778889600000`) — existed ONLY in the plan-doc, NEVER in any shipped .py (adapter test asserts via `int(since.timestamp()*1000)` runtime, not the literal) → capital path safe, code math-correct; corrected doc-only + derivation trace, no code/test change, no re-test. Lesson: plan-doc Hand-verification numbers are not code-verified evidence; math-validator must independently recompute (L-018 sibling; a future mirror-task copying the stale literal into a test = real regression).
- **L-021 GREEN**: `test_0021_migration.py` run locally vs real dev TimescaleDB (5 passed — funding_fees 5-col shape + L-005 numeric(20,4) on funding + composite PK (settled_at,id) + no-FK + hypertable 7d chunk + L-012 explicit downgrade '0020'; migration chains cleanly from 0020). Creds from container env (`docker exec printenv`), no secret echoed (output redacted). The T-537a1/T-533a pre-push L-021 precedent honoured (CI not the first execution surface).
- 4 gates: plan-reviewer Gate-1 single-pass APPROVE (0/0; every load-bearing citation source-verified; migration-chain confirmed; 5 WG) → drift ON TRACK (5/5 WG, scope-clean, 318 src ≪400) → brief SHIP (0 findings; verbatim-mirror verified) → math-validator VERIFIED (Gate-4 COMMITTED IN-scope; the epoch CONCERN doc-only, non-blocking). 140 targeted pass (+13; shipped siblings byte-preserved); mypy strict staged incl. tests clean (L-024); ruff/format/mypy/bandit pre-commit Passed (RUF002 U+2212→ASCII). **Commit** `50965d7`; **Plan** `docs/plans/T-532a.md` (APPROVED + 5 WG + corrected epoch); recursive-split-record `docs/plans/T-532.md`; `docs/review-lessons.md` +L-025.
- **Next**: T-532b (poll tick mirror T-531 equity_snapshot + cumulative-delta audit integration [separate cumulative funding term, OQ-3=A H-017-clean] + main add_job + §N9/L-001 `execution_funding_fee_poll_interval_seconds` Settings; own plan-reviewer Gate-1, OQ-1/3/4=A baked). Then F5 close-out tail T-519 (§20 hazard test audit) → T-521 (final docs) → T-522 (E1..E6 sign-off, Live-ready MVP). Operator picks order. Open backlog: T-F5+ §17.2 per-module coverage gate; T-F5+ tier_promotion/tier_demotion (T-527 OQ-2=A).

## 2026-05-16 (T-528b risk-per-SL cross-service wiring CLOSED — L-007 split consumer leaf 2/2 of T-528 [FINAL leaf]; **T-528 risk-per-SL sizing system COMPLETE** [T-528a config+compute + T-528b wiring]; F5 counter 59/65 → 60/65 [numerator +1, denom unchanged — L-007 split +1 was applied at T-528a close]; all 4 gates pass [plan-reviewer Gate-1 single-pass APPROVE / drift ON TRACK / brief SHIP / math-validator VERIFIED Gate-4 COMMITTED IN-scope MODERATE — dispatch correctness, sl_pct↔risk_pct NOT transposed]; tier else-branch relocation byte-identical vs git show HEAD [SOUND dispatch structuring, mirror T-527b2b _resolve_sub_account]; OQ-B1=A reuse sub_lowest_tier verbatim for risk-per-SL no-capital; L-022 point 33 ≈ 1.0× on-target wiring leaf; L-024 — no hook surprise, scripts/ grep clean)

**F5 counter 59/65 → 60/65**: T-528b (cross-service risk-per-SL wiring, blocked-by the shipped T-528a) — consumer leaf 2/2 of the L-007 split T-528→{a,b} (mirror T-527b2a→T-527b2b). Numerator 59→60; denominator unchanged 65 (the T-528 1-slot→2-leaf +1 was applied at T-528a close — T-528b was already the 65th slot). **T-528 lineage FULLY CLOSED**: T-528 → {a [config+pure-compute, done], b [cross-service wiring, done]}. **The risk-per-SL `sizing.method` alternative is live end-to-end** (ADR-0013): a bot with `sizing.method: risk_per_sl` + `risk_pct` sizes every order so hitting the SL loses exactly `risk_pct` of total_equity (`notional = total_equity*risk_pct/sl_pct`); a `method: tier` (or absent) bot is byte-unchanged (the shipped T-527 tier ladder).

### T-528b — risk-per-SL cross-service wiring

- `SizingSpecForWire` (packages/bus/payloads.py): NEW additive `method: Literal["tier","risk_per_sl"]="tier"` + `risk_pct: Decimal | None = None` (plain defaults — NO `Field`/`gt=0`; wire = thin transport, `SizingSection` producer-side is THE validation authority incl. method↔risk_pct coupling; `tiers`/`score_multipliers` stay REQUIRED — NOT default_factory; `OrderRequest.schema_version` stays "1.0" additive — old payloads validate). Producer `_publish_order_request` gains `method=`/`risk_pct=` on the shipped ctor. Placement seam method-dispatch: `risk_per_sl` → defensive `if request.sizing.risk_pct is None:`→verbatim compute_error skip (L-019 — thin-transport wire NOT re-validated; also mypy Decimal|None→Decimal narrowing) then `compute_qty_from_risk(...)`; `tier`/default (else) → shipped `compute_qty_from_sizing` relocated into the else-branch BYTE-IDENTICAL (mechanically-required dispatch structuring, drift+brief+math SOUND vs `git show HEAD` — analogous to the T-527b2b `_resolve_sub_account` relocation, NOT silent refactor / NOT scope creep). `if computed_qty is None:`→`sub_lowest_tier` skip reused VERBATIM for BOTH methods (OQ-B1=A — for risk_per_sl None = total_equity≤0 no-capital; shared low-cardinality label's observability trade-off documented in-code). `working_qty` stays the SOLE pre-place qty round (single-rounding). `request.sizing is None` OR `method=="tier"` → shipped T-527b2b tier path byte-unchanged. 0 §20 regression, no new H-NNN.
- 4 gates: plan-reviewer Gate-1 single-pass APPROVE (0 BLOCKER/CONCERN; claims source-verified; 5 WG verbatim) → drift ON TRACK (5/5 WG, scope clean, tier-relocation byte-identical, ~115 src ≪400) → brief SHIP (0 findings; tier else-branch byte-identical vs git show HEAD) → math-validator VERIFIED (Gate-4 COMMITTED IN-scope MODERATE — dispatch keyword-args byte-match shipped compute.py:183-191, sl_pct↔risk_pct NOT transposed [pinned 0.005 vs 0.01], defensive narrowing sound, single-rounding preserved, cap not double-applied, no score_multiplier leak [OQ-3=A type-level], fixtures hand-computable; no findings). 122 targeted pass (+8; shipped T-527b2b siblings byte-preserved); mypy strict over staged incl. tests clean (L-024 — scripts/ grep clean, make_per_bot_handler signature UNCHANGED → backtest.py NOT impacted, the T-527b2b lesson applied); ruff/format/mypy/bandit pre-commit Passed; L-021 N/A. **Commit** `610c8b4`; **Plan** `docs/plans/T-528b.md` (APPROVED + 5 WG); recursive-split-record `docs/plans/T-528.md`.
- **Next**: position-sizing cluster (T-527 tier + T-528 risk-per-SL) now COMPLETE. **T-532** (funding-fee tracking + migration 0021) remains, then the F5 close-out tail T-519 (§20 hazard test audit) → T-521 (final docs) → T-522 (E1..E6 sign-off, Live-ready MVP). Operator picks order. Open backlog: T-F5+ §17.2 per-module coverage gate; T-F5+ tier_promotion/tier_demotion (T-527 OQ-2=A).

## 2026-05-16 (T-528a `sizing.method` discriminator + risk-per-SL pure compute CLOSED — L-007 split foundation leaf 1/2 of T-528 [T-528b cross-service wiring blocked-by, now unblocked]; F5 counter 58/64 → 59/65 [L-007 split denom +1, numerator +1]; all 4 gates pass [plan-reviewer Gate-1 single-pass APPROVE / drift ON TRACK / brief SHIP / math-validator VERIFIED Gate-4 COMMITTED IN-scope HEAVY — risk-per-SL Decimal arithmetic + loss-at-SL identity]; §N10 shipped-T-527-tier-path byte-preservation the sole regression surface, handled + regression-pinned; L-022 calibration point 32 ≈ 1.45× pure-math-docstring volume, ≪400 no waiver; L-024 — no hook surprise)

**F5 counter 58/64 → 59/65**: T-528 "risk-per-SL sizing" (ADR-0011 — the `sizing.method`-discriminated alternative to the T-527 tier ladder) → **L-007 split** (operator OQ-1=A T-528 plan-stage; full inventory exceeds §0.3 400 once §N4 Hand-verification docstrings counted, structurally identical to the split T-527b2) → T-528a (config discriminator + pure compute, done) + T-528b (cross-service wiring, blocked-by → now unblocked). Denominator 64→65 (+1, T-528 1-slot → 2 leaves — mirror T-527b2→{b2a,b2b}/T-533b→{b1,b2}); T-528a done → numerator 58→59. **Structure mirrors T-527b2a→T-527b2b** (pure config+math foundation leaf → cross-service consumer leaf). ADR-0013 §Consequences anticipates T-528 verbatim → no new ADR.

### T-528a — `sizing.method` discriminator + risk-per-SL pure compute

- NEW additive `SizingSection.method: Literal["tier","risk_per_sl"]="tier"` + `risk_pct: Decimal | None = Field(default=None, gt=0)` on the EXISTING model (OQ-2=A — NOT a discriminated-union refactor of the just-shipped T-527 surface). `tiers`/`score_multipliers` gain `default_factory` SOLELY so a `risk_per_sl` block may omit them; `max_notional_per_symbol` stays REQUIRED (cap applies to BOTH methods — baked safety rail). NEW `packages/sizing/compute.py:compute_qty_from_risk` (keyword-only, Decimal-only): `notional = total_equity*risk_pct/sl_pct` → `cap_notional` (shipped, reused) → ÷mark_price; guard ordering byte-mirrors `compute_qty_from_sizing` (skip-sentinel `total_equity<=0`→None BEFORE the 3 fail-loud ValueErrors); single-rounding-point (NO round — T-529 `quantize_qty` is the sole downstream round in T-528b); NO `apply_score_multiplier` (OQ-3=A). NO consumer (T-528b first); zero behavior change (`method` defaults "tier"); 0 §20 regression, no new H-NNN.
- **§N10 byte-preservation (the sole regression-risk surface)**: `_structural_guards` rewritten method-conditional so the shipped T-527a tier accept/reject surface is byte-identical — the `method=="tier"` branch RESTORES the loud rejections field-requiredness used to give (risk_pct-not-None / empty-tiers / empty-score_multipliers — a silently `[]`/`{}` tier config = capital-safety regression); guard order risk_pct→tiers→score_multipliers→ascending keeps the shipped `test_sizing_section_rejects_empty_tiers` raising the original "non-empty" message. Regression-pin test + L-015 sibling enumeration (all shipped T-527a SizingSection/SizingTier validator + yaml sizing tests pass byte-identically — verified by drift + brief + math).
- **§N1 risk_pct coercion**: `_parse_sizing` REQUIRED `_to_decimal` block — YAML `risk_pct: 0.01` is a Python float; the non-strict SizingSection would pydantic-coerce via `Decimal(0.01_float)` = binary-float artefact corrupting the capital path; `method` is an INTENTIONAL non-coerced str passthrough; pinned by `str(risk_pct)=="0.01"`.
- 4 gates: plan-reviewer Gate-1 single-pass APPROVE (0 BLOCKER/CONCERN; 8 claims source-verified; 5 WG verbatim) → drift ON TRACK (5/5 WG, scope clean, 174 src ≪400) → brief SHIP (0/0; §N10 + §N1 verified) → math-validator VERIFIED (Gate-4 COMMITTED IN-scope HEAVY — risk-per-SL algebra [loss-at-SL identity], guard-ordering byte-mirror, no silent Decimal→float, single-rounding, hand-compute 6/6, §N10 + div-by-zero clean; no findings). 164 targeted pass (+18 new; shipped siblings byte-preserved); mypy strict over staged incl. tests clean; ruff/format/mypy/bandit pre-commit Passed; L-021 N/A. **Commit** `5856293`; **Plan** `docs/plans/T-528a.md` (APPROVED + 5 WG); recursive-split-record `docs/plans/T-528.md`.
- **Next**: T-528b (cross-service wiring — `SizingSpecForWire` `method`/`risk_pct` additive + strategy-engine producer mapping + `placement.py` seam method-dispatch reusing the verbatim T-527b2b skip-before-place path; own plan-reviewer Gate-1, OQ-2/3=A baked). Then T-532 (funding-fee tracking + migration 0021), then F5 close-out tail T-519 (§20 hazard test audit) → T-521 (final docs) → T-522 (E1..E6 sign-off, Live-ready MVP). Operator picks order. Open backlog: T-F5+ §17.2 per-module coverage gate; T-F5+ tier_promotion/tier_demotion (T-527 OQ-2=A).

## 2026-05-16 (T-527b2b §B.1 sizing cross-service wiring CLOSED — recursive L-007 split consumer leaf 2/2 of T-527b2 [FINAL leaf]; **T-527 §B.1 tier-ladder position-sizing system COMPLETE** [T-527a+T-527b1+T-527b2a+T-527b2b]; F5 counter 57/64 → 58/64 [numerator +1, denom unchanged — the L-007 split +1 was applied at T-527b2a close]; all 4 gates pass [plan-reviewer Gate-1 pass-2 APPROVE / drift ON TRACK / brief SHIP / math-validator VERIFIED Gate-4 COMMITTED IN-scope MODERATE]; L-022 calibration point 31 ≈ 1.0× on-target [wiring leaf, not docstring-heavy — contrast T-527b2a 1.8×]; **L-024 hook DID fire** — pre-commit project-wide mypy caught a 46th L-015 sibling [scripts/backtest.py] the local staged-set mypy missed, fixed pre-commit)

**F5 counter 57/64 → 58/64**: T-527b2b (the cross-service §B.1 sizing wiring, blocked-by the shipped T-527b2a) — consumer leaf 2/2 of the T-527b2 recursive L-007 split (mirror T-527b→{b1,b2}/T-533b→{b1,b2}). Numerator 57→58; denominator unchanged 64 (the T-527b2 1-slot→2-leaf +1 was applied at T-527b2a close — T-527b2b was already the 64th slot). **T-527 lineage now FULLY CLOSED**: T-527 → {a [config, done], b → {b1 [get_mark_price, done], b2 → {b2a [packages/sizing compute, done], b2b [cross-service wiring, done]}}}. **The §B.1 balance→tier→score-multiplier→notional-cap→qty position-sizing system is fully live in the execution-service placement seam** (ADR-0013 Accepted): a bot with a `sizing:` block now sizes every order off live account balance + mark price; a bot without one is byte-unchanged (static `execution.qty`).

### T-527b2b — §B.1 sizing cross-service wiring

- NEW `SizingTierWire`+`SizingSpecForWire` (`packages.bus.payloads`, OQ-6b — bus-owned to avoid the `OrderRequest`(bus)→`SizingSection`(scoring) import cycle; mirror T-511b2 `VariantSpec`) + additive `OrderRequest.sizing: SizingSpecForWire | None = None` (mirror T-527a `score`; schema_version stays "1.0") + strategy-engine `_publish_order_request` maps `bot_config.sizing`→wire (mirror `shadow_variants`) + execution `placement.py:_handle` seam before the T-529 `quantize_qty`: balance+mark-price fetch → rehydrate `SizingTier` (strict=True) → `compute_qty_from_sizing` (b2a-VERIFIED) → `working_qty` into the **SOLE existing `quantize_qty` change-point** (single-rounding). `request.sizing is None` → `working_qty=request.qty` byte-unchanged.
- 3 **skip-before-`place_market_order`** seams (log + `signals_skipped_sizing{bot_id,reason}` Counter, T-531 `build_execution_metrics` scaffold; skip-before-place = no capital at risk): B1 fetch raise `(AuthError|NetworkTimeout|RateLimitError)`→fetch_failed; `except ValueError` (b2a `mark_price<=0` guard)→compute_error; `None` sub-lowest-tier (OQ-4=A)→sub_lowest_tier. `sub_account`+`metrics` kwargs threaded into `make_per_bot_handler` (now_fn-binding precedent); main.py `sub_account=_resolve_sub_account(adapter)`+`metrics=exec_metrics`.
- **Plan-vs-reality (T-533a/T-527b2a tooling-correction class, drift+brief SOUND)**: plan + plan-reviewer pass-2 asserted "reuse `_resolve_sub_account` at the :174 call-site" but the `def` was at main.py:212 — AFTER the :174 loop (def-after-use → NameError). Relocated the pure helper (body byte-identical) above the loop; documented in-body; not a silent refactor / not scope creep.
- **L-024 hook fired (watch-out for next session)**: the new `make_per_bot_handler` required kwargs broke 46 L-015 sibling call-sites — 45 tests AND **1 non-test caller `scripts/backtest.py`**. The local staged-set mypy I run during implementation did NOT see backtest.py (not in the staged set); the **pre-commit project-wide `uv run mypy` hook (443 files) caught it on the first `git commit` attempt** and blocked the commit. Fixed (`sub_account=str(bot_id)` paper Decision-#8 synonym + throwaway `build_execution_metrics(CollectorRegistry())` mirror `strategy_metrics`), re-ran brief-reviewer (Gate-3 mandatory after the staged-set change → SHIP), re-verified project-wide mypy clean, committed. **Active control for the next shared-signature change (T-528 also touches execution/sizing): when adding a required param/field to a shared function/type, grep ALL callers project-wide incl. non-test `scripts/`+`tools/`, not just staged files + sibling tests — local staged-set mypy under-scopes; only the project-wide hook (L-024) catches non-staged script callers.** No new L-NNN (no reviewer negative verdict; already covered by L-015+L-024; "don't add for every catch") — recorded here as the operative reinforcement.
- 4 gates: plan-reviewer Gate-1 pass-2 APPROVE (pass-1 REVISE 3 BLOCKER+2 CONCERN, all wording/consolidation) → drift ON TRACK (5/5 WG, 164 src on-target ≪ plan ~130-165) → brief SHIP (re-run after the backtest.py delta) → math-validator VERIFIED (Gate-4 COMMITTED IN-scope MODERATE; backtest.py delta OUT of trigger [scripts/], math-scope files byte-unchanged → no re-run). 2523 unit pass (+12) + 0 regressions; project-wide mypy clean; ruff/ruff-format/mypy/bandit pre-commit Passed; L-021 N/A. **Commit** `96fae4f`; **Plan** `docs/plans/T-527b2b.md` (APPROVED pass-2 + 5 WG); ADR-0013 (Accepted).
- **Next**: T-527/T-528 were ADR-0011 alternatives — **T-527 (tier-ladder) is now COMPLETE**; T-528 (risk-per-SL sizing, the `sizing.method` discriminator alternative) + T-532 (funding-fee tracking + migration 0021) remain, then the F5 close-out tail T-519 (§20 hazard test audit) → T-521 (final docs) → T-522 (E1..E6 sign-off, Live-ready MVP). Operator picks order. Open backlog: T-F5+ §17.2 per-module coverage gate (T-527b2a); T-F5+ tier_promotion/tier_demotion (T-527 OQ-2=A).

## 2026-05-16 (T-527b2a packages/sizing pure §B.1 tier-ladder compute CLOSED — recursive L-007 split foundation leaf 1/2 of T-527b2 [T-527b2b the cross-service wiring blocked-by, now unblocked]; F5 counter 56/63 → 57/64 [recursive L-007 split denom +1, numerator +1]; plan-reviewer Gate-1 pass-2 APPROVE [pass-1 REVISE 1 BLOCKER — §N5-gate on a non-existent packages/pnl precedent; fixed → NEW T-F5+ §17.2-coverage-gate backlog ticket]; math-validator VERIFIED [Gate-4 COMMITTED IN scope, HEAVY exhaustive — T-536/T-525b class]; L-022 calibration point 30 ≈ 1.8× Hand-verification-docstring density, ≪400 no waiver; new uv-workspace package scaffold; L-024 — no hook surprise)

**F5 counter 56/63 → 57/64**: T-527b2 (the §B.1 sizing compute, blocked-by the shipped T-527b1) → **recursive L-007 split** (operator OQ-1=A T-527b2 plan-stage; single-task code-inventory ~432 src EXCEEDS §0.3 400 cap) → T-527b2a (pure compute, done) + T-527b2b (the cross-service wiring, blocked-by → now unblocked). Denominator 63→64 (+1, T-527b2 1-slot → 2 leaves — mirror T-527b→{b1,b2}/T-533b→{b1,b2}); T-527b2a done → numerator 56→57. **Structure mirrors T-527b1→T-527b2 / T-533b1→T-533b2** (pure foundation leaf → cross-service consumer leaf). T-527 lineage now: T-527 → {a [config, done], b → {b1 [get_mark_price, done], b2 → {b2a [packages/sizing, done], b2b [wiring, pending]}}}.

### T-527b2a — packages/sizing pure §B.1 tier-ladder compute

- NEW `packages/sizing/{__init__.py,compute.py}` — 4 Decimal-only pure functions, the §B.1 sizing **math heart** (T-527b2b wires it into the execution-service placement seam per ADR-0013): `select_tier` (highest `balance_min<=total_equity`, `>=`-inclusive; sub-lowest→`None` — the OQ-4=A skip sentinel b2b acts on) / `apply_score_multiplier` (OQ-3=A: `str(floor(score))` clamped to the present digit-key range; `None`/sparse-after-clamp/empty-multipliers→×1.0) / `cap_notional` (`min(notional, max_notional_per_symbol[symbol|"default"])`, post-multiplier) / `compute_qty_from_sizing` (orchestrator: `None`-propagate BEFORE the `mark_price<=0`→`ValueError` guard; `notional/mark_price` Decimal, **NO rounding — single-rounding-point**: the shipped T-529 `quantize_qty` does the sole qty_step round-down downstream in T-527b2b; rounding here too would double-round). Zero I/O, **NO consumer** (T-527b2b first), zero behavior change, 0 §20 regression, no new H-NNN (pure; `mark_price<=0`→`ValueError` defensive fail-loud). §N1 Decimal-throughout — `score:float` feeds ONLY `floor()`→int key-index, never the money value path. `SizingTier` TYPE_CHECKING annotation-only (`packages.sizing→packages.scoring` acyclic — scoring does NOT import sizing; value-type-only, distinct from the b2b `OrderRequest`(bus)→scoring cycle OQ-6b solves).
- **New uv-workspace member scaffold (plan-vs-reality, T-533a/T-536/T-527a in-body class, drift+brief adjudicated SOUND)**: ANY new `packages/*` is a uv-workspace member → mechanically requires a per-package `pyproject.toml` (hatchling per ADR-0002 — verbatim-mirror `packages/core` + `scalper-v2-scoring` workspace dep) + `py.typed` + a `uv.lock` member-entry. The plan flagged a first-of-its-kind scaffold (§0.3/L-022) but under-specified the exact files; added in-body, not scope creep (unavoidable uv contract).
- **Pass-1 plan-reviewer REVISE BLOCKER (the headline catch — L-018)**: my §N5 coverage-gate proposal claimed "add `packages/sizing` to the CI gate the same way `packages/pnl` is wired" — but `packages/pnl/` **does not exist** (named in the §N1 invariant text, unimplemented) and the §17.2 per-dir `--cov-fail-under=80` seam is **unwired repo-wide** (`pyproject.toml:127 --cov-fail-under=0`, `fail_under=0`, whole-root `--cov`, no `.coveragerc`/`codecov.yml`). An L-018 failure (claimed a precedent that doesn't exist). Fixed per the reviewer's exact prescription: dropped CI-gate wiring from this 2-file pure-math leaf; ≥80% via §N4 TDD verified locally; the §17.2 gap predates this task + report-only-elsewhere is the §17.2 posture → **NOT an N5-by-omission**; NEW `T-F5+` backlog ticket ("implement the §17.2 per-module coverage gate; revisit `packages/sizing`/`packages/pnl` membership"). Disposition closed COMMITTED in §N5/AC#5/§N5-section/Out-of-scope (no residual "plan-reviewer to confirm" — the T-527b1 pass-1 open-disposition lesson applied).
- **Operator OQs (T-527b2 plan-stage 2026-05-16, all recommended)**: OQ-1=A 2-leaf recursive split; OQ-6b=A `SizingSpecForWire` in `packages.bus.payloads` (binds T-527b2b — `OrderRequest`(bus) cannot import `SizingSection`(scoring) since scoring→bus already exists → reverse = cycle; mirror the T-511b2 `VariantSpec` precedent exactly); OQ-7=A `sub_account` kwarg into `make_per_bot_handler` (binds T-527b2b — mirror the `now_fn` binding + reuse main.py `_resolve_sub_account`, T-531 equity-tick precedent). OQ-6b/OQ-7 bind b2b only (b2a is pure, no wire/handler) — baked in the recursive-split-record for b2b's plan-stage.
- **All 4 gates**: plan-reviewer Gate-1 **pass-2** APPROVE (pass-1 REVISE 1 BLOCKER + tightly-coupled CONCERN, both the §N5 item; everything else — the 4 math contracts, Hand-verification arithmetic, b2a/b2b boundary, import acyclicity, Gate-4 disposition, §0.3 — verified SOUND pass-1 "do not re-litigate") → drift-checker ON TRACK (5/5 WG, scope clean — NO `.coveragerc`/`--cov-fail-under`/`ci-*.yml` in the diff, T-F5+ ticket present, zero T-527b2b-scope leak, scaffold SOUND) → brief-reviewer SHIP (no findings; math spot-check ✓; concurs Gate-4 IN-scope-HEAVY) → **math-validator VERIFIED** (Gate-4 **COMMITTED IN scope, HEAVY exhaustive** — the §B.1 sizing math feeding the T-527b2b capital path, T-536/T-525b class NOT the T-527b1 fast-pin; no-float, single-rounding-point, guard-ordering, boundary-inclusive, hand-computable-not-impl-against-itself fixtures all verified; a cosmetic test-literal scale `Decimal("4200.00")→"4200.0"` [2800*1.5 scale 1; numerically identical, non-blocking] corrected post-validation so the §N4 hand-fixture is arithmetically exact).
- **L-022 calibration point 30 ≈ ~189 src vs plan ~85-110 (~1.8×)** — the recurring "Hand-verification/WG-docstring density under-budgeted" pattern (T-526 1.92× / T-534b1 1.59× / T-536 1.32× class, all adjudicated plan-faithful ON TRACK; compute.py ≈70 logic + ≈98 §N4 Hand-verification-in-docstring per the `quantize.py` hand-verified-cases precedent). **≪400 hard cap → NO §0.3 cap breach, NO operator waiver needed** (per-file breakdown in commit body for audit trail per L-022 active control — distinct from a waiver). drift-checker reasoned the L-022-auto-NEEDS-DISCUSSION exception (doc-driven not scope-driven, ≪400, wire-clean) → ON TRACK + operator-note, NOT escalated. **L-018 honored** at last (the pass-1 BLOCKER WAS an L-018 miss — a precedent claim not code-verified; caught by plan-reviewer, the active control working). **NO new deps** (`scalper-v2-scoring` is a workspace member; `math.floor`/`decimal` stdlib; hatchling existing). 2511 unit pass (+21) + 0 regressions; ruff/ruff-format/mypy-strict/bandit pre-commit Passed; L-021 N/A. **Commit** `aa52d6b` pre-merge. **Plan** `docs/plans/T-527b2a.md` (APPROVED pass-2 + 5 WG) + recursive-split-record `docs/plans/T-527b2.md`; ADR-0013 (Accepted) governs the cluster.

### Next session pickup

- **T-527b2b** (the §B.1 sizing cross-service wiring, blocked-by T-527b2a now DONE → **unblocked**, obvious next pick — completes the entire in-flight T-527 split tree). NEW `SizingSpecForWire` in `packages.bus.payloads` (OQ-6b — mirror T-511b2 `VariantSpec`) + additive `OrderRequest.sizing: SizingSpecForWire | None = None` (mirror the shipped T-527a `score`) + strategy-engine `_publish_order_request` maps `bot_config.sizing`→`OrderRequest.sizing` (mirror `shadow_variants`) + `sub_account` kwarg into `make_per_bot_handler` (OQ-7 — mirror `now_fn`; main.py reuse the shipped `_resolve_sub_account`) + `placement.py:_handle` seam (before the T-529 `quantize_qty`): `get_account_balance().total_equity` + `get_mark_price(symbol)` (shipped T-527b1) + `packages.sizing.compute_qty_from_sizing` (shipped T-527b2a) → `None` ⇒ **skip before `place_market_order`** (no order; structured log + a NEW `signals_skipped_sizing` Prom counter on the T-531 `build_execution_metrics` scaffold per OQ-4=A) else substitute the computed qty into the existing `quantize_qty` → place. Backward-compat: `request.sizing is None` → static `request.qty` byte-unchanged. **Gate-4 math-validator IN scope** (touches `services/execution/` — literally in the trigger list this time; the wiring/skip-path + producer/placement integration) + plan `## Hand verification`. Own plan-reviewer Gate-1 cycle (OQ-6b/OQ-7 baked from the recursive-split-record); **L-007/L-014 split-watch** re-confirm at its plan-stage (`SizingSpecForWire` + `OrderRequest` field + producer + `placement.py` seam + skip + `sub_account` plumbing + §N4 TDD ≈ ~200-260 src; could itself split). All upstream primitives shipped (`SizingSection`/`OrderRequest.score`/`get_mark_price`/`get_account_balance`/`packages.sizing`); ADR-0013 binds the architecture.
- Then **T-528** (Risk-per-SL sizing — alternative to tier ladder via `sizing.method: tiers | risk_per_sl` discriminator; inherits ADR-0013's execution-resident shape; L-007/L-014 multi-mode-dispatch split-watch) + **T-532** (funding fee tracking + migration 0021). Then F5 close-out tail: **T-519** (§20 hazard test audit — after the full hardening surface) → **T-521** (final docs) → **T-522** (E1..E6 sign-off, Live-ready MVP). NEW backlog: `T-F5+` §17.2 per-module coverage gate (surfaced this task).
- Position-sizing (T-527/528) is the last open hardening sub-system. T-527 tree: a + b1 + b2a done; **T-527b2b is the obvious next pick** (direct unblock; every upstream primitive shipped; ADR-0013 + OQ-6b/OQ-7 baked). After T-527b2b the whole T-527 §B.1 tier-sizing is live (still BotConfig.sizing-opt-in, backward-compat). Operator picks.

## 2026-05-16 (T-527b1 get_mark_price adapter-protocol extension CLOSED — recursive L-007 split foundation leaf 1/2 of T-527b [T-527b2 the sizing compute blocked-by, now unblocked]; F5 counter 55/62 → 56/63 [recursive L-007 split denom +1, numerator +1]; **ADR-0013 Accepted — operator §6.7** [§B.1 sizing computes in execution-service placement seam, NOT a strategy-engine gate]; plan-reviewer Gate-1 pass-2 APPROVE + 5 WG; math-validator VERIFIED [Gate-4 COMMITTED IN scope, fast — single decode zero arithmetic]; L-024 — no hook surprise)

**F5 counter 55/62 → 56/63**: T-527b (the §B.1 sizing compute, blocked-by the shipped T-527a) → **recursive L-007 split** (operator OQ-2=A T-527b plan-stage) → T-527b1 (`get_mark_price` protocol ext, done) + T-527b2 (the `packages/sizing` compute + wiring, blocked-by T-527b1 → now unblocked). Denominator 62→63 (+1, T-527b 1-slot → 2 leaves — mirror T-534→{a,b}/T-533b→{b1,b2} recursive-split-counter convention); T-527b1 done → numerator 55→56. **Structure mirrors T-534a→T-534b / T-533b1→T-533b2** (adapter-protocol-extension foundation leaf → consumer leaf).

### ADR-0013 — §B.1 sizing computes in the execution-service placement seam (the headline; Accepted, operator §6.7)

- At T-527b plan-stage an Explore code-path analysis surfaced a **cross-service architecture fork the BRIEF is silent on**: §B.1 tier-sizing needs (a) the account balance and (b) a pre-trade reference price to turn notional USD → order qty — and **both are adapter-only**. The exchange adapter exists ONLY in execution-service; strategy-engine (home of the T-524 caps / T-526 cooldown DB-derived pre-publish risk gates) has **no adapter and no clean current-price source** (the scoring feature-store carries only configured indicators, not a guaranteed live price; the signal envelope carries no price); and `ExchangeClient` had **no pre-trade price method** (only post-fill `get_fill_price`).
- **Operator chose (T-527b plan-stage 2026-05-16, all recommended)**: OQ-1=A compute in the **execution-service placement seam** (NOT a strategy-engine gate — the only place both inputs exist); OQ-2=A **2-leaf recursive split** T-527b→{b1,b2}; OQ-3=A score→multiplier `str(floor(score))` clamped to the present-key range, `None`/absent→×1.0. Per CLAUDE.md Gate-1 NEEDS-DISCUSSION→ADR: **ADR-0013 drafted → plan-reviewer reviewed sound → operator §6.7 accepted-as-drafted → flipped Proposed→Accepted**. Rejected alternatives recorded: B strategy-engine gate (no clean price source — would need an adapter in the scoring service, breaking the T-524/T-526-preserved separation, or an unspecified price-feature); C hybrid two-stage (max moving parts, no capital-safety gain — A skips pre-place); post-fill `get_fill_price` (logically impossible — a market order's qty must be decided before the order is placed); thread a pre-computed qty from strategy-engine (it lacks the inputs); no-split (trips §0.3). Does NOT supersede ADR-0011 (defines *what* T-527 is; ADR-0013 the cross-service *how*); the T-524/T-526 gates are unchanged — ADR-0013 explains why §B.1 sizing alone is execution-resident.

### T-527b1 — get_mark_price adapter-protocol extension

- NEW `ExchangeClient.get_mark_price(symbol) -> Decimal` `@idempotent` Read-group method + Bybit `GET /v5/market/tickers?category=linear` impl (mirrors `get_instrument_info` MINUS the cache block — mark price is **live**, not the 1h-TTL deterministic metadata `get_instrument_info` caches; ADR-0013 Trade-offs; `"market"` limiter bucket NOT `"positions"`, no sub-account validation [public endpoint], empty list→`OrderRejected` NOT `ExchangeError`, `RateLimitError→_on_rate_limit_hit("market")` re-raise, `Decimal(str(items[0]["markPrice"]))` no-float, `markPrice` — the liquidation/PnL reference, manipulation-resistant — NOT `lastPrice`/`indexPrice`) + paper deterministic stub (`self._last_price.get(symbol)` — the SAME OHLC-close source `place_market_order` simulates fills from, so sizing source == fill source, backtest/replay-deterministic; unobserved→`OrderRejected`). **Bare `Decimal` return → ZERO type-layer change** (no TYPE_CHECKING/`__all__`/types.py/`__init__.py` — explicitly avoids the T-530 plan-vs-reality `__init__.py` inaccuracy). **NO consumer** (T-527b2 first consumer). Zero behavior change, 0 §20 regression, no new H-NNN (read-only no-caller, mirror T-530/T-529 posture).
- **Conformance test**: exactly 4 precise sub-edits (count `13→14`, error-msg `+ T-527b1`, comment, "4 writes + 7 reads" tally) NOT a blind bump (T-530 precedent); `_UNLABELED_METHODS` unchanged ==3 (get_mark_price is a Read, not stream/lifecycle); introspective presence/marker tests self-catch. NEW explicit `get_mark_price` `@idempotent` marker-pin test (the introspective test only pins "some marker"; this pins the correct one for a retry-safe read) — used a **variable `name="get_mark_price"; getattr(adapter_cls, name)`** mirroring the file's :89 convention to avoid (i) mypy `[attr-defined]` on the bare `type` param, (ii) ruff B009 getattr-constant, AND (iii) the L-018/T-536 "noqa for a rule that may not be enabled" trap — chose the convention-faithful variable form over a noqa whose rule-enablement I'd have to verify.
- **All 4 gates**: plan-reviewer Gate-1 **pass-2** APPROVE (pass-1 REVISE — single CONCERN: the Gate-4 disposition was left "plan-reviewer to confirm" in 3 loci; the reviewer adjudicated it IN-scope and I baked the committed disposition into §Hand verification + AC#7 + the split-record, then re-ran → pass-2 APPROVE) + ADR-0013 reviewed sound + operator §6.7 accepted → drift-checker ON TRACK (5/5 WG, zero scope creep, ZERO production caller grep-confirmed, L-007 boundary clean) → brief-reviewer SHIP (no blocking findings; 1 optional nit — the ADR Authors line still said "plan-reviewer (Gate-1, pending)" while Status was Accepted; fixed in-line, durable ADR kept accurate) → **math-validator VERIFIED**.
- **Gate-4 disposition (the process point)**: `packages/exchange/` is NOT literally in the CLAUDE.md Gate-4 trigger list, so by the literal "if and only if" rule math-validator would be skipped (as for T-533a). BUT the established **T-530 non-exhaustive-path precedent** governs: the identical-shape `Decimal(str())` exchange price/money decode in `packages/exchange/` feeding a capital path was adjudicated DEFINITIVELY IN SCOPE. `get_mark_price`'s `Decimal(str(markPrice))` feeds the T-527b2 `qty = notional ÷ mark_price` capital path → IN scope, **COMMITTED** (not left to the reviewer — baked in the plan). Honest caveat preserved: T-527b1's numeric surface is **strictly smaller than T-530's** (T-530 also had a Decimal addition + alias identities + a negative case; T-527b1 has ONLY the single decode, ZERO arithmetic — notional÷price is T-527b2) → **fast VERIFIED** on the no-float decode pin alone (the T-534b1/T-531 in-scope-zero-arithmetic precedent, NOT the heavier T-530/T-535/T-536). math-validator returned exactly that.
- **L-022 calibration point 29 ≈ ~76 src** (plan ~55-90 — on-target; adapter-protocol extension mirroring `get_instrument_info`/`get_account_balance` 1:1, doc-light). **L-018/T-536 honored** — the marker-pin getattr-variable choice explicitly avoided a noqa for a possibly-unenabled rule (the recurring tooling-correction class; here pre-empted, not corrected-after). **L-024 reused clean**. **NO new deps** (`/v5/market/tickers` is a new path on the existing client). 2490 unit pass (+10) + 0 regressions; ruff/ruff-format/mypy-strict/bandit pre-commit Passed; L-021 N/A. **Commit** `93245ba` pre-merge. **Plan** `docs/plans/T-527b1.md` + recursive-split-record `docs/plans/T-527b.md` + **ADR-0013** (Accepted). Scope-clean: no new deps/migration/Settings/BRIEF; the only doc decision is ADR-0013 (accepted).

### Next session pickup

- **T-527b2** (the §B.1 sizing compute, blocked-by T-527b1 now DONE → **unblocked**, obvious next pick — completes the in-flight T-527 split). NEW `packages/sizing/` pure module (`select_tier`/`apply_score_multiplier`/`cap_notional`/`compute_qty_from_sizing` — **§N4 financial-math TDD mandatory**, hand-verified) + additive `OrderRequest.sizing: SizingSection | None = None` (mirror the shipped T-527a `score`) + strategy-engine producer maps `BotConfig.sizing`→`OrderRequest.sizing` + `placement.py:_handle` seam (before the T-529 `quantize_qty`): `get_account_balance().total_equity` → tier-select (highest `balance_min ≤ total_equity`; sub-lowest → **skip before `place_market_order`** no order, structured log + Prom counter on the T-531 execution-metrics scaffold per OQ-4=A) → base notional `tier.size` → × `score_multipliers[str(floor(score)) clamped to present-key range; None/absent→×1.0 per OQ-3=A]` → clamp `max_notional_per_symbol[symbol|default]` → `qty = notional ÷ get_mark_price(symbol)` (the shipped T-527b1) → existing T-529 `quantize_qty`. Backward-compat: `BotConfig.sizing is None` → static `request.qty` byte-unchanged. **Gate-4 math-validator IN scope** (touches `services/execution/` — literally in the trigger list this time, AND real notional÷price + floor+clamp + cap arithmetic — the heavier verification, contrast T-527b1's fast pin) + plan `## Hand verification` (tier-select / floor+clamp score-mult / max_notional clamp / notional÷price worked examples). Own plan-reviewer Gate-1; **L-007/L-014 split-watch** re-confirm at its plan-stage (pure module + OrderRequest field + producer + placement seam + skip + extensive §N4 TDD could itself be sizable). The shipped `SizingSection`/`OrderRequest.score`/`get_mark_price`/T-530 `get_account_balance` + the T-526/T-524 silent-skip pattern are the direct precedents; ADR-0013 binds the architecture.
- Then **T-528** (Risk-per-SL sizing — alternative to tier ladder via `sizing.method: tiers | risk_per_sl` discriminator; inherits ADR-0013's execution-resident shape; L-007/L-014 multi-mode-dispatch split-watch) + **T-532** (funding fee tracking + migration 0021). No blocked-by between T-528/T-532 once T-527b2 lands. Then F5 close-out tail: **T-519** (§20 hazard test audit — after the full hardening surface) → **T-521** (final docs) → **T-522** (E1..E6 sign-off, Live-ready MVP).
- Risk-mgmt (T-524/525), balance/equity (T-530/531), SL/TP (T-534/535/536), lifecycle-FSM (T-533) clusters ALL COMPLETE; position-sizing (T-527/528) is the last open hardening sub-system — T-527a + T-527b1 done, **T-527b2 is the obvious next pick** (direct unblock; SizingSection + score + get_mark_price + get_account_balance all shipped; ADR-0013 binds the design). Operator picks.

## 2026-05-16 (T-527a §B.1 sizing config foundation CLOSED — L-007 pre-emptive split foundation leaf 1/2 of T-527 [T-527b compute blocked-by, now unblocked]; F5 counter 54/61 → 55/62 [L-007 split denom +1, numerator +1]; plan-reviewer Gate-1 APPROVE + 5 WG; plan-vs-reality `_parse_risk`-splat correction [L-018 tooling-class, in-body, drift-adjudicated SOUND]; math-validator NOT invoked [out-of-scope, "if and only if"]; L-024 — no hook surprise)

**F5 counter 54/61 → 55/62**: T-527 (§B.1 sizing block, explicitly TASKS-split-watch-flagged, large) → **operator OQ-1=A L-007 pre-emptive split** → T-527a (config foundation, done) + T-527b (the balance→tier→score-mult→cap→qty compute, blocked-by T-527a → now unblocked). Denominator 61→62 (+1, T-527 1-slot → 2 leaves — mirror T-533/T-534 split-counter convention); T-527a done → numerator 54→55. **Structure mirrors T-533a→T-533b / T-534a→T-534b** (schema/plumbing foundation leaf → capital-path consumer leaf).

### T-527a — §B.1 sizing config foundation

- **Scope (5 src, zero behavior change, no consumer)**: NEW `SizingTier`+`SizingSection` Pydantic models `packages/scoring/types.py` (`extra="forbid"` net-new mirror RiskSection/ShadowConfig; 4 structural `@model_validator` guards — non-empty + strictly-ascending `balance_min` + `max_notional_per_symbol["default"]` + digit-string `score_multipliers` keys; **deliberately NO `tier_promotion`/`tier_demotion` fields** — OQ-2=A deferred) + `BotConfig.sizing: SizingSection | None = None` (Optional-sentinel mirror `shadow:`, NOT `risk`'s default_factory — None = no tier sizing → static `execution.qty` byte-unchanged; a defaulted empty-tiers section is invalid by the non-empty validator anyway) + `yaml_loader._parse_sizing` + additive `OrderRequest.score: float | None = None` (mirror T-511b2 additive shadow_* fields; schema_version stays "1.0"; **float not Decimal** — scoring metric not §5.13 money, mirrors `trigger_threshold:float`) + strategy-engine `_publish_order_request` threads `score=result.total_score` onto the OrderRequest. **qty selection byte-unchanged** (`bot_config.execution.qty`); score carried producer→wire but UNCONSUMED until T-527b (first consumer). 0 §20 regression, no new H-NNN.
- **Operator OQs (T-527 plan-stage, all default A, baked)**: OQ-1=A 2-leaf split; **OQ-2=A tier_promotion/tier_demotion DEFERRED** → NEW `T-F5+` backlog ticket (TASKS.md:344, §0.7 not absorbed — under-specified stateful tier-adjustment layer needing its own spec); OQ-3=A `size`/`max_notional_per_symbol`=notional USD (binds T-527b: qty=notional÷mark-price); OQ-4=A tier by `AccountBalance.total_equity`, sub-lowest-tier→skip signal (binds T-527b; T-527a validators guarantee a well-defined lowest tier). Split-record `docs/plans/T-527.md`.
- **Plan-vs-reality `_parse_risk`-splat correction (the headline; T-536/T-533a/T-533b1/T-533b2 tooling-correction class, L-018, in-body, drift-checker-adjudicated SOUND — 5th consecutive, L-018 active control already covers it, no new lesson)**: plan/WG#3 said `_parse_sizing` "mirror `_parse_shadow` EXACTLY". `_parse_shadow` reads keys explicitly and never `**spec`-splats → an unknown `sizing:` key (the OQ-2=A-**deferred** `tier_promotion`, or any operator typo) would be **silently dropped** — a capital-safety gap exactly defeating WG#2's stated "extra='forbid' rejects them at YAML load" guarantee (operator writes `tier_promotion:` believing promotion is configured; it is silently ignored). Surfaced by 3 failing tests (1 pre-existing sibling + my 2 new typo/tier_promotion-rejection tests). Corrected to mirror **`_parse_risk`'s `RiskSection(**coerced)` splat** (preserve every key → `SizingSection` `extra="forbid"` loudly rejects unknowns), adapted for the nested per-element `_to_decimal` coercion the flat `risk:` block doesn't need. A present-but-malformed `sizing:` block now fails LOUD at load (operator wrote `sizing:` → intends tier sizing; never a silent fall-back to static qty). Corrected in-body: plan §Status + §Scope #2 + `_parse_sizing` docstring. The pre-existing sibling test `test_b1_remaining_unmodeled_extras_ignored_via_extra_ignore` updated per the plan's L-015 enumeration (`sizing` is no longer an unmodeled-extra — minimal `sizing:` block removed from its YAML + docstring note, mirroring how that same test already documents T-514's `shadow` promotion).
- **All 4 gates**: plan-reviewer Gate-1 APPROVE + 5 WG (all 6 scrutiny points sound; the AC#8 backlog-ticket flagged as a WG#1 deliverable) → drift-checker ON TRACK (5/5 WG, the splat-correction adjudicated SOUND not drift, ~168 src ≪ L-022 ~210 tripwire ≪ §0.3 400, scope clean — grep-confirmed NO reader of `BotConfig.sizing`/`request.score` outside tests, L-007 boundary clean, L-015 enumeration complete) → brief-reviewer SHIP (no findings, WG 5/5, §N1-N10, 0 §20 regression, docstring touches accurate not stale-prose) → **math-validator NOT invoked** (Gate-4 out-of-scope — none of the 5 src files in the CLAUDE.md trigger list: `packages/scoring/` ≠ `services/scoring/`, `packages/bus/` + `services/strategy_engine/` not listed; zero arithmetic; per "if and only if" Gate-4 NOT invoked — drift-checker + brief-reviewer both concurred; mirror T-533a framing).
- **L-022 calibration point 28 ≈ ~168 src** (plan ~110-180 contingency-adjusted — on-target; mostly Pydantic models + validators + nested parse + docstrings, schema-shaped not logic-heavy). **L-024 reused clean** (mypy over staged incl. tests; setattr() for frozen-mutation test mirrors the existing `test_models_are_frozen` precedent — avoided a dead `# type: ignore[misc]`). **L-018 honored** — the `_parse_shadow`-silently-drops claim is backed by direct code-reading (`_parse_shadow` genuinely has no `**spec` splat), not reasoning. **NO new deps** (`from itertools import pairwise` = stdlib). 2480 unit pass (+17) + 0 regressions; ruff/ruff-format/mypy-strict/bandit pre-commit Passed; L-021 N/A (no SQL/migration). **Commit** `8ddc655` pre-merge. **Plan** `docs/plans/T-527a.md` + split-record `docs/plans/T-527.md`. Scope-clean: no new deps/migration/Settings/ADR/BRIEF; legacy qty path byte-unchanged (additive).

### Next session pickup

- **T-527b** (the §B.1 sizing compute, blocked-by T-527a now DONE → **unblocked**, obvious next pick — completes the in-flight T-527 split). Consume `BotConfig.sizing` + `OrderRequest.score` + `adapter.get_account_balance().total_equity` + a mark/ticker reference price; wire into the execution-service placement seam (`placement.py:_handle`, before the T-529 `quantize_qty`): tier-select (highest `balance_min ≤ total_equity`; **sub-lowest → skip signal** no order, soft-block + structured log + Prom counter per OQ-4=A, mirror T-526/T-524 silent-skip) → base notional `tier.size` → `× score_multipliers[floor(score) clamped]` → clamp `max_notional_per_symbol[symbol|default]` → `qty = notional ÷ reference_price` → existing T-529 `quantize_qty`. Backward-compat: `BotConfig.sizing is None` → static `request.qty` byte-unchanged. **§N4 financial-math TDD mandatory** + **Gate-4 math-validator IN scope** (touches `services/execution/`) + plan `## Hand verification` (tier-select / score-mult / max_notional-clamp / notional÷price worked examples — first sizing-cluster task with real Decimal qty arithmetic). Own plan-reviewer Gate-1 cycle; **L-007/L-014 split-watch** re-confirm at its plan-stage (the score→multiplier mapping + balance fetch + skip path + placement wiring could itself be sizable). The shipped `SizingSection`/`_parse_sizing`/`OrderRequest.score` + the T-530 `get_account_balance()` + the T-526/T-524 derive-from-trades silent-skip pattern are the direct precedents.
- Then **T-528** (Risk-per-SL sizing — alternative to tier ladder via `sizing.method: tiers | risk_per_sl` discriminator; coexists with T-527; L-007/L-014 multi-mode-dispatch split-watch) + **T-532** (funding fee tracking + migration 0021). No blocked-by between T-528 and T-532 once T-527b lands. Then F5 close-out tail: **T-519** (§20 hazard test audit — ideally after the full hardening surface lands) → **T-521** (final docs) → **T-522** (E1..E6 sign-off, Live-ready MVP).
- The risk-mgmt (T-524/525), account-balance/equity (T-530/531), SL/TP-verification (T-534/535/536), trade-lifecycle-FSM (T-533) clusters are ALL COMPLETE; position-sizing (T-527/528) is the last open hardening sub-system, T-527a done, **T-527b is the obvious next pick** (direct unblock; SizingSection + score + get_account_balance all shipped). Operator picks.

## 2026-05-16 (T-533b2 call-site dual-write wiring CLOSED — recursive-split consumer leaf 2/2 of T-533b; **T-533 trade-lifecycle-FSM cluster COMPLETE** [T-533a + T-533b1 + T-533b2]; F5 counter 53/61 → 54/61 [T-533b2 leaf, denom unchanged — split denom +1 applied at T-533b1 close]; plan-reviewer APPROVE pass-2 [pass-1 REVISE 2 BLOCKER both site #7, Option A non-operator-facing in-body resolution]; math-validator VERIFIED out-of-scope additive-only; plan-vs-reality L-018 class no new lesson; L-024 — no hook surprise)

**F5 counter 53/61 → 54/61**: T-533b2 is the last leaf of the T-533b recursive split (T-533b1 primitive + T-533b2 wiring). Numerator 53→54; **denominator unchanged 61** — the L-007 split denom +1 was applied at T-533b1 close, T-533b2 was already the 61st slot. **T-533 trade-lifecycle-FSM cluster fully shipped** (T-533a backfill + T-533b1 `update_trade_lifecycle_state` primitive + T-533b2 call-site wiring; ADR-0011).

### T-533b2 — call-site dual-write wiring

- **9 sites / 5 execution-service modules**, each one `await update_trade_lifecycle_state(conn, trade_id=…, state=TradeLifecycleState.X)` (direct enum literal, OQ-3=A) added-after the unchanged legacy write, same conn/tx: `placement_persist` #1 persist_placement_tx→OPEN / #2 emergency_close→OPEN / #3 emergency_close→CLOSED / #4 emergency_close_tracked_position→CLOSED; `lifecycle` #5 sl_type=be→BREAKEVEN_SET / #6 sl_type=trail→TRAILING_ACTIVE; `dispatcher` #7→PARTIALLY_CLOSED; `reconcile.reconcile_close` #8→CLOSED; `restart._close_orphan_db` #9→RECONCILED. **NO further split** (OQ-1=A — code-inventory ~30-65 src ≪ §0.3 400 cap; the L-007/L-014/L-016 pre-flag was conservative worst-case, not borne out: additive 1-call wiring, not an FSM-body module — L-014/L-016 multipliers don't apply). Additive-observable T-533 OQ-1=A: legacy 4-col authoritative+byte-unchanged, lifecycle_state observability-only, nothing reads it, 0 behavior change, 0 §20 regression.
- **Site #7 = the headline (plan-reviewer pass-1 B1/B2 resolution)**: pass-1 REVISE, 2 BLOCKER both site #7. **B1**: my draft pinned site #7 to `dispatcher.py:234 update_position_state_after_fill` on "the partial-TP-only branch (exec_type='partial_tp')" — but `:234` fires for ALL non-open exec types (`_derive_exec_type` :369-411 → partial_tp/close/trail/sl/unknown). L-018 (mapping claim not code-verified — my grep misread). **B2**: OQ-3=A "no per-site conditional" needed explicit reconciliation with the site #7 wiring. **Resolution Option A (NOT a fold-away → NOT operator-facing → in-body fix + re-review, consistent with established T-533a/T-536/T-533b1 in-body discipline)**: re-read dispatcher.py:216-310, re-pinned site #7 to an additive `elif (exec_type != "open" and ps_after is not None)` arm on the dispatcher's PRE-EXISTING `:274` `remaining_qty==Decimal("0")` close-trigger `if` (the `if` arm routes to CLOSED via `reconcile_close` = site #8; the new `elif` is reached iff a non-open fill did NOT zero remaining_qty ⇒ partial, position still open). Each arm a *fixed* literal on a branch the host already computes — same model as site #9 (`_close_orphan_db` sole `reconcile_gone` call-site → RECONCILED vs other `update_trade_close` sites → CLOSED). OQ-3=A clarified: "no per-site conditional" prohibits a NEW `state = X if … else Y`, NOT a fixed literal per arm of host code's pre-existing structural branch. Pre-existing `if` body byte-unchanged (0 deleted lines in dispatcher.py); H-030 open-fill-never-triggers-close intact (both arms gated `exec_type!="open"`); elif carries its own `trade_id None` guard (WG#2). plan-reviewer pass-2 APPROVE + 5 WG (incl. 2 dispatcher branch-discrimination tests WG#3).
- **Plan-vs-reality (L-018 class, no new lesson — 4th consecutive: T-536 noqa / T-533a vocab / T-533b1 TYPE_CHECKING / now this)**: per-file runtime `TradeLifecycleState` import follows the established `placement_persist.py:405`/`reconcile.py:116` local-in-function-import convention for these 5 modules (they keep `packages.core` annotation-only at top-level; top-level runtime is cycle-free elsewhere e.g. pool.py but these 5 modules' convention is local). Comment moved off the import line onto its own line above (ruff E501/I001 — `TradeLifecycleState` is longer than the `CorrelationId` precedent, inline comment exceeded 100). 2 mechanically-required no-op `AsyncMock` test-guards added to pre-existing tests (`test_reconcile` lock-scope test that doesn't use the fixture + `test_concurrent_bot_cumulative_delta` fixture) — the wired `reconcile_close` path now calls `update_trade_lifecycle_state`; bare-MagicMock-conn tests would `await` a MagicMock without the guard; P&L/cumulative-delta assertions byte-unchanged (brief-reviewer confirmed not test-weakening).
- **All 4 gates**: plan-reviewer APPROVE pass-2 (pass-1 REVISE 2 BLOCKER both site #7; 5 WG) → drift-checker ON TRACK (WG 5/5, 90 raw / ~30-65 logical-add src, L-022 untripped, 0 scope creep) → brief-reviewer SHIP (9/9 sites, H-030 intact, 0 §20 regression, 2 guards mechanically-required not weakening, WG 5/5) → **math-validator VERIFIED** (Gate-4 `services/execution/` IN trigger list but out-of-scope additive-only — zero arithmetic, existing P&L/SL/cumulative-delta math bit-identical per fix(T-218b) precedent; reconcile.py D3 `delta = after_total - before_total` outside staged hunks). 2463 unit pass + 0 regressions; mypy strict over staged incl. tests clean (L-024 — no hook surprise; 8th reuse); ruff/ruff-format/mypy/bandit pre-commit Passed; **L-021 N/A** (no SQL/migration — `update_trade_lifecycle_state` reused unmodified). **L-022 calibration point 27 ≈ ~50-60 logical src** (in plan's ~30-65 estimate; site #7's +2-3 LOC structural-exception immaterial; no waiver). **Commit** `e87aee7` pre-merge. **Plan** `docs/plans/T-533b2.md` (APPROVED pass-2 + 5 WG verbatim). Scope-clean: no new deps/migration/Settings/ADR/BRIEF; every legacy write byte-unchanged (additive, OQ-1=A).

### Next session pickup

- **T-533 cluster is DONE** (a/b1/b2 all shipped — lifecycle-FSM structural piece complete). The risk-mgmt (T-524/525), account-balance/equity (T-530/531), SL/TP-verification (T-534/535/536), and trade-lifecycle-FSM (T-533) clusters are ALL COMPLETE.
- **Remaining ADR-0011 hardening (operator picks)**: **T-527/T-528** (position sizing — §B.1 tier ladder + risk-per-SL; L-007 split-watch — likely the next obvious pick, last hardening sub-system) and **T-532** (funding fee tracking + migration 0021; integrates the T-220 cumulative-delta audit). No blocked-by between them — operator chooses order.
- **Then F5 close-out tail**: **T-519** (§20 hazard test audit — H-027/H-028/H-029 have `**Test.**`-named tests; T-533*/T-536 have NO H-NNN [additive/emit-only diagnostics, §0.8] so out of the H-catalog audit but their named tests still run) → **T-521** (final docs) → **T-522** (close-out E1..E6 sign-off, Live-ready MVP). T-519 should ideally run after T-527/528/532 land (full hardening surface to audit).

## 2026-05-16 (T-533b1 update_trade_lifecycle_state forward-write primitive CLOSED — ADR-0011 trade-lifecycle-FSM; **recursive L-007 split foundation leaf 1/2 of T-533b** [T-533b2 call-site wiring blocked-by, pending]; F5 counter 52/60 → 53/61 [recursive split denom +1, numerator +1]; plan-reviewer single-pass APPROVE [UNDECORATED §N3 code-verified — pre-empted T-534b1 BLOCKER class]; math-validator N/A out-of-scope; plan-vs-reality TYPE_CHECKING tooling-correction [T-536/T-533a class]; L-024 — no hook surprise)

**F5 counter 52/60 → 53/61**: T-533b (1 leaf-slot of the T-533 split) → **operator OQ-1=A recursive L-007 split** → T-533b1 (write primitive, done) + T-533b2 (call-site wiring, blocked-by T-533b1, pending). Denominator 60→61 (+1, T-533b 1-slot → 2 leaves — mirror T-534b→{b1,b2} recursive-split-counter convention); T-533b1 done → numerator 52→53. **Structure mirrors T-534b1→T-534b2** (write-primitive foundation leaf → call-site consumer leaf).

### T-533b1 — `update_trade_lifecycle_state` forward-write primitive

- NEW `update_trade_lifecycle_state(conn, *, trade_id, state: TradeLifecycleState) -> None` in `packages/db/queries/execution.py` (beside trades writers, NOT pure `lifecycle.py`) — `UPDATE trades SET lifecycle_state = $1 WHERE id = $2` (H-018 PK-only, verbatim-mirror `update_trade_close`). T-533b2's exact need: every post-trades-row transition site calls this (direct enum literal, OQ-3=A) alongside its unchanged legacy write. T-530/T-534b1/T-533a-shaped **no-caller** foundation leaf.
- **UNDECORATED — pre-empted the exact §N3-marker BLOCKER that hit T-534b1** (the headline): code-verified that the sibling trades/position_state PK-`SET` UPDATE helpers (`update_trade_close`/`update_trade_fees_incremental`/`update_position_state_sl`/`update_position_state_monitor_tick`) are ALL undecorated; `@non_idempotent` is reserved for INSERT/place-order writers; §N3 "every external write classified" is satisfied by the idempotent-by-construction PK-SET convention. Marker-mirror test pins `is_idempotent==False AND is_non_idempotent==False` (inverse of T-534b1's `is_non_idempotent True`). At T-534b1 this surfaced as a pass-1 plan-reviewer BLOCKER (plan assumed `@non_idempotent`); here the plan baked the UNDECORATED decision with sibling-precedent citation up-front → **plan-reviewer single-pass APPROVE, 0 BLOCKER/0 CONCERN** (the review-lesson loop converting a one-off catch into pre-emptive immunity, working as designed).
- **Plan-vs-reality tooling-correction (T-536/T-533a class, L-018, drift-checker-adjudicated SOUND, NOT a new lesson)**: WG#5/Scope §1 prescribed adding `TradeLifecycleState` to the runtime `from packages.core import non_idempotent` line. `execution.py:17` has `from __future__ import annotations` → `state: TradeLifecycleState` is a runtime-noop annotation string (`state.value` accesses the passed argument), so a runtime import is ruff `flake8-type-checking` TC002 → would fail the L-023 ruff pre-commit hook. Implemented in the `if TYPE_CHECKING:` block (ruff-clean, AC#6-correct); the test file keeps the runtime import (genuinely runtime-needed — parametrize values + marker introspection). Plan corrected in-body (Status note + Scope §1 + WG#5). 3rd consecutive plan-vs-reality tooling-config correction (T-536 noqa/PLC2701, T-533a stale-status-vocab grep-misread, now this) — all L-018-class, all caught by the gate system, none a new lesson (L-018 active control already covers "follow actual tooling/code config over plan-assumed").
- **All 4 gates**: plan-reviewer APPROVE single-pass (5 WG; UNDECORATED §N3 + all code-citations verified) → drift-checker ON TRACK (execution.py +41 src ≪ §0.3 400 cap, no L-022 escalation — doc-heavy sub-50-LOC primitive [WG#4 H-018/L-021/§N3 docstring] the plan pre-called-out; 5/5 WG; 0 §20 regression — only additive hunks; TYPE_CHECKING-placement adjudicated sound tooling-correction NOT drift) → brief-reviewer SHIP (5/5 WG, §N1-N10, §0.3 no waiver, §20 no-H-NNN, plan-vs-reality verified consistent) → **math-validator N/A** (out-of-scope — `packages/db/queries/` NOT in CLAUDE.md Gate-4 trigger list, zero arithmetic — `state.value` string + int PK; per "if and only if" not invoked; 2nd consecutive genuinely-N/A Gate-4 after T-533a).
- **L-022 calibration point 26 = ~41 src** (plan ~15 logic+docstring; 41 incl. the WG#4-mandated H-018/L-021/§N3 reference-dense docstring — doc-not-logic, anticipated by the plan §0.3 "no L-022 concern at this size"; ≪ cap, no escalation, no waiver). **L-024 active control reused — clean** (mypy over staged incl. tests pre-commit; MagicMock conn avoids the typed-AsyncMock `.await_args` union-attr trap; 7th successful reuse). **L-021 N/A** (column-direct `$1`/`$2`, mock SQL-string pin only, no migration/testcontainer — contrast T-533a backfill which DID need real-PG).
- **Tests**: `test_queries_execution.py` +4 cases (parametrized exact-SQL-string + bind-order `(state.value, trade_id)` OPEN/RECONCILED/FAILED→open/reconciled/failed + `"::" not in sql` L-021 no-cast tripwire + marker-mirror undecorated pin). 2454 unit pass (+4) + 0 regressions; ruff/ruff-format/mypy-strict/bandit pre-commit Passed. **Commit** `6598453` pre-merge. **Plan** `docs/plans/T-533b1.md` + recursive-split-record `docs/plans/T-533b.md`. Scope-clean: no new deps/migration/Settings/ADR/BRIEF; legacy writers/readers byte-unchanged (additive, OQ-1=A).

### Next session pickup

- **T-533b2** (call-site dual-write wiring, blocked-by T-533b1 now DONE → **unblocked**, obvious next pick — completes the in-flight T-533 split's last leaf-pair). Wire `update_trade_lifecycle_state(state=TradeLifecycleState.X)` (direct enum literal per site, OQ-3=A) into every post-trades-row transition site alongside the unchanged legacy write: `insert_trade`→OPEN; dispatcher partial/close→PARTIALLY_CLOSED/CLOSING/CLOSED; lifecycle BE/trail/TP→BREAKEVEN_SET/TRAILING_ACTIVE/TP_HIT; reconcile/restart→ORPHANED/RECONCILED; emergency/error→CLOSED/FAILED. OQ-4=A post-trades-row only (pre-`insert_trade` SIGNAL_RECEIVED/ORDER_REQUESTED/ORDER_PLACED enum-defined but NOT persisted — no trades row, §0.8; CLOSING/TP_HIT only if clean distinct site else fold — site→state mapping table in the T-533b2 plan). Still additive-observable (legacy authoritative; OQ-1=A; 0 §20 regression). Own plan-reviewer Gate-1 cycle; **L-007/L-014/L-016 recursive-split-watch** (cross-module wiring across ~5 modules — likely pre-emptive recursive-split by module-group placement / dispatcher+lifecycle / reconcile, mirror T-534b2-after-T-534b1). `update_trade_lifecycle_state` + the enum are the shipped primitives; the T-533a/b1 plan-vs-reality + UNDECORATED-precedent patterns are direct precedents.
- Then remaining ADR-0011 hardening (operator picks): **T-527/T-528** (position sizing — §B.1 tier ladder + risk-per-SL; L-007 split-watch), **T-532** (funding fee tracking + migration 0021; integrates T-220 cumulative-delta audit). Then F5 tail: **T-519** (§20 hazard test audit — H-027/H-028/H-029 have `**Test.**`-named tests; T-533*/T-536 have NO H-NNN [additive/emit-only diagnostics, §0.8] so out of the H-catalog audit but their named tests still run) → **T-521** (final docs) → **T-522** (close-out E1..E6 sign-off, Live-ready MVP).
- T-533b1/T-533b2 is the in-flight recursive split — leaf 1 (primitive) done, **T-533b2 is the obvious next pick** (direct unblock; `update_trade_lifecycle_state`+enum shipped). After the full T-533 cluster (a/b1/b2) the lifecycle-FSM structural piece is done; only T-527/528 (sizing) + T-532 (funding) hardening remain before the F5 close-out tail (T-519→T-521→T-522). Operator picks.

## 2026-05-16 (T-533a TradeLifecycleState enum + migration 0020 + derive_lifecycle_state CLOSED — ADR-0011 trade-lifecycle-FSM cluster; **L-007 pre-emptive-split foundation leaf 1/2 of T-533** [T-533b dual-write blocked-by, pending]; F5 counter 51/59 → 52/60 [L-007 split denom +1, numerator +1]; plan-reviewer 3-pass APPROVE; **L-021 GREEN — L-003 SQL≡Python≡hand backfill proven on real PG**; math-validator N/A out-of-scope; NO new §20 H-NNN [additive, §0.8]; L-024 — no hook surprise)

**F5 counter 51/59 → 52/60**: T-533 (largest hardening task / F5 critical-path bottleneck, ADR-0011-pre-flagged L-007/L-014/L-016) → **operator OQ-2=A L-007 pre-emptive split** → T-533a (foundation, done) + T-533b (dual-write, blocked-by T-533a, pending). Denominator 59→60 (+1, T-533 1-slot → 2 leaves — mirror T-534/T-525a split-counter convention); T-533a done → numerator 51→52. **Structure mirrors T-530→T-531 / T-534a→T-534b** (schema/type foundation leaf → consumer leaf).

### T-533a — TradeLifecycleState enum + migration 0020 + derive_lifecycle_state mapping

- **OQ-1=A ADDITIVE-observable** (the headline scope call): `lifecycle_state` is a NEW canonical *observable* state; the legacy 4-column model (`trades.status`/`close_reason` + `position_state.{tp_hit,sl_type,trailing_active}`) **REMAINS authoritative for every read/decision** — nothing reads lifecycle_state (T-533b owns forward dual-write). ZERO behavior change, 0 §20 regression, **no new §20 H-NNN** (ADR-0011 reserved no T-533 slot; additive diagnostic; §0.8), no Settings (§N9 enum-fixed). Authoritative-switch + legacy-deprecation deliberately deferred F5+/F6 (NOT T-533 at all). 13-state `TradeLifecycleState(StrEnum)` `packages/core/types.py` + `derive_lifecycle_state` pure helper NEW `packages/db/queries/lifecycle.py` + migration 0020 (`down_revision="0019"` — TASKS '0018' doubly-stale) nullable `trades.lifecycle_state` TEXT + legacy-4-col backfill.
- **3-pass plan-reviewer (the core value was the review archaeology)**: pass-1 REVISE 3 BLOCKER — my §Mapping table was built on a **factually-wrong `trades.status` vocab `{open,closed,failed}`**; actual is `{open,closed,error}` (`TradeStatus(StrEnum)` types.py:144-154; `error`=T-221 orphan/partial-failure intent, no current writer — `insert_trade`→'open'/`update_trade_close`→'closed' only writers). Re-derived `error→FAILED` defensive/enum-vocab-complete + status-sole-discriminator + close_reason evidence-pinned (`_EXEC_TYPE_TO_CLOSE_REASON` + emergency + reconcile_gone) + 2 CONCERN (ORPHANED code-cited; **L-020 trade_id-anchored JOIN** not composite bot_id+symbol reuse-hazard). pass-2 REVISE 1 CONCERN — L-015 "(none expected — sole origin)" was false (test_0006:213/test_0014:120 also `INSERT INTO trades`, nullable-safe); full sibling enumeration. pass-3 APPROVE 9 WG, every code-citation verified. (My grep misread 'failed' from the enum-state name — plan-reviewer caught the load-bearing defect pre-coding; the review system working exactly as designed.)
- **L-003 golden cross-check** (frozen migration SQL CASE can't import mutable app code → two impls): `test_0020::test_backfill_sql_equals_python_and_hand` seeds the full legacy combination matrix (bots→orders→trades→position_state FK chain) → upgrade 0020 → asserts DB lifecycle_state == hand-authored-expected **AND** == `derive_lifecycle_state` (SQL ≡ Python ≡ hand, independent). **L-021 GREEN — 4 env-gated migration tests run locally vs real PG** (`POSTGRES_TEST_DSN` dev container `scalper-v2-postgres-1`): backfill triple-equivalence PROVEN on real PG + L-012 explicit `downgrade 0019` + test_0005 element-22 + column-shape. **L-021 run surfaced 2 test-only seed bugs** (meta-jsonb passed dict not JSON-str → L-011/L-013 family; missing bots-FK-chain seed → ForeignKeyViolation) — fixed + folded into feat via `git --amend` pre-merge (commit not pushed; test-only seed-plumbing, enum/migration/helper/mapping substance unchanged & gate-reviewed). This is exactly why L-021 mandates local-real-PG-before-push (CI-first sub-gap; T-537a1 broke master 2×).
- **All 4 gates**: plan-reviewer APPROVE pass-3 (9 WG) → drift-checker ON TRACK (**124 src** ≪ ~205 L-022 tripwire ≪ §0.3 400 cap; single-leaf-no-further-split; 0 §20 regression — only 8 expected files; L-021-pending correctly NOT flagged as drift) → brief-reviewer SHIP (9/9 WG, §N1-N10, §0.3 no waiver, §20 no-H-NNN correct, L-015 only test_0005, Gate-4 out-of-scope confirmed) → **math-validator N/A** (out-of-scope — `packages/core`/`packages/db`/migrations NOT in CLAUDE.md Gate-4 trigger list [packages/features/*, packages/pnl/, services/feature-engine|execution|scoring], zero arithmetic — pure string-enum mapping; per "if and only if" math-validator NOT invoked — first task this session where Gate-4 is genuinely N/A).
- **L-022 calibration point 25 = 124 src vs ~136 base = 0.91×** (undershoot — foundation leaf, schema+enum+pure-helper, no docstring-heavy logic body unlike the SL/TP cluster's 1.3-1.6× WG-expansion). Single-leaf, no-further-split correct (T-533b separate consumer). **L-024 active control reused — clean** (mypy over staged incl. tests pre-commit; no hook surprise; 6th successful reuse). **L-018 honored** — every plan precedent claim code-cited (plan-reviewer verified all; the 'failed' vocab error was the failure-mode L-018 exists to catch, caught at Gate-1).
- **Tests**: `test_queries_lifecycle.py` (16-case pure unit table + ValueError totality guard) + `test_types.py` 13-member enum assertion + `test_0020_migration.py` (column-shape + L-003 backfill + L-012 downgrade, env-gated) + `test_0005_migration.py` element-22 L-015 pin. 2450 unit pass (+17) + 0 regressions; ruff/ruff-format/mypy-strict/bandit pre-commit Passed; L-021 4/4 vs real PG. **Commit** `d4f006d` (amended) pre-merge. **Plan** `docs/plans/T-533a.md` + split-record `docs/plans/T-533.md`. Scope-clean: no new deps/ADR/Settings/BRIEF; legacy columns byte-unchanged.

### Next session pickup

- **T-533b** (dual-write wiring, blocked-by T-533a now DONE → **unblocked**, obvious next pick — completes the in-flight T-533 split). Every state-transition WRITE site also writes `trades.lifecycle_state` via shipped `derive_lifecycle_state`/direct-enum (placement → ORDER_*/OPEN; dispatcher → PARTIALLY_CLOSED/CLOSING/CLOSED; lifecycle → TP_HIT/BREAKEVEN_SET/TRAILING_ACTIVE; reconcile/restart → ORPHANED/RECONCILED; placement-fail → FAILED), alongside unchanged legacy columns; transient states (SIGNAL_RECEIVED/ORDER_REQUESTED/ORDER_PLACED/CLOSING) become reachable. Still additive-observable (legacy authoritative; OQ-1=A). Own plan-reviewer Gate-1 cycle; **L-007/L-014/L-016 recursive-split-watch** (cross-module dual-write across ~6 execution-service modules — likely pre-emptive recursive-split by module-group, mirror T-534b→{b1,b2}/T-525a→{a1,a2}). `derive_lifecycle_state` + the enum + the L-003 cross-check pattern are the direct shipped precedents.
- Then remaining ADR-0011 hardening (operator picks): **T-527/T-528** (position sizing — §B.1 tier ladder + risk-per-SL; L-007 split-watch), **T-532** (funding fee tracking + migration 0021; integrates T-220 cumulative-delta audit). Then F5 tail: **T-519** (§20 hazard test audit — H-027/H-028/H-029 all have `**Test.**`-named tests; T-533a/T-536 have NO H-NNN [additive/emit-only diagnostics, §0.8] so not in the H-catalog audit but their named tests still run) → **T-521** (final docs) → **T-522** (close-out E1..E6 sign-off, Live-ready MVP).
- T-533a/T-533b is the in-flight split — leaf 1 (foundation) done, **T-533b is the obvious next pick** (direct unblock, all primitives shipped: enum + derive_lifecycle_state + column). The risk-mgmt / account-balance / SL-TP clusters are all COMPLETE; T-533 (lifecycle FSM) is the last big structural piece before the F5 close-out tail. Operator picks.

## 2026-05-16 (T-536 Trailing SL audit pass CLOSED — ADR-0011 SL/TP-verification cluster **FINAL task → cluster COMPLETE** (T-534+T-535+T-536); NEW `trail_audit.py` 4th APScheduler tick; F5 counter 50/59 → 51/59 [T-536 single leaf, numerator +1]; all 4 gates incl. **math-validator VERIFIED — first SL/TP-cluster task with REAL Decimal arithmetic**; NO new §20 H-NNN [emit-only diagnostic, §0.8]; noqa plan-vs-reality T-530-class; L-022 1.32× ON-TRACK no-escalation; L-024 — no hook surprise)

**F5 counter 50/59 → 51/59**: T-536 done = numerator 50→51; denominator unchanged 59 (single leaf, no split). **ADR-0011 SL/TP-verification cluster FULLY CLOSED** — T-534 (watchdog/H-028) + T-535 (overwrite/H-029) + T-536 (trail-drift, no-H-NNN) — three disjoint diagnostic/enforcement arms shipped. Remaining ADR-0011 hardening: T-527/T-528 (sizing), T-532 (funding fees), T-533 (TradeLifecycleState FSM — the big one).

### T-536 — Trailing SL audit pass (NEW `trail_audit.py` 4th APScheduler tick)

- **OQ-1=A separate stateless emit-only tick** (NOT extending sl_watchdog — cohesion: H-028 counter-stateful enforcement vs T-536 stateless diagnostic; per-tick-owns-one-concern). 3-phase conn lifecycle (Phase-A `select_position_states_for_bots`+per-trailing-trade `select_trade_fsm_params` released BEFORE Phase-B conn-free per-bot `get_positions` → Phase-C re-acquire emit; conn never held across network — mirror sl_watchdog). Per `sl_type=='trail'`+`best_price≠None`+fsm pos: `expected = _compute_trail_sl_price(...)` (**OQ-2=A reused FSM fn**, `from .lifecycle import` — zero two-impl math-drift / L-003); `drift = abs(exchange_sl−expected)/expected > tolerance` (strict `>`, div-guard `expected<=0` AFTER compute BEFORE divide) → emit `trail_sl_drift_detected` row + WARN; **OQ-3=A** emit-only/non-destructive, no cross-suppression vs T-535.
- **Disjoint 3-arm SL/TP seam** (the cluster's design payoff): SL removed/naked → T-534b2/H-028 (`exchange_sl is None` skip + paper short-circuit T-534a `sl_price=None`); SL modified-vs-DB-exact → T-535/H-029 (FSM-site); SL drift-vs-recomputed-tolerance → T-536 (periodic). Independent tick/site/trigger-basis; a genuinely-wrong SL may legitimately raise >1 (operator triages).
- **NO new §20 H-NNN (the single biggest plan call — adjudicated sound)**: ADR-0011 §Anticipated-hazards reserved slots ONLY for H-027/028/029 (T-525/534/535); T-536 has no slot + is emit-only diagnostic (no capital-safety enforcement — neither closes nor re-asserts; lifecycle FSM stays authoritative) → §0.8 anti-hypothetical forbids a speculative catalog entry. The SL/TP cluster's only task without a §20 allocation — correctly (diagnosis not enforcement). NO BRIEF edit. Behaviour pinned by named tests only.
- **plan-vs-reality noqa correction (T-530/T-534b1 class, L-018 — NOT a new lesson)**: plan specified `# noqa: PLC2701` per L-013 convention, but PLC2701 not enabled in this repo's ruff config → `ruff --fix` removed it as RUF100 unused-noqa; the private sibling import `from .lifecycle import _compute_trail_sl_price` is clean WITHOUT noqa here (L-013 noqa convention is ruff-config-scoped, not repo-wide). Followed actual repo config over plan-assumed convention; OQ-2=A decision unchanged; corrected in-body in `docs/plans/T-536.md` (Status note + 5 spots); drift-checker verified sound.
- **All 4 gates**: plan-reviewer APPROVE single-pass (8 WG; all 6 scrutiny points sound incl. no-H-NNN + Gate-4 hand-verified + disjoint-seam-complete; 1 citation slip corrected) → drift-checker ON TRACK (app src **262 < ~300 L-022 tripwire — NO escalation**, contrast T-535 151≥143 fired; 262/~200≈**1.32×** = same-session T-534b2 plan-faithful WG-docstring ratio, cluster-calibrated 230-290 band, ≪400 cap; 8/8 WG; lifecycle.py NOT modified — imported only; NO BRIEF edit; no scope creep) → brief-reviewer SHIP (8/8 WG, §N1-N10, §0.3 no waiver [below tripwire], §20 no-H-NNN correct, noqa plan-vs-reality accurate) → **math-validator VERIFIED** (first SL/TP-cluster REAL Decimal arithmetic; exhaustive: reuse-not-reimpl, div-guard ordering, strict-`>` boundary `drift==tol`→no-emit, Decimal scale `"99.500"` not `"99.5"`, no float(), hand-table↔impl 1:1, `_compute_trail_sl_price` NOT patched = L-003 cross-check).
- **L-022 calibration point 24 = 1.32×** (cluster: T-534b2 1.32× / T-535 1.59× / T-536 1.32× — plan-faithful WG-docstring expansion, stable; below tripwire, no waiver needed). **L-024 active control reused — clean** (5th successful reuse after T-534a/b1/b2/T-535; no hook fix-and-recommit). **L-021 N/A** (no SQL/migration — query helpers reused unmodified).
- **Tests** ~17 new `test_trail_audit.py` (drift-emit + JSON-native pins + expected `"99.500"`/sell `"100.500"` / within-tol + exact no-emit / strict-`>` boundary / sell formula / non-trail / best_price-None / fsm-None / pos-None+flat / exchange_sl-None / 5× error-taxonomy / expected<=0 div-guard / paper / no-live) + L-015 sibling `test_app_factory` 4-job; 2433 unit pass (+18) + 0 regressions; ruff/ruff-format/mypy-strict/bandit pre-commit Passed. **Commit** `dbae961` pre-merge. **Plan** `docs/plans/T-536.md`. Scope-clean: no new deps/migration/query/type/ADR; lifecycle.py imported only (no silent refactor).

### Next session pickup

- **ADR-0011 SL/TP-verification cluster (T-534/T-535/T-536) FULLY CLOSED.** Remaining ADR-0011 hardening (operator picks): **T-527** (§B.1 sizing block — tier ladder + max_notional + altcoin cap; L-007 split-watch — pre-emptive split likely T-527a/b), **T-528** (risk-per-SL sizing; `sizing.method` discriminator coexists with T-527; L-007 split-watch), **T-532** (funding fee tracking — Bybit `/v5/asset/transaction-log` SETTLEMENT + migration 0020; integrates T-220 cumulative-delta audit), **T-533** (named-state `TradeLifecycleState` enum refactor — LARGEST hardening task / critical-path bottleneck per ADR-0011; L-007/L-014/L-016 split-watch + migration + state population across modules). Then F5 tail: **T-519** (§20 hazard test audit — must now cover H-027 [T-525a1] + H-028 [T-534b2] + H-029 [T-535]; T-536 has NO H-NNN [emit-only, §0.8] so not in the H-catalog audit but its named tests still run; all H-001..H-036 must have passing `**Test.**`-named tests) → **T-521** (final docs) → **T-522** (close-out E1..E6 sign-off, Live-ready MVP).
- T-533 (FSM enum) is the critical-path bottleneck per ADR-0011 — tackling it sooner de-risks the F5 tail; T-527/T-528 (sizing) independent + L-007 split-watch; T-532 (funding fees) migration-bearing. The risk-mgmt (T-524/525*/526) + account-balance (T-530/531) + SL/TP (T-534/535/536) clusters are all COMPLETE — the derive-from-trades / APScheduler-tick / emit-only-diagnostic / disjoint-seam patterns are mature precedents for the remainder. Operator picks.

## 2026-05-16 (T-535 SL overwrite protection CLOSED — ADR-0011 SL/TP-verification cluster; FSM-site `_detect_sl_overwrite` in lifecycle.py + formal H-029 §20 allocation; F5 counter 49/59 → 50/59 [T-535 single leaf, numerator +1]; all 4 gates incl. math-validator VERIFIED comparison-only/existing-math-bit-identical; L-022 1.59× tripwire operator-WAIVED; L-024 applied — no hook surprise)

**F5 counter 49/59 → 50/59**: T-535 done = numerator 49→50; denominator unchanged 59 (single leaf, no split). SL/TP-verification cluster: **T-534 ✓ + T-535 ✓; T-536 (trailing SL audit pass) remains** + T-533 (TradeLifecycleState FSM) the big one.

### T-535 — SL overwrite protection (+ formal H-029 §20 allocation)

- **OQ-1=A FSM-site** (NOT the T-534b2 watchdog / NOT a new tick): NEW `_detect_sl_overwrite` pure-DI helper in `services/execution/app/lifecycle.py` + 1-line call site in `run_position_monitor_for_trade` at tick-start (after `update_position_state_monitor_tick`, before `select_trade_fsm_params`). Reads `adapter.get_positions(symbol)` → match-by-symbol → compare exchange `Position.sl_price` (T-534a) vs DB `ps.sl_price` (**OQ-3=A exact `Decimal` equality**, no tolerance). Mismatch ⇒ emit one `sl_overwrite_detected` `trading_events` row + WARN; **OQ-2=A** emit-only/non-destructive — FSM continues, its own next BE/trail re-asserts the bot's SL.
- **H-029 false-positive guarantee = structural** (the headline): the lifecycle monitor is the *single* per-trade task + the *only* in-FSM SL writer + its tick is strictly sequential (`set_trading_stop` then `update_position_state_sl` complete within a tick before the next tick's compare) → a legitimate trail/BE update leaves exchange==DB at the next tick-start → no false-positive (exact equality correct, no tolerance needed). Failed/transient `get_positions` (taxonomy verbatim `(AuthError,OrderRejected,NetworkTimeout,RateLimitError,UnknownState)` = sibling BE/trail) = uncertainty → ERROR + return, NEVER emit (H-028 sibling guard). Disjoint from T-534b2/H-028 via the `exchange_sl is None → return` removal-vs-modification seam (removed/naked→watchdog/emergency-close; modified→T-535 audit; matching→neither; gone→T-221). Paper short-circuits via T-534a paper `sl_price=None` (code-cited, no `paper_bot_ids` plumbing needed).
- **Formal H-029 §20 catalog allocation** (§0.8 — slot reserved ADR-0011, allocated at impl task; entry between H-028/H-030 + numbering-note "reserved"→"formally allocated 2026-05-16 at T-535" + test-NAME pin, no `@pytest.mark.hazard` — mirror T-525a1 H-027 / T-534b2 H-028 mechanism EXACTLY). L-013 payload JSON-native (`trade_id` int, SL prices `str(Decimal)`; `insert_trading_event` reused unmodified, no `_to_jsonable`). **§N9** "protocol fixed (no thresholds)" honoured — exact equality, NO Settings knob (deliberate, not an L-001 omission; contrast T-534b2's 2 knobs).
- **All 4 gates**: plan-reviewer APPROVE **single-pass** (5 WG; all code-citations verified accurate [unlike T-534b2's 2 imprecisions — none here]; H-029 single-task analysis ruled sound; disjoint split clean; §N9/§0.8 compliant; 1 L-022 LOC-calibration CONCERN) → drift-checker **DRIFT-verdict → adjudicated plan-faithful calibration miss** (lifecycle.py **+151 src vs ~95 base = 1.59×** = pre-acknowledged same-session T-534b1 WG-docstring-expansion ratio; all 6 drift checks PASS; ZERO scope creep / ZERO unenumerated code; ≪400 §0.3 cap; the +56 over base is entirely WG#2/#1-mandated reference-dense docstring + enumerated inline rationale) → brief-reviewer SHIP (5/5 WG, §N1-N10, §0.3 151≪400 no over-cap waiver, §20 §0.8) → **math-validator VERIFIED** (Gate-4 IN scope but **comparison-only / zero NEW arithmetic** — Decimal equality + None-guards; **existing `_compute_be_sl_price`/`_compute_trail_sl_price`/`_check_be_trigger`/`running_pnl`/MFE-MAE math bit-identical**, diff = 4 purely-additive hunks; hand truth-table ↔ impl 1:1; per fix(T-218b) bit-identical-preservation precedent).
- **L-022 1.59× tripwire OPERATOR-WAIVED 2026-05-16**: the plan's authoritative tripwire (src ≥ ~143 → escalate) fired at 151; drift-checker adjudicated plan-faithful (not scope drift); per L-022/L-016 active control surfaced to operator as a one-line waive/split decision → **operator chose waive** (plan-faithful, ≪cap, no split — single pure-DI leaf, not L-007 replay/FSM profile; plan-reviewer's standing L-022 CONCERN + plan APPROVE pre-acknowledged the exact 1.59× ratio). Recorded in commit body for audit trail. **L-022 calibration point 23 = 1.59×** (= T-534b1 exactly; non-tiny-base WG-docstring expansion is the stable SL/TP-cluster signature: T-534a 4.2× tiny-base / T-534b1 1.59× / T-534b2 1.32× / **T-535 1.59×**).
- **L-015 generalized to unit-test helper**: `_build_args` defaults `get_positions=AsyncMock(return_value=[])` via `isinstance`-guard (the call site adds a per-tick get_positions → existing monitor-body tests would break; empty list → `_detect_sl_overwrite` returns before any emit, undistorted; tests pre-setting own get_positions preserved). Mirror T-531/T-534b2 L-015-generalized pattern. **L-024 active control reused — clean** (mypy over staged incl. tests pre-commit, no hook fix-and-recommit; 4th successful reuse after T-534a/T-534b1/T-534b2).
- **Tests** 7 new `_detect_sl_overwrite` unit + 1 call-site monitor + `_build_args` L-015 fix; 2415 unit pass (+12) + 0 regressions; ruff/ruff-format/mypy-strict/bandit pre-commit Passed; **L-021 N/A** (no SQL/migration — `insert_trading_event` reused unmodified). **Commit** `8c29c52` pre-merge. **Plan** `docs/plans/T-535.md`. Scope-clean, no new deps/migration/Settings/query/type/ADR; existing set_trading_stop/BE/trail/arithmetic untouched (no silent refactor).

### Next session pickup

- **SL/TP-verification cluster: T-534 + T-535 done; T-536 (trailing SL audit pass) remains.** Remaining ADR-0011 hardening (operator picks): **T-536** (trailing SL audit — sibling of T-534/T-535; ADR-0011:54 "T-534 + T-536 share APScheduler infra" — the just-shipped `sl_watchdog.py` tick + the `_detect_sl_overwrite` lifecycle pattern + L-024 control are direct precedents; likely a periodic-audit-tick or lifecycle-monitor extension), **T-527/T-528** (position sizing — §B.1 tier ladder + risk-per-SL + `sizing.method` discriminator; L-007 split-watch — pre-emptive split likely T-527a/b), **T-532** (funding fee tracking; Bybit `/v5/asset/transaction-log` SETTLEMENT + migration 0020; integrates T-220 cumulative-delta audit), **T-533** (named-state `TradeLifecycleState` enum refactor — LARGEST hardening task / critical-path bottleneck; L-007/L-014/L-016 split-watch + migration + state population). Then F5 tail: **T-519** (§20 hazard test audit — must now cover H-027 [T-525a1] + H-028 [T-534b2] + **H-029 [T-535, this session]**; all H-001..H-036 have passing `**Test.**`-named tests) → **T-521** (final docs) → **T-522** (close-out E1..E6 sign-off, Live-ready MVP).
- T-536 is the closest structural sibling (completes the SL/TP sub-cluster) — the obvious continuation; T-533 (FSM enum) is the big critical-path one; T-527/T-528 (sizing) independent. Operator picks.

## 2026-05-16 (T-534b2 SL watchdog APScheduler tick + formal H-028 §20 allocation CLOSED — recursive-split leaf 2/2 of T-534b; **T-534 periodic SL watchdog cluster COMPLETE**; F5 counter 48/59 → 49/59 [numerator +1, denom unchanged — T-534b2 already a leaf]; all 4 gates incl. math-validator VERIFIED zero-arithmetic; L-024 applied — no hook surprise; L-022 calibration point = 1.32×)

**F5 counter 48/59 → 49/59**: T-534b2 done = numerator 48→49; denominator unchanged 59 (T-534b2 was already a leaf since the T-534b recursive split — no new split). **T-534 cluster COMPLETE**: T-534a (`Position.sl_price`) + T-534b1 (`emergency_close_tracked_position` helper) + T-534b2 (watchdog tick) all shipped — mid-session naked-position protection fully live. Mirrors T-525a→{a1,a2} recursive-split precedent EXACTLY (infra/primitive leaf → consumer leaf, each own Gate-1 cycle).

### T-534b2 — SL watchdog APScheduler tick (+ formal H-028 §20 allocation)

- **3rd execution-service scheduled job** (sibling T-220b P&L audit + T-531 equity tick; shares lifespan-owned `AsyncIOScheduler`). NEW `services/execution/app/sl_watchdog.py` `run_sl_watchdog_tick`. **OQ-1=A per-bot loop** mirror `restart.py:66-93` — the data model forces per-bot (counter keyed `(bot,symbol)`, helper takes per-bot `ps_row`, paper-skip per-bot); APScheduler wiring mirrors T-531. `live_bot_ids = sorted(adapters) − paper_bot_ids` (OQ-4=A live/testnet-only via `paper_bot_ids`/H-031) → one `select_position_states_for_bots` → per-bot `get_positions` match-by-symbol → matched+SL-missing advances `(bot,symbol)` counter → Nth consecutive (default 3) → `emergency_close_tracked_position` (T-534b1) + post-fire pop.
- **H-028 false-positive guard (the headline; OQ-3=A)**: transient `(NetworkTimeout|RateLimitError|AuthError)` warn / any other `Exception` error → both `continue` WITHOUT `bots_observed.add` = *no observation* → counter neither incremented nor reset nor pruned (streak survives a transient blip); job never raises (other bots still verified, next tick still fires). Counter advances ONLY on a successful `get_positions` returning a matched + SL-missing state. **OQ-2=A ∧ OQ-3=A derived prune** (baked, plan-reviewer-verified sound deduction NOT a new decision): end-of-tick `stale = [k for k in counters if k[0] in bots_observed and k not in seen_eligible]` — drops observed-ineligible only (SL-restored inline `pop`; flat/absent/orphan-ex here); errored-bot keys preserved. Orphan-exchange/orphan-DB defer-`info`-log only (OQ-B=A — T-221 reconcile territory, watchdog never reconciles).
- **Formal H-028 §20 catalog allocation** (§0.8 — slot reserved by ADR-0011, allocated at the implementing task; mechanism = §20 catalog entry inserted between H-027/H-030 + H-numbering-note update + test-NAME pin, NO `@pytest.mark.hazard` marker [grep-verified no such convention] — mirror T-525a1 H-027 mechanism EXACTLY).
- **All 4 gates**: plan-reviewer APPROVE **single-pass** (0 BLOCKER, 5 WG; prune-derivation verified sound; 2 low-materiality L-018 citation imprecisions [false "BotId already imported" + ambiguous `execution.py` path] corrected in-plan before save) → drift-checker ON TRACK (**246 src vs ~186 plan = 1.32×** — below L-022 1.5×≈279 escalation tripwire AND ≪ §0.3 400 cap; adjudicated plan-faithful WG#2 reference-dense docstring density, NOT scope creep; no metric/migration/query/type/ADR creep) → brief-reviewer SHIP (5/5 WG, §N1-N10, §0.3 no over-cap waiver needed, §20 §0.8-compliant) → **math-validator VERIFIED** (Gate-4 IN scope `services/execution/` but zero-arithmetic comparison/counter-only — only Decimal/int comparisons + an int counter increment, no `float()`, no silent Decimal→float; hand truth-table ↔ impl; T-534b1/T-534a/T-531 zero-arithmetic precedent).
- **L-022 calibration point 22 = 1.32×** (src 246 vs plan ~186): T-526 1.92× / T-524 0.77× / T-525a1 1.10× / T-525a2 0.89× / T-525b 1.02× / T-530 0.89× / T-531 0.97× / T-534a 4.2× (tiny-base) / T-534b1 1.59× / **T-534b2 1.32×** — WG#2 (keep docstring reference-dense not prose-expanded) held it below the same-cluster T-534b1 1.59×, under the §0.3 cap with comfortable headroom. **L-024 active control reused — clean** (mypy over staged incl. tests pre-commit; no hook fix-and-recommit cycle; 3rd successful reuse after T-534a/T-534b1). **L-021 N/A** (no SQL/migration — `select_position_states_for_bots` reused unmodified).
- **Tests** 12 new (`test_sl_watchdog.py`: threshold-fire H-028 pin + transient-no-increment H-028 pin ×3 parametrized + broad-error + SL-restored-reset + paper-skip + no-live-bots + orphan-ex-defer + flat-prune + prune-skips-errored-bot + sub-threshold) + L-015 sibling `test_app_factory` `test_daily_report_...` 2→3; 2403 unit pass (+12) + 0 regressions; ruff/ruff-format/mypy-strict/bandit pre-commit Passed. **Commit** `f513fde` pre-merge. **Plan** `docs/plans/T-534b2.md` + split-record `docs/plans/T-534b.md`. Scope-clean, no new deps/ADR/migration; H-028 NEW, 0 §20 regression.

### Next session pickup

- **T-534 cluster DONE.** Remaining ADR-0011 hardening cluster (operator picks): **T-527/T-528** (position sizing — §B.1 tier ladder + risk-per-SL + `sizing.method` discriminator; L-007 split-watch — pre-emptive split likely T-527a/b), **T-532** (funding fee tracking — Bybit `/v5/asset/transaction-log` SETTLEMENT + migration 0020; integrates T-220 cumulative-delta audit), **T-533** (named-state `TradeLifecycleState` enum refactor — LARGEST hardening task / critical-path bottleneck; L-007/L-014/L-016 split-watch + migration + state population), **T-535** (SL overwrite protection — H-029 anticipated/reserved per ADR-0011), **T-536** (trailing SL audit pass). Then F5 tail: **T-519** (§20 hazard test audit — now must cover H-027 [T-525a1] + **H-028 [T-534b2, this session]**; all H-001..H-036 have passing `**Test.**`-named tests) → **T-521** (final docs) → **T-522** (close-out E1..E6 sign-off, Live-ready MVP).
- The SL/TP-verification sub-cluster (T-534/T-535/T-536) is partially done — T-534 complete; T-535 (SL overwrite, H-029) + T-536 (trailing SL audit) remain. T-534b1's `emergency_close_tracked_position` + the `sl_watchdog.py` tick pattern + L-024 active control are direct precedents for T-535/T-536. T-527/T-528 (sizing) independent; T-533 (FSM enum) is the big one. Operator picks.

## 2026-05-15 (T-534b RECURSIVE SPLIT L-007/L-022 → T-534b1 `emergency_close_tracked_position` helper CLOSED + T-534b2 pending; plan-vs-reality no-decorator correction T-530-class; F5 counter 47/58 → 48/59 [recursive split denom +1, numerator +1]; all 4 gates incl. math-validator VERIFIED zero-arithmetic; L-024 applied — no hook surprise)

**F5 counter 47/58 → 48/59**: T-534b recursive L-007/L-022 split (plan-reviewer Gate-1 on consolidated T-534b plan → REVISE: §0.3/L-022 cap-proximity ~320-410, same profile as T-525a ~436) → T-534b1 + T-534b2; denominator 58→59 (+1, T-534b 1-slot → 2 leaves); T-534b1 done → numerator 47→48. T-534b2 (watchdog tick, blocked-by T-534b1) pending — now unblocked. Mirrors T-525a→{a1,a2} recursive-split precedent EXACTLY.

### T-534b1 — `emergency_close_tracked_position` shared helper

- **Recursive-split leaf 1/2 of T-534b**; T-530/T-534a-shaped no-caller leaf. NEW undecorated `async` helper in `placement_persist.py`: reduce_only opposite-side flatten (`ps_row.remaining_qty`) + `UnknownState` graceful (H-003) + atomic `conn.transaction()` { `update_trade_close`(close_reason='emergency') + `delete_position_state` + `insert_trading_event`(event_type='sl_watchdog_emergency_close') } + single `OrderClosed` emit (one try/except, no for-loop, no OrderPlaced). committed `CorrelationId(f"sl-watchdog-{bot_id}-{symbol}")` built once = sole envelope correlation_id + str-cast in-tx audit. Verbatim-mirror `restart._close_orphan_db` tx + `emergency_close` graceful/emit/H-012-placeholder (NOT placement-time `emergency_close` — OQ-A=A signature mismatch). NO caller (T-534b2 first consumer).
- **Plan-vs-reality correction (T-530 precedent class) — the headline**: plan AC#1/WG#7 said `@non_idempotent` + `is_non_idempotent`-pin "mirror emergency_close" — but `emergency_close` (placement_persist.py:180) is **UNDECORATED**. placement_persist orchestration helpers carry NO idempotency marker; §N3 "every external write annotated" is satisfied on the *composed leaf primitives* (`place_market_order`/`update_trade_close`/`delete_position_state`/`insert_trading_event` — all `@non_idempotent`). Followed the ACTUAL precedent (no decorator) + documented why in the docstring + replaced the planned pin with `test_tracked_close_marker_mirrors_undecorated_emergency_close` (pins BOTH helpers `is_idempotent==False` AND `is_non_idempotent==False` — stronger than the planned single-pin). §N3 NOT regressed (brief-reviewer + math-validator independently confirmed). Same lesson family as T-530 ("plan §Scope text factually inaccurate vs actual precedent → followed actual precedent + documented") — L-018 active control already covers; NOT a new L-lesson.
- **All 4 gates**: plan-reviewer APPROVE pass-2 (pass-1 REVISE 1 BLOCKER [fresh CorrelationId "confirm later" in consolidated plan → committed deterministic form] + 2 CONCERN [insert_trading_event inside tx = atomic close+delete+audit; single-emit no for-loop scaffold]) → drift-checker ON TRACK (src **151 vs plan ~95 = 1.59×** — L-022 active-control ≥1.5× escalation trigger; drift-checker adjudicated plan-faithful WG/plan-vs-reality docstring expansion, ≪400 cap, < T-534a 4.2× precedent; ratio formally noted to operator, no action needed) → brief-reviewer SHIP (WG 7/7, §N3-correction independently verified) → **math-validator VERIFIED** (zero arithmetic exhaustively — every numeric is `Decimal("0")` H-012 placeholder or `ps_row.remaining_qty` pass-through; no silent Decimal→float; fixtures hand-authored).
- **L-022 calibration**: src 151 vs plan ~95 = 1.59× (plan-faithful — the recursive split deliberately put this ≪400; the over-estimate is WG#1 deviation-rationale + the *unplanned* no-decorator-rationale docstring [plan wrongly assumed a decorator]). NOT a cap concern. **L-024 active control reused — clean** (mypy over staged incl. tests pre-commit, no hook failure; 2nd successful reuse after T-534a).
- **Tests** 7 new; 2391 unit pass (+7) + 0 regressions; ruff+mypy strict clean; **L-021 N/A** (no migration/DB). **Commit** `195f7a6` pre-merge. **Plan** `docs/plans/T-534b1.md` + split-record `docs/plans/T-534b.md`. Scope-clean, no new deps/ADR/H-NNN.

### Next session pickup

- **T-534b2** (SL watchdog APScheduler tick — 3rd execution-service scheduled job; blocked-by T-534b1 now DONE → **unblocked**, obvious next pick: direct unblock, all primitives shipped — `Position.sl_price` [T-534a] + `select_position_states_for_bots` + `emergency_close_tracked_position` [T-534b1]). Carries: H-028 in-memory consecutive-threshold N=3 (transient `(NetworkTimeout|RateLimitError|AuthError)` skip-no-increment; SL-found reset; OQ-B=A size==0/absent → defer-log no-reconcile) + **formal H-028 §20 catalog allocation** (mirror T-525a1 H-027 mechanism: §20 entry + `**Test.**` pin, §0.8-compliant) + `execution_sl_watchdog_tick_interval_seconds=300` + `execution_sl_watchdog_missing_threshold_ticks=3` (§N9/L-001) + live/testnet-only (paper skip via `AdapterPoolResult.paper_bot_ids`, mirror H-031) + main 3rd `scheduler.add_job` (L-023 cast) + sibling test_app_factory 2→3 add_job (L-015 generalized) + `sl_miss_counters` in-memory app.state/DI (§N6). Mirror T-531/T-220 tick + T-534b1 helper + L-024 active control. Own plan-reviewer Gate-1 cycle (mirror T-525a1→T-525a2 sequence). math-validator Gate-4 in-scope (likely VERIFIED fast — counter int compare, no Decimal math).
- Then remaining ADR-0011 cluster (operator picks): **T-527/T-528** (sizing, L-007 split-watch), **T-532** (funding fees, migration), **T-533** (TradeLifecycleState FSM — largest, L-007/L-014/L-016 split-watch), **T-535/T-536** (SL overwrite H-029 anticipated / trailing SL audit) → F5 tail **T-519** (§20 hazard test audit) → **T-521** (final docs) → **T-522** (close-out E1..E6 Live-ready MVP sign-off).
- T-534b1/T-534b2 is the natural sub-cluster in flight — leaf 1 (helper) done, **T-534b2 is the obvious next pick**. Operator picks.

## 2026-05-15 (T-534 SPLIT L-007 → T-534a `Position.sl_price` adapter-protocol extension CLOSED + T-534b pending; OQ-5=b PositionEvent decouple; F5 counter 46/57 → 47/58 [denominator +1 split, numerator +1]; all 4 gates incl. math-validator VERIFIED; L-024 active control applied — no hook surprise)

**F5 counter 46/57 → 47/58**: T-534 L-007 split (operator OQ-1=A) → denominator 57→58 (+1, T-534 1-slot → T-534a/T-534b 2 leaves); T-534a done → numerator 46→47. T-534b (SL watchdog tick, blocked-by T-534a) pending. Structure deliberately mirrors the shipped T-530→T-531 (protocol/type-extension leaf → consumer leaf).

### T-534a — `Position.sl_price` adapter-protocol extension

- **L-007 leaf 1/2 of T-534**; T-530-shaped. `sl_price: Decimal | None` last `Position` frozen+slots field (no default; Position docstring sanctions) + Bybit `_map_position_row` decode `/v5/position/list` `stopLoss` (`Decimal(str(...))` no-float + `_sl>0` guard; blank/`"0"`/`"0.00"`/non-positive = no-SL sentinel → None, **deliberate divergence from sibling `avgPrice "0"`-preserve W#3**) + paper `sl_price=None` (OQ-4=A paper-skip).
- **OQ-5=b decouple (the headline architectural call)**: `PositionEvent` was field-mirrored to `Position` (+occurred_at), enforced by 2 hard parity tests. Adding `sl_price` to Position alone breaks that invariant. Operator chose **(b) decouple**: `sl_price` is REST-snapshot-only (T-534b watchdog polls `get_positions()`, no WS-stream SL consumer → §0.8 forbids a consumer-less PositionEvent field). PositionEvent dataclass byte-for-byte UNCHANGED; only its docstring rewritten as the decision anchor. First intentional divergence of the two types; no full ADR (single field/one seam/T-cluster — mirror T-530 AccountBalance-without-ADR; §6.7 ADR reserved for cross-cutting/brief-deviation, this is §0.8 compliance).
- **L-015 generalized closed list** (plan-reviewer pass-1 forced explicit enumeration, not write-time): `test_types.py` field-set rename `…minimal_seven_with_sl_price` + PositionEvent parity test rewrite-to-explicit-7-set + rename (HARD-FAIL) vs `test_paper_parity.py:181` rename/redocstring only (GREEN-stale — uses explicit `expected` set, PositionEvent unchanged → still passes) + mechanical ctor adds + flat-semantic pins. analytics `sl_price` + `PositionStateRow` correctly out-of-scope (separate seams).
- **All 4 gates**: plan-reviewer APPROVE pass-2 (pass-1 REVISE 2 BLOCKER [PositionEvent parity invariant; L-015 deferred] + 2 CONCERN) → drift ON TRACK (42 src = plan-faithful WG-docstring expansion ≪400 cap) → brief SHIP (WG 5/5) → **math-validator VERIFIED** (zero arithmetic; `Decimal(str)` no-float; scale-zero `Decimal("0.00")>0==False` hand-verified; fixtures hand-authored).
- **L-024 active control applied this cycle**: ran `mypy` over the STAGED set incl. tests before commit → caught nothing (clean) → **no pre-commit-hook fix-and-recommit cycle** (contrast T-531 which discovered L-024 via a hook failure). L-024 working as designed on first reuse.
- **L-022**: no calibration point (T-534a logic ~10 src, total 42 with WG docstrings — plan explicitly budgeted "~10 + docstring lines"; not a logic-estimate-vs-reality data point). **Tests** ~8 changed/new; 2384 unit pass (+5 parametrized golden) + 0 regressions; ruff+mypy strict clean; **L-021 N/A** (no migration/DB). **Commit** `fe631d4` pre-merge. **Plan** `docs/plans/T-534a.md`. Scope-clean, no new deps/ADR/H-NNN.

### Next session pickup

- **T-534b** (SL watchdog APScheduler tick — 3rd execution-service scheduled job, mirror T-220/T-531 pattern; blocked-by T-534a now DONE → **unblocked**). Reuses `select_position_states_for_bots` + `placement_persist.emergency_close` + `adapter.get_positions().sl_price` (just shipped). Carries: H-028 in-memory consecutive-threshold N=3 (transient `(NetworkTimeout|RateLimitError|AuthError)` skip-no-increment, SL-found reset) + **formal H-028 §20 catalog allocation** (mirror T-525a1 H-027 mechanism: §20 entry + `**Test.**` pin, §0.8-compliant) + `execution_sl_watchdog_tick_interval_seconds=300` + `execution_sl_watchdog_missing_threshold_ticks=3` (§N9/L-001) + live/testnet-only (paper skip). L-024 active control + the T-531 equity_snapshot.py tick + the T-220 audit.py two-tier resilience are direct precedents. Own plan-reviewer Gate-1 cycle; math-validator Gate-4 in-scope (no arithmetic but threshold/emergency_close decision logic — likely VERIFIED fast).
- Then remaining ADR-0011 cluster (operator picks): **T-527/T-528** (sizing, L-007 split-watch), **T-532** (funding fees, migration), **T-533** (TradeLifecycleState FSM — largest, L-007/L-014/L-016 split-watch), **T-535/T-536** (SL overwrite protection H-029 anticipated / trailing SL audit) → F5 tail **T-519** (§20 hazard test audit) → **T-521** (final docs) → **T-522** (close-out E1..E6 Live-ready MVP sign-off).
- T-534a/T-534b is the natural sub-cluster in flight — T-534a (top-of-DAG) done, **T-534b is the obvious next pick** (direct unblock, all primitives shipped). Operator picks.

## 2026-05-15 (T-531 equity snapshot hypertable + APScheduler tick + virtual_balance{bot_id} gauge CLOSED — ADR-0011 account-balance/equity sub-cluster; first consumer of T-530; migration 0019 + execution-service tick; F5 counter 45/57 → 46/57; all 4 gates incl. math-validator VERIFIED; NEW L-024; L-022 point 20 = 0.97×)

**F5 counter 45/57 → 46/57**: T-531 done = numerator +1; denominator unchanged 57 (T-531 leaf, no split). **T-530/T-531 account-balance/equity sub-cluster COMPLETE** (top-of-DAG T-530 → consumer T-531 both shipped).

### T-531 — equity snapshot hypertable + APScheduler tick + virtual_balance{bot_id} gauge

- **Host = execution-service** (owns `AsyncIOScheduler` per ADR-0007 D1/D2 + adapter pool + `/metrics`). Equity tick = **sibling scheduled job to T-220 P&L audit loop** → **NO new dependency** (apscheduler already pinned per ADR-0007 D5; prometheus_client already used). First consumer of T-530 `get_account_balance()`.
- **Migration 0019** (NOT stale TASKS/ADR-0011 "0016" — 0016 outbox / 0017 symbol_map / 0018 kill_switch already landed; key plan-stage catch) `bot_equity_snapshots` hypertable: 8 cols, surrogate `id` + composite PK `(snapshot_at,id)`, 5× `Numeric(20,4)` USD-money (mirror 0009 trade_pnl_deltas / 0006 `running_pnl`; NOT 0007/0009 "no PK" — plan-reviewer pass-1 BLOCKER corrected that factual error), 7d chunk, bot_snapshot DESC idx, symmetric downgrade, no FK (hypertable-sibling).
- `packages/db/queries/equity.py` `@non_idempotent` writer (L-021 N/A all column-direct, own `_DbExecutor` alias) + `services/execution/app/metrics.py` (**first execution-service service-metric** + `virtual_balance` Gauge, no `_total`) + `equity_snapshot.py` tick (snapshot_at-once, 1 fetch/sub-account → per-bot fan-out, two-tier resilience mirror audit.py) + main.py wiring (L-023 `cast` NOT inline ignore) + config interval (§N9/L-001 default 300s) + sibling test_app_factory.py fix (L-015 generalized to unit test).
- **Operator OQs (4, all default A)**: gauge=`total_equity`; interval 300s env-configurable; persist 5 totals; mirror audit.py fan-out (shared live sub-account → N identical account-level rows, surrogate-id-distinct).
- **Gate-4 numeric boundaries documented**: (a) unbounded T-530 Decimal → `Numeric(20,4)` PG round-half-even at INSERT (monitoring, NOT P&L-truth = T-220 cumulative-delta audit); (b) sole `float()` at gauge `.set()` from pre-persist in-memory `bal` (gauge/row may diverge 5th+ decimal). Zero arithmetic in T-531 (records T-530 1:1).
- **All 4 gates**: plan-reviewer APPROVE **3-pass** (pass-1 2 BLOCKER [schema "no PK" factual error → 0007/0009 synthetic id+composite PK; L-023 inline-ignore at new closure] + 3 CONCERN; pass-2 1 BLOCKER [bare `Numeric()` vs repo `Numeric(20,4)` USD-money] + 1 CONCERN [scale-4 truncation doc]; pass-3 APPROVE 5 WG) → drift-checker ON TRACK (297 src 0.97×; persist_failed = faithful audit.py two-tier mirror) → brief-reviewer SHIP (§N1-N10, 0 §20, WG 5/5) → **math-validator VERIFIED** (truncation 125000.12345678→125000.1235 round-half-even hand-verified + confirmed on live PG; L-005 precision pin).
- **L-022 calibration point 20 = ~0.97×** (src 297 vs plan ~305): T-526 1.92× / T-524 0.77× / T-525a1 1.10× / T-525a2 0.89× / T-525b 1.02× / T-530 0.89× / **T-531 0.97×** — first-of-its-kind metrics.py scaffold contingency correctly absorbed; L-022 stable.
- **NEW L-024**: pre-commit mypy hook type-checks test files; local source-only mypy missed typed-AsyncMock `.await_args` `[union-attr]` + dead `[unused-ignore]` → one fix-and-recommit cycle. Active control: run mypy over staged set INCL. tests before commit (`uv run mypy $(git diff --cached --name-only -- '*.py')`); narrow typed-mock `.await_args` with `assert ... is not None`.
- **Tests**: ~18 new; 2379 unit pass + 0 regressions; **env-gated integration 7/7 run locally on live TimescaleDB** (L-021 active control — `POSTGRES_TEST_DSN`, container `scalper-v2-postgres-1`; truncation + L-005 pin confirmed on real PG). mypy strict + ruff clean. **Commit** `78e74cc` on `feat/T-531-equity-snapshot` pre-merge. **Plan** `docs/plans/T-531.md`. Scope-clean, no new deps, no new ADR, no new H-NNN.

### Next session pickup

- **T-530/T-531 sub-cluster DONE.** Remaining ADR-0011 hardening cluster (operator picks): **T-527** (§B.1 sizing block — tier ladder + max_notional + altcoin cap; L-007 split-watch — pre-emptive split likely T-527a/b), **T-528** (risk-per-SL sizing; `sizing.method` discriminator; L-007 split-watch), **T-532** (funding fee tracking; Bybit `/v5/asset/transaction-log` SETTLEMENT + migration 0020; integrates T-220 cumulative-delta audit), **T-533** (named-state `TradeLifecycleState` enum refactor — LARGEST hardening task / critical-path bottleneck; L-007/L-014/L-016 split-watch + migration + state population), **T-534** (periodic SL watchdog APScheduler — sibling to T-220/T-531 scheduler pattern; H-028 anticipated; L-007 split-watch), **T-535** (SL overwrite protection; H-029 anticipated), **T-536** (trailing SL audit). Then F5 tail: **T-519** (§20 hazard test audit — H-027 entry + tests exist via T-525a1/a2) → **T-521** (final docs) → **T-522** (close-out E1..E6 sign-off, Live-ready MVP).
- T-534 (periodic SL watchdog APScheduler tick) is the closest structural sibling to T-531/T-220 (3rd scheduled job in execution-service) — equity_snapshot.py + the L-024 active control are direct precedents. T-527/T-528 (sizing) independent; T-533 (FSM enum) is the big one. Operator picks.

## 2026-05-15 (T-530 get_account_balance ExchangeClient port CLOSED — ADR-0011 hardening; AccountBalance value type + Bybit UNIFIED decode + paper seed+Σrealized; unblocks T-531 equity; F5 counter 44/57 → 45/57; math-validator VERIFIED; L-022 point 19 = 0.89×)

**F5 counter 44/57 → 45/57**: T-530 done = numerator +1; denominator unchanged 57 (T-530 was always a leaf, no split). First task of the T-530/T-531 account-balance/equity sub-cluster — **top-of-DAG done; T-531 now unblocked**.

### T-530 — ExchangeClient.get_account_balance() protocol extension

- **NEW `AccountBalance` frozen+slots** value type (5 Decimal: wallet/available/equity/margin/unrealized) + `@idempotent` protocol method + Bybit `GET /v5/account/wallet-balance?accountType=UNIFIED` (`list[0]` 5 totals `Decimal(str(...))` 1:1, no float) + paper `seed + Σ realized` (shipped `sum_paper_trades_realized_pnl` reuse; `unrealized_pnl=Decimal('0')` OQ-2=A no-mark-to-market limitation; 3 totals alias wallet). **NO caller** — T-531 first consumer (separate task).
- **Mirror precedent**: `get_closed_pnl_cumulative` 1:1 (sub_account validate BEFORE `limiter.acquire`, `RateLimitError → _on_rate_limit_hit("positions") + raise`, empty-list → `ExchangeError` [account-level anomaly, NOT `OrderRejected` which is order-semantic]). Shares `positions` limiter bucket (no new `EndpointGroup`).
- **Conformance test 4 precise sub-edits** (WG#1, NOT blind 12→13): the latent shipped-stale docstring "11 / 4 reads" corrected to accurate **13 = 4 writes + 6 reads + 2 streams + 1 lifecycle** in the same diff; `_UNLABELED_METHODS` stays 3; introspective tests untouched (auto-catch). L-015 sibling-test-impact handled.
- **Plan-vs-reality catch**: plan §Scope said "re-export AccountBalance via `__init__.py` mirroring InstrumentInfo" — but `__init__.py:26` re-exports only ExecutionEvent/OrderPlaceResult/Position/PositionEvent; InstrumentInfo NOT there. Followed the **actual** InstrumentInfo precedent = `types.py __all__` only, NO `__init__.py` change (plan text was a factual bug, not a scope gap — drift + brief both confirmed). protocols.py:69-71 stale group-doc deferred to `docs(exchange-protocol-groupdoc)` F5+ backlog (§0.7, not absorbed).
- **All 4 gates**: plan-reviewer APPROVE 3rd-pass (2× REVISE — L-015 conformance count + imprecise line-153 stale-prose enumeration) → drift-checker ON TRACK (src ~147 < affirmed ~166) → brief-reviewer SHIP (11/11 AC + 5/5 WG) → **math-validator VERIFIED** (Gate-4 IN SCOPE per T-525a2/b non-exhaustive-path-list precedent — Bybit golden fixture hand-computable JSON-string literal, paper helper monkeypatch-stubbed not self-ref, negative `totalPerpUPL` preserved signed, NULL→Decimal('0') verified in persistence.py:515 COALESCE).
- **L-022 calibration data point 19**: T-526 1.92× / T-524 0.77× / T-525a1 1.10× / T-525a2 0.89× / T-525b 1.02× / **T-530 ~0.89×** (src ~147 vs affirmed ~166). L-022 remains battle-tested + stable.
- **Tests**: 8 new (Bybit 5 golden-decode incl. negative `-125.2500` + L-017 bilateral sub_account pin + 1 parametrized rate-limit row; paper 3 parity incl. negative-realized 9500 + identity); pytest packages/exchange 327 passed + 41 skipped + 0 regressions; mypy strict clean; ruff clean. **Commit** `93faa9e` pre-merge. **Plan** `docs/plans/T-530.md`. Scope-clean.

### Next session pickup

- Remaining ADR-0011 hardening cluster: **T-531** (equity snapshot table + APScheduler + Prom gauge — now unblocked, first consumer of T-530 `get_account_balance()`; carries the `bot_equity_snapshots` migration + env-gated testcontainer integration), **T-527** (§B.1 sizing block — tier ladder + max_notional + altcoin cap; L-007 split-watch — pre-emptive split likely T-527a/b), **T-528** (risk-per-SL sizing; L-007 split-watch), **T-532** (funding fee tracking; migration), **T-533** (named-state `TradeLifecycleState` enum refactor — LARGEST hardening task / critical-path bottleneck; L-007/L-014/L-016 split-watch + migration), **T-534** (periodic SL watchdog; H-028 anticipated; L-007 split-watch), **T-535** (SL overwrite protection; H-029 anticipated), **T-536** (trailing SL audit). Then F5 tail: **T-519** (§20 hazard test audit — H-027 entry + tests exist via T-525a1/a2) → **T-521** (final docs) → **T-522** (close-out E1..E6 sign-off, Live-ready MVP).
- T-530/T-531 is the natural next sub-cluster — T-530 top-of-DAG is now done, **T-531 is the obvious next pick** (direct unblock, self-contained, adds migration + scheduler). T-527/T-528 (sizing) independent; T-533 (FSM enum) is the big one. Operator picks.

## 2026-05-15 (T-525b max-drawdown hard-stop CLOSED — FINAL leaf; **T-525 daily-loss+drawdown kill-switch family COMPLETE**; math-validator VERIFIED Decimal division; F5 counter 43/57 → 44/57; NEW L-023; L-022 point 18 = 1.02× on-target)

**F5 counter 43/57 → 44/57**: T-525b done = numerator +1; denominator unchanged 57 (T-525b was already a leaf since the recursive split). **T-525 family COMPLETE** — T-525a1 (substrate) + T-525a2 (daily-loss writer) + T-525b (max-drawdown writer) all merged this session. The whole daily-loss + max-drawdown kill-switch is now live behind YAML knobs.

### T-525b — max-drawdown hard-stop gate (final leaf)

- **Structural twin of T-525a2**; the 4th sibling pre-scoring gate. Chain is now **cooldown → caps → loss-limit → drawdown**. Consumes the T-525a1 substrate UNMODIFIED (kill_switch.py/reconcile imported as-is); the max_drawdown forward-compat (is_stale_daily_latch→False for max_drawdown + T-525a2 reason-agnostic block) shipped + tested in T-525a1/a2.
- **Operator OQs all default A**: OQ-A peak-to-current give-back `(peak-current)/peak` computed ONLY when peak>0 (peak≤0 → not-tripped; pure-loss is T-525a2's domain; **the guard makes the division total — no /0, no /negative, the math-validator-critical invariant**); OQ-B lifetime cumulative (all closed trades, no window — NEW window-fn `select_pnl_peak_and_current` running prefix-sum + MAX peak); OQ-C separate `drawdown_gate.py` 4th sibling trips the shared single-row latch via T-525a1 `upsert_kill_switch_trip(trip_reason='max_drawdown')` — HARD-STOP (never UTC-day-cleared).
- **Gate-4 math-validator RAN + VERIFIED again** (2nd risk-gate where it ran; T-525a2 was additive SUM, T-525b is the NEW Decimal **DIVISION** element — precision/rounding semantics SUM lacked). Hand-ran CPython 28-digit ROUND_HALF_EVEN: `(3-1)/3==Decimal("0.6666666666666666666666666667") >= 0.66` True; `(120.0000-75.0000)/120.0000==Decimal("0.375")` scale-mix exact; boundary `0.20>=0.20` True; `(100-(-50))/100==1.5` legit (give-back >100% when current<0<peak). peak>0 guard verified to STRICTLY precede the division (unreachable when peak≤0). peak=MAX(running)≥current invariant holds via byte-identical doubled WHERE. Fixtures hand-computable (+50/+70/-30/-15→running{50,120,90,75}→peak120/current75/0.375), not implementation-against-self. `ROWS UNBOUNDED PRECEDING` correct prefix-sum frame (no off-by-one).
- **All 4 gates**: plan-reviewer single-pass APPROVE → drift-checker ON TRACK (~297 core src ≈1.02× of affirmed ~290 — on-target, within ceiling ~315, far under 400) → brief-reviewer SHIP → math-validator VERIFIED.
- **NEW L-023 lesson**: an inline `# type: ignore[arg-type]` on a specific kwarg line inside a multi-line call is fragile under the ruff-format pre-commit hook (reflow migrates the comment to a different physical line → simultaneous unused-ignore + missing-ignore mypy failure → pre-commit fails → fix-and-recommit cycle). Hit T-525a2 (integration test) AND AGAIN T-525b (unit test, 18 errors). Active control: use `cast("BotId", value)` / typed local instead; plan-reviewer + brief-reviewer flag inline per-arg type:ignore on NewType-typed params across multi-site tests. (The local pre-commit mypy/ruff passed because they ran BEFORE the ruff-format hook reflowed; the hook reformats then mypy re-runs and fails — this is why it bit twice.)
- **L-022 calibration data point 18**: T-526 1.92× / T-524 0.77× / T-525a1 1.10× / T-525a2 0.89× / **T-525b ~1.02× ON-TARGET**. L-022 (recursive split + per-file docstring-inclusive anchored estimates) is battle-tested + stable across the entire 3-task T-525 family — none breached §0.3 after the lesson.
- **Tests**: 28 new; 735 passed + 0 regressions; mypy 0/90; ruff clean; integration 3/3 vs real PG locally (WG#9 — window-fn exercised). **Commit** `7a4459b` pre-merge. **Plan** `docs/plans/T-525b.md`. Scope-clean; T-526/T-524/T-525a1/T-525a2 not regressed.

### Next session pickup

- **T-525 family is DONE.** Remaining ADR-0011 hardening cluster (per `## Next`): **T-527** (§B.1 sizing block reified — tier ladder + max_notional + altcoin cap; L-007 split-watch flagged at ADR-0011 time — pre-emptive split likely T-527a/b), **T-528** (risk-per-SL sizing; coexists with T-527 via sizing.method discriminator; L-007 split-watch), **T-530** (`ExchangeClient.get_account_balance()` protocol extension — top-of-DAG for T-531), **T-531** (equity snapshot table + APScheduler + Prom gauge; blocked-by T-530; migration), **T-532** (funding fee tracking; migration), **T-533** (named-state `TradeLifecycleState` enum refactor — LARGEST hardening task; L-007/L-014/L-016 split-watch + migration + state population; ADR-0011 flagged the critical-path bottleneck), **T-534** (periodic SL watchdog APScheduler; H-028 anticipated; L-007 split-watch), **T-535** (SL overwrite protection; H-029 anticipated), **T-536** (trailing SL audit pass). Then the F5 tail: **T-519** (§20 hazard test audit — H-027 §20 entry + its **Test.**-named tests now exist via T-525a1+T-525a2; the audit will verify all H-001..H-027 have passing tests) → **T-521** (final docs) → **T-522** (close-out E1..E6 sign-off, Live-ready MVP).
- Risk-management cluster (T-524/T-525*/T-526) is the 3-task derive-from-trades + persistent-latch precedent for the remaining hardening tasks. T-530/T-531 (account balance/equity) is the natural next sub-cluster (top-of-DAG T-530 → T-531); T-527/T-528 (sizing) is independent; T-533 (FSM enum) is the big one. Operator picks.

## 2026-05-15 (T-525a2 daily-loss kill-switch GATE CLOSED — ADR-0011 risk-mgmt; trip writer + enforcement half of T-525a; reason-agnostic latch; math-validator VERIFIED Decimal P&L; F5 counter 42/57 → 43/57; L-022 calibration point 17 = 0.89× undershoot)

**F5 counter 42/57 → 43/57**: T-525a2 done = numerator +1; denominator unchanged 57 (T-525a2 was already a leaf since the recursive split). **T-525a2 = the gate (trip writer + enforcement) half of T-525a**; T-525a1 substrate shipped earlier this session.

### T-525a2 — daily-loss kill-switch gate

- **Consumes T-525a1 substrate as-is**: `kill_switch.py` (@idempotent upsert/clear + select + is_stale_daily_latch) + `kill_switch_reconcile.py` UNMODIFIED — imported. T-525a2 adds: `sum_realized_pnl_since` (today-P&L derive-from-trades) + `loss_limit_gate.py` (`check_daily_loss_limit` 9-step) + consumer chain wiring + Prom counter + fixture yaml. Chain is now cooldown → caps → loss-limit (3 sibling pre-scoring gates).
- **Operator OQ (plan-stage 2026-05-15)**: gate blocks on ANY non-stale tripped latch (reason-agnostic kill-switch — block whether the latch tripped on daily_loss_limit or future T-525b max_drawdown; no chain-order fragility). reason = state.trip_reason for a pre-existing latch, 'daily_loss_limit' on a fresh trip. Everything else carried from the T-525a OQ round / sibling precedent (silent-skip, sticky intra-day, gate-day-rollover, short-circuit-when-disabled, `<=` boundary-trips).
- **L-021 BLOCKER (from the T-525a review) discharged on 4 layers**: SQL ships `closed_at >= $2::timestamptz` verbatim + mock-test `assert "$2::timestamptz" in sql` + HARD-AC local testcontainer run (`POSTGRES_TEST_DSN` 3/3 PASS pre-push — the cast exercised vs real PG, NOT mock-only; the T-537a1 master-broke-twice anti-pattern avoided) + schema-type-anchored (`closed_at` genuinely timestamptz per migrations 0005/0008). The "resolved in-advance, NOT verify-at-impl" mandate honored.
- **Gate-4 math-validator RAN and VERIFIED** (first risk-gate task where it did — T-526/T-524 were int-compare no-ops; T-525a2 is real Decimal P&L SUM ≤ -limit). brief-reviewer ruled the CLAUDE.md Gate-4 path-list non-exhaustive when real Decimal money math is present (intent: "math correctness in financial code = capital loss"). math-validator hand-ran CPython Decimal: -105.0000<=-100 True, boundary -100.0000<=-100 True (scale-mix safe), -50.0000<=-100 False, 0<=-100 False; no float() on P&L path; Decimal(str()) fallback never Decimal(float); integration fixtures hand-computable (-40+-35+-30=-105, not implementation-against-self); sticky no-recompute precision-drift-free; UTC-day window correct.
- **All 4 gates**: plan-reviewer single-pass APPROVE (0 BLOCKERs/CONCERNs; L-021 4-layer discharge) → drift-checker ON TRACK (~259 core src undershoot ~0.89× of affirmed ~290; FAR under §0.3 400) → brief-reviewer SHIP (10/10 WG + 14/15 AC; Gate-4-must-run ruling) → math-validator VERIFIED (AC#11).
- **§N3 nuance**: the gate orchestrates the T-525a1 @idempotent upsert/clear helpers + defines no new external write → no new @idempotent marker (correct per §5.8 — markers annotate the write helper, not every caller; mirror reconcile which is itself unmarked). sum/select read-only unmarked.
- **L-022 calibration data point 17**: T-526 1.92× / T-524 0.77× / T-525a1 1.10× / **T-525a2 ~0.89× undershoot**. The recursive L-007/L-022 split (the original ~436 cap-breach caught PRE-coding at T-525a) + per-file docstring-inclusive estimates anchored to shipped actuals keep every post-L-022 task comfortably under §0.3. L-022 is battle-tested and stable.
- **Tests**: 22 new; 708 passed + 0 regressions; mypy strict 0/87; ruff clean; integration 3/3 vs real PG locally. **Commit** `061f1d3` pre-merge. **Plan** `docs/plans/T-525a2.md`. Scope-clean: kill_switch.py/reconcile/cooldown_gate/concurrent_caps_gate/migrations untouched; T-526/T-524/T-525a1 not regressed.

### Next session pickup

- **T-525b** — max_drawdown_pct (P&L-drawdown from trades; peak vs current cumulative realized P&L; NOT blocked-by-T-531 per OQ-4=A; hard-stop reset semantic). **The reconcile + gate forward-compat for max_drawdown is ALREADY shipped**: `is_stale_daily_latch` returns False for `trip_reason='max_drawdown'` (T-525a1) AND the T-525a2 gate already blocks on a non-stale `max_drawdown` latch (reason-agnostic, test #9 pins it). T-525b therefore only needs: the drawdown computation (peak vs current cumulative realized P&L) + a `max_drawdown` trip writer (its own gate or fold into the loss-limit gate's recompute path — plan-stage decides) + `max_drawdown_pct` RiskSection knob + yaml. Own plan-reviewer cycle; L-022 active control (4 calibration anchors now). math-validator Gate-4 IN scope (Decimal drawdown ratio).
- Remaining ADR-0011 cluster after T-525b: T-527, T-528, T-530..T-536 → gates T-519 (hazard audit — H-027 §20 entry + its **Test.**-named tests now exist via T-525a1+T-525a2) → T-521 (docs) → T-522 (close-out E1..E6).
- T-525 lineage fully traced: T-525 → T-525a (T-525a1 DONE + T-525a2 DONE) + T-525b (pending). Once T-525b ships, the entire T-525 daily-loss + drawdown kill-switch family is complete.

## 2026-05-15 (T-525a1 kill-switch persistence infra CLOSED — ADR-0011 risk-mgmt #3; recursive L-007/L-022 split T-525→T-525a→{a1,a2}+T-525b; NEW H-027; F5 counter 41/55 → 42/57; L-022 first-application caught ~436 cap-breach PRE-coding)

**F5 counter 41/55 → 42/57** per L-007 split convention: T-525 (1 slot) → recursive split → 3 leaves (T-525a1 DONE + T-525a2 + T-525b) = denominator +2 (55→57); T-525a1 done = numerator +1 (41→42). **T-525a1 = 3rd ADR-0011 risk-management hardening task family member shipped** (after T-526 cooldown + T-524 caps, both this session).

### T-525a1 — kill-switch persistence infrastructure (H-027)

- **Recursive L-007/L-022 split — the headline**: T-525 →(operator OQ-1=A plan-stage)→ T-525a (daily-loss) + T-525b (max-drawdown, P&L-from-trades, NOT blocked-by-T-531). plan-reviewer Gate-1 on `T-525a.md` → **REVISE**: my own L-022 per-file decomposition self-assessed ~436 core src — **exceeded the §0.3 400 cap, caught BEFORE any code was written**. This is L-022 working exactly as designed (T-526 was the lesson source: 1.92× overshoot discovered mid-impl; L-022's whole point is surface §0.3 proximity at plan stage). plan-reviewer correctly rejected my optimistic "apply T-524's 0.77× undershoot ratio" (that ratio is for logic-only-optimistic estimates; my T-525a estimate was already docstring-inclusive). →(recursive L-007/L-022)→ T-525a1 (persistence infra, this) + T-525a2 (gate, blocked-by a1). Split boundary mechanical (infra vs gate), within operator-approved L-007 mechanism — no open architectural decision.
- **Departs from T-524/T-526 stateless-derive mirror**: T-525a* is a *latched persistent kill-switch* (H-027). The persistent state is the latch (tripped + UTC-day anchor + audit); the running P&L sum stays derived-from-trades (computed by T-525a2's gate). NEW `bot_kill_switch_state` PG table (migration 0018), NOT `bots.status` overload (OQ-2=A — would pollute operator ACTIVE/PAUSED/ARCHIVED), NOT NATS KV.
- **NEW H-027** formally allocated (§20 catalog edit, §0.8-compliant — added at implementation, ADR-0011 reserved the slot; mechanism = §20 edit not new ADR; pinned by test NAME, no `@pytest.mark.hazard` marker convention exists — grep-verified). H-027 = "risk kill-switch latch must persist across restart + re-evaluate on startup". T-525a1 ships the persistence+reconcile substrate + the restart-survives test; T-525a2 ships the trip writer; T-525b the drawdown hard-stop.
- **`is_stale_daily_latch` forward-compat for T-525b shipped now**: `trip_reason='max_drawdown'` → False (a drawdown latch is a hard-stop, NOT auto-cleared by UTC-day rollover) — so T-525b needs no change to the reconcile predicate.
- **All 4 gates**: plan-reviewer 2-pass (T-525a REVISE-recursive-split → T-525a1 APPROVE, affirmed L-022 ~278 ceiling ~290, 8 WG + 1 CONCERN) → drift-checker ON TRACK (~318 net core src; slight-over affirmed ~290 by ~10%, UNDER L-022 1.5×≈333 AND §0.3 400 cap; kill_switch.py 184-vs-122 overshoot = WG#2/#3/#5-mandated docstring, NOT scope creep) → brief-reviewer SHIP (14/14 AC + 8/8 WG + CONCERN; §N1/N3/N5/N6/N7/N8/N9; scope-clean). Math-validator out-of-scope a1 (Decimal declaration-only; SUM/threshold = T-525a2).
- **§N3 idempotency**: `upsert_kill_switch_trip` + `clear_kill_switch` @idempotent (ON CONFLICT (bot_id) DO UPDATE convergent); `is_idempotent()` explicitly test-asserted. **§N1**: migration 0018 NO time server_default (only boolean `false` constant on `tripped`); reconcile derives today via UTC `now_fn().date()`; no NOW()/CURRENT_TIMESTAMP. **§N6** DI via lifespan (no new make_signal_handler kwarg).
- **WG#7 HARD AC**: integration + migration 7/7 executed locally `POSTGRES_TEST_DSN` pre-push (L-021 sub-gap — CI not first execution surface; T-537a1 master-broke-twice anti-pattern avoided).
- **L-022 calibration data point 16**: affirmed→actual T-526 1.92× / T-524 0.77× / T-525a1 ~1.10× slight-over. The recursive split was itself the correct L-022/L-007 response to the original ~436 cap-breach — L-022's first real test, passed.
- **Commit** `e93f8cf` on `feat/T-525a1-kill-switch-infra` pre-merge. **Plan** `docs/plans/T-525a1.md` (+ `docs/plans/T-525a.md` split-record). Scope-clean: consumer.py / cooldown_gate.py / concurrent_caps_gate.py / trades.py / metrics.py / execution / analytics_api / ui / bus untouched; T-526/T-524 not regressed. No new deps.

### Next session pickup

- **T-525a2** — daily-loss gate (blocked-by T-525a1, now merged). `sum_realized_pnl_since` (**L-021 `closed_at >= $2::timestamptz` explicit cast — resolved in-advance in plan, NOT "verify at impl"; this is the T-537a1 anti-pattern guard**) + `loss_limit_gate.py check_daily_loss_limit` (lazy trip, sticky latch, reads `bot_kill_switch_state` + writes via `upsert_kill_switch_trip`/`clear_kill_switch`, UTC-day reset via shipped `is_stale_daily_latch`) + consumer.py gate wiring (chain cooldown→caps→loss-limit) + `metrics.py signals_blocked_loss_limit` Counter + alpha/beta.yaml `daily_loss_limit_usd: "0"`. **math-validator Gate 4 IN scope** (Decimal SUM ≤ -limit). Own plan-reviewer cycle; plan-reviewer-estimated ~215-245 core src. L-022 active control: per-file decomposition anchored to T-525a1 + T-526/T-524 actuals (now 3 calibration anchors).
- **T-525b** — max_drawdown_pct (P&L-drawdown from trades; hard-stop; reconcile forward-compat already shipped). Own cycle.
- Remaining ADR-0011 cluster: T-525a2, T-525b, T-527, T-528, T-530..T-536 → gates T-519 (hazard audit; H-027 now in §20 with **Test.** pins — audit will check these once T-525a2 ships the named tests' green) → T-521 (docs) → T-522 (close-out E1..E6).
- **L-022 is now battle-tested**: it caught a real §0.3 cap-breach at plan stage (T-525a ~436) and forced the recursive split before a single wasted LOC. Calibration anchors: T-526 1.92× / T-524 0.77× / T-525a1 ~1.10×. plan-reviewer is correctly L-022-bound and enforcing per-file docstring-inclusive estimates.

## 2026-05-15 (T-524 concurrent-trades caps CLOSED — ADR-0011 risk-mgmt hardening #2; F5 counter 40/55 → 41/55; L-022 first-application = conservative-undershoot success; T-526 master CI was runner-starvation infra-fail, re-run green)

**F5 phase counter advances 40/55 → 41/55** per L-007 (T-524 already in denominator; numerator+1). **T-524 = second of 13 ADR-0011 mandatory hardening tasks** (risk-management cluster, sibling of T-526 cooldown shipped earlier this session).

### T-524 — per-bot + per-exchange-mode-realm concurrent-trades caps gate

- **Origin**: F5 ADR-0011 risk-management cluster task #2. Per-bot pre-scoring caps gate at strategy-engine `consumer.py` §9.4 3b→3c boundary, AFTER the T-526 cooldown gate (OQ-5 default A). Deliberately mirrors T-526 architecture (derive-from-trades, silent-skip, RiskSection container, trades.py extension, metrics.py extension).
- **Operator OQs (3 asked + 2 defaulted, all A)**: OQ-1=A derive-from-trades `count_open_trades` (NO orders.events subscribe / NO in-memory counter / NO restart reconcile — **required** for `max_open_trades_global`: per-bot strategy-engine process can't observe cross-bot `orders.events`, so an in-memory counter could never see other bots' positions); OQ-2=A silent skip + `signal_blocked_caps` trading.log + `signals_blocked_caps_total{bot_id,reason}` Prom; OQ-3=A `max_open_trades_global` per-exchange-mode realm; OQ-4 default A `status='open'` strict; OQ-5 default A caps AFTER cooldown.
- **RiskSection forward-compat container realized**: T-526 shipped it as "forward-compat for T-524/T-525"; T-524 extends the SAME model with `max_open_trades_per_bot` + `max_open_trades_global` (no parallel section). T-525 will add daily-loss-limit + drawdown to the same model.
- **L-022 FIRST APPLICATION**: plan-reviewer 1st-pass REVISE — 2 BLOCKERs, both L-022 contingency-math. This was the **first plan-reviewer invocation bound by the L-022 lesson** (shipped with T-526 same session). It correctly rejected the flat ×1.25 contingency and enforced per-file logic/docstring decomposition anchored to T-526 shipped actuals (`cooldown_gate.py` 239, `select_recent_closed_trades` 102). 2nd-pass APPROVE affirmed ~323 src. **Outcome: reality ~249 core src — an UNDERSHOOT of ~74 (opposite of T-526's 1.92× overshoot).** L-022's conservative-estimate discipline worked: the affirmed estimate was safe-side, no §0.3 surprise. Calibration data point 15. **No new lesson needed** — this was a successful application of L-022, not a new catchable pattern (drift-checker + brief-reviewer both confirmed).
- **All 4 gates**: plan-reviewer REVISE→APPROVE (6 WG) → drift-checker ON TRACK (WG#6 §0.3 escalation report: 249 < 400, not triggered) → brief-reviewer SHIP (16/16 AC + 6/6 WG + §N1-N9 + 0 §20 regression + T-526 not regressed). Math-validator out of scope per Gate 4 (pure int count-compare; no Decimal/financial math — fast no-op like T-526).
- **§N1 N/A** (verified not a false-negative by brief-reviewer): caps is pure count-compare, `CapsDecision` has no datetime field, `count_open_trades` SQL has no timestamp predicate → no `::timestamptz` cast site. §N6 DI via **existing** `make_signal_handler` closure (no new factory kwarg — pool/bot_config/metrics already plumbed for T-526). No new H-NNN (derive-from-trades durable §18.3; ADR-0011 reserves H-027/028/029 for T-525/534/535).
- **Tests**: 22 new (9 caps-gate + 6 count_open_trades + 3 RiskSection-ext + 2 consumer + 2 integration; integration executed locally `POSTGRES_TEST_DSN` 2/2 PASS per WG#3). 665 passed strategy_engine+scoring+db + 0 regressions; mypy strict 0/79; ruff clean.
- **Commit**: `3ef5265` on `feat/T-524-concurrent-caps` pre-merge. **Plan**: `docs/plans/T-524.md`.

### T-526 master CI — runner-starvation infra failure (NOT code), re-run green

- Operator flagged "skontroluj aj ci lebo failnula". Investigated: T-526 master push (`5382e8b`) `ci-fast` + `ci-full` runs reported `completed failure`, but **all jobs were stuck `status: queued` with `completed_at: null`** — runners never picked them up (GH Actions runner-starvation / queue-timeout). The jobs that DID get a runner PASSED (`e2e` run success; `ci-full` `integration` job completed success in ~3min). **Not a code regression** — consistent with local verification (T-526 2266 passed + 2/2 integration). Re-ran both failed workflows (`gh run rerun`); **ci-fast + ci-full both `success`**. Master green confirmed before T-524 stacked. Lesson reinforced (no new L-entry): a "completed failure" run with all-jobs-queued/never-started is infra, not code — check job-level `status`/`conclusion` before assuming regression.

### Next session pickup

- **T-525** — Daily loss limit + max drawdown stop. **H-027 anticipated** (kill-switch persistence across restart — mirror T-221 reconcile; this is NOT derive-from-trades — kill-switch state must persist). **L-007 split-watch flagged at ADR-0011** (FSM + persistence + reconcile matches L-014 multiplier profile — confirm at plan stage; likely pre-emptive split). Extends the SAME RiskSection (T-526/T-524 precedent). Est ~280 src + ~200 tests (pre-split). **L-022 active control applies** — plan-reviewer must do per-file logic/docstring decomposition anchored to T-526/T-524 actuals; T-524 undershoot (~0.77× affirmed) is now a second calibration anchor alongside T-526 overshoot (1.92×).
- Remaining ADR-0011 hardening cluster after T-524/T-526: T-525, T-527, T-528, T-530..T-536 (11 tasks) — gates T-519 (hazard audit) → T-521 (docs) → T-522 (close-out E1..E6).
- T-526/T-524 set the precedent: derive-from-trades pre-scoring gates, RiskSection extension (not parallel sections), `services/strategy_engine/app/metrics.py` Metrics-dataclass extension, env-gated integration testcontainer reusing the shared `services/strategy_engine/tests/integration/conftest.py` `pool` fixture.

## 2026-05-15 (T-526 cooldown gate CLOSED — first ADR-0011 risk-management hardening task; F5 counter advances 39/55 → 40/55; NEW RiskSection forward-compat container; NEW L-022 lesson; §0.3 overage 1.92× operator-approved)

**F5 phase counter advances 39/55 → 40/55** per L-007 (T-526 already in denominator; numerator+1). **T-526 = first of the 13 ADR-0011 mandatory hardening tasks (T-524..T-536) shipped** — risk-management cluster lead.

### T-526 — per-bot pre-scoring cooldown gate (after-loss + losing-streak)

- **Origin**: F5 mandatory hardening per ADR-0011 risk-management cluster (T-524 + T-525 + T-526). Per-bot signal-acceptance gate at strategy-engine `consumer.py` between BRIEF §9.4 step 3b (symbol filter) + 3c (signal_id resolve). Mirror `signal_expired` / `signal_outside_universe` silent-skip precedent verbatim.
- **Architecture (operator OQs 4, plan-stage 2026-05-15, all default A)**: OQ-1=A **derive-from-trades** (NO `bot_cooldown_state` table, NO `orders.events.<bot_id>` subscribe, NO restart reconcile — `trades`/`paper_trades` durable per §18.3; ADR-0011 anticipated H-027 stays reserved for T-525 kill-switch persistence, explicitly NOT triggered here); OQ-2=A loss = `realized_pnl < 0` strict; OQ-3=A silent skip + `signal_blocked_cooldown` trading.log info + `signals_blocked_cooldown_total{bot_id,reason}` Prom counter (NO scoring_evaluations / NO signals.rejected / NO shadow-rejected-start); OQ-4=A combined `cooldown_until = max(loss_until, streak_until)`, reason names binding knob(s).
- **NEW `RiskSection`** (`packages/scoring/types.py`): frozen + `extra="forbid"` (operator-approved CONCERN swap from plan-stated `strict=True` 2026-05-15 — ShadowConfig precedent; typo-catch goal; strict-coercion irrelevant for `int Field(ge=0)`). Forward-compat container — T-524 (`max_open_trades_*`) + T-525 (`daily_loss_limit_usd` + `max_drawdown_pct`) will add fields to this same model. `BotConfig.risk` + `yaml_loader._parse_risk` + alpha/beta.yaml all-zero `risk:` block (fixture parity).
- **NEW modules**: `packages/db/queries/trades.py` (`select_recent_closed_trades` + `ClosedTradeRow` + `TradeTableName` Literal); `services/strategy_engine/app/cooldown_gate.py` (`check_cooldown` + `CooldownDecision` + 3 helpers); `services/strategy_engine/app/metrics.py` (FIRST strategy-engine svc-metric scaffolding — mirror signal_gateway pattern; future T-524/T-525 reuse the `Metrics` dataclass).
- **All 4 review gates**: plan-reviewer single-pass APPROVE 2026-05-15 (5 WG) → drift-checker ON TRACK (mid-impl) → drift-checker **DRIFT** (final; §0.3 LOC BLOCKER 1.92× + 2 CONCERNs; operator-resolved Option A accept-overage + keep-extra-forbid 2026-05-15) → brief-reviewer **SHIP** (5/5 WG + 15/15 AC + §N1-N9 + 0 H-NNN regression). Math-validator out of scope per Gate 4 (no financial math).
- **§0.3 LOC overage**: ~515 src vs plan ~245 = **1.92× (~115 over 400 cap)**. Operator-approved 2026-05-15 Option A (accept + declared 14-row per-file breakdown in commit body + plan §LOC budget). Root cause: WG-heavy plan under-estimated docstring overhead (cooldown_gate.py 241 LOC ≈ 130 logic + 111 WG-mandated docs; trades.py 102 ≈ 15 logic + 87 docs) + AC#8-mandated first-svc-metric scaffolding (metrics.py 63) never line-budgeted.
- **NEW L-022 lesson**: WG-heavy plans (≥4 WG items) or first-of-kind-scaffolding ACs systematically under-estimate src LOC; plan-reviewer adds +20-30% contingency + states adjusted estimate; **drift-checker treats "ON TRACK but ≥1.5× plan src" as auto NEEDS DISCUSSION** (escalate at mid-impl, don't silently pass — §0.3 cap is a brief gate independent of plan-fidelity verdict).
- **Tests**: 30 new unit + 2 integration (env-gated testcontainer; executed locally `POSTGRES_TEST_DSN=postgresql://scalper:devpass@127.0.0.1:5432/postgres` 2/2 PASS per WG#3) = 32. CI-fast 2266 passed + 48 skipped + 0 regressions; mypy strict 0/77; ruff clean. cooldown_gate.py 99% unit coverage (§N5).
- **No new deps (§0.9)**. **Commit**: `c1363f4` on `feat/T-526-cooldown-gate` pre-merge. **Plan**: `docs/plans/T-526.md`.

### Next session pickup

- **T-524** — Bot-level concurrent-trades caps (`max_open_trades_per_bot` + `max_open_trades_global`); extends NEW `RiskSection` (T-526 seed). Likely NEEDS `orders.events.<bot_id>` subscribe for in-flight position counter (T-526 deliberately did NOT add it — §9.4 step 4 `ctx.bot.concurrent_positions_count` still unimplemented).
- **T-525** — Daily loss limit + max drawdown stop; **H-027 anticipated** (kill-switch persistence across restart — mirror T-221 reconcile); L-007 split-watch flagged at ADR-0011 time.
- **T-526 sets the RiskSection + first-svc-metric precedent** — T-524/T-525 should reuse `RiskSection` (extend, don't add parallel sections) + `services/strategy_engine/app/metrics.py` `Metrics` dataclass (add Counter fields).
- Remaining ADR-0011 hardening cluster: T-524, T-525, T-527, T-528, T-530..T-536 (11 tasks) — biggest remaining F5 block; gates T-519 (hazard audit) → T-521 (docs) → T-522 (close-out E1..E6).
- **L-022 active control now live** — next plan-reviewer invocations apply the +20-30% WG-contingency rule; next drift-checker invocations apply the ≥1.5× auto-NEEDS-DISCUSSION rule.

## 2026-05-12 (late-night XXVII — T-520 hardening shortlist multi-commit CLOSED; F5 counter advances 38/55 → 39/55; 4 sub-commits ship 4 shortlist items; closes T-F3+ + F4+ T-306 + T-401c + audit residue runbook)

**F5 phase counter advances 38/55 → 39/55** per L-007. **T-520 multi-commit task fully CLOSED** at 4 sub-commits (`c075b1e` feat(T-520-livemode) + `505f8ed` feat(T-520-history) + `8486ce3` fix(T-520-symbolmap) + `046451d` docs(T-520-audit-runbook)) + chore close `___pending___`. 3 prior cherry-picks from F5-start session (`bb5d57b` + `9d1370e` + `bc1cab7`) bring total T-520 closed items to 7.

### T-520 — hardening shortlist multi-commit (4 sub-commits)

- **Origin**: F5 numbered task; multi-commit hardening batch per TASKS.md T-520 entry semantics. Operator-approved 4-item multi-select shortlist at plan-stage 2026-05-12. 3 of 5 originally-flagged tech-debts already shipped as cherry-picks during F5-start session.
- **Sub-commit #1 (`c075b1e`)** — BRIEF §16.5 live-mode safeguard. NEW async `_check_live_mode_safeguard` helper in `services/execution/app/pool.py`; called BEFORE `_construct_bybit_adapter` for live/testnet bots; raises RuntimeError if `BOT_CONFIRM_LIVE!=yes` for live mode (operator-protective fail-fast); logs `LIVE MODE ENGAGED` warning + publishes NATS `system.alerts` envelope per BLOCKER#2 fix (alerting-svc catch-all rule routes via configs/alerts.yaml:32-35 → Telegram). Per WG#3 RuntimeError raised BEFORE bus.publish on error path with `assert_not_awaited` test pin. 7 NEW tests; 336 passed in services/execution + 7 NEW; 0 regressions.
- **Sub-commit #2 (`505f8ed`)** — T-306 feature_history population for series + plugin conditions. NEW `select_feature_history` DB helper (N capped at 200; L-021 explicit `$1::text/$2::text/$3::int` casts per WG#4) + NEW `FeatureResolver.resolve_history` method + NEW `_required_history_window` evaluator helper with explicit fallback chain covering BOTH T-303 series `n_samples` AND T-305 plugin `rule.lookback_candles` paths per BLOCKER#1 fix. `Sequence` from `collections.abc` per WG#6. 9 NEW tests + 4 F3 fixture updates (beta.yaml uses oi_squeeze plugin → triggers new resolve_history; existing resolver mocks needed AsyncMock); 615 passed; 0 regressions.
- **Sub-commit #3 (`8486ce3`)** — symbol_map cleanup migration 0017. Defensive `DELETE FROM symbol_map WHERE exchange_source NOT IN ('binance','bybit','custom')` per L-012 explicit `downgrade 0016`. Per CONCERN#1: alembic logger WARNING with structured 'count=%d table=symbol_map' for postmortem. 3 testcontainer-gated tests verified locally per WG#5 + L-021 (`POSTGRES_TEST_DSN=postgresql://scalper:devpass@127.0.0.1:5432/scalper uv run pytest tests/integration/migrations/test_0017_migration.py -v` → 3/3 PASS pre-push).
- **Sub-commit #4 (`046451d`)** — operator-runnable cleanup runbook `docs/runbooks/cleanup-f4-e1-audit-residue.md`. Documents detection (SELECT query targeting smoke-window rows id IN (1,2) with escaped JSON-string scalars in JSONB columns) + cleanup (DELETE in transaction with verify) + verification + background (L-011 lesson cross-ref + c241c15 → 67e8c5f window context). Per CONCERN#4: docs-only triggers brief-reviewer trivial-diff path; drift-checker + math-validator skipped per CLAUDE.md trivial-task convention.
- **All 4 review gates passed** (aggregated): plan-reviewer pass-1 REVISE 2026-05-12 (2 BLOCKERs + 4 CONCERNs) → revised plan addresses all 6 items → plan-reviewer pass-2 APPROVE with 6-item Write-time guidance → drift-checker N/A (multi-commit task per plan §Sub-commit structure) → brief-reviewer per sub-commit (#1 SHIP 6/6 WG; #2 SHIP 3/3 applicable WG; #3 + #4 trivial-diff path) → math-validator per sub-commit (#1 VERIFIED out-of-scope; #2 OUT OF SCOPE per packages/scoring + packages/db NOT in math-binding list per CLAUDE.md gate-4 line 121).
- **Operator OQs (1 multi-select, 2026-05-12)**: 4-item shortlist multi-select selecting ALL 4 candidates (T-F3+ live-mode safeguard + F4+ T-306 feature_history + T-401c symbol_map cleanup + audit pre-fix rows cleanup runbook).
- **Hazards bound**: §16.5 live-mode safeguard (operator-protective fail-fast + Telegram alert); §N1 UTC + §N6 DI + §N7 thin helpers + §N8 forward-only migration; L-001/L-007/L-008/L-012/L-021 active controls applied.
- **§0.3 LOC**: src ~210 LOC across 4 sub-commits (sub-commit #1 ~75 + sub-commit #2 ~85 + sub-commit #3 ~30 + sub-commit #4 0); under cap by ~190 LOC. Tests ~280 LOC across mock + testcontainer + integration.
- **L-006 / L-014 / L-016 calibration 13th data point**: multi-commit hardening batch = 1.62× plan estimate (210/130; honest miss attributable to F3 fixture updates + bus.publish ordering test discipline + WG-fix scope).
- **No new deps (§0.9)**.
- **Plan**: `docs/plans/T-520.md` (APPROVED pass-2 with 6 WG verbatim).
- **Closes shortlist items**: T-F3+ live-mode safeguard runtime check (sub-commit #1) + F4+ T-306 feature_history population (sub-commit #2) + T-401c symbol_map cleanup migration (sub-commit #3) + audit pre-fix rows cleanup runbook (sub-commit #4); 7 total T-520 items closed across this session + earlier cherry-picks.

### Next session pickup

- **T-519** — §20 hazard test audit (E4 owner; gated all T-501..T-518 + T-520 + T-524..T-536). T-501..T-518 + T-520 now ALL DONE; T-519 still gated by T-524..T-536 (12 tasks remaining; T-529 done).
- **T-524..T-528, T-530..T-536** — pre-live operational hardening cluster (12 mandatory tasks per ADR-0011) — biggest remaining cluster.
- **T-521** — final docs pass (gated T-519).
- **T-522** — F5 close-out runbook + E1..E6 sign-off (Live-ready MVP per ADR-0011).

## 2026-05-12 (late-night XXVI — T-518 feature auto-backfill on registration shipped; F5 counter advances 37/55 → 38/55; ADR-0012 4th NATS KV bucket; ci-full pre-fix urllib3 CVE bump c59a703)

**F5 phase counter advances 37/55 → 38/55** per L-007 (numerator+1; T-518 already in denominator).

### T-518 — feature auto-backfill on registration via YAML-diff at lifespan startup

- **Origin**: F5 numbered task (BRIEF §9.3:1525-1528 spec literal). Plan-stage scope ambiguity surfaced + resolved: TASKS.md entry referenced a `feature_definitions` table that doesn't exist in the repo; operator OQ-1=A confirmed YAML-diff detection per spec literal (NOT DB table). OQ-2=A NATS KV bucket (revisit accepted ADR-0012 cost vs DB-table alternative). OQ-3=A 30d window default.
- **NEW ADR-0012**: BRIEF §8.2 amendment per §6.7 protocol; 4th NATS KV bucket `feature_registry_seen` (ttl=0 forever + history=1 + replicas=1 + storage=file); inline-vs-filesystem decision deferred to N>=6.
- **All 4 review gates passed**: plan-reviewer pass-1 APPROVE 2026-05-12 (6-item Write-time guidance — clean first-pass) → drift-checker ON TRACK (9 staged files; ~276 src LOC pod 400 cap; 6/6 WG verified incl. WG#6 LOC self-check 231 < 250 STOP; mirror verbatim verified line-by-line vs `scripts/backfill_features.py:186-193`) → brief-reviewer skipped per operator preference → math-validator **VERIFIED out of scope** (per CLAUDE.md gate-4; services/feature_engine/ IS in math-binding list — explicitly invoked per WG#1; no new Decimal arithmetic added beyond existing T-110c precedent).
- **Implementation**: NEW `services/feature_engine/app/auto_backfill.py` (231 LOC) — `schedule_auto_backfills` (registry-diff detector + asyncio.create_task scheduler) + `_backfill_and_mark` (mirror `scripts/backfill_features.py:_backfill_one_feature` lines 134-216 verbatim incl. WG#3 Decimal→float seam). main.py +25 LOC: lifespan integration step 9.5 AFTER `pipeline.start_consuming()` + reverse-shutdown cancel block + state attach. config.py +12 LOC: `backfill_window_days: int = 30` + `backfill_max_batch_size: int = 5000` (RESERVED label per WG#2). bootstrap.sh +8 LOC: NEW `apply_kv_bucket feature_registry_seen` + comment delta preserves closed-set rétorika per WG#5 (cites ADR-0012 + N>=6 deferral).
- **Tests**: 8 NEW `test_auto_backfill.py` mock-based + 2 NEW lifespan integration tests in `test_app_factory.py` (WG#4 ordering pin `call_order == ['start_consuming', 'schedule']` + shutdown cancel) + 3 NEW Settings tests = 13 NEW total. Repo-wide 0 regressions; ruff + mypy strict + bandit clean.
- **§0.3 LOC**: src ~276 LOC (auto_backfill 231 + main 25 + config 12 + bootstrap 8); under cap by ~124 LOC.
- **L-006 / L-014 / L-016 calibration 12th data point**: backend orchestration with novel KV+lifespan integration = 1.51× plan estimate (within band).
- **Hazards bound**: §N1 UTC; §N3 (kv_put `@idempotent` + insert_feature `ON CONFLICT DO UPDATE`); §N6 DI; §N7 thin scheduler; §N9 + L-001 configurable. L-013/L-021 N/A.
- **No new deps (§0.9)**.
- **Plan**: `docs/plans/T-518.md` (APPROVED single-pass with 6 WG verbatim). ADR: `docs/adr/0012-feature-registry-seen-kv-bucket.md`.
- **Commits**: pre-T-518 fix `c59a703` (urllib3 2.6.3 → 2.7.0 CVE-2026-44431 + CVE-2026-44432 — ci-full failure on T-517a2 master push); feat `be5c8f7` on `feat/T-518-feature-auto-backfill`; chore close pending.
- **Operator deployment note**: bootstrap.sh delta requires `nats-init` container re-run on next deploy to provision 4th bucket. Verifiable via `nats kv ls` showing 4 buckets including `feature_registry_seen` post-rerun.

### Next session pickup

- **T-519** — §20 hazard test audit (E4 owner; gated all T-501..T-518 + T-524..T-536 passing).
- **T-520** — hardening shortlist (multi-commit; 5 today-flagged tech-debts).
- **T-524..T-528, T-530..T-536** — pre-live operational hardening cluster (12 mandatory tasks per ADR-0011; T-529 done) — biggest remaining cluster.
- **T-521** — final docs pass (gated T-519).
- **T-522** — F5 close-out runbook + E1..E6 sign-off (gated all).

## 2026-05-12 (late-night XXV — T-517a2 per-symbol best-variant aggregate UI shipped; F5 counter advances 36/55 → 37/55; T-517 trio FULLY CLOSED (4/4 sub-tasks); BRIEF §13.6 dashboard integration cluster CLOSED)

**F5 phase counter advances 36/55 → 37/55** per L-007 split convention (numerator+1; T-517a2 already in denominator since T-517a sub-split). **T-517 trio FULLY CLOSED** at 4/4 sub-tasks: T-517b1 backend `8df70da` + T-517b2 UI `1643789` + T-517a1 backend `f6bf49a` + T-517a2 UI `d582e18`. **BRIEF §13.6 dashboard integration cluster fully shipped** (per-trade drill-down via T-516 trio + per-symbol aggregate via T-517a + per-rejected explorer via T-517b).

### T-517a2 — per-symbol best-variant aggregate UI (index landing + `/shadow/aggregate/$symbol` detail + nav entry + api-types)

- **Origin**: F5 numbered task (BRIEF §13.6 second bullet "which variant would have been best over last N trades?"). UI half of T-517a sub-split per OQ-3=A (T-517a1 backend shipped earlier this session as `f6bf49a`). Mirror `shadow.rejected.tsx` filter card + DataTable shape pattern modulo path-param symbol + 9-col aggregate-metric layout + 'Best' pill on first row + no pagination + no row navigate.
- **All 4 review gates passed**: plan-reviewer pass-1 APPROVE 2026-05-12 (6-item Write-time guidance — clean first-pass) → drift-checker ON TRACK (8 staged files + 1 plan doc; 326 src LOC = 81% pod 400 cap; +0.3% off plan estimate uncharacteristically precise; all 6 WG verified) → brief-reviewer skipped per operator preference (drift-checker verified all 6 WG + tests/typecheck/lint clean) → math-validator out-of-scope per CLAUDE.md (UI directory NOT in math-binding list; aggregator math lives in T-517a1 backend).
- **Operator OQs (4 OQs, 2026-05-12)**: OQ-1=A index landing + $symbol detail (2 routes; symbol picker UX-friendly); OQ-2=A 'Best' pill on first row (sorted DESC by total_pnl per backend; row.index === 0); OQ-3=A inline nav between Rejected signals + Backtest lab (no new section); OQ-4=A BarChart3 Lucide icon.
- **Implementation**: NEW `ui/src/routes/shadow.aggregate.index.tsx` (79 LOC; symbol picker landing with text input + Go button + Enter-key parity; per WG#1 `trimmed.toUpperCase()` normalization before navigate — backend predicate is case-sensitive; lowercase yields silent empty result). NEW `ui/src/routes/shadow.aggregate.$symbol.tsx` (201 LOC; aggregate view: filter card BotSelector + TimeRangePicker default 30d + 10-col DataTable variant_name with conditional 'Best' pill on row.index===0 + 4× Decimal-PriceDelta money fields total_pnl/avg_pnl/best_pnl/worst_pnl + avg_mfe_pct/avg_mae_pct via formatPct plain text). NEW api-types entries (+35 LOC; `VariantAggregate` 10-field interface + `VariantAggregateListResponse` envelope with from_at/to_at as `string | null` per WG#3 mirror ShadowRejectedListResponse echo). `__root.tsx` +11 LOC (BarChart3 import + NEW Link with data-testid="nav-shadow-aggregate" between Rejected signals + Backtest lab). `routeTree.gen.ts` +42 LOC auto-regen (2 NEW routes; excluded per T-517b2 convention).
- **Write-time guidance verified (6 items)**: WG#1 uppercase symbol normalization with test pin `"  ethusdt  "` → `/shadow/aggregate/ETHUSDT`; WG#2 Best-pill cell comment cites backend sort key `analytics_compute.py:389` + tie-break compute test; WG#3 envelope from_at/to_at as `string | null` mirror ShadowRejectedListResponse precedent; WG#4 formatPct + formatPctNumber duplication comments cite extract-at-4th-consumer rule of three (currently 3 consumers); WG#5 ShadowAggregateIndex test landed 76 LOC within pre-cleared 80-100 LOC band; WG#6 test fixture sampleVariants exercises win_rate edge values [1.0, 0.5, 0.0] verified via test #2 "100.0%" + "50.0%" + "0.0%" all-visible assertion.
- **Tests**: 3 NEW index tests + 7 NEW symbol tests + 1 NEW nav test = 11 NEW total. Full UI suite (src/ scope) 204 → 215 tests / 44 → 47 files; 0 regressions. `pnpm typecheck` + `pnpm lint` clean.
- **§0.3 LOC**: src 326 LOC (79 index + 201 $symbol + 35 api-types + 11 __root); under cap by 74 LOC; +0.3% off plan estimate (most precise calibration to date — index landing being small standalone + $symbol detail tight mirror reduced variance).
- **L-006 / L-014 / L-016 calibration 11th data point**: UI mirror task with 2 routes = 1.0× plan estimate.
- **No new deps (§0.9)** — BarChart3 already in lucide-react 0.468.0.
- **Plan**: `docs/plans/T-517a2.md` (APPROVED single-pass with 6 WG verbatim).
- **Commit**: feat `d582e18` on `feat/T-517a2-aggregate-ui`; chore close pending.
- **CLOSES T-517 trio**: T-517b1 backend (`8df70da`) + T-517b2 UI (`1643789`) + T-517a1 backend (`f6bf49a`) + T-517a2 UI (`d582e18`); 4/4 sub-tasks DONE; BRIEF §13.6 dashboard integration cluster (per-trade drill-down + per-symbol aggregate + per-rejected explorer) FULLY CLOSED.

### Next session pickup

- **T-518** — feature auto-backfill on registration (BRIEF §9.3); top-of-DAG independent. Est: ~200 LOC src + ~150 LOC tests.
- **T-519** — §20 hazard test audit (E4 owner; gated all T-501..T-518 + T-524..T-536).
- **T-520** — hardening shortlist (multi-commit; 5 today-flagged tech-debts from F4 E1 smoke).
- **T-521** — final docs pass (gated T-519).
- **T-522** — F5 close-out runbook + E1..E6 sign-off (Live-ready MVP per ADR-0011).
- **T-524..T-528, T-530..T-536** — pre-live operational hardening cluster (12 mandatory tasks per ADR-0011; T-529 done) — biggest remaining cluster; lands BEFORE T-522.

## 2026-05-12 (late-night XXIV — T-517a1 per-symbol best-variant aggregate backend shipped; F5 counter advances 35/54 → 36/55 per L-007 split; T-517a sub-split per OQ-3=A; T-517 trio 3/4 progress; T-517a2 aggregate UI is final remaining sub-task)

**F5 phase counter advances 35/54 → 36/55** per L-007 split convention (numerator+1 for shipped T-517a1; denominator+1 for T-517a sub-split this session — T-517a → T-517a1 + T-517a2 = 2 sub-tasks). **T-517 trio progresses 2/3 → 3/4** (sub-split adds 1 effective sub-task; remaining: T-517a2 UI).

### T-517a1 — per-symbol best-variant aggregate backend (analytics-api endpoint + DB JOIN helper + Python aggregator + Pydantic)

- **Origin**: F5 numbered task (BRIEF §13.6 second bullet "which variant would have been best over last N trades?"). Pre-emptive sub-split T-517a → T-517a1 (this; backend) + T-517a2 (UI; next) per L-007 + operator OQ-3=A 2026-05-12. Combined T-517a est ~470 LOC trip §0.3 cap; sub-split keeps each well under cap. Mirror T-517b1+T-517b2 split precedent.
- **All 4 review gates passed**: plan-reviewer pass-1 REVISE 2026-05-12 (2 CONCERNs: dict-iteration tie-break fragility + sibling builder divergence) → revised plan applies CONCERN-1 composite key sort `(-total_pnl, variant_name)` + CONCERN-2 Možnosť A switch to dynamic `_build_shadow_variant_aggregate_where_clause` builder mirror sibling T-517b1 → plan-reviewer pass-2 APPROVE with 7-item Write-time guidance → drift-checker ON TRACK (9 staged files; 373 src LOC = 93% pod 400 cap; 1.37× plan estimate within L-006 mirror band; all 7 WG verified) → brief-reviewer skipped per operator preference (drift-checker verified all 7 WG + tests/typecheck/lint/L-021 testcontainer all clean) → math-validator out-of-scope per CLAUDE.md (analytics_api + packages/db NOT in math-binding list); plan §Hand verification section is the math safety net.
- **Operator OQs (4 OQs, 2026-05-12)**: OQ-1=A multi-metric table (8 metrics per variant: variant_name + n_trades + win_rate + total_pnl + avg_pnl + best_pnl + worst_pnl + avg_mfe_pct + avg_mae_pct); OQ-2=A time-based window via TimeRangePicker; OQ-3=A sub-split T-517a → T-517a1+T-517a2; OQ-4=A optional bot_id filter (default all-bots aggregated for symbol).
- **Implementation**: NEW DB layer in `packages/db/queries/shadow.py` (+150 LOC; `ShadowVariantAggregateRow` 8-field subset projection with parent_symbol via JOIN + `_row_to_shadow_variant_aggregate` narrower mirror existing pattern + `_build_shadow_variant_aggregate_where_clause` dynamic builder mirror sibling T-517b1 with 3 always-included charter predicates + 3 optional appended as direct column comparisons + `select_shadow_variants_for_aggregate` helper with LEFT JOIN trades + paper_trades on parent_kind discriminator + COALESCE on symbol). NEW Python aggregator in `services/analytics_api/app/analytics_compute.py` (+89 LOC; `VariantAggregateMetrics` + `compute_variant_aggregate` with composite key sort per WG#2). NEW `services/analytics_api/app/models/shadow_aggregate.py` (49 LOC; `VariantAggregateResponse` + `VariantAggregateListResponse` envelope). NEW `services/analytics_api/app/routers/shadow_aggregate.py` (76 LOC; prefix `/api/shadow/aggregate`; `GET /{symbol}` with optional bot_id + from + to). main.py +2 LOC adjacency PRED `shadow_rejected_router` per WG#7.
- **Hand verification (per CLAUDE.md §5)**: 3 variants × 4 trades fixture; aggressive total=20 + no_be total=20 + conservative total=10; tie-break by variant_name ASC ('aggressive' < 'no_be') → output order aggressive PRED no_be PRED conservative; 21 expected values independently verified.
- **Tests**: 5 NEW DB mock-based + 9 NEW compute (8 plan + 1 dataclass shape addition; covers WG#2 tie-break case) + 10 NEW router + 3 NEW testcontainer L-021 (live-parent JOIN + paper-parent JOIN + mixed-parents-with-filters). Repo baseline 2301 → 2325 (+24 visible; +3 testcontainer skipped without `POSTGRES_TEST_DSN`). 0 regressions. ruff + mypy strict + bandit clean.
- **L-021 testcontainer-gated tests verified locally** per WG#4 mandate: `POSTGRES_TEST_DSN=postgresql://scalper:devpass@127.0.0.1:5432/scalper uv run pytest tests/integration/queries/test_shadow.py -v` → 3/3 PASS pre commit/push (against scalper-v2-postgres-1 timescale/timescaledb container; verified 2026-05-12).
- **§0.3 LOC**: src 373 LOC; under cap by 27 LOC; 1.37× plan estimate within L-006 1.0-1.4× backend-mirror calibration band. Test code 916 LOC excluded from cap per BRIEF §0.3.
- **L-006 / L-014 / L-016 calibration 10th data point**: backend mirror task with novel SQL JOIN + Python aggregator = 1.37× plan estimate.
- **Hazards bound**: L-008 ($N placeholders only via SQL injection sentinel); L-021 (testcontainer 3/3 PASS pre-push; dynamic builder eliminates `$N::type IS NULL OR ...` trigger; only `$1::text` defensive cast); §N1/N3/N6/N7 clean; §N9 N/A.
- **No new deps (§0.9)**.
- **Plan**: `docs/plans/T-517a1.md` (APPROVED pass-2 with 7 WG verbatim).
- **Commit**: feat `f6bf49a` on `feat/T-517a1-aggregate-backend`; chore close pending.
- **Unblocks T-517a2**: UI route `/shadow/aggregate/$symbol` + nav entry + api-types; consumes endpoint shipped here. T-517a2 closes T-517 trio (T-517b1 + T-517b2 + T-517a1 + T-517a2 = 4 sub-tasks; all DONE).

### Next session pickup

- **T-517a2** — UI route `/shadow/aggregate/$symbol` + nav entry + api-types (consumes T-517a1 endpoint). Mirror T-517b2 UI structure pattern; ~240 LOC src est. Closes T-517 trio.
- **T-518..T-521** F5 backend polish + close-out gating.
- **T-524..T-528, T-530..T-536** pre-live operational hardening cluster (12 mandatory tasks per ADR-0011) — biggest remaining cluster.
- **T-522** Live-ready close-out runbook (E5 + E6 sign-off).

## 2026-05-12 (late-night XXIII — T-517b2 rejected-signal explorer UI shipped; F5 counter advances 34/54 → 35/54; T-517b sub-split CLOSED; T-517 trio 2/3 progress; T-517a aggregate is final remaining sub-task)

**F5 phase counter advances 34/54 → 35/54** per L-007 split convention (numerator+1; T-517b2 already in denominator since T-517 reorg this session). **T-517b sub-split CLOSED** (T-517b1 backend `8df70da` + T-517b2 UI `1643789`); T-517 trio progresses 1/3 → 2/3 (remaining: T-517a per-symbol best-variant aggregate).

### T-517b2 — rejected-signal explorer UI (route `/shadow/rejected` + nav entry + api-types)

- **Origin**: F5 numbered task (BRIEF §13.6 third bullet "what would rejected signals have yielded?"). UI half of T-517b sub-split per OQ-5=A (T-517b1 backend shipped earlier this session as `8df70da`). Mirror `paper-trades.index.tsx` pattern modulo NEW terminal_outcome filter + NO row-navigate (list-only per OQ-1=A).
- **All 4 review gates passed**: plan-reviewer pass-1 APPROVE 2026-05-12 (6-item Write-time guidance — clean first-pass) → drift-checker ON TRACK (7 staged files; 347 src LOC = 87% pod 400 cap; 1.42× plan estimate within L-006 1.0-1.4× UI mirror calibration band; all 6 WG verified) → brief-reviewer skipped per operator preference 2026-05-12 (drift-checker already verified all 6 WG + tests/typecheck/lint clean) → math-validator out-of-scope per CLAUDE.md (UI directory NOT in math-binding list).
- **Operator OQs (4 OQs, 2026-05-12)**: OQ-1 = List-only (no drill-down detail; backend GET /api/shadow/rejected/{id} exists from T-517b1 but no UI consumer yet); OQ-2 = Inline nav between Paper trades + Backtest lab (no new section header); OQ-3 = ShieldOff Lucide icon; OQ-4 = Select dropdown for terminal_outcome (6 options: "all" + 5 ShadowRejectedTerminal values).
- **Implementation**: NEW `ui/src/routes/shadow.rejected.tsx` (301 LOC; URL `/shadow/rejected` via TanStack Router dot-separator; 5 filters: BotSelector + symbol Input + status select active/terminated + NEW terminal_outcome select + TimeRangePicker; 8-col DataTable with StatusPill + formatPct helpers; pagination block). NEW api-types entries (`ShadowRejectedTerminal` type + `ShadowRejected` 11-field interface + `ShadowRejectedListResponse` envelope; +35 LOC). `__root.tsx` +11 LOC (ShieldOff import alphabetic position + NEW Link with data-testid="nav-shadow-rejected"). `routeTree.gen.ts` +21 LOC auto-regen by TanStack Router plugin (excluded from §0.3 per T-516a2 convention).
- **Write-time guidance verified (6 items)**: WG#1 plain text `formatPct` `${(n*100).toFixed(2)}%` mirror `ShadowVariantsView.formatPctPair` — `<PriceDelta>` NOT used for stat pct ratios; WG#2 ShieldOff explicit import + Link usage = 2 occurrences; WG#3 test count 8+1=9 NEW; WG#4 `buildShadowRejectedUrl` explicit comment for NO omit-when-active heuristic (inversion of paper-trades; created_at non-null per migration 0014); WG#5 StatusPill inline span (option b — "active"/"terminated" not in StatusBadge enum); WG#6 explicit `TERMINAL_OUTCOME_OPTIONS` array (5 enum values + "all" sentinel; TS type alias compile-time only, NOT runtime enum so `Object.values()` not available).
- **Tests**: 8 NEW component tests in `ShadowRejectedIndex.test.tsx` (empty / populated rows / pagination Next / bot filter + offset reset / status filter / terminal_outcome filter / time range always-applies NO-omit / active row dash rendering) + 1 NEW nav presence test in `ShadowRejectedNav.test.tsx`. Full UI suite (src/ scope) 195 → 204 tests / 42 → 44 files; 0 regressions. `pnpm typecheck` + `pnpm lint` clean.
- **§0.3 LOC**: src 347 LOC (35 api-types + 11 __root + 301 shadow.rejected.tsx); under cap by 53 LOC; routeTree.gen.ts 21 LOC excluded per auto-regen convention; no waiver needed.
- **L-006 / L-014 / L-016 calibration 9th data point**: UI mirror task = 1.42× plan estimate (within 1.0-1.4× band).
- **No new deps (§0.9)** — ShieldOff already in `lucide-react` package; všetky ostatné libs už pinnté.
- **Plan**: `docs/plans/T-517b2.md` (APPROVED single-pass with 6 WG verbatim).
- **Commit**: feat `1643789` on `feat/T-517b2-rejected-explorer-ui`; chore close pending.
- **Closes T-517b sub-split**: T-517b1 backend (`8df70da`) + T-517b2 UI (`1643789`); T-517 trio 2/3 progress.

### Next session pickup

- **T-517a** — per-symbol best-variant aggregate (`/shadow/aggregate/$symbol` route + backend endpoint + aggregation SQL); "best variant" metric definition deferred to T-517a plan-stage per OQ-4=A 2026-05-12 (multi-metric vs single best-by-pnl; total_pnl / win_rate / Sortino / profit_factor tradeoff). Closes T-517 trio. Est: ~280 LOC src + ~180 LOC tests.
- **T-518..T-521** F5 backend polish + close-out gating.
- **T-524..T-528, T-530..T-536** pre-live operational hardening cluster (12 mandatory tasks per ADR-0011; T-529 done) — biggest remaining cluster.
- **T-522** Live-ready close-out runbook (E5 + E6 sign-off).

## 2026-05-12 (late-night XXII — T-517b1 rejected-signal explorer backend shipped; F5 counter advances 33/52 → 34/54 per L-007 split; T-517 reorg 2-level split T-517 → T-517a + T-517b1 (DONE) + T-517b2; T-517b2 unblocked)

**F5 phase counter advances 33/52 → 34/54** per L-007 split convention (numerator+1 for shipped T-517b1; denominator+2 for T-517 reorg this session — 2-level split T-517 → T-517a + T-517b1 + T-517b2 = 3 sub-tasks where there was 1). **T-517b1 = first child of T-517 trio** to ship.

### T-517b1 — rejected-signal explorer backend (analytics-api endpoints + DB helpers + Pydantic)

- **Origin**: F5 numbered task (BRIEF §13.6 third bullet "what would rejected signals have yielded?"). Pre-emptive 2-level split T-517 → T-517a + T-517b → T-517b1 (this; backend) + T-517b2 (UI; next) per L-007 + operator OQ-1=A 2026-05-12 (split T-517) + OQ-5=A 2026-05-12 (sub-split T-517b). Combined T-517b est ~467 LOC src would trip §0.3 cap; sub-split keeps each well under cap. Mirror T-516a1+T-516a2 split precedent.
- **All 4 review gates passed**: plan-reviewer pass-1 APPROVE 2026-05-12 (5-item Write-time guidance — no REVISE; clean first-pass) → drift-checker ON TRACK (6 staged files; 308 src LOC = 77% pod 400 cap; 1.39× plan estimate within L-006 1.0-1.4× calibration band; all 5 WG verified) → brief-reviewer SHIP (5/5 WG verified) → math-validator out-of-scope per CLAUDE.md (analytics-api + packages/db NOT in math-binding list).
- **Operator OQs (5 OQs, 2026-05-12)**: OQ-1 = Split T-517 → T-517a + T-517b; OQ-2 = T-517b first (simpler scope; mirror exists); OQ-3 = Full mirror trades.index filters (bot_id + symbol + status + terminal_outcome + date range + pagination); OQ-4 = Defer aggregate metric definition to T-517a plan-stage (multi-metric vs single-best tradeoff); OQ-5 = Pre-emptive sub-split T-517b → T-517b1 + T-517b2.
- **Implementation**: NEW DB helpers in `packages/db/queries/shadow.py` (+123 LOC; `_build_shadow_rejected_where_clause` 6 filters with status='active'/'terminated' as constant `terminated_at IS NULL/NOT NULL` predicate (no $N site) + `select_shadow_rejected_paginated` ORDER BY `created_at DESC, id DESC` per `select_signals_paginated` precedent + `count_shadow_rejected` mirror `count_paper_trades`; reuses existing `_SHADOW_REJECTED_BASE_COLUMNS` + `_row_to_shadow_rejected` + `select_shadow_rejected_by_id`). NEW `services/analytics_api/app/models/shadow_rejected.py` (60 LOC; `ShadowRejectedResponse` 11-col mirror + `ShadowRejectedListResponse` envelope `rejected/total/limit/offset`; `use_enum_values=True` for `ShadowRejectedTerminal` StrEnum string serialization). NEW `services/analytics_api/app/routers/shadow_rejected.py` (123 LOC; prefix `/api/shadow/rejected`; tags `["shadow-rejected"]`; 2 endpoints — paginated list with 6 Query params + 422-validated `Literal["active","terminated"]` + ShadowRejectedTerminal StrEnum + datetime aliases + limit ≤ 200 + offset ≥ 0; detail-by-PK with 404 detail format `f"shadow_rejected {id} not found"`). main.py +2 LOC: import + include_router adjacent to paper_trades_router (alphabetical inside routers block; sibling positioning per WG#5).
- **Tests**: 7 NEW DB helper tests in `test_queries_shadow.py` (no-WHERE/all-filters/active-constant/L008-injection-sentinel/ORDER-BY+L021-cast-pin/count-sync/WG#1-`.value`-form-pin) + 16 NEW router tests in `test_router_shadow_rejected.py` (envelope shape `rejected` key + 4× pagination negatives + 4× filter forwarding + 2× detail 200/404 + 4× serialization covering enum-string/double-precision/JSONB-passthrough/active-null-fields). Repo baseline 2278 → 2301 (+23 net new). 0 regressions. ruff + mypy strict + bandit clean.
- **§0.3 LOC**: src 308 LOC (123 shadow.py + 60 models + 123 router + 2 main.py); under cap by 92 LOC; no waiver needed.
- **L-006 / L-014 / L-016 calibration 8th data point**: backend mirror task = 1.39× plan estimate (within band 1.0-1.4× for backend mirror tasks; mirror-reuse pattern matures further).
- **Hazards bound**: L-008 ($N placeholders only via SQL injection sentinel test — `"alpha-injection-attempt'); DROP TABLE bots; --"` confirmed in bind_args NOT in sql); L-021 preventive `"::" not in sql_string` guard for future cast-site introduction; L-011 codec-registered → meta dict passthrough at read side; §N1/N3/N6/N7 clean.
- **No new deps (§0.9)**.
- **Plan**: `docs/plans/T-517b1.md` (APPROVED single-pass with 5 WG verbatim).
- **Commit**: feat `8df70da` on `feat/T-517b1-rejected-explorer-backend`; chore close pending.
- **Unblocks T-517b2**: UI route `/shadow/rejected` + nav entry + api-types.ts; consumes `/api/shadow/rejected/*` endpoints shipped here.

### Next session pickup

- **T-517b2** — UI route `/shadow/rejected` + nav entry + api-types (consumes T-517b1 endpoints; ~235 LOC src est paginated list mirror `paper-trades.index.tsx` + 6-filter panel + nav entry).
- **T-517a** — per-symbol best-variant aggregate (`/shadow/aggregate/$symbol`); "best variant" metric definition deferred to T-517a plan-stage per OQ-4=A; ~280 LOC src est.
- **T-518..T-521** F5 backend polish + close-out gating.
- **T-524..T-528, T-530..T-536** pre-live operational hardening cluster (12 mandatory tasks per ADR-0011; T-529 done) — biggest remaining cluster.
- **T-522** Live-ready close-out runbook (E5 + E6 sign-off).

## 2026-05-09 (late-night XXI — T-516b shadow variants section shipped; F5 counter advances 32/52 → 33/52; T-516 trio CLOSED; placeholder #4 now real renderer in BOTH drill-down routes)

**F5 phase counter advances 32/52 → 33/52** per L-007 split convention (numerator+1 only; T-516b already in denominator since T-516 reorg 2026-05-08). T-516 trio (T-516a1 backend + T-516a2 UI routes + T-516b shadow variants section) now FULLY CLOSED.

### T-516b — shadow variants per-trade drill-down section (closes BRIEF §13.6)

- **Origin**: F5 numbered task closing BRIEF §13.6 dashboard integration verbatim ("per-trade drill-down shows all 5 variants alongside the live outcome"). Just-unblocked after T-516a2 (DONE 2026-05-09 earlier today). Replaces placeholder #4 in BOTH `trades.$tradeId.tsx` (live) + `paper-trades.$paperTradeId.tsx` (paper) routes with real `<ShadowVariantsView />`.
- **All 4 review gates passed**: plan-reviewer pass-1 REVISE 3 CONCERNs (test paths convention drift `packages/db/queries/tests/` → `packages/db/tests/test_queries_shadow.py`; test-gating mode mismatch — operator wanted full mirror per OQ-4 but plan over-applied L-021 testcontainer claim; testid discipline binding for placeholder count 5→4 transition) → pass-2 REVISE 1 CONCERN (Estimate sec carrying old paths post pass-1 fix) → pass-3 APPROVE with 7-item Write-time guidance → drift-checker mid + final ON TRACK → brief-reviewer SHIP (7/7 WG verified) → math-validator out-of-scope (UI render + read-only analytics-api).
- **Operator OQs (4 OQs)**: OQ-1 = Split per parent_kind (mirror existing convention); OQ-2 = 7-col compact table (variant_name + side + entry/qty + outcome pill + PriceDelta + MFE/MAE); OQ-3 = Separate Live parent row at top of variants table per BRIEF §13.6 verbatim; OQ-4 = Full mirror tests (non-default; extensive coverage with parent_kind=live vs paper distinct cases).
- **Implementation**: NEW DB helper `select_shadow_variants_by_parent` (parameterized SQL; mock-based AsyncMock tests). NEW Pydantic models in `services/analytics_api/app/models/shadow_variants.py` (15-col mirror; `use_enum_values=True` for ShadowVariantTerminal StrEnum + Decimal-as-string per §5.3). NEW endpoints in `routers/trades.py` + `routers/paper_trades.py` with `parent_kind` hardcoded per route (NOT query param per WG#3). NEW UI component `ShadowVariantsView.tsx` (198 LOC; 8-col table + Live parent row + variant rows + Pill helper + formatPctPair; `data-testid='shadow-variants-view'` root + `'shadow-variants-loading'` skeleton; NO `timeline-placeholder`). Both routes replace placeholder #4 with `<ShadowVariantsView />` + parent prop pass-through (Trade | PaperTrade | undefined union).
- **Tests**: 5 NEW DB + 5 NEW router + 6 NEW component (incl. L-017 dual-pin on parent-undefined test) + 2 NEW integration + 2 existing test updates (placeholder count 5→4). Backend pytest 34 pass; UI 195 tests / 42 files pass (+8 net new). 0 regressions. typecheck + lint clean.
- **§0.3 LOC**: ~395 net src LOC; under 400 cap by ~5. Plan estimate was ~243 (+63% miss); component grew 198 vs 110 due to mechanical render code (table cells + Pill helper + formatPctPair) — no scope creep.
- **No new deps (§0.9)**.
- **ADR-0010 parent_kind discriminator routing pinned** via `test_router_shadow_variants.py` test #4 (`live_kwargs["parent_kind"]=="live"` + `paper_kwargs["parent_kind"]=="paper"`).
- **Plan**: `docs/plans/T-516b.md` (3-pass APPROVED with 7 WG verbatim).
- **Commit**: feat `4b8ff86` on `feat/T-516b-shadow-variants-section`; chore close pending.
- **Closes T-516 trio**: T-516a1 backend (DONE 2026-05-08) + T-516a2 UI routes + shared module (DONE 2026-05-09 earlier) + T-516b shadow variants (DONE now). Placeholder #4 slot in BOTH drill-down routes is now real component.

### Next session pickup

- **T-517** — per-symbol best-variant aggregate + per-rejected-signal explorer (BRIEF §13.6 second + third bullets; pre-emptively split-flagged per L-007).
- **T-518..T-521** F5 backend polish + close-out gating.
- **T-524..T-536** pre-live operational hardening cluster (12 mandatory tasks per ADR-0011) — biggest remaining cluster, mandatory pre E6 Live-ready.
- **T-522** Live-ready close-out runbook (E5 + E6 sign-off).

## 2026-05-09 (late-night XX — T-516a2 paper-trade UI drill-down shipped; F5 counter advances 31/52 → 32/52; T-516b unblocked)

**F5 phase counter advances 31/52 → 32/52** per L-007 split convention (numerator+1 only; T-516a2 already in denominator since T-516 reorg 2026-05-08).

### T-516a2 — paper-trade UI drill-down routes + nav entry + shared trade-drill module

- **Origin**: F5 numbered task; UI half of paper-trade drill-down per BRIEF §14.3:2068-2078. Backend `/api/paper-trades/*` shipped via T-516a1 (DONE 2026-05-08); T-516a2 wires those into routes + nav.
- **All 4 review gates passed**: plan-reviewer pass-1 APPROVE 2026-05-09 (7-item Write-time guidance, no REVISE — clean first-pass) → drift-checker mid + final ON TRACK → brief-reviewer SHIP → math-validator out-of-scope (UI render).
- **Operator decisions (4 OQs)**: OQ-1 = Union prop type `Trade | PaperTrade` (no kind discriminator field; backend §3.1:268 paper-live symmetry); OQ-2 = Placeholder #4 wording = "Coming T-516b (... parent_kind=paper)" + parallel update to live route; OQ-3 = Slim contract test scope; OQ-4 = `FileText` lucide-react icon for nav.
- **Implementation**: NEW shared module `ui/src/components/trade-drill/` (TradeSummary + SignalDetailView lifted from `trades.$tradeId.tsx`; Row helper internal-only per WG#6 NOT in barrel). NEW api-types `PaperTrade` + `PaperTradeListResponse` with JSDoc citation per WG#3 (cites backend source + §3.1:268 + drift mitigation TWO distinct interfaces, no `type PaperTrade = Trade` alias). NEW route `paper-trades.$paperTradeId.tsx` (133 LOC) with "Paper trade #N not found" 404 fallback per WG#4 + L-017 strict not-called assertions on tests #3/#4. NEW route `paper-trades.index.tsx` (234 LOC) paginated list mirror. Refactored `trades.$tradeId.tsx` (-77 LOC) uses shared module; placeholder #4 = "Coming T-516b (... parent_kind=live)" per WG#1. NEW nav entry `__root.tsx` FileText icon + `data-testid="nav-paper-trades"` between Trade explorer + Backtest lab per WG#5.
- **Tests**: 4 NEW test files (PaperTradeDrillDown 6/6 + PaperTradesIndex 6/6 + PaperTradesNav 1/1) + TradeDrillDown.test.tsx placeholder split. Full UI suite **187 tests / 40 files / 0 regressions** (excl. e2e Playwright). `pnpm typecheck` + `pnpm lint` clean.
- **§0.3 LOC**: ~461 net src LOC; **+15% over 400 cap**. **Operator §0.3 waiver granted 2026-05-09** — mirror+lift task (paper-trades.index.tsx 234 = 1:1 mirror of trades.index.tsx 233); trim would require NEW `TradesListPage` shared abstraction outside plan-reviewer scope.
- **No new deps (§0.9)**. All required libs already in `ui/package.json`.
- **Plan**: `docs/plans/T-516a2.md` (APPROVED pass-1 with 7 WG verbatim).
- **Commit**: feat `8b76db5` on `feat/T-516a2-paper-trades-ui`; chore close pending.
- **Unblocks T-516b**: shadow variants section now has both `trades.$tradeId.tsx` + `paper-trades.$paperTradeId.tsx` placeholder slots ready (parent_kind=live + parent_kind=paper).

### Next session pickup

- **T-516b** — shadow variants drill-down section (now unblocked per T-516a2 placeholder slots).
- **T-518..T-521** F5 backend polish + close-out gating (T-519 hazard audit blocked-by ALL T-501..T-518 passing).
- **T-524..T-536** pre-live operational hardening cluster (12 tasks per ADR-0011) — not yet started; biggest remaining cluster.
- **T-522** Live-ready close-out runbook (E5 + E6 sign-off; blocked-by T-507 + T-508 + T-509 + T-512 + T-516 + T-518 + T-519 + T-521 + T-524..T-536).

## 2026-05-09 (late-night XIX — T-529 qty quantization shipped; F5 counter advances 30/51 → 31/52; audit Item 6 RESOLVED — LAST audit item; **7 of 7 audit items DONE; H-030..H-036 audit cluster fully CLOSED**; NEW H-036 hazard)

**F5 phase counter advances 30/51 → 31/52** per WG#5 (numerator+1 for shipped T-529, denominator+1 for new T-529 numbered task per L-007 split convention). **Audit cluster H-030..H-036 fully shipped at this commit**. T-529 was the LAST of 7 audit items; pre-live blocker resolved.

### T-529 — qty quantization / pre-flight validation (closes audit Item 6 — final)

- **Origin**: derived from operator instruction 2026-05-09: *"Surface Item 6 as T-529 qty quantization/pre-flight validation: current concern is that placement may send raw request.qty to Bybit without instrument-aware qtyStep/minOrderQty/minNotional normalization. Treat it as a known-deferred critical pre-live blocker, not yet a newly verified concrete bug. Start plan-stage by auditing the sizing → placement → Bybit place_market_order path and existing instruments-info support, then decide whether T-529 is a single task or split into metadata cache + quantization + tests."* Audit found NO existing instruments-info support → single-task scope.
- **All 4 review gates passed**: plan-reviewer pass-2 APPROVE 2026-05-09 (after pass-1 REVISE 1 BLOCKER + 2 CONCERNs — only 1 of 6 request.qty sites enumerated; AC#16 6-site enumeration with grep evidence + AC#17 5-site explicit test pin added) → drift-checker ON TRACK with 2 CONCERNs (AC#17 partial-coverage addressed post-drift via 5-site explicit spy pin; LOC +49% src 313/210 informational under §0.3 cap by 87 LOC) → brief-reviewer SHIP → math-validator VERIFIED (4-case hand-fixture independently confirmed; Decimal arithmetic precision-preserved).
- **Bug**: `placement.py` forwarded raw `request.qty` to `adapter.place_market_order` with only warn-log stub `execution.qty_step_rounding_pending_t_f2_plus` (BLOCKER #3 visibility marker, T-216a-era). Non-pre-aligned qty configs → high reject rate via Bybit-side OrderRejected (retCode 110017 etc.). H-036 NEW invariant pinned.
- **Fix shape**: NEW `packages/exchange/types.py InstrumentInfo` dataclass + NEW `errors.py QtyValidationError(ExchangeError)` + NEW `protocols.py @idempotent get_instrument_info(symbol)` Protocol method (12th method) + NEW `quantize.py quantize_qty(qty, info)` helper (Decimal floor-div). `BybitV5Adapter.get_instrument_info` HTTP GET `/v5/market/instruments-info?category=linear&symbol=...` with LRU/TTL cache (default 3600s; mirror set_leverage). `EndpointGroup` Literal extended with `"market"`. `PaperExchange.get_instrument_info` hardcoded fixture (BTCUSDT/ETHUSDT/SOLUSDT). `placement.py`: replaced BLOCKER #3 warn-only with pre-flight quantize; on QtyValidationError → log `execution.qty_validation_failed` + return; on (AuthError, NetworkTimeout, RateLimitError) → log `execution.get_instrument_info_failed` + return. **AC#16 6-site substitution**: quantized_qty (NOT request.qty) at place_market_order + compute_tp_size + compute_notional_usd + paper shadow_start emit + persist_placement_tx kwarg + emergency_close kwarg + live shadow_start emit + lifecycle qty kwarg (also covers 7th emergency_close site found mid-implementation). `placement_persist.py persist_placement_tx` + `emergency_close` gain `qty: Decimal` kwarg. minNotional pre-flight DEFERRED to T-529-future (requires last_price extra HTTP/OHLC); Bybit-side OrderRejected handles via existing taxonomy.
- **Hand-verified fixture**: `Decimal("0.0015") // Decimal("0.001") = Decimal("1")`; `1 * Decimal("0.001") = Decimal("0.001")` exact (math-validator independent confirmation).
- **Tests**: 4 NEW `test_quantize.py` unit tests (aligned + round-down + below-floor pre-round + below-floor post-round) + 5 NEW bybit_v5 (HTTP shape + cache hit + cache TTL expiry + OrderRejected on empty list + market limiter group) + 2 NEW paper get_instrument_info + 4 NEW placement integration tests (incl. AC#17 5-site spy pin via monkeypatch.setattr on compute_tp_size + compute_notional_usd + persist_placement_tx + bus.publish iteration). Existing `test_handler_logs_qty_rounding_pending_warning_before_place` REMOVED. 7 emergency_close + 1 persist_placement_tx test sites updated. Protocol conformance count 11 → 12.
- **Repo baseline 2254 → 2268** (+14 net new). 0 regressions.
- **§0.3 LOC**: ~313 src + ~250 tests = ~563 LOC delta. Under cap.
- **NEW H-036 hazard** in BRIEF §20 (after H-035, before H-032; full audit cluster CLOSED). H-035 numbering note updated to reflect H-036 closure.
- **Plan**: `docs/plans/T-529-qty-quantization.md` (APPROVED pass-2 with 5 WG verbatim).
- **Commits**: feat `f90c382` on `feat/T-529-qty-quantization`; chore close pending.

### Audit cluster H-030..H-036 — FULL CLOSURE TIMELINE

| H-NNN | Audit Item | Task | Shipped |
|-------|------------|------|---------|
| H-030 | #1 open-fill remaining_qty contract | T-216 precedent | (pre-cluster) |
| H-031 | #5 paper adapter must NOT feed live ExecutionDispatcher | precedent | (pre-cluster) |
| H-032 | #3 retry loop transient-exception coverage | fix(T-216c) precedent | 2026-05-09 |
| H-033 | #1.b composite-PK position_state UPDATE trade_id guard | fix(T-217c) precedent | 2026-05-09 |
| H-034 | #2 + #7 outbox relay shutdown ordering | T-537a1+a2+b | 2026-05-09 |
| H-035 | #4 fill_price MUST be VWAP across all exec rows | T-538 | 2026-05-09 |
| H-036 | #6 qty MUST be quantized vs instrument qtyStep + minOrderQty | **T-529** | **2026-05-09** |

**7 of 7 audit items CLOSED**. Pre-live blocker fully resolved.

## 2026-05-09 (late-night XVIII — T-538 VWAP fill price shipped; F5 counter advances 29/50 → 30/51; audit Item 4 RESOLVED; 6 of 7 audit items DONE; NEW H-035 hazard)

**F5 phase counter advances 29/50 → 30/51** per WG#7 (numerator+1 for shipped T-538, denominator+1 for new T-538 numbered task). Audit cluster H-030..H-035 fully shipped.

### T-538 — VWAP fill price across all exec rows (closes audit Item 4)

- **Origin**: derived from operator audit Item 4 (fill-price uses last-trade close, not VWAP) — 7-bug audit 2026-05-08; T-537 cluster shipped 2026-05-09 left only Items 4 + 6 unresolved; T-538 closes Item 4. Item 6 detail still pending operator.
- **All 4 review gates passed**: plan-reviewer pass-2 APPROVE 2026-05-09 (after pass-1 REVISE 1 BLOCKER + 2 CONCERNs; final 3-item Write-time guidance) → drift-checker ON TRACK → brief-reviewer SHIP → math-validator VERIFIED — out of scope, hand-fixture confirmed per AC#14a (packages/exchange/{bybit_v5,paper}/ outside default Gate 4 scope; OQ-4 explicit hand-verification request honored).
- **Operator decisions (4 OQs)**: OQ-1 = Single-page VWAP with explicit limit=100 + nextPageCursor warn; OQ-2 = VWAP parity for paper (NEW SUM/NULLIF helper); OQ-3 = NEW H-035 hazard; OQ-4 = Full hand-verification.
- **Bug**: `bybit_v5/adapter.py:273-296 get_fill_price` returned `items[0]["execPrice"]` only; paper helper was `LIMIT 1 ORDER BY executed_at ASC`. For partial-fill orders, this is the FIRST leg's price NOT the VWAP. Errors compound through compute_sl_price + compute_tp_price + compute_notional_usd + P&L attribution.
- **Fix shape**: bybit_v5 — VWAP loop Decimal arithmetic + explicit `limit=100` + nextPageCursor warn + zero-qty defensive None + warn. Paper — NEW `select_paper_execution_vwap_by_order_id` SUM(price*qty)/NULLIF(SUM(qty),0) helper; PaperExchange.get_fill_price repointed; old helper deprecated kept for backward-compat. `@idempotent` decorator preserved on both adapters.
- **Hand-verified fixture (per OQ-4 + WG#2)**: prices=[100, 101, 99] * qty=[2, 5, 3] → numerator=1002, denominator=10, VWAP=Decimal("100.2") exact. Verbatim across bybit_v5 mock test + paper persistence testcontainer test.
- **Tests**: 3 NEW bybit_v5 + 2 NEW paper persistence testcontainer-gated + 4 existing UPDATED (limit=100 / execQty / single-leg rename) + 2 mock repoints in test_paper_emission.py.
- **Repo baseline 2249 → 2254** (+5 net new). 0 regressions.
- **§0.3 LOC**: ~58 src + ~145 tests = ~205 LOC delta. Far under cap.
- **NEW H-035 hazard** in BRIEF §20 (after H-034). Companion to H-030..H-035 audit cluster.
- **Per L-021 active control**: testcontainer tests verified locally with POSTGRES_TEST_DSN BEFORE push.
- **Plan**: `docs/plans/T-538-vwap-fill-price.md` (APPROVED pass-2 with 3 WG verbatim).
- **Commits**: feat `e0ad247` on `feat/T-538-vwap-fill-price`; chore close pending.

### 7-bug operator audit (2026-05-08) — final progress tracker

| # | Title | Severity | Status |
|---|-------|----------|--------|
| 1 | Paper mode silent dispatcher kill | CRITICAL | DONE — `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 |
| 2 | Signal-loss between dedup-check and publish | HIGH | DONE — T-537 cluster 2026-05-09 |
| 3 | position_state row identity could mismatch trade_id | HIGH | DONE — `fix(T-217c-position-state-trade-id-guard)` 2026-05-09 |
| 4 | Fill-price uses last-trade close (not VWAP) | MEDIUM | **DONE — T-538 2026-05-09** |
| 5 | Fill-price-fetch retry exception swallowing | HIGH | DONE — `fix(T-216c-fill-price-retry-exception)` 2026-05-09 |
| 6 | Reserved (audit detail not yet pulled) | TBD | TBD — operator surface |
| 7 | Outbox-publish reliability gap | HIGH | DONE — T-537 cluster 2026-05-09 |

**6 of 7 audit items fully DONE**. Only Item 6 (detail-pending) remains. Audit cluster H-030..H-035 fully shipped (5 hazards from operator audit + 1 hazard from outbox cluster cross-reference).

### Next session pickup

- **Item 6 detail surface** — operator to surface the deferred audit detail.
- **F5 numbered tasks remaining**: T-516a2 (UI routes for paper trades), T-516b (shadow variants section), T-518..T-521 (existing F5 backend polish), T-524..T-536 (pre-live operational hardening per ADR-0011), T-522 close-out + Live-ready sign-off.

## 2026-05-09 (late-night XVII — T-537b signal-gateway outbox integration shipped; F5 counter advances 28/49 → 29/50; T-537 cluster 3 of 3 done; audit Items 2 + 7 fully RESOLVED; NEW H-034 hazard)

**F5 phase counter advances 28/49 → 29/50** per WG#7 (numerator+1 for shipped T-537b, denominator+1 for new T-537b numbered task). T-537 cluster (T-537a1 + T-537a2 + T-537b) FULLY COMPLETE.

### T-537b — signal-gateway outbox integration (closes audit Items 2 + 7)

- **Origin**: final task of T-537 cluster L-007 split per operator decision 2026-05-09. Wires T-537a1 base infra + T-537a2 relay worker into signal-gateway lifespan + collapses webhook publish path through outbox.
- **All 4 review gates passed**: plan-reviewer pass-2 APPROVE 2026-05-09 (after pass-1 REVISE 0 BLOCKERs+2 CONCERNs: test_webhook_e2e.py timing post-relay + docs/modules/signal_gateway.md drift; final 5-item Write-time guidance) → drift-checker ON TRACK → brief-reviewer SHIP → math-validator VERIFIED — out of scope.
- **Operator decisions (4 OQs)**: OQ-1 = Single tx (atomic state-and-publish-intent); OQ-2 = Full removal of direct bus.publish; OQ-3 = NEW H-034 hazard; OQ-4 = Testcontainer PG + mocked NATS.
- **Webhook refactor**: Step 11 + 12 collapse into `async with pool.acquire() as conn, conn.transaction():` wrapping insert_signal + insert_outbox_event. Direct bus.publish('signals.validated') REMOVED. Single error path on tx fail.
- **Lifespan**: OutboxRelayWorker hosted as asyncio.create_task; H-034 shutdown ordering pinned (stop → bus.close → pool.close); `_ = relay_task` RUF006 dance.
- **Settings**: `Settings.outbox_relay: OutboxRelaySettings = Field(default_factory=...)` nested env routing.
- **Tests**: 3 NEW unit (validated path does NOT call bus.publish + same-tx via spy + tx-rollback) + 1 NEW lifespan H-034 ordering test (exact call ordering pin) + 2 NEW testcontainer-gated e2e (split into 2 to avoid TestClient cross-loop asyncio quirk per WG#3); 1 obsolete publish-failure test REMOVED. Existing test_webhook_e2e.py timeout 5.0 → 10.0 per WG#4 with explicit T-537b comment.
- **NEW H-034 hazard** in BRIEF §20: outbox relay shutdown ordering must be `stop()` → `bus.close()` → `pool.close()` (in-flight tx needs bus + pool alive until cancellation propagates). Test pin via shared call_order list + exact equality assertion.
- **BRIEF §9.1 step 12 reword**: removed direct publish reference, cites T-537 outbox routing.
- **docs/modules/signal_gateway.md** 4 sites updated per pass-1 CONCERN #2 fix.
- **Repo baseline 2244 → 2249** (+5 net new: 3 unit + 1 lifespan + 2 e2e). 0 regressions.
- **§0.3 LOC**: net **-14 src LOC** (refactor reduces) + ~250 tests = ~250 total.
- **Per L-021 active control**: testcontainer tests verified locally with `POSTGRES_TEST_DSN` BEFORE push (closes L-008 sub-gap that motivated L-021).
- **Plan**: `docs/plans/T-537b-signal-gateway-outbox-integration.md` (APPROVED pass-2 with 5 WG verbatim).
- **Commits**: feat `687ec82` on `feat/T-537b-signal-gateway-outbox-integration`; chore close pending.

### 7-bug operator audit (2026-05-08) — final progress tracker

| # | Title | Severity | Status |
|---|-------|----------|--------|
| 1 | Paper mode silent dispatcher kill | CRITICAL | DONE — `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 |
| 2 | Signal-loss between dedup-check and publish | HIGH | **DONE — T-537b 2026-05-09 (cluster: T-537a1 + T-537a2 + T-537b)** |
| 3 | position_state row identity could mismatch trade_id | HIGH | DONE — `fix(T-217c-position-state-trade-id-guard)` 2026-05-09 |
| 4 | Fill-price uses last-trade close (not VWAP) | MEDIUM | DEFERRED → NEW T-538 VWAP fill price |
| 5 | Fill-price-fetch retry exception swallowing | HIGH | DONE — `fix(T-216c-fill-price-retry-exception)` 2026-05-09 |
| 6 | Reserved (audit detail not yet pulled) | TBD | TBD |
| 7 | Outbox-publish reliability gap | HIGH | **DONE — T-537b 2026-05-09 (same cluster)** |

5 of 7 audit items fully DONE (Items 1 + 2 + 3 + 5 + 7); Item 4 → NEW T-538 (VWAP) deferred; Item 6 detail still pending operator. Audit cluster H-030..H-034 fully shipped.

### Next session pickup

- **NEW T-538 VWAP fill price** (Item 4; replace last-trade-close with VWAP across exec list).
- **Item 6 detail pull** still pending — operator to surface.
- **F5 numbered tasks remaining**: T-516a2 (UI routes for paper trades), T-516b (shadow variants section), T-518..T-521 (existing F5 backend polish), T-524..T-536 (pre-live operational hardening per ADR-0011), T-522 close-out + Live-ready sign-off.

## 2026-05-09 (late-night XVI — fix(T-537a1-sql-typecast) shipped; ci-full unblock; NEW L-021 lesson; F5 counter UNCHANGED 28/49)

**F5 phase counter UNCHANGED at 28/49** (fix() commits don't count toward F5 numbered task counter; mirror `fix(T-218b)` + `fix(T-218c)` + `fix(T-216c)` + `fix(T-217c)` + `fix(T-511b2a)` precedent).

### fix(T-537a1-sql-typecast) — explicit ::timestamptz casts (ci-full unblock)

- **Origin**: T-537a1 (commit 6008ea0) ci-full FAILED 2026-05-09 with 7 testcontainer-gated tests in `tests/integration/queries/test_outbox.py`. T-537a2 (commit d30f36a) ci-full propagated the same failure. Master shipped broken; local pytest passed because tests skip without `POSTGRES_TEST_DSN` — L-008 sub-gap (tests existed but were never locally executed before push).
- **All 4 review gates passed**: drift-checker ON TRACK → brief-reviewer SHIP → math-validator VERIFIED out-of-scope (trivial 13-LOC surgical SQL fix).
- **Bug 1 (5 of 7 failures)**: `select_pending_outbox_events` SQL `last_attempt_at <= $2 - make_interval(...)`. Without explicit cast on `$2`, PG type-inference resolves to `interval - interval = interval`; `timestamptz <= interval` has no operator. Fix: `$2::timestamptz`.
- **Bug 2 (2 of 7 failures)**: `mark_outbox_event_failed` SQL `failed_at = CASE WHEN ... THEN $5 ELSE NULL END`. CASE result-type defaults to text for untyped param + NULL branch. Fix: `$5::timestamptz`.
- **Tests**: 2 NEW mock SQL string assertions pinning `$2::timestamptz - make_interval` + `$5::timestamptz ELSE NULL END` literals (regression guard).
- **NEW L-021 lesson** in `docs/review-lessons.md`: PG parameter type-inference fails in non-column-direct contexts (arithmetic with operator overloading, CASE branches); active control requires explicit `::type` casts + locally-executed testcontainer tests before push (L-008 sub-gap closure).
- **Verification**: `POSTGRES_TEST_DSN=postgresql://scalper:devpass@localhost:5432/postgres uv run pytest -q` → 2244 passed (+116 testcontainer-gated; 0 regressions; the 7 outbox testcontainer tests now pass).
- **§0.3 LOC**: ~3 src + ~8 tests = ~11 LOC trivial.
- **Branch**: `fix/T-537a1-sql-typecast`.
- **Commits**: feat `d1b531d` on branch; chore close pending.

### Process gap exposed (L-021 motivation)

L-008 active control says "non-trivial SQL helpers need a testcontainer integration test." T-537a1 + T-537a2 BOTH satisfied this (test_outbox.py + test_relay.py already shipped 11 testcontainer-gated tests). But L-008 stops at "test exists" — does NOT mandate "test was executed locally before push." Result: master shipped broken twice in succession; CI was the first execution surface.

L-021 closes this sub-gap — for any task adding a new testcontainer-gated test file (or modifying existing one with new SQL), implementation session MUST run `POSTGRES_TEST_DSN=... uv run pytest <test_file>` locally before push. Plus the deeper SQL-pattern lesson: PG parameter type-inference is fragile in non-column-direct contexts (arithmetic with operator overloading, CASE branches) — explicit `::type` casts are required defensive.

### 7-bug operator audit (2026-05-08) — progress tracker

| # | Title | Severity | Status |
|---|-------|----------|--------|
| 1 | Paper mode silent dispatcher kill | CRITICAL | DONE — `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 |
| 2 | Signal-loss between dedup-check and publish | HIGH | IN PROGRESS — T-537 cluster (T-537a1 + T-537a2 done; T-537b pending) |
| 3 | position_state row identity could mismatch trade_id | HIGH | DONE — `fix(T-217c-position-state-trade-id-guard)` 2026-05-09 |
| 4 | Fill-price uses last-trade close (not VWAP) | MEDIUM | DEFERRED → NEW T-538 VWAP fill price |
| 5 | Fill-price-fetch retry exception swallowing | HIGH | DONE — `fix(T-216c-fill-price-retry-exception)` 2026-05-09 |
| 6 | Reserved (audit detail not yet pulled) | TBD | TBD |
| 7 | Outbox-publish reliability gap | HIGH | IN PROGRESS — T-537 cluster (T-537a1 + T-537a2 done; T-537b pending) |

3 of 7 audit items fully DONE; Items 2 + 7 IN PROGRESS via T-537 cluster (infra + relay both shipped + green on CI after typecast fix); T-537b signal-gateway integration completes them.

### Next session pickup

- **NEW T-537b signal-gateway integration** (Items 2 + 7 final close): refactor `services/signal_gateway/app/webhook.py:411-474` + wire `OutboxRelayWorker` into lifespan + integration test. F5 counter 28/49 → 29/50.
- **NEW T-538 VWAP fill price** (Item 4).
- **Item 6 detail pull** still pending.
- **F5 numbered tasks remaining**: T-516a2 + T-516b + T-518..T-521 + T-524..T-536 + T-522 close-out.

## 2026-05-09 (late-night XV — T-537a2 outbox relay worker shipped; F5 counter advances 27/48 → 28/49; T-537 cluster 2 of 3 done)

**F5 phase counter advances 27/48 → 28/49** per WG#7 (numerator+1 for shipped T-537a2, denominator+1 for new T-537a2 numbered task). T-537b (signal-gateway integration) remains in pending column with denominator increment at its plan-stage time.

### T-537a2 — outbox relay worker (OutboxRelayWorker + 11 unit tests)

- **Origin**: T-537 cluster L-007 split per operator decision 2026-05-09 (parent T-537a → T-537a1 + T-537a2 + T-537b); consumes T-537a1 outbox base infra (queries + types + migration 0016 shipped 2026-05-09 commits 6008ea0 + d06aaee).
- **All 4 review gates passed**: plan-reviewer pass-3 APPROVE 2026-05-09 (after pass-1 REVISE 2 BLOCKERs+4 CONCERNs + pass-2 REVISE 3 BLOCKERs+3 CONCERNs surfaced post-fix; final 5-item Write-time guidance baked) → drift-checker ON TRACK → brief-reviewer SHIP → math-validator VERIFIED — out of scope.
- **Operator decisions (2 NEW T-537a2-specific OQs + 7 carried-forward)**: OQ-1 = Serial publish (FIFO order per service); OQ-2 = Silent cancel (cancellation = shutdown, not failure; no mark_failed on cancel; mirror dispatcher / shadow_worker precedents).
- **Transaction & lock semantics — Variant B (batch-level tx)** per plan-reviewer pass-2 BLOCKER #1 fix: `async with pool.acquire() as conn, conn.transaction():` wraps entire batch; FOR UPDATE SKIP LOCKED holds rows through publish-and-mark; one COMMIT covers all marks at batch tx exit; partial-batch failure isolation via mark semantics (NOT per-event tx).
- **Envelope construction from outbox row fields** per pass-2 BLOCKER #2 fix: payload column stores BUSINESS event dict; correlation_id is separate column; publisher = service; relay constructs `MessageEnvelope(correlation_id=CorrelationId(event.correlation_id or ""), publisher=self._service, payload=event.payload)` then `await bus.publish(event.subject, envelope)`.
- **Per-event try/except** per WG#4: `except Exception` (NOT `except BaseException`) with `# noqa: BLE001`; CancelledError propagates UP uncaught; conn.transaction __aexit__ ROLLBACK; rows return to pending state. Test #7 pins via `mark_failed.assert_not_called()`.
- **Logger keys** per WG#3: 5 module-level `Final[str]` constants + `_LOG_KEYS` frozenset registry; NO f-string concat; NO class-level constants.
- **Tests**: 11 mock-based unit tests (9 planned + 2 bonus pins). SQL semantics already pinned at T-537a1 testcontainer level — no duplicate testcontainer test needed.
- **Repo baseline 2117 → 2128** (+11 net new unit tests). 0 regressions.
- **§0.3 LOC**: ~226 src under 400 cap (no waiver needed; smaller than T-537a1 at 365 LOC).
- **`run_relay_for_service` adapter DROPPED** from T-537a2 scope per pass-1 BLOCKER #2 fix — T-537b owns its lifespan integration shape directly via `asyncio.create_task(worker.run())` + `worker.stop()`.
- **No NEW lesson** for T-537a2 (no generalizable catch beyond existing L-007/L-008/L-014 active controls re-applied).
- **Plan**: `docs/plans/T-537a2-outbox-relay-worker.md` (APPROVED pass-3 with 5 WG verbatim).
- **Commits**: feat `d30f36a` on `feat/T-537a2-outbox-relay-worker`; chore close pending.

### 7-bug operator audit (2026-05-08) — progress tracker

| # | Title | Severity | Status |
|---|-------|----------|--------|
| 1 | Paper mode silent dispatcher kill | CRITICAL | DONE — `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 |
| 2 | Signal-loss between dedup-check and publish | HIGH | IN PROGRESS — T-537 cluster (T-537a1 + T-537a2 done; T-537b pending) |
| 3 | position_state row identity could mismatch trade_id | HIGH | DONE — `fix(T-217c-position-state-trade-id-guard)` 2026-05-09 |
| 4 | Fill-price uses last-trade close (not VWAP) | MEDIUM | DEFERRED → NEW T-538 VWAP fill price |
| 5 | Fill-price-fetch retry exception swallowing | HIGH | DONE — `fix(T-216c-fill-price-retry-exception)` 2026-05-09 |
| 6 | Reserved (audit detail not yet pulled) | TBD | TBD |
| 7 | Outbox-publish reliability gap | HIGH | IN PROGRESS — T-537 cluster (T-537a1 + T-537a2 done; T-537b pending) |

3 of 7 audit items fully DONE (Items 1 + 3 + 5); Items 2 + 7 IN PROGRESS — outbox infra (T-537a1) + relay worker (T-537a2) shipped; T-537b signal-gateway integration completes them.

### Next session pickup

- **NEW T-537b signal-gateway integration** (Items 2 + 7 final close): refactor `services/signal_gateway/app/webhook.py:411-474` to write event-intent via `insert_outbox_event` inside the same tx as `insert_signal` + remove direct `bus.publish("signals.validated")` call (relay handles it post-commit); wire `OutboxRelayWorker` into `services/signal_gateway/app/main.py` lifespan with shutdown ordering `worker.stop()` → `bus.close()` → `pool.close()`; `OutboxRelaySettings` composition into Settings; integration test exercising full pipeline (insert_signal → insert_outbox_event → relay polls → bus.publish → mark_published). F5 counter 28/49 → 29/50. Items 2 + 7 fully resolved at T-537b ship.
- **NEW T-538 VWAP fill price** (Item 4; replace last-trade-close with VWAP across exec list).
- **Item 6 detail pull** still pending — operator to surface.
- **F5 numbered tasks remaining** (separate from outbox + audit fixes): T-516a2 (UI routes for paper trades), T-516b (shadow variants section), T-518..T-521 (existing F5 backend polish), T-524..T-536 (pre-live operational hardening per ADR-0011), T-522 close-out + Live-ready sign-off.

## 2026-05-09 (late-night XIV — T-537a1 outbox base infra shipped; F5 counter advances 26/47 → 27/48; T-537 cluster decomposed per L-007 split + operator hybrid scope)

**F5 phase counter advances 26/47 → 27/48** per WG#7 (numerator+1 for shipped T-537a1, denominator+1 for new T-537a1 numbered task). T-537a2 (relay worker) + T-537b (signal-gateway integration) remain in pending column with their denominator increments at THEIR ship-time per existing TASKS.md narrative pattern.

### T-537a1 — outbox base infra (queries + types + migration 0016)

- **Origin**: operator audit Items 2 + 7 (signal-loss between dedup-record and publish + generic publish-after-persist gap; 7-bug audit 2026-05-08).
- **Operator hybrid scope decision 2026-05-09 + L-007 sub-split**: T-537 → T-537a → T-537a1 (this; queries + types + migration) + T-537a2 (relay worker; pending) + T-537b (signal-gateway integration; pending). T-537c (execution) + T-537d (strategy-engine) deferred indefinitely.
- **All 4 review gates passed**: plan-reviewer single-pass APPROVE 2026-05-09 (parent T-537a APPROVE → split per operator → T-537a1 re-review APPROVE with 13-item Write-time guidance; 8 carried forward + 5 NEW from re-review) → drift-checker ON TRACK → brief-reviewer SHIP → math-validator VERIFIED — out of scope.
- **Migration 0016**: NEW generic `outbox_events` table (11 columns + 2 partial indexes; service column discriminator; mirror trading_events single-table pattern). Forward-only per §N8; `alembic downgrade 0015` explicit revision target per L-012. 4 testcontainer-gated migration tests.
- **NEW `packages/outbox/` shared package**: `types.py` (OutboxEvent frozen dataclass + OutboxRelaySettings Pydantic with env_prefix=OUTBOX_RELAY_ + Field validators + cap >= base validator); `queries.py` (4 SQL helpers per §N3 markers: insert @non_idempotent, select read-only with FOR UPDATE SKIP LOCKED + PG `power` backoff math single source of truth, mark_published @idempotent, mark_failed @non_idempotent with CASE flip; codec-immune `json.dumps(_to_jsonable(payload))` per L-013).
- **Tests**: 8 unit (test_types) + 12 unit (test_queries) + per L-008 7 testcontainer-gated integration (test_outbox.py: round-trip + mark_published + mark_failed below-max/exhaustion + backoff window with hardcoded 4.0s per WG#5 + service filter + FOR UPDATE SKIP LOCKED disjoint replicas).
- **Repo baseline 2097 → 2117** (+20 net new unit tests; +11 testcontainer-gated skip without POSTGRES_TEST_DSN per F1 pattern). 0 regressions.
- **§0.3 LOC**: ~365 src under 400 cap (no waiver needed). Smallest of new-infra cohort (vs T-511b1 627, T-512a 570, T-513b1 491).
- **NEW BRIEF §8.7** sub-section appended (outbox pattern reference; cross-link to plan + L-013).
- **No NEW lesson** for T-537a1 (no generalizable catch beyond existing L-008/L-012/L-013/L-014 active controls re-applied).
- **Plan**: `docs/plans/T-537a1-outbox-queries-types-migration.md` (APPROVED single-pass with 13 WG verbatim).
- **Commits**: feat `6008ea0` on `feat/T-537a1-outbox-queries-types-migration`; chore close pending.

### 7-bug operator audit (2026-05-08) — progress tracker

| # | Title | Severity | Status |
|---|-------|----------|--------|
| 1 | Paper mode silent dispatcher kill | CRITICAL | DONE — `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 |
| 2 | Signal-loss between dedup-check and publish | HIGH | IN PROGRESS — T-537 cluster (T-537a1 done; T-537a2 + T-537b pending) |
| 3 | position_state row identity could mismatch trade_id | HIGH | DONE — `fix(T-217c-position-state-trade-id-guard)` 2026-05-09 |
| 4 | Fill-price uses last-trade close (not VWAP) | MEDIUM | DEFERRED → NEW T-538 VWAP fill price |
| 5 | Fill-price-fetch retry exception swallowing | HIGH | DONE — `fix(T-216c-fill-price-retry-exception)` 2026-05-09 |
| 6 | Reserved (audit detail not yet pulled) | TBD | TBD |
| 7 | Outbox-publish reliability gap | HIGH | IN PROGRESS — T-537 cluster (T-537a1 done; T-537a2 + T-537b pending) |

3 of 7 audit items fully DONE (Items 1 + 3 + 5); Items 2 + 7 in progress (T-537 cluster started — infrastructure half shipped this commit, remaining T-537a2 relay worker + T-537b signal-gateway integration).

### Next session pickup

- **NEW T-537a2 outbox relay worker** — `packages/outbox/relay.py` `OutboxRelayWorker` class with `run` + `stop` lifecycle, retry math wiring to SQL helpers (T-537a1 already provides backoff math), shutdown ordering contract, 5 logger keys (`outbox.relay.*`). Plan stage → plan-reviewer → implement → 4 gates → ff-merge. F5 counter 27/48 → 28/49.
- **After T-537a2**: T-537b signal-gateway integration (webhook.py refactor: replace `bus.publish("signals.validated")` with `insert_outbox_event` inside same tx as `insert_signal`; wire OutboxRelayWorker into `services/signal_gateway/app/main.py` lifespan; Settings composition; integration test). F5 counter 28/49 → 29/50. Items 2 + 7 of audit fully resolved at T-537b ship.
- **NEW T-538 VWAP fill price** (Item 4; replace last-trade-close with VWAP across exec list).
- **Item 6 detail pull** still pending — operator to surface before any further T-NNN allocation.
- **F5 numbered tasks remaining** (separate from outbox + audit fixes): T-516a2 (UI routes for paper trades), T-516b (shadow variants section), T-518..T-521 (existing F5 backend polish), T-524..T-536 (pre-live operational hardening per ADR-0011), T-522 close-out + Live-ready sign-off.

## 2026-05-09 (late-night XIII — fix(T-217c-position-state-trade-id-guard) shipped; H-033 NEW hazard + L-020 NEW lesson; 7-bug operator audit Item 3 of 7 done; branch step restored)

**F5 phase counter UNCHANGED at 26/47** (fix() commits don't count toward F5 numbered task counter; mirror `fix(T-218b)` + `fix(T-218c)` + `fix(T-216c)` + `fix(T-511b2a)` precedent).

### fix(T-217c-position-state-trade-id-guard) — composite-PK position_state UPDATE must include trade_id in WHERE clause

- **All 4 review gates passed**: plan-reviewer single-pass APPROVE 2026-05-09 (10-item Write-time guidance verbatim) → drift-checker ON TRACK (5 staged files; ~204 LOC; all 10 WG verified) → brief-reviewer SHIP (10/10 WG; §11.3 N/A; §N1/§N3/§N5/§N6/§N8 unchanged; §N4 TDD deviation justified) → math-validator VERIFIED — out of scope (composition-only at semantic level; SQL signature extension + integer comparison + log; no Decimal/float arithmetic; mirror T-218c/T-216c precedent).
- **Bug**: `packages/db/queries/execution.py:636-680` `update_position_state_after_fill` modified `position_state` rows via composite PK `(bot_id, symbol)` only — `trade_id` was NOT in the WHERE clause. ExecutionDispatcher's `_derive_exec_type` Path A (`order_id_match is not None`) sources `trade_id` from the `trades` table; under a benign close→reopen race, the position_state row identity changed between derivation and write — UPDATE silently mutated the wrong trade's row. Phantom close cascade.
- **Real-world impact**: Live: real money committed on T2 open; phantom-closed in DB → T-221 reconcile_orphan flow → emergency_close real position closure. Paper: corruption stays in DB; could mask paper P&L drift. Either mode (sibling L-018 active control: NOT a "dormant in mode" claim).
- **Why not surfaced earlier**: T-218a/T-218b/T-218c reviewer focus was the `exec_type` derivation branch table; the WHERE-clause omission for `update_position_state_after_fill` slipped through because all existing test cases used a SINGLE trade per (bot_id, symbol) lifetime fixture (L-020 active control). Race-window observation requires sustained live operation across multiple close→reopen cycles.
- **Fix shape per operator OQ-1=B (NOT default A; SQL WHERE trade_id extension chosen over composition-only dispatcher guard) + OQ-2=A + OQ-3=A + OQ-4=A**: SQL helper signature gains required kwarg `trade_id: int` + return type changed `None` → `int` (parsed from asyncpg `"UPDATE <n>"` command tag via `int(result.split()[-1])` mirror analytics.py:2108 precedent). Both UPDATE branches add `AND trade_id = $N` to WHERE. Dispatcher caller threads derived `trade_id` (`assert trade_id is not None` mypy-narrowing BEFORE call) + checks `rows_updated == 0` → ERROR log + raise RuntimeError. Transaction rolls back; NATS redelivery + T-221 reconciliation own recovery.
- **Tests**: 2 existing unit tests updated with `trade_id` kwarg + `"UPDATE 1"` return mock + SQL assertion. NEW unit test `test_update_position_state_after_fill_returns_zero_on_zero_rows_tag`. NEW testcontainer-gated integration tests per L-008: `test_update_position_state_after_fill_returns_zero_when_trade_id_mismatches` (real PG round-trip; row UNCHANGED on mismatch) + companion `test_update_position_state_after_fill_returns_one_when_trade_id_matches` ($N bind regression guard). NEW dispatcher halt regression `test_dispatcher_halts_on_position_state_trade_id_mismatch_during_fill_update`. 2 existing dispatcher tests add `kwargs["trade_id"] == 1` (Path B trade_id from `_ps_row` default).
- **Repo baseline 2095 → 2097** (+2 net new unit/dispatcher tests; +2 testcontainer-gated integration skipped without POSTGRES_TEST_DSN per F1 pattern). 0 regressions.
- **§0.3 LOC**: ~50 src + ~165 tests = ~215 LOC delta in feat; under cap. Mid-cluster size (T-218b ~98, T-218c ~151, T-216c ~84, T-217c ~215 — expansion due to L-008 testcontainer integration test pair).
- **NEW H-033 hazard entry** in BRIEF §20 (after H-032; companion forming execution-service operational hardening cluster H-030/H-031/H-032/H-033). H-018 vs H-033 scope clarification: H-018 governs `trades` table single-PK updates; H-033 governs `position_state` composite-PK updates under identity-reuse — different tables, different invariants.
- **NEW L-020 lesson** in `docs/review-lessons.md`: composite-PK SQL update helpers under concurrent INSERT/DELETE-then-INSERT need authoritative-id verification at dispatch site; helpers returning rows-updated count must have callers check for 0 and halt-on-mismatch. Active control: plan-reviewer + brief-reviewer MUST grep callers for any composite-PK helper reused across distinct logical entities and verify authoritative identity threading.
- **Branch step RESTORED**: `fix/T-217c-position-state-trade-id-guard` per CLAUDE.md branching policy. Recovery from T-216c slip documented in late-night XII process slip note. Branch flow followed verbatim: checkout -b → feat commit → chore commit → ff-merge → push → branch delete.
- **Plan**: `docs/plans/T-217c-fix-position-state-trade-id-guard.md` (APPROVED single-pass with 10 WG verbatim).
- **Commits**: feat `b74fca2` on `fix/T-217c-position-state-trade-id-guard`; chore close pending.

### 7-bug operator audit (2026-05-08) — progress tracker

| # | Title | Severity | Status |
|---|-------|----------|--------|
| 1 | Paper mode silent dispatcher kill | CRITICAL | DONE — `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 |
| 2 | Signal-loss between dedup-check and publish | HIGH | DEFERRED → NEW T-537 outbox pattern (combined with Item 7) |
| 3 | position_state row identity could mismatch trade_id | HIGH | DONE — `fix(T-217c-position-state-trade-id-guard)` 2026-05-09 |
| 4 | Fill-price uses last-trade close (not VWAP) | MEDIUM | DEFERRED → NEW T-538 VWAP fill price |
| 5 | Fill-price-fetch retry exception swallowing | HIGH | DONE — `fix(T-216c-fill-price-retry-exception)` 2026-05-09 |
| 6 | Reserved (audit detail not yet pulled) | TBD | TBD |
| 7 | Outbox-publish reliability gap | HIGH | DEFERRED → NEW T-537 outbox pattern (combined with Item 2) |

3 of 7 audit items DONE (Items 1 + 3 + 5). Items 2 + 7 → NEW T-537 (outbox); Item 4 → NEW T-538 (VWAP); Item 6 detail pending.

### Next session pickup

- **Item 6 detail pull** — operator to surface the deferred audit detail before any further T-NNN allocation; Items 2/4/7 already mapped to T-537/T-538/T-537.
- **NEW T-537 outbox pattern** (Items 2 + 7; signal-loss publish-after-dedup + outbox-publish reliability gap). Full F5 task; will count toward F5 phase counter.
- **NEW T-538 VWAP fill price** (Item 4; replace last-trade-close with VWAP). Full F5 task.
- **F5 numbered tasks remaining** (separate from fixes): T-516a2 (UI routes for paper trades), T-516b (shadow variants section), T-518..T-521 (existing F5 backend polish), T-524..T-536 (pre-live operational hardening per ADR-0011), T-522 close-out + Live-ready sign-off.

## 2026-05-09 (late-night XII — fix(T-216c-fill-price-retry-exception) shipped; H-032 NEW hazard + L-019 NEW lesson; 7-bug operator audit Item 5 of 7 done)

**F5 phase counter UNCHANGED at 26/47** (fix() commits don't count toward F5 numbered task counter; mirror `fix(T-218b)` + `fix(T-218c)` + `fix(T-511b2a)` precedent).

### fix(T-216c-fill-price-retry-exception) — get_fill_price retry must catch transient exceptions

- **All 4 review gates passed**: plan-reviewer single-pass APPROVE 2026-05-09 (15-item Write-time guidance verbatim) → drift-checker ON TRACK (84 LOC staged; all 15 WG verified) → brief-reviewer SHIP (15/15 WG; §11.3 taxonomy mirror exact) → math-validator VERIFIED — out of scope (composition-only fix; no Decimal/float arithmetic; mirror T-218c Gate 4 verdict).
- **Bug**: `services/execution/app/placement.py:205-232` retry loop iterated `for attempt in range(fill_price_retry_attempts)` over `await adapter.get_fill_price(...)` with NO try/except. Transient adapter exceptions (NetworkTimeout/RateLimitError/AuthError) bypassed (1) the retry counter, (2) the `await asyncio.sleep(backoff)` step, (3) the post-loop `if fill_price is None: DLQ + FillPriceUnresolvedError` contract. Sibling step 4 (set_leverage) + step 5 (place_market_order) DO wrap their await sites with explicit `(AuthError, OrderRejected, NetworkTimeout, RateLimitError)` catches per §11.3 taxonomy — step 6 retry block was forgotten.
- **Real-world impact**: Live: position OPENS on exchange (real money) but DB persistence aborts mid-handler (no trades row, no position_state row, no SLMoved emit). Paper: transient asyncpg errors → similar fatal exit. Either mode: handler exception trips up out of consumer; bus-level swallow means no operator-facing trace. Operator's primary mode is paper per `deployment.md`; v2 multi-service NIE JE deployed; sibling v1 testnet stack disabled 2026-05-02; T-222 testnet smoke (F2 close-out) was never executed end-to-end. Kill-path was never observed at runtime.
- **Why not surfaced earlier**: T-216a / T-216b1 / T-216b2 reviewer focus was post-fill_price pipeline + paper fork + persist-tx + emit ordering; the retry block's `await` site exception coverage slipped through both plan-reviewer and brief-reviewer Gate 3 because no `for attempt in range(*retry*)` audit pattern existed (now becomes L-019 active control).
- **Fix shape per operator OQ-1=A + OQ-2=A + OQ-3=A + OQ-4=A 2026-05-09**: wrap `await adapter.get_fill_price(...)` in `try/except (AuthError, NetworkTimeout, RateLimitError) as exc` (mirror sibling step 5 trio per §11.3 taxonomy); on exception → warn-log key `execution.get_fill_price_transient_error` (kwargs `bot_id` + `exchange_order_id` + `attempt` 1-indexed + `error=str(exc)`) + defensive `fill_price = None` reset → retry counter advances + sleep on remaining attempts. After exhaustion falls through to existing DLQ + `FillPriceUnresolvedError` contract.
- **Tests**: 4 NEW regression tests inserted after `test_handler_dlq_publish_failure_still_raises_FillPriceUnresolvedError`: `test_handler_retries_when_get_fill_price_raises_NetworkTimeout` + `test_handler_retries_when_get_fill_price_raises_RateLimitError` + `test_handler_retries_when_get_fill_price_raises_AuthError` + `test_fill_price_unresolved_after_all_exception_attempts_publishes_to_dlq_and_raises`. All 4 written FIRST + verified FAIL pre-fix per §N4 TDD ordering (4-failed → patch → 21-passed). Existing 4 None-path tests UNCHANGED.
- **Repo baseline 2091 → 2095** (+4 net new tests; 0 regressions).
- **§0.3 LOC**: ~15 src + ~69 tests = ~84 LOC delta in feat; smallest of the surgical-fix cluster (T-218b ~98, T-218c ~151, T-216c ~84).
- **NEW H-032 hazard entry** in BRIEF §20 (after H-031; companion to H-030/H-031 forming execution-service operational hardening cluster).
- **NEW L-019 lesson** in `docs/review-lessons.md`: retry loops over external calls must wrap `await` site with try/except matching the SAME error taxonomy as non-retried sibling calls in the same handler. Active control: plan-reviewer + brief-reviewer MUST grep `for ... in range(*retry*)` patterns and BLOCK any raw `await ext_call(...)` inside without exception handling.
- **Plan**: `docs/plans/T-216c-fix-fill-price-retry-exception.md` (APPROVED single-pass with 15 WG verbatim).
- **Commits**: feat `b9fed0b` directly on master (process slip — branch step skipped; resulting state functionally equivalent to ff-merge); chore close pending.

### 7-bug operator audit (2026-05-08) — progress tracker

| # | Title | Severity | Status |
|---|-------|----------|--------|
| 1 | Paper mode silent dispatcher kill | CRITICAL | DONE — `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 |
| 2 | Signal-loss between dedup-check and publish | HIGH | DEFERRED → NEW T-537 outbox pattern (combined with Item 7) |
| 3 | position_state row identity could mismatch trade_id | HIGH | NEXT — `fix(T-217c-position-state-trade-id-guard)` |
| 4 | Fill-price uses last-trade close (not VWAP) | MEDIUM | DEFERRED → NEW T-538 VWAP fill price |
| 5 | Fill-price-fetch retry exception swallowing | HIGH | DONE — `fix(T-216c-fill-price-retry-exception)` 2026-05-09 |
| 6 | Reserved (audit detail not yet pulled) | TBD | TBD |
| 7 | Outbox-publish reliability gap | HIGH | DEFERRED → NEW T-537 outbox pattern (combined with Item 2) |

Operator-chosen fix order (post-Item-1): Item 5 (DONE this commit) → Item 3 (`fix(T-217c-position-state-trade-id-guard)` next) → NEW T-537 + T-538.

### Process slip note (2026-05-09)

`fix(T-216c)` feat commit was made directly on master (`b9fed0b`) instead of on a `fix/T-216c-fill-price-retry-exception` branch per CLAUDE.md branching policy. Resulting state on master is functionally equivalent to ff-merge from branch (master HEAD pre-commit was `1321d0a` from T-218c chore; T-216c is the only delta on top). Future fix() tasks should restore the branch step explicitly. No corrective action needed for this commit; flagged here for next-session awareness only.

### Next session pickup

- **Next critical fix**: `fix(T-217c-position-state-trade-id-guard)` for Item 3 (position_state row identity could mismatch trade_id). Plan stage → plan-reviewer → implement → 4 gates → ff-merge.
- **After T-217c**: NEW T-537 outbox pattern (Items 2 + 7) + NEW T-538 VWAP fill price (Item 4). Both are full F5 tasks (will count toward F5 phase counter; numbering depends on TASKS.md insertion order and operator OQ at plan time).
- **F5 numbered tasks remaining** (separate from fixes): T-516a2 (UI routes for paper trades), T-516b (shadow variants section), T-518..T-521 (existing F5 backend polish), T-524..T-536 (pre-live operational hardening per ADR-0011), T-522 close-out + Live-ready sign-off.

## 2026-05-08 (late-night XI — fix(T-218c-paper-dispatcher-skip) shipped; H-031 NEW hazard + L-018 NEW lesson + T-218b retrospective correction; 7-bug operator audit Item 1 of 7 done)

**F5 phase counter UNCHANGED at 26/47** (fix() commits don't count toward F5 numbered task counter; mirror `fix(T-218b)` + `fix(T-511b2a)` precedent).

### fix(T-218c-paper-dispatcher-skip) — paper-mode silent kill-path; pre-live blocker

- **All 4 review gates passed**: plan-reviewer single-pass APPROVE 2026-05-08 (7-item Write-time guidance) → drift-checker ON TRACK (5 staged files, +12% LOC vs estimate, 7/7 WG verified) → brief-reviewer SHIP (7/7 WG; 4 feat-staged, 3 chore-deferred per AC-14/OQ-3) → math-validator VERIFIED (out-of-scope per Gate 4 fast no-op; composition-only field add + set-membership skip).
- **Bug**: PaperExchange's `stream_executions` emits ExecutionEvent on every fill (open `paper/adapter.py:820`, close `:930`, synthetic SL/TP `:1185`). ExecutionDispatcher consumes those events but processes them via LIVE tables (`orders` / `trades` / `position_state`). For paper bots, LIVE-table lookups always return None → `_derive_exec_type` returns `('unknown', None, None)` → `dispatcher.py:188 RuntimeError("unattributable fill")` → dispatcher task dies silently. Paper bots simply never received functional dispatcher service from app start.
- **Real-world impact**: Operator's primary mode is paper per memory `deployment.md`; full v2 multi-service NIE JE deployed yet, so kill-path was never observed at runtime. In live deployment, paper bots running alongside live bots in same execution-service process would have BOTH paper dispatcher (crash) AND live dispatchers (functional) — paper events silently lost while live continues.
- **Why not surfaced earlier**: T-218b plan analysis 2026-05-08 incorrectly claimed "PaperExchange synthetic-fill flow does NOT emit ExecutionEvent for open fills via stream_executions (only SL/TP synthetic fills emit). Bug dormant in paper mode" — this was reasoning, NOT code-cited evidence. Direct reading of `paper/adapter.py:820-831` shows `_persist_open` does emit on open. Paper had its own (different) crash path. T-218c addresses paper-mode independently of T-218b's H-030 live-mode fix.
- **Fix shape per operator OQ-1=A + OQ-2=A + OQ-3=A**: `services/execution/app/pool.py` extends `AdapterPoolResult` dataclass with `paper_bot_ids: frozenset[BotId]` field; `build_adapter_pool` populates it during the existing per-bot loop when `bot_row.exchange_mode == "paper"`. `services/execution/app/main.py` dispatcher creation block consults this set via `if bot_id in adapter_pool.paper_bot_ids: continue`. The `orders.requests.<bot_id>` subscriber STAYS for paper bots (placement handler routes paper orders via PaperExchange independently).
- **Tests**: NEW `test_build_adapter_pool_populates_paper_bot_ids` (positive build path) + NEW `test_lifespan_does_not_create_dispatcher_task_for_paper_bots` (lifespan regression guard with 4 explicit assertions: live dispatcher created + paper dispatcher NOT created + orders.requests subscribers preserved for both bots). 13 fake_pool_result fixture sites in test_app_factory.py updated with `paper_bot_ids = frozenset()` default. mock_adapter_pool_result conftest fixture updated.
- **Repo baseline 2089 → 2091** (+2 net new tests; 0 regressions).
- **§0.3 LOC**: ~13 src + ~138 tests = ~151 LOC delta in feat; far under cap.
- **NEW H-031 hazard entry** in BRIEF §20 (after H-030; paired sibling — H-030 covers live-mode dispatcher contract, H-031 covers paper-mode contract; together complete dispatcher safety contract for both modes).
- **NEW L-018 lesson** in `docs/review-lessons.md`: plan analysis claiming "bug dormant in <mode>" or "<mode> not affected by this issue" requires code-citation evidence (file:line + grep), NOT reasoning. Active control: plan-reviewer Gate 1 must BLOCK any "no-impact" claim about a code path without grep evidence; brief-reviewer + math-validator should similarly verify at commit time. Operator-driven audit caught this; review system should catch earlier at plan time.
- **T-218b retrospective correction**: T-218b row in TASKS.md amended to flag the "bug dormant in paper mode" claim as INCORRECT and document the actual paper-mode crash path (per operator OQ-3 — single-line append within original T-218b row, not a new task entry).
- **Plan**: `docs/plans/T-218c-fix-paper-dispatcher-skip.md` (APPROVED single-pass with 7 WG verbatim).
- **Commits**: feat `c84c65b` on `fix/T-218c-paper-dispatcher-skip`; chore close pending.

### 7-bug operator audit (2026-05-08) — progress tracker

Operator submitted 7-item shipped-code bug audit 2026-05-08; chose Item 1 first per recommendation.

| # | Title | Severity | Status |
|---|-------|----------|--------|
| 1 | Paper mode silent dispatcher kill | CRITICAL | DONE — `fix(T-218c-paper-dispatcher-skip)` shipped |
| 2 | Signal-loss between dedup-check and publish | HIGH | DEFERRED → NEW T-537 outbox pattern (per OQ; combined with Item 7) |
| 3 | position_state row identity could mismatch trade_id | HIGH | NEXT — `fix(T-217c-position-state-trade-id-guard)` |
| 4 | Fill-price uses last-trade close (not VWAP) | MEDIUM | DEFERRED → NEW T-538 VWAP fill price |
| 5 | Fill-price-fetch retry exception swallowing | HIGH | NEXT — `fix(T-216c-fill-price-retry-exception)` |
| 6 | Reserved (audit detail not yet pulled) | TBD | TBD |
| 7 | Outbox-publish reliability gap | HIGH | DEFERRED → NEW T-537 outbox pattern (combined with Item 2) |

Operator-chosen fix order: Item 1 first (DONE this commit), then Item 5 (`fix(T-216c)` — fill-price retry exception swallowing), then Item 3 (`fix(T-217c)` — position_state trade_id guard). Items 2 + 7 → NEW T-537 outbox pattern (full F5 task; not a fix). Item 4 → NEW T-538 VWAP fill price (full F5 task; not a fix).

### Next session pickup

- **Next critical fix**: `fix(T-216c-fill-price-retry-exception)` for Item 5 (fill-price-fetch retry exception swallowing). Plan stage → plan-reviewer → implement → 4 gates → ff-merge.
- **After T-216c**: `fix(T-217c-position-state-trade-id-guard)` for Item 3.
- **After all 3 fixes**: NEW T-537 + T-538 plan stages (full F5 tasks; will count toward F5 phase counter as 27/47 + 28/47 — actual numbering depends on TASKS.md insertion order and operator OQ at plan time).
- **F5 numbered tasks remaining** (separate from fixes): T-516a2 (UI routes for paper trades), T-516b (shadow variants section), T-518..T-521 (existing F5 backend polish), T-524..T-536 (pre-live operational hardening per ADR-0011), T-522 close-out + Live-ready sign-off. Most can resume after current fix cluster lands.

## 2026-05-08 (late-night X — fix(T-218b-open-fill-qty-bug) CRITICAL pre-live blocker shipped; H-030 NEW hazard + L-017 NEW lesson)

**F5 phase counter UNCHANGED at 26/47** (fix() commits don't count toward F5 numbered task counter; mirror `fix(T-511b2a)` precedent).

### fix(T-218b-open-fill-qty-bug) — pre-live blocker

- **All 4 review gates passed**: plan-reviewer single-pass APPROVE 2026-05-08 (10-item Write-time guidance) → drift-checker ON TRACK (10/10 WG verified) → brief-reviewer SHIP → math-validator VERIFIED (conditional-skip only; no new arithmetic; existing partial_tp/sl/trail/close arithmetic preserved bit-identical).
- **Bug**: ExecutionDispatcher unconditionally called `update_position_state_after_fill(qty_delta=message.qty)` for ANY exec_type including 'open'. Placement tx (`placement_persist.py:419`) writes `remaining_qty=request.qty` at trade-open commit; WS execution event for open fill with `message.qty == request.qty` would zero remaining_qty → trigger close-flow → trade marked closed in DB while position open on exchange.
- **Real-world impact (live/testnet)**: phantom close + cumulative-delta P&L drift + spurious shadow variant cancel + reconcile_orphan emergency_close. Bug dormant in paper mode (PaperExchange does NOT emit ExecutionEvent for open fills via stream_executions per analysis).
- **Operator decision**: surgical patch as `fix(T-218b-...)` urgent-fix on master mirror `fix(T-511b2a)` precedent; NOT new F5 task. Operator-confirmed analysis 2026-05-08 + approved fix shape + suggested defensive close-trigger guard.
- **Fix shape**: dispatcher.py wrap `update_position_state_after_fill` in `if exec_type != "open":`. `insert_execution` (audit row) + `update_trade_fees_incremental` (entry fee) STAY OUTSIDE. Defensive close-trigger guard: prepend `exec_type != "open" and` to existing `if ps_after.remaining_qty == Decimal("0"):` check.
- **Tests**: existing `test_process_open_fill_orders_lookup_to_open_branch` fixed with realistic placement-time pre-fill (matching factory default `Decimal("5")`); 4 explicit assertions per L-017. NEW `test_process_open_fill_does_not_decrement_remaining_qty` regression guard. REPURPOSED former B1 test (encoded bug-as-expected) → renamed `test_process_open_fill_with_zero_remaining_qty_does_NOT_trigger_close` as defensive-guard pin (assertion FLIPPED to assert_not_called).

### NEW H-030 hazard catalog entry (BRIEF §20)

H-030 verbatim: "Open-fill must not decrement remaining_qty". Inserted after H-026 (H-027/H-028/H-029 reserved for ADR-0011 anticipated hazards per T-525/T-534/T-535 pre-live operational hardening cluster). H-030 is the first concrete hazard discovered post-ADR-0011.

### NEW L-017 lesson (review-lessons.md)

Pattern: Test fixtures using artificial "post-update" mock values to bypass downstream state-checks can hide bugs in the pre-update logic. Active control: state-mutation tests MUST use REALISTIC pre-mutation entering values (not artificial post-update placeholders) + assert BOTH "what was called" AND "what was NOT called" sides via explicit `assert_called_once` / `assert_not_called` on EVERY mutating helper. Plan-reviewer Gate 1 must flag fixtures with comments like "post-update non-zero". Brief-reviewer Gate 3 must grep `assert_not_called` count in tests touching `services/execution/` paths.

### Watch-outs for next session

- **fix(T-218b) closes a live-deployment blocker** — without this fix, live mode would have phantom-closed every trade on first open-fill WS event. T-525 / T-534 / T-535 (ADR-0011 anticipated hazards) likely have similar dormant-in-paper bugs that would need pre-live testnet validation.
- **PaperExchange open-fill stream verification** — flagged as out-of-scope per fix WG#8 but should be verified at T-516a2 plan stage or T-525 plan stage. Question: does PaperExchange emit ExecutionEvent for OPEN fills (synthetic) via `stream_executions`? If yes, paper mode also affected by the same bug class. Analysis suggests no (only SL/TP synthetic fills emit) but explicit verification needed before live deployment.
- **L-017 active control** strengthens future plan-reviewer + brief-reviewer for state-mutation tests. Plan-reviewer Gate 1 must flag artificial post-update mock values. Brief-reviewer Gate 3 must grep `assert_not_called` count in `services/execution/` test files.
- **F5 next pickup** — T-516a2 (UI routes for paper-trade drill-down) + T-516b (shadow variants section in both routes). Then T-518..T-521 (existing F5 backend polish). Then T-524..T-536 (pre-live operational hardening per ADR-0011). Then T-522 close-out + Live-ready sign-off.

### Lessons surfaced (2 today)

- **L-017 (this session)**: artificial post-update mock values in state-mutation tests mask pre-update bugs. Active control above.
- **No new calibration data point** — fix() tasks are surgical/specialized; calibration band of L-006/L-014/L-016 not applicable to bug-fix commits (which are scoped by bug semantic, not phase work-item).

---

## 2026-05-08 (late-night IX — T-516a1 paper-trade analytics-api backend shipped; T-516 split into T-516a1 + T-516a2 + T-516b; backend mirror /api/trades/* 1:1)

**F5 phase: 26/47 tasks done (~55%).** Master HEAD pre-merge `6c5365d` (chore for T-513b2). T-516 reorg 2026-05-08 split into T-516a1 (this; backend; DONE) + T-516a2 (UI routes + nav + shared component module; PENDING) + T-516b (shadow variants section in BOTH live + paper drill-down routes; original T-516 scope per BRIEF §13.6; PENDING) per L-007 + operator OQ-1/2/3=A 2026-05-08. Mirror T-512 + T-513 split precedent.

### T-516a1 delivered — backend half of paper-trade drill-down infra

- **All review gates passed**: plan-reviewer 2-pass APPROVE 2026-05-08 (pass-1 REVISE 3 BLOCKER + 4 CONCERN — AC#2 contradiction + test path convention + helper symbol name + test count + TASKS.md split timing + docstring guidance; all 6 mechanical fixes applied; pass-2 APPROVE with 15-item Write-time guidance) → drift-checker ON TRACK (7 staged files; 15/15 WG verified) → brief-reviewer SHIP (27 ACs satisfied; §N1/§N3/§N5/§N6/§N7/§N9 clean; L-008/L-013/L-014/L-015 active controls applied) → math-validator N/A per CLAUDE.md Gate 4 (services/analytics_api/ NOT in math-binding list).
- Mirror live `/api/trades/*` feature stack 1:1 for paper_trades — paper_trades schema (migration 0008) is structurally identical to trades schema (migration 0005) per §3.1:268 paper-live symmetry invariant.
- **Operator audit 2026-05-08 surfaced gap**: analytics-api + UI had ZERO paper_trade support. Operator's primary mode is paper, so live-only T-516 (original UI placeholder) had limited operational value until paper-trade drill-down ships.
- **NEW `PaperTradeRow`** dataclass (analytics.py; frozen+slots+21 fields; status: TradeStatus enum reuse since paper_trades.status values 'open'/'closed' identical to live).
- **NEW DB helpers** (analytics.py +193 LOC): `select_paper_trade_by_id` + `select_paper_trades_paginated` + `count_paper_trades` + `_build_paper_trades_where_clause` + `_row_to_paper_trade` (byte-for-byte mirror `_row_to_trade`) + 2 SQL constants. All filter binds use $N parameterized placeholders only (L-008); ORDER BY closed_at DESC NULLS FIRST + id DESC tiebreaker mirror live.
- **NEW `services/analytics_api/app/models/paper_trades.py`** (68 LOC): PaperTradeResponse + PaperTradeListResponse Pydantic models. Decimal NUMERIC columns serialize as strings; DOUBLE PRECISION stay as float. Envelope key `paper_trades` (NOT `trades`).
- **NEW `services/analytics_api/app/routers/paper_trades.py`** (113 LOC): `GET /api/paper-trades/` paginated list + `GET /api/paper-trades/{id}` single detail (404 'paper trade {id} not found'). Mirror trades.py 1:1.
- main.py (+2 LOC): `app.include_router(paper_trades_router)` registered.
- 26 nových testov (target +24; +2 bonus): 15 router tests + 11 DB helper tests. Repo baseline 2062 → 2088 (0 regressions).

### §0.3 LOC kalibrácia — 7. dátový bod L-006/L-014/L-016

- T-516a1: src 376 LOC (analytics.py +193 + paper_trades.py 113 + models 68 + main.py 2) under 400 cap; no waiver needed. Plan target ~272 LOC src → reality 376 = 1.39× (within L-006 backend-mirror band 1.0-1.4×).
- 7. dátový bod confirms calibration band stratifikácia: backend-mirror tasks (1.0-1.4×) vs FSM tasks (1.5-1.8×) vs verification-only mirror tasks (UNDER target — T-513b2 -24%).
- Mirror-reuse pattern compresses overshoot consistently. Future backend-mirror tasks should plan with multiplier 1.2-1.4× as central tendency.

### Watch-outs for next session

- **T-516a2 next pickup** — UI routes + nav + shared component module. NEW `ui/src/routes/paper-trades.index.tsx` + NEW `/paper-trades/$paperTradeId.tsx` (mirror trades.\$tradeId.tsx 8 timeline sections; shadow variants section is placeholder for T-516b) + NEW `ui/src/components/trade-drill/` shared module (lift TradeSummary + SignalDetailView + Row + TimelineSection; refactor existing route via discriminated union `Trade | PaperTrade`) + NEW nav sidebar entry. Reuses T-516a1 shipped backend endpoints. Est ~400 LOC src + ~250 LOC tests. Per L-006 calibration realistic ~500-600 LOC src.
- **T-516b** — shadow variants section in BOTH live + paper drill-down routes (original T-516 scope per BRIEF §13.6). NEW `<ShadowVariantsView />` + endpoint(s) for shadow_variants by parent_trade_id (per ADR-0010 parent_kind dispatch) + NEW DB helper `select_shadow_variants_by_parent_trade_id`. Blocked-by T-516a1 + T-516a2.
- **F5 critical-path bottleneck post-T-516**: T-533 (named-state FSM enum refactor; largest hardening task per ADR-0011). Sizing block T-527 / risk-per-SL T-528 also split-watch-flagged.
- **Hardening tasks (T-524..T-536) land AFTER existing F5 tail** per OQ-3=A 2026-05-08 baked: T-516a2 + T-516b → T-518..T-521 → T-524..T-536 → T-522 close-out + Live-ready sign-off.

### Lessons surfaced

- **L-006/L-014/L-016 calibration 7th data point** — backend-mirror tasks consistently 1.0-1.4×, distinct from FSM tasks 1.5-1.8× and verification-only mirror tasks (UNDER target). Plan-reviewer Gate 1 multiplier guidance can stratify by task-type: backend-mirror 1.2-1.4×; UI mirror 1.0-1.3× (predicted); FSM/replay-recovery 1.5-1.8×; verification-only mirror tests of just-shipped infra ~1.0×.
- **TASKS.md split row timing convention** — 4 instances now (T-512 + T-513 + T-516 + per task-close pattern memory): row-update happens in chore commit accompanying feat (NOT in feat itself). T-516a1 follows precedent (chore commit accompanying this feat).

---

## 2026-05-08 (late-night VIII — T-513b2 rejected-signal kill-test integration test shipped; F5 E3 FULLY SATISFIED; Shadow runtime cluster 12/12 sub-tasks COMPLETE)

**F5 phase: 25/45 tasks done (~56%).** Master HEAD pre-merge `4a533b4` (chore for T-513b1). Shadow runtime cluster **12/12 sub-tasks COMPLETE** (T-510a + T-510b + T-511a + T-511b1 + T-511b2a + T-511b2 + T-512a + T-512b + T-513a + T-513b1 + T-513b2 + T-514). **F5 E3 FULLY SATISFIED 2026-05-08** per both halves of BRIEF §19:2589 verbatim *"Shadow variants persist across restart (verified by killing execution-service mid-variant)"*: T-512b variant kill-test (shipped earlier today) + T-513b2 rejected-signal kill-test (this; mirror T-512b in-process pattern).

### T-513b2 delivered — closes F5 E3 rejected-signal half

- **All 4 review gates passed**: plan-reviewer single-pass APPROVE 2026-05-08 (20-item Write-time guidance) → drift-checker ON TRACK (2 staged files; all 20 WG verified) → brief-reviewer SHIP (17/22 ACs satisfied in feat; AC#14-18 deferred to chore commit) → math-validator VERIFIED (math reuse from T-513a + T-513b1; orchestration only; classification-only assertions; hand-verification reproducible byte-for-byte).
- Implements **BRIEF §20:2790 verbatim test name** `test_rejected_signal_shadow_survives_restart_via_replay`. Closes the rejected-signal kill-test half of F5 E3 exit criterion.
- **Mirror T-512b in-process simulated restart Path A** per operator OQ-1=A 2026-05-08 (subprocess+SIGTERM novel-infra deferred since BRIEF §13.7:2037 verbatim does not specifically mandate subprocess; T-512b shipped in-process pattern is the established convention).
- **NEW `services/execution/tests/integration/test_rejected_observation_restart.py`** (455 LOC; 2 test functions + 4 helpers; clean separation per OQ-2 default A).
- **Test #1 replay-finalize path** (verbatim test name): bot row + ohlc_1m candles where candle 4 low (64600) crosses SL threshold (64675 = 65000 × 0.995); after restart asserts `terminal_outcome='would_sl'` + non-null mfe_pct/mae_pct.
- **Test #2 replay-resume path**: 29 no-trigger candles in [64850, 65150]; after restart asserts row stays `terminated_at NULL` + ShadowRejectedWorker B `_active_tasks[rejected_id]` has registered live continuation task (1:1 keying vs T-512b 1:N).
- **NO realized_pnl assertion** — `shadow_rejected` schema (migration 0014:94-114) has no `realized_pnl` column; rejected obs don't trade per BRIEF §13.5.
- **NO source code changes** outside tests (reuses T-512b conftest fixtures + T-513b1 resume API + T-513a worker + payload helpers verbatim).
- 2 nové testy (skip-state until POSTGRES_TEST_DSN + NATS_TEST_URL set; CI-fast green); 0 regressions.

### F5 E3 — FULL SIGN-OFF

E3 verbatim BRIEF §19:2589: *"Shadow variants persist across restart (verified by killing execution-service mid-variant)."* Both halves now satisfied:

- **Variant half**: `services/execution/tests/integration/test_shadow_restart.py::test_shadow_variant_survives_restart_via_replay` (T-512b shipped 2026-05-08; verbatim per BRIEF §20:2787).
- **Rejected-signal half**: `services/execution/tests/integration/test_rejected_observation_restart.py::test_rejected_signal_shadow_survives_restart_via_replay` (T-513b2 shipped 2026-05-08; verbatim per BRIEF §20:2790).

H-023 (Shadow restart via OHLC replay) hazard fully covered — T-512a + T-512b for variants + T-513b1 + T-513b2 for rejected = full F5 H-023 owner satisfied. T-519 hazard audit (E4 owner) can verify on cadence.

### §0.3 LOC kalibrácia — 6. dátový bod L-014/L-016

- T-513b2: 455 LOC test body (~24% UNDER plan target ~600). **First-time UNDER target in cohort** (T-511b1 +70%, T-512a +150%, T-513a +58%, T-512b +61%, T-513b1 +21%, T-513b2 -24%). Mirror reuse + leaner rejected-obs setup (no parent paper_trade seed; smaller payload) compresses overshoot toward 1.0× and below.
- §0.3 SRC LOC = 0 (test-only); §0.3 cap N/A per BRIEF §0.3 (test code excluded).
- **Pattern matures**: split-task with mirror reuse from prior shipped infra produces predictable LOC; calibration multiplier band 0.76× to 2.5× across the 6-task cohort.

### Watch-outs for next session

- **F5 E3 + Shadow runtime cluster fully complete** — no more shadow-runtime gating tasks. Remaining F5 backlog: T-516 + T-517 (UI surfaces; now fully unblocked since shadow runtime 12/12 done) + T-518 + T-519 + T-520 + T-521 + T-522 (existing backend polish + close-out) + T-524..T-536 (pre-live hardening per ADR-0011) = 19 tasks remaining.
- **Critical-path bottleneck**: T-533 (named-state FSM enum refactor; largest hardening task; touches 4 columns + migration 0018 + state population). T-512 OHLC replay (orig bottleneck) + T-513 rejected-signal cluster all closed.
- **L-015 active control reminder for T-531/T-532/T-533 plan stages**: each plan-doc MUST include "Sibling migration test impact" section per L-015 (migrations 0016 + 0017 + 0018 each modify earlier-migration-introduced tables).
- **Hardening tasks land AFTER existing F5 tail per OQ-3=A**: T-516+T-517 (UI) → T-518..T-521 (existing backend polish) → T-524..T-536 (hardening cluster) → T-522 close-out + Live-ready sign-off.
- **F5 phase ~56% complete**: 25/45. Pace: 19 tasks remaining; ~3-4 weeks realistic at current cadence per ADR-0011 §Consequences est.

### Lessons surfaced

- **L-014/L-016 calibration confirmed (6th data point; first-time UNDER target)**: Pattern matures — mirror-reuse split-tasks with prior-shipped-infra dependencies compress LOC overshoot. Future replay-recovery / verification tasks following the split-then-reuse pattern (T-512a → T-512b mirror; T-513b1 → T-513b2 mirror) consistently land within or under target. Active control: plan-reviewer Gate 1 multiplier guidance can relax for "verification-only mirror tests of just-shipped infra" (1.0× target acceptable; was 1.5-1.8× for new FSM/math tasks).
- **No new lesson** — calibration data point only.

---

## 2026-05-08 (late-night VII — T-513b1 rejected-signal observation restart-recovery via OHLC replay shipped; H-023 replay half complete for rejected branch; T-513b2 kill-test mirror is sole remaining E3 gating sub-task)

**F5 phase: 24/45 tasks done (~53%).** Master HEAD pre-merge `7875392` (chore for T-523). Shadow runtime cluster 11/12 sub-tasks done (T-510a + T-510b + T-511a + T-511b1 + T-511b2a + T-511b2 + T-512a + T-512b + T-513a + T-513b1 + T-514; T-513b2 sole remaining = mandatory rejected-signal kill-test integration test). T-513b split per L-007 + operator OQ-1=A 2026-05-08 (mirror T-512 split precedent — combined T-513b scope ~1100 LOC trips §0.3 cap + L-014/L-016 calibration miss risk).

### T-513b1 delivered — replay-recovery infrastructure half of H-023 for rejected-signal shadow

- **All 4 review gates passed**: plan-reviewer 2-pass APPROVE 2026-05-08 (pass-1 REVISE 4 textual CONCERN — phase counter math + OQ wording + OQ markers + Operator decisions section enumeration; all 4 mechanical fixes applied; pass-2 APPROVE with 20-item Write-time guidance) → drift-checker ON TRACK (12 staged files; all 20 WG verified) → brief-reviewer SHIP (24 nových testov; §N1/§N3/§N5/§N6/§N9 clean; L-008/L-013/L-014/L-015/L-016 active controls applied) → math-validator VERIFIED (math reuse from T-513a; orchestration only; hand-verification byte-for-byte reproducible).
- Implements **BRIEF §13.5:2024 verbatim** *"Restart recovery: also via OHLC replay in v2 (unlike v1). No `lost_on_restart` state."* v2 improvement requirement.
- Mirror T-512a `shadow_replay.py` pattern but for `shadow_rejected` rows. Key differences from T-512a: NO PaperExchange (pure observer FSM), NO parent_kind discriminator (rejected don't trade), 1:1 ID-to-task mapping (vs 1:N), reuses T-513a `_compute_thresholds` + `_compute_mfe_mae_pcts` + `_make_observation_candle_handler` (imported; no math drift).
- **NEW `services/execution/app/shadow_rejected_replay.py`** (491 LOC): `resume_active_observations_on_startup` lifespan hook + `replay_rejected_observation_to_now` per-row replay-finalize-or-resume + `_replay_observation_candle_loop` cursor iteration + `_finalize_replay_terminal` cascade-delete-race tolerance + `_decode_meta` JSONB round-trip.
- **NEW Settings + enum + cross-bot helper + register_resume_task**:
  - `ShadowRejectedTerminal.SHUTDOWN_MID_REPLAY` enum value (mirror T-512a OQ-4=A forward-compat; column TEXT no CHECK per migration 0014).
  - `select_all_active_shadow_rejected` cross-bot enumeration helper (mirror T-512a precedent).
  - `ShadowRejectedWorker.register_resume_task(*, rejected_id, task)` public API (1:1 keying).
  - `shadow_rejected_replay_query_window_max_hours: Decimal = Decimal("48")` + `shadow_rejected_replay_per_observation_timeout_seconds: float = 120.0` (mirror T-512a values for symmetry; per §N9 + L-001).
- **main.py wire**: `resume_active_observations_on_startup` AFTER `shadow_rejected_worker.start()` (mirror T-512a OQ-4=A precedent; functionally agnostic since rejected obs have no cancel-hook subscribe equivalent of `trade.closed.>`; operator-symmetry rationale per OQ-4=A baked).
- 21 nových testov (target +20; +1 bonus on register_resume_task path); pytest baseline 2041 → 2062 (+21; 0 regressions).

### §0.3 LOC kalibrácia — 5. dátový bod L-014/L-016

- src ~553 LOC (vs naive 400 cap = +38% over). 5. data point: T-511b1 +70%, T-512a +150%, T-513a +58%, T-512b +61%, T-513b1 +21% — calibration band 1.5-1.8× systematicky drží pre FSM/integration cohort. L-014/L-016 active controls v plan-reviewer Gate 1 + brief-reviewer Gate 3 enforced. Pre-authorized §0.3 over-cap waiver per plan §LOC budget §6 mirror T-511b1/T-512a/T-513a/T-512b precedent.

### F5 E3 exit-criterion — len T-513b2 zostáva pre full sign-off

- E3 verbatim: BRIEF §19:2589 *"Shadow variants persist across restart (verified by killing execution-service mid-variant)."*
- T-512b shipped: variant kill-test (replay-finalize + replay-resume paths) — variant half complete.
- T-513b1 shipped (this): rejected-signal replay-recovery infrastructure — replay half complete for rejected branch.
- **T-513b2 remaining**: mandatory kill-during-rejected-observation integration test (BRIEF §20:2790 verbatim `test_rejected_signal_shadow_survives_restart_via_replay`); in-process simulated restart Path A mirror T-512b. Final F5 E3 sign-off pin.

### Watch-outs for next session

- **T-513b2 sole remaining critical-path pickup** — rejected-signal kill-test integration test. Mirror T-512b in-process simulated restart Path A. Reuses T-512b shipped conftest fixtures (`base_dsn` + `nats_test_url` + `migrated_db_dsn` + `bus` + `pool`). NEW `services/execution/tests/integration/test_rejected_observation_restart.py` OR extend existing `test_shadow_restart.py` — plan-stage decides. Est: ~600 LOC test body (mirror T-512b 677 LOC).
- **F5 critical-path bottleneck post-T-513b2 ship** revised to **T-533** (named-state FSM enum refactor; largest hardening task; touches 4 columns + migration 0018 + state population). T-513b2 + T-512 OHLC replay (orig bottleneck) closed once T-513b2 ships.
- **Hardening tasks (T-524..T-536) land AFTER existing F5 tail per OQ-3=A**: T-513b2 → T-516+T-517 (UI) → T-518..T-521 (existing backend polish) → T-524..T-536 → T-522 close-out + Live-ready sign-off.
- **L-015 active control reminder for T-531/T-532/T-533 plan stages**: each plan-doc MUST include "Sibling migration test impact" section per L-015 (migrations 0016 + 0017 + 0018 each modify earlier-migration-introduced tables).

### Lessons surfaced

- **L-014/L-016 calibration confirmed (5th data point)**: T-513b1 +21% over-cap is the SMALLEST overshoot in the cohort so far — likely because T-513b1 reuses T-513a math wholesale (orchestration only; no new financial math). When task scope is "wire-up + import existing helpers" the multiplier compresses toward 1.0×; when scope adds genuine new FSM/math (T-512a +150%, T-511b1 +70%) the multiplier expands. No new lesson — calibration data point only.
- **Pre-emptive split + math-reuse pattern matures**: T-513b1 demonstrates that splitting replay-recovery into infra + verification halves enables clean math-reuse (T-513a `_compute_thresholds` + `_compute_mfe_mae_pcts` + `_make_observation_candle_handler` imported, not re-implemented) → math-validator VERIFIED with byte-for-byte hand-verification reproducibility. Future restart-recovery tasks should follow this split + reuse pattern.

---

## 2026-05-08 (late-night VI — T-523 F5 scope extension to Live-ready MVP shipped per ADR-0011; 13 mandatory pre-live hardening tasks T-524..T-536 added; "Plný MVP" → "Live-ready MVP" rename + new exit criterion E6)

**F5 phase: 23/44 tasks done (~52% post-scope-extension).** Master HEAD pre-merge `387b67d` (`chore(tasks)` for T-512b). T-523 reorg 2026-05-08 added 13 mandatory pre-live operational hardening tasks (T-524..T-536) + T-523 meta-chore itself (mirror T-500 named-task precedent). F5 cluster expanded from 30 entries (22 done + 8 pending) to 44 entries (23 done + 21 pending: T-513b + T-516..T-522 existing + T-524..T-536 hardening).

### T-523 delivered — F5 scope extension via BRIEF §19 addendum + ADR-0011

- **All 4 review gates passed**: plan-reviewer 2-pass APPROVE 2026-05-08 (pass-1 REVISE 2 BLOCKER + 5 CONCERN; all 7 mechanical fixes applied; pass-2 APPROVE with 10-item Write-time guidance) → drift-checker IMPLICIT (markdown only; no source code) → brief-reviewer SHIP (9/9 WG addressed; WG#9 status.md deferred to chore commit) → math-validator VERIFIED — out of scope per CLAUDE.md Gate 4 fast no-op (no services/execution/, packages/pnl/, packages/features/ touched).
- Mirror T-500 / T-019 / T-200 named-task precedent. Markdown-only meta-chore.
- **Operator session 2026-05-08 audit** (general-purpose research agent) surfaced 6 categories of pre-live ops gaps post-F5-feature-complete:
  1. Bot-level risk caps (max_open_trades, daily_loss, max_drawdown halt, cooldowns, losing-streak)
  2. Automatic position sizing (qty from balance/equity, % of account, risk-per-SL, min/max notional, altcoin cap, qty_step rounding, min order size, balance pre-check)
  3. Account balance / equity tracking (`get_account_balance()` protocol gap, wallet/available/total/margin balance, unrealized PnL, historical snapshots)
  4. Better PnL accounting (8/9 sub-items already-shipped; only funding fees genuine gap)
  5. Named-state trade lifecycle FSM enum (currently free-text status + flags + close_reason split)
  6. SL/TP lifecycle verification (periodic watchdog, overwrite protection, trailing audit)
- **Bucket A (already shipped)**: PnL accounting majority via cumulative-delta ADR-0006 + fee-per-fill + partial TP + reduce-only + realized/unrealized split + executions audit + T-220 audit loop + restart reconciliation T-221.
- **Bucket B (BRIEF-deferred-no-task)**: §B.1 sizing block + qty_step rounding + virtual_balance Prom gauge.
- **Bucket C (silent everywhere)**: All bot-level risk caps + % / risk-per-SL sizing + altcoin cap + min order / available_balance pre-checks + get_account_balance() adapter protocol + wallet/available/equity/margin tracking + funding fees + periodic SL watchdog + SL overwrite protection + named SIGNAL_RECEIVED..RECONCILED enum.

### Operator decisions baked (session 2026-05-08)

- **OQ-1=B**: Extend F5 scope (rename "Plný MVP" → "Live-ready MVP"); 13 hardening tasks land within F5 vs separate F6 phase; single sign-off semantic.
- **OQ-2=A**: Formal BRIEF §19 addendum + NEW ADR-0011 (per §6.7 ADR discipline).
- **OQ-3=A (reconciled)**: Hardening tasks land within F5 after existing tail (T-513b + UI T-516+T-517 + T-518..T-521) and before T-522 close-out; T-522 scope expanded to 2-section runbook (paper feature-complete + Live-ready).

### 13 mandatory hardening tasks (T-524..T-536)

- **Risk management (3)**: T-524 bot concurrent-trades caps + T-525 daily loss limit + max drawdown stop (L-007 split-watch) + T-526 cooldown after loss + losing-streak cooldown.
- **Position sizing (3)**: T-527 §B.1 sizing block reified (absorbs F4+ opportunistic; L-007 split-watch) + T-528 risk-per-SL sizing (L-007 split-watch) + T-529 qty_step rounding + min order + balance pre-check (absorbs T-F2+ opportunistic + placement.py:153 stub).
- **Account balance / equity tracking (3)**: T-530 ExchangeClient.get_account_balance() protocol extension + T-531 equity snapshot table + virtual_balance Prom gauge (absorbs BRIEF §15:2151) + T-532 funding fee tracking.
- **Trade lifecycle FSM (1)**: T-533 named-state TradeLifecycleState enum refactor (L-007/L-014/L-016 split-watch — largest task in cluster).
- **SL/TP verification (3)**: T-534 periodic SL watchdog APScheduler tick (L-007 split-watch) + T-535 SL overwrite protection + T-536 trailing SL audit pass.

### Anticipated hazards H-027 / H-028 / H-029

3 anticipated hazards documented in ADR-0011 §Consequences but **NOT added to BRIEF §20 catalog at T-523 time per §0.8 anti-hypothetical**. Each surfaces during the relevant task's plan stage and gets formal H-NNN allocation then:

- **H-027 (anticipated, T-525)**: Daily loss limit / drawdown stop must be persisted across restart and re-evaluated on startup (mirror T-221 reconcile pattern). Otherwise restart resets the kill-switch.
- **H-028 (anticipated, T-534)**: Periodic SL watchdog must distinguish "Bybit dropped SL" from "Bybit returned 'no positions' on transient error" — false-positive emergency_close would close real positions wrongly.
- **H-029 (anticipated, T-535)**: SL overwrite detection must NOT fire false-positive on legitimate trail SL updates — only on out-of-FSM updates.

### Watch-outs for next session

- **F5 critical-path bottleneck revised post-T-523**: T-533 named-state FSM enum refactor (largest hardening task; touches 4 columns + migration 0018 + state population) likely dominates final F5 cycles. T-512 OHLC replay (orig bottleneck) closed via T-512a + T-512b shipped 2026-05-08.
- **Hardening tasks land AFTER existing F5 tail per OQ-3=A**: T-513b (E3 final) + T-516+T-517 (UI) + T-518..T-521 (existing backend polish) shipnú prv. Potom T-524..T-536. Potom T-522 close-out + Live-ready sign-off.
- **L-015 active control reminder for T-531/T-532/T-533 plan stages**: each plan-doc MUST include "Sibling migration test impact" section per L-015 (migrations 0016 + 0017 + 0018 each modify earlier-migration-introduced tables → potential ci-full integration test breakage).
- **Pre-emptive split likely candidates**: T-525 (loss-limit FSM + persistence + reconcile profile = L-014 multiplier) + T-527 (§B.1 block large) + T-528 (sizing.method discriminator dispatcher) + T-533 (large refactor across modules + migration + state population) + T-534 (APScheduler tick + emergency_close on miss = L-016 restart-recovery profile). Each plan stage decides per L-007.
- **L-019 named-task precedent extended**: T-019 (F1) + T-200 (F2) + T-500 (F5 init) + T-523 (F5 extension) all use named-task path. F3 / F4 anonymous chore(tasks) commits remain historical exceptions; named-task path preferred for plans of any non-trivial scope per L-019 active control + drift-checker + brief-reviewer visibility.

### Lessons surfaced

- **No new lesson** — T-523 is markdown-only meta-chore; no implementation surface for review system to catch new patterns. ADR-0011 captures the F5 scope-extension pattern that may itself become a precedent if future BRIEF amendments via ADR mechanism are needed.

---

## 2026-05-08 (late-night V — T-512b mandatory kill-during-variant integration test shipped; F5 E3 partially satisfied; in-process simulated restart per OQ-1=A; T-513b sole remaining E3 gating sub-task)

**F5 phase: 22/22 numbered tasks done (~100%).** Master HEAD pre-merge `6aa96a9` (`chore(tasks)` for T-513a). Shadow runtime cluster 10/11 sub-tasks done (T-510a + T-510b + T-511a + T-511b1 + T-511b2a + T-511b2 + T-512a + T-512b + T-513a + T-514; T-513b remaining as the only gating sub-task for full F5 E3 exit-criterion sign-off).

### T-512b delivered — verification half of H-023 + F5 E3 sign-off pin

- **All 4 review gates passed**: plan-reviewer single-pass APPROVE 2026-05-08 (10-item Write-time guidance) → drift-checker ON TRACK (4 staged files; all WG#1..#10 + AC#1..#15 verified) → brief-reviewer SHIP → math-validator VERIFIED — out of scope per CLAUDE.md Gate 4 (no `services/execution/app/` source touched; src LOC = 0).
- Implements **BRIEF §19:2589 verbatim** *"Shadow variants persist across restart (verified by killing execution-service mid-variant)"* + **BRIEF §20:2787 verbatim test name** `test_shadow_variant_survives_restart_via_replay`.
- **In-process simulated restart per operator OQ-1=A 2026-05-08**: mirror existing repo integration patterns (signal-gateway e2e + T-221 reconcile); subprocess+SIGTERM is novel-infra deferred since BRIEF §13.7:2037 says "Integration: full variant lifecycle under testcontainers with simulated ticks" — does not specifically mandate subprocess.
- **NEW directory `services/execution/tests/integration/`**: `__init__.py` (empty) + `conftest.py` (157 LOC; 6 fixtures env-gated on `POSTGRES_TEST_DSN` + `NATS_TEST_URL` mirror signal-gateway pattern) + `test_shadow_restart.py` (520 LOC; 5 helpers + 2 test functions).
- **Test #1 replay-finalize path** (verbatim test name): paper_trade + variant + ohlc_1m s candle 4 low (64600) crossing SL (64675 = 65000 × 0.995); after restart asserts `terminal_outcome='sl_hit'` + non-null realized_pnl/mfe_pct/mae_pct.
- **Test #2 replay-resume path**: same setup s no-trigger candles; after restart asserts row stays terminated_at NULL + ShadowWorker B `_active_tasks[parent_trade_id]` má registered live continuation task.
- **Path A synthetic publish per WG#1**: ShadowWorker A self-INSERTs row + spawns variant task → variant id captured BEFORE stop() per WG#2 → cancellation-does-not-finalize contract verified (shadow_worker.py:388 try/finally — `update_shadow_variant_terminal` is INSIDE try:, NOT in finally:).
- **Cleanup discipline per WG#5 + WG#9**: cancel + drain via `contextlib.suppress + await` BEFORE pool fixture teardown (otherwise pool.close blocks on outstanding-conn timeout).
- 2 nové testy (2039 → 2041 expected post-merge by skip-state); 0 regressions.

### §0.3 LOC kalibrácia — 4. dátový bod L-014/L-016

- **src LOC = 0** (test-only); §0.3 cap N/A per BRIEF §0.3 (test code excluded).
- **Test body ~677 LOC vs plan target ~420** (~+61% overshoot). 4. dátový bod kalibrácia (T-511b1 +70%, T-512a +150%, T-513a +58%, T-512b +61%); FSM/integration tasks systematically over plan-budget. L-014/L-016 active control already enforced; no new lesson, just calibration data point. Pre-authorized waiver line v pláne §6 mirroring T-511b1 / T-512a / T-513a precedent.

### F5 E3 exit-criterion partial satisfaction

- **E3 verbatim**: BRIEF §19:2589 *"Shadow variants persist across restart (verified by killing execution-service mid-variant)."*
- **T-512b satisfies the variant half** per `test_shadow_variant_survives_restart_via_replay` + replay-resume companion test.
- **T-513b kill-test mirror remains** — rejected-signal observation FSM kill-during-observation integration test (mirror T-512b pattern but for `shadow_rejected` table). Full E3 sign-off needs both T-512b + T-513b. Plus T-513b's own scope additionally needs replay-recovery infra (mirror T-512a `shadow_replay.py` for rejected-observation FSM) — surface in T-513b plan stage per OQ.

### Watch-outs for next session

- **T-513b sole remaining critical-path pickup** — rejected-signal kill-test + replay-recovery infra. Heavy: ~150 LOC src (replay-recovery side mirror T-512a) + ~280 LOC integration test body. Pre-emptive split T-513b1 (replay infra) + T-513b2 (kill-test) likely warranted per L-007 + L-014 mirror T-512a/T-512b pattern. Plan stage will surface OQs.
- **UI tasks T-516 + T-517** — already unblocked since T-513a; can prep frontend mockup work in parallel with T-513b.
- **F5 phase ~100% numbered scope complete**: 22/22 numbered tasks shipped. Remaining work is T-513b (E3 second half) + T-516/T-517 (UI) + T-518..T-522 (backend polish + ops + close-out runbook). Pace: 5-6 tasks remaining; ~5-7 days realistic at current cadence.
- **Today total**: 27+ master commits anticipated post-merge (16 prior + T-511b2a feat + chore + fix + T-511b2 feat + chore + UI feat + T-512a feat + chore + L-016 lesson + T-513a feat + chore + T-512b feat + chore).

### Lessons surfaced

- **L-014 / L-016 calibration confirmed (4th data point)**: T-512b ~+61% over plan target consistent s prior data points (T-511b1 +70%, T-512a +150%, T-513a +58%); plan-reviewer Gate 1 calibration multiplier 1.5-1.8× holds; brief-reviewer Gate 3 commit-body waiver discipline holds. No new lesson — calibration band is well-established.
- **In-process simulated restart pattern viable for kill-tests**: T-512b proves that the `cancellation-does-not-finalize` contract from shadow_worker.py:388 try/finally placement allows in-process restart simulation to faithfully exercise the H-023 replay-finalize + replay-resume code paths. Mirror this approach for T-513b's rejected-observation kill-test (parent finalizer placement in shadow_rejected_worker.py is symmetric per T-513a shipped contract).

---

## 2026-05-08 (late-night IV — T-513a rejected-signal observation FSM + producer shipped; BRIEF §13.5 4-outcome classification; T-513b kill-test deferred per OQ-3=A pre-emptive split)

**F5 phase: 21/22 numbered tasks done (~95%).** Master HEAD pre-merge `374afdd` (feat T-513a). Shadow runtime cluster 9/11 sub-tasks (T-510a + T-510b + T-511a + T-511b1 + T-511b2a + T-511b2 + T-512a + T-513a + T-514; T-512b + T-513b remaining — both gating E3 exit criterion).

### T-513a delivered — observation FSM half of BRIEF §13.5 rejected-signal tracking

- **All 4 review gates passed**: plan-reviewer 5-pass APPROVE (4 BLOCKERs initial → APPROVE pass-5 after FeatureResolver.kv_get → select_latest_close switch + source filter add + L-014 LOC calibration + L-015 sibling migration test attestation) → drift-checker ON TRACK (12 files; all WG#1..#18 verified) → brief-reviewer SHIP (21/21 acceptance + 17/18 WG; WG#17 §N4 TDD ordering attestation in commit body) → math-validator VERIFIED.
- Implements BRIEF §13.5 verbatim: 60-min observation window, MFE/MAE tracking, 4-outcome classification (would_tp / would_sl / would_be / no_trigger). NEW `services/execution/app/shadow_rejected_worker.py` (397 LOC) with 1:1 ID-to-task mapping, SL-first conservative race bias, BE-trigger sticky `>=`/`<=` flag, entry==0 defensive early-return.
- Strategy-engine consumer.py rejection branch publishes `ShadowRejectedStartPayload` ALONGSIDE existing T-310b `SignalRejected` (parallel emits; separate concerns). NEW `select_latest_close(conn, *, symbol, source)` DB helper (PK source filter REQUIRED).
- Composition root wiring AFTER T-512a `resume_active_variants_on_startup` BEFORE `scheduler.start()` (settings-gated `shadow_rejected_enabled`).
- 26 nových testov (2013 → 2039); 0 regressions.

### §0.3 over-cap waiver per L-014

- **632 LOC src vs 400 cap (~+58%)**: shadow_rejected_worker.py 397 LOC (FSM minimum surface — 3 pure helpers + worker class + 60-min observation lifecycle); consumer.py +117 (rejection branch _resolve_virtual_entry + _publish_shadow_rejected_start producer); payloads.py +60 + market_data.py +32 + main.py +19 + config.py +7. Mirror T-511b1 / T-512a precedent — FSM-task LOC pattern systemic, not split-able without operationally-expensive cross-cutting refactor. Plan §6 pre-authorized over-cap waiver line; operator approval 2026-05-08.
- **L-014 / L-016 calibration data**: 3rd data point (T-511b1 ~70%, T-512a ~150%, T-513a ~58%) on FSM/integration tasks systematically running 50-180% over plan target. L-014 active control already enforces drift-checker DRIFT-but-waivable verdict + commit-body waiver line.

### Watch-outs for next session

- **T-512b + T-513b twin critical-path pickup** — both kill-test integration tests gate E3 exit criterion. T-512b mandatory per BRIEF §20:2787 verbatim `test_shadow_variant_survives_restart_via_replay`; T-513b mirror pattern for rejected-observation FSM kill-during-observation. Heavy integration scope: testcontainer postgres + nats jetstream + subprocess.spawn execution-service + SIGTERM + restart assertions. Could ship together (T-512b first per E3 criticality) or separately. T-513b additionally needs replay-recovery side via OHLC replay (mirror T-512a `shadow_replay.py` pattern but for rejected_observation FSM) — surface in T-513b plan stage.
- **UI tasks T-516 + T-517** — now BOTH unblocked. T-516 needs T-510 + T-511 + T-512 shadow runtime (all done as of T-513a); T-517 rejected-explorer side needs T-513a (done). Could prep frontend mockup work in parallel with T-512b/T-513b.
- **F5 phase ~95% complete**: 21/22 numbered tasks shipped. Remaining: T-512b + T-513b (E3 gating) + T-516 + T-517 + T-518..T-522 (backend polish + ops + close-out runbook). Pace: 5-7 tasks remaining; ~1 week realistic at current cadence.

### Lessons surfaced (additions to recent body of work)

- **L-014 / L-016 calibration confirmed**: 3rd consecutive FSM-task over-cap (T-511b1 +70%, T-512a +150%, T-513a +58%); plan-budget systematically optimistic on FSM tasks. Active control already in place — no new lesson, just calibration data point.
- **Pre-emptive split pattern matures further**: T-510 → T-510a/b; T-511 → T-511a/b1/b2a/b2; T-512 → T-512a/b; T-513 → T-513a/b. Consistently chosen at L-007 trigger threshold. Mid-write splits avoided across 5+ task families this session cluster.

---

## 2026-05-08 (late-night III — T-512a shadow variant restart-recovery via OHLC replay shipped; L-016 replay-recovery LOC calibration lesson + UI redesign separate)

**F5 phase: 20/22 numbered tasks done (~91%).** Master HEAD pre-merge `067ad6f` (feat T-512a). Shadow runtime cluster 8/10 sub-tasks (T-510a + T-510b + T-511a + T-511b1 + T-511b2a + T-511b2 + T-512a + T-514; T-512b + T-513 remaining). Plus **independent UI redesign** shipped earlier in session (master HEAD `784e397`; visual/CSS only with bonus YamlDiffView typecheck regression fix).

### T-512a delivered — replay-recovery infrastructure for H-023 hazard

- **All 4 review gates passed**: plan-reviewer pass-2 APPROVE (20-item WG; pass-1 REVISE 2 BLOCKERs overrides-derivation + terminal-detection-method + 4 CONCERNs resolved) → drift-checker DRIFT (LOC overshoot ~625 src vs 400 cap; operator-waived per L-014 mirror T-511b1 precedent) → brief-reviewer single-pass SHIP (20/20 WG + 18/18 AC) → math-validator VERIFIED.
- Implements BRIEF §13.4: enumerate active variants → check parent state → cursor-iterate ohlc_1m → drive PE._on_candle + _make_candle_handler → terminal_future.done() detection (handles partial-TP-then-SL H-024 v2) → finalize OR spawn live continuation. NEW `services/execution/app/shadow_replay.py` (570 LOC).
- **Required-effect retro-fits to T-511b1 shipped**: (a) `meta={"symbol":..., "overrides":{...}}` in `insert_shadow_variant` call (overrides persistence enables resume to reconstruct PE seed_open_state); (b) `_drive_variant_to_terminal` helper extraction (single source of truth for terminal-classification + MFE/MAE; called from BOTH live + resume paths); (c) NEW public `ShadowWorker.register_resume_task` API replacing direct `_active_tasks` access.
- **NEW ShadowVariantTerminal value `SHUTDOWN_MID_REPLAY`** (per T-510a OQ-4=A forward-compat; column TEXT no CHECK; no DB migration). Triggered when parent closed during downtime OR window cap exceeded OR per-variant compute timeout fired.
- **Operator-resolved OQs baked**: OQ-1=A inline DB-cursor replay (NOT T-503/T-507b reuse); OQ-3=A wall-clock carry-over timer; OQ-4=A SHUTDOWN_MID_REPLAY enum value + skip when parent closed.
- 20 nových testov (1993 → 2013); 0 regressions.

### Pre-T-512a UI redesign (independent commit cycle)

- **feat(ui)** `784e397` → terminal-aesthetic redesign (electric `#00e5a0` teal palette + Space Mono `font-trading` + lucide-react sectioned sidebar + ConnectionDot animate-ping + TimeRangePicker pill group + card top-edge accent line per operator's 8-step spec). Bonus: pre-existing YamlDiffView `noUncheckedIndexedAccess` typecheck regression from T-515 fixed (6 LOC). 4 test text-content updates (`scalper-v2`→`SCALPER-V2`; `Coming F4+`→`F4+` badge + `Coming soon`). 12 src files; ZERO touch outside `ui/`.
- ci-full PASSES on UI push (verified via `gh run list`).

### L-016 appended — replay-recovery LOC calibration

- **Pattern**: replay-recovery / restart-resume FSM tasks systematically under-budgeted ~150-180% (2nd data point with T-511b1 → 2nd canonical case for plan-budget calibration miss). Specifically: defensive paths (parent-state checks per mode, window cap, timer carry-over, per-variant timeout, structured logging diagnostic fields, cascade-delete race logging, live-continuation closure, replay state seeding) each contribute 15-30 LOC realistic.
- **Active control**: plan-reviewer at gate 1 MUST flag <500 LOC src budgets for replay-recovery / restart-resume FSM tasks as optimistic. Brief-reviewer at gate 3 MUST verify §0.3 over-cap accompanied by explicit operator waiver. Drift-checker at gate 2 MUST distinguish "scope drift" (REVISE) from "plan-budget calibration miss" (DRIFT but waivable — replay-recovery is canonical case).

### Watch-outs for next session

- **T-512b next critical-path pickup** — mandatory kill-during-variant integration test per E3 exit criterion (BRIEF §20:2787 verbatim test name `test_shadow_variant_survives_restart_via_replay`). Heavy integration: testcontainer postgres + nats jetstream + subprocess.spawn execution-service + SIGTERM mid-variant + restart + assert variant resumed via T-512a infra. Est ~80 LOC src + ~250 LOC integration test body. **Final E3 sign-off pin** for T-522 close-out runbook.
- **T-513 rejected-signal observation** — mirrors persistence pattern (T-510b shipped) but separate input source (rejected signals from `signals.rejected.<bot_id>` topic; 60-min observation window). Independent of T-512b.
- **UI tasks T-516 + T-517** — now unblocked via T-512a + T-511b2 shadow runtime fully operational. Could prep frontend mockup work in parallel with T-512b.
- **Today total**: ~26 master commits anticipated post-merge (16 prior + T-511b2a feat + chore + fix + T-511b2 feat + chore + UI feat + T-512a feat + chore + L-016 lesson).

### Lessons surfaced (additions to recent body of work)

- **L-016 (this session)**: replay-recovery LOC calibration. 2nd data point with L-014 — both confirm ~150-180% under-budgeting on FSM/integration cohort.
- **Operator decision pattern matures**: pre-emptive split (T-510 → T-510a/b; T-511 → T-511a/b1/b2a/b2; T-512 → T-512a/b) consistently chosen at L-007 trigger threshold. Mid-write splits avoided across 4 task families this session.

---

## 2026-05-08 (late-night II — T-511b2 shadow-worker integration shipped + L-015 sibling-migration-test lesson + fix(T-511b2a) ci-full follow-up)

**F5 phase: 19/22 numbered tasks done (~86%).** Master HEAD `c16e9cb` (`ebb6155` fix(T-511b2a) test_0014_migration post-0015 head schema + `c16e9cb` feat T-511b2). Shadow runtime cluster 7/9 sub-tasks (T-510a + T-510b + T-511a + T-511b1 + T-511b2a + T-511b2 + T-514; T-512 + T-513 remaining; T-511 split into 4 sub-tasks via 3 pre-emptive splits per L-007). **Shadow runtime fully operational end-to-end for both live + paper modes** (per ADR-0010).

### T-511b2 delivered — paper-aware producer + parent-close H-016 hook (full H-016 ownership achieved)

- **All 4 review gates passed**: plan-reviewer pass-1 APPROVE (8-item WG checklist) → drift-checker pass-1 DRIFT (3 test coverage gaps: paper_trade_id population test #18, lifespan shutdown order pin #15, WG#8 negative duplicate-publish assertion) → drift-checker pass-2 ON TRACK (after adding all 3 missing tests, +30 LOC) → brief-reviewer single-pass SHIP (15/15 acceptance + 8/8 WG; 360 LOC src counted under §0.3 400 cap; over plan target ~260 absorbed cleanly per WG#6 PE adapter delta calibration) → math-validator VERIFIED (18 parity grid hand-computed values verified byte-for-byte; no Decimal→float drift).
- 13 src files modified + 1 NEW test file (`test_shadow_parity.py` 18-assertion BRIEF §13.7 grid) + 8 test files extended; 1993 repo-wide passing (+32 from baseline 1961); 0 regressions.
- **H-016 full ownership**: T-511b1 finalizer half + T-511b2 parent-close cancel hook half = complete shadow task cleanup contract per BRIEF §20 verbatim policy.

### Pre-T-511b2 fix(T-511b2a) follow-up

- **ci-full failed on T-511b2a master push** (commit `7b74c2b`) — 3 tests in `test_0014_migration.py` regressed because `migrated_db_dsn` fixture upgrades to head (post-0015 schema), so `shadow_variants` had 15 cols + parent_kind NOT NULL + FK dropped, but tests asserted 0014-era state.
- **Fix shipped** (`ebb6155`): `_EXPECTED_VARIANTS_COLUMNS` updated to 15 cols + INSERTs include `parent_kind="live"` + FK cascade test repurposed as `test_migration_0014_shadow_variants_no_fk_cascade_after_0015_relax` (asserts post-0015 reality: NO cascade).
- ci-full now PASSES on master (verified via `gh run list`).

### L-015 appended — sibling migration test impact watch

- **Pattern**: when a NEW migration modifies a table introduced in earlier migration, integration tests for the earlier migration (which run against `migrated_db_dsn` upgraded to head) inherit head schema state → 3 distinct failure modes (column shape / NOT NULL INSERT / FK behaviour) all surface in ci-full.
- **Active control**: plan-reviewer at Gate 1 MUST require "Sibling migration test impact" section listing every earlier `test_NNNN_migration.py` whose assertions touch the modified table + the specific assertion needing update. Brief-reviewer at Gate 3 MUST grep staged diff for those test files.
- **Why ci-full-only catch**: env-gated `POSTGRES_TEST_DSN` integration tests skip locally → ~2 min latency post-merge surface (L-009 sibling lesson).

### Implementation summary

- **Producer half**: `emit_post_commit_shadow_start_event` helper in `placement_persist.py` + LIVE emit at `placement.py:328` area + PAPER emit at `placement.py:240-252` paper-fork (uses `OrderPlaceResult.paper_trade_id` populated from `PaperExchange._persist_open` `insert_paper_trade` return — NEW dataclass field per AC#3).
- **Paper close emit**: NEW `PaperExchange.emit_parent_lifecycle: bool = False` ctor flag (default False; variant PE in `shadow_worker._run_shadow_variant` stays default False to avoid self-cancel loop; primary bot PE in `pool.py:198` wires True). `_persist_close` publishes `TradeClosedPayload(parent_kind='paper')` post-commit.
- **Live close emit**: extended `reconcile.emit_post_commit_close_event` dual-publish (`OrderClosed` to `orders.events.<bot_id>` + `TradeClosedPayload(parent_kind='live')` to `trade.closed.<bot_id>`; per-publish try/except — first publish failure does NOT short-circuit second).
- **Consumer half**: `ShadowWorker._on_parent_close` H-016 cancellation hook subscribes to `trade.closed.>` wildcard (in addition to T-511b1's `shadow.start.>`); cancel-only semantic (no await — each task's own try/finally finalizer handles bus_unsubscribe lazily).
- **Strategy-engine `consumer.py`**: `_publish_order_request` populates 2 new OrderRequest fields when `bot_config.shadow.enabled` (maps `ShadowVariant` (scoring) → `VariantSpec` (bus); float→Decimal cast on `max_duration_hours` via `Decimal(str(value))`).
- **OrderRequest schema delta**: `shadow_variants: list[VariantSpec]` + `shadow_max_duration_hours: Decimal | None`; schema_version stays "1.0" (additive non-breaking).
- **NEW envelope** `TradeClosedPayload(parent_trade_id, parent_kind, bot_id, closed_at)` + 2 subject helpers (`subject_for_shadow_start` + `subject_for_trade_closed`) in `packages/bus/payloads.py` (L-002 active control).
- **`main.py` wire**: ShadowWorker constructed in lifespan after dispatcher_tasks (always-on; data-driven by per-bot YAML); `start()` + state attach. **Shutdown order BLOCKER 2 fix** from T-511b2a pass-1 review: `bus.close()` runs FIRST (drains subscriptions), THEN `shadow_worker.stop()` (cancels in-flight variant tasks; finalizer bus_unsubscribe is no-op since bus already drained). Test #15 verbatim pins shutdown order via index assertion.
- **`config.py`**: 2 NEW Settings fields `shadow_seed_balance_usd` + `shadow_fee_rate` (service-wide; deliberate isolation from per-bot paper-bot fee config per WG#4).

### Watch-outs for next session

- **T-512 next critical-path pickup** — OHLC replay restart-recovery (H-023 owner; mandatory kill-during-variant integration test per E3 exit criterion). Heaviest remaining F5 task. Consumes T-511b2a foundation (`select_active_shadow_variants` from `packages/db/queries/shadow.py` for resume scan) + T-511b2 producer (variants registered in shadow_variants with `WHERE terminated_at IS NULL` for restart resume).
- **T-513 rejected-signal observation** — mirror persistence pattern (T-510b shipped read+write helpers); separate input source. Independent of T-512.
- **UI tasks T-516 + T-517** soft-blocked on T-512 runtime; could prep frontend mockup work.
- **Today total**: 21+ master commits anticipated post-merge (16 prior + T-511b2a feat + chore + fix(T-511b2a) + T-511b2 feat + chore).

### Lessons surfaced (additions to recent body of work)

- **L-015 (this session)**: sibling migration test impact watch — plan-stage gate is the most reliable prevention point for the "head schema bleeds into earlier-migration test" failure class.
- **WG#6 PE adapter delta calibration miss**: plan claimed ~30 LOC for PE adapter delta; reality 52 LOC (1 over upper-bound 50). L-014 calibration band held (within 30-50; calibration miss flag, not DRIFT).

---

## 2026-05-08 (late-night I — T-511b2a shadow runtime schema foundation shipped; ADR-0010 paper-aware deviation recorded)

**F5 phase: 18/22 numbered tasks done (~82%).** Master HEAD `da7413a` pre-merge; T-511b2a feat commit `741f086` on branch `feat/T-511b2a-shadow-foundation` awaiting ff-merge. Shadow runtime cluster 6/9 sub-tasks (T-510a + T-510b + T-511a + T-511b1 + **T-511b2a NEW** + T-514; T-511b2 + T-512 + T-513 remaining; T-511 split into 4 sub-tasks via 3 pre-emptive splits per L-007).

### T-511b2a delivered — 4-gate review system caught architectural §6.4 deviation requiring ADR-0010 + dual-FK alternative consideration

- **Plan-reviewer pass-1 REVISE** — 2 BLOCKERs + 3 CONCERNs:
  - BLOCKER 1: BRIEF §2.5:268 deviation ("Downstream services cannot distinguish paper from live") without ADR per §6.7 protocol — paper-aware shadow runtime explicitly distinguishes paper/live via `parent_kind` discriminator at 3 layers (DB schema + wire envelope + strategy-engine producer mapping)
  - BLOCKER 2: dual-FK + XOR CHECK alternative not discussed in plan as rejected option (T-510a OQ-6=A precedent had different rationale — composite PK technical constraint vs paper/live dual-target here)
  - 3 CONCERNs: §7.4:1192 destructive-migration ADR coverage; two-step ALTER pattern test assertion gap; L-014 budget calibration watch
- **Architectural OQ surfaces driving plan revision**:
  - OQ-Paper-mode (operator decision 2026-05-08): chose **B = paper plno-scope** over A (live + testnet only) and C (defer F6+) — operator's primary v2 mode is paper today (v2 not deployed; sibling v1 disabled 2026-05-02), so shadow runtime needs to fire there; but migration 0014 FK `shadow_variants.parent_trade_id → trades(id)` would FK-violate for paper bots writing to `paper_trades`
  - OQ-Split (operator decision 2026-05-08): chose **A = pre-emptive split T-511b2a + T-511b2** per L-007 + L-014 — original T-511b2 estimate ~190 src grew to ~250-330 src across 9+ files with paper-aware support; mirror T-510 (T-510a schema + T-510b helpers) split pattern
- **NEW ADR-0010** (`docs/adr/0010-shadow-runtime-distinguishes-paper-from-live.md`; Accepted 2026-05-08 per §6.7 protocol after operator review): records §2.5:268 invariant narrowing rationale (binds on listed services strategy-engine + analytics-api; shadow runtime is post-spec first-class) + Trade-offs (loss of single-table FK referential integrity; cross-table writeback complexity for paper_trade_id sourcing in T-511b2; discriminator drift risk; downgrade-on-paper-rows risk) + 4 rejected alternatives (live-only, defer-F6, **dual-FK + XOR CHECK** quantitatively rejected at 530 LOC vs 340 LOC = 60% overhead, **paper-dual-write trades + paper_trades** rejected as architecturally invasive breaking T-219 cumulative-delta close-flow)
- **Plan-reviewer pass-2 APPROVE** with 10-item write-time guidance verbatim checklist
- **Drift-checker ON TRACK** — 48 src counted (under plan target 70); zero out-of-scope changes; no T-511b2 producer-side leak; runtime ValueError narrowing in `_row_to_shadow_variant` decoder is in-scope defensive narrowing (mirror existing terminal_outcome pattern); 1 extra parametrized round-trip test scope-aligned (strengthens WG#4 Literal validation contract)
- **Brief-reviewer single-pass SHIP** — 10/10 acceptance + 10/10 WG; §N1 / §N3 / §N8 / §N9 hazards clean; L-002 / L-008 / L-009 / L-013 N/A verified
- **Math-validator VERIFIED** — services/execution/ touched by 1-LOC propagation only; zero financial math changes; no Decimal→float casts; no seed conventions; `_compute_be_sl_price` / `_compute_trail_sl_price` / `_check_be_trigger` / `_terminal_from_pe_state` / mfe-mae casts from T-511b1 untouched (verified by grep on +/- LOC)

### Implementation summary

- **migration 0015**: drops `shadow_variants_parent_trade_id_fkey` FK + adds `parent_kind: TEXT NOT NULL` discriminator with two-step ALTER pattern (server_default 'live' + nullable=False → drop default); explicit `downgrade 0014` per L-012 (NEVER relative `-1`)
- **`packages/db/queries/shadow.py`**: `insert_shadow_variant` adds keyword-only no-default `parent_kind: Literal["live", "paper"]` kwarg (TypeError if omitted; test verifies); `ShadowVariantRow.parent_kind` field appended at end (preserved field order); SQL INSERT $9 + RETURNING + SELECT projections updated; `_row_to_shadow_variant` decoder ValueError narrowing on unexpected literal value
- **`packages/bus/payloads.py`**: `ShadowStartPayload.parent_kind: Literal["live", "paper"]` no-default field with Pydantic Literal validation
- **`services/execution/app/shadow_worker.py:_run_shadow_variant`** — 1-LOC required-effect propagation `parent_kind=payload.parent_kind` (NOT silent refactor; payload schema delta is the cause)
- **5 env-gated migration integration tests** (`tests/integration/migrations/test_0015_migration.py`): drop FK + column NOT NULL with `column_default IS NULL` post-upgrade per WG#1 two-step ALTER test assertion + paper insert + live-orphan insert + explicit downgrade 0014
- **3 unit query tests** + **4 unit payload tests** + **7 fixture cascades** in T-511b1 shipped tests
- 41 src LOC counted (under plan target ~70) + 81 LOC migration §0.3-excluded + ~370 LOC tests = ~492 total
- 1960 repo-wide passing (up from 1953 = +7 new tests); 0 regressions

### Watch-outs for next session

- **T-511b2 next reasonable pickup** — producer + integration half consumes T-511b2a foundation. Plan-doc draft `docs/plans/T-511b2.md` exists (pre-T-511b2a-split version) but **needs revision** before plan-reviewer pass to incorporate: (1) paper-aware emit at placement.py paper-fork (line 240-252) AND live branch (line 328) — paper_trade_id sourcing requires `OrderPlaceResult.paper_trade_id` field extension or PaperExchange method; (2) **main.py shutdown-order BLOCKER 2 fix** from T-511b2a pass-1 reviewer (existing main.py runs `bus.close()` FIRST, then position_lifecycle_tasks cancel, etc. — plan WG#10 had backwards "stop() BEFORE bus.close" which contradicted convention; correct order is shadow_worker.stop() AFTER bus.close, alongside position_lifecycle_tasks gather); (3) parity test verbatim name `test_variant_step_transitions_match_live_lifecycle_fsm` (BRIEF §13.7 wording with `_step` + "fsm" suffix); (4) NEW `TradeClosedPayload` envelope + `subject_for_trade_closed` + `subject_for_shadow_start` helpers; (5) extended `emit_post_commit_close_event` dual-publish (orders.events + trade.closed); (6) OrderRequest schema delta `shadow_variants` + `shadow_max_duration_hours` + parent_kind carry-through; (7) strategy-engine `_publish_order_request` populates from `bot_config.shadow.enabled` + maps `BotConfig.exchange.mode` → ShadowStartPayload.parent_kind. Per L-014 calibration: realistic ~200 LOC src + ~320 LOC tests across 9+ files
- **Critical-path gating task** — T-512 OHLC replay restart-recovery (H-023 owner; mandatory kill-during-variant integration test per E3 exit criterion). Heaviest remaining F5 task; UI tasks T-516/T-517 soft-blocked
- **Today total**: 18 master commits anticipated post-merge (16 prior + T-511b2a feat + chore)

### Lessons surfaced

- **§6.4 invariant scope-narrowing pattern**: when adding a new first-class component (like shadow runtime per BRIEF §13) post-spec, invariants on "downstream services" can be narrowed via ADR if the new component's purpose requires the distinction. ADR-0010 sets precedent — narrowing not deviation when the listed services preserve the invariant.
- **Pre-emptive split mechanic for cross-cutting deviations**: when operator decision (paper plno-scope) dramatically expands scope (~190 → ~330 LOC), L-007 + L-014 + T-510 split precedent makes T-Xa (foundation) + T-X (consumer/producer) the operationally-cheap path. T-511b2a → T-511b2 mirrors T-510a → T-510b mirror T-507a → T-507b.

---

## 2026-05-08 (late-evening IV — T-511b1 shadow-worker FSM core shipped)

**F5 phase: 17/22 numbered tasks done (~77%).** Master HEAD `da7413a` (`4bf4e63` feat + `da7413a` chore). Shadow runtime cluster 5/8 sub-tasks (T-510a + T-510b + T-511a + T-511b1 + T-514; T-511b2 + T-512 + T-513 remaining). Today total: 16 master commits.

### T-511b1 delivered — 4-gate review system caught material issues across 3 plan-reviewer passes + 2 drift-checker passes

- **Plan-reviewer pass-1 REVISE** — 4 BLOCKERs + 3 CONCERNs (helper count 3 not 4; H-016 verbatim test name; emit module mismatch reconcile.py vs placement_persist.py:466; BRIEF §13.7 parity test missing; hand-verification syntax; ADR-0009 needed; pre-emptive split recommended)
- **Operator decisions** — split T-511b → T-511b1 + T-511b2 (CONCERN 7 = YES); ADR-0009 = YES (CONCERN 6); BE-trigger included in T-511b1 scope (OQ-1 = include BE); OHLC-1m stream over BRIEF §13.3 ticks (OQ-2 = OHLC)
- **Plan-reviewer pass-2 REVISE** — 4 NEW BLOCKERs all duplicates with T-510b shipped surface (analytics.py duplicates; ShadowTerminalOutcome duplicate of ShadowVariantTerminal; @idempotent annotation mismatch; return type int vs ShadowVariantRow). All resolved by reuse-from-T-510b refactor (~50 LOC saved).
- **Plan-reviewer pass-3 APPROVE** — final 12-item write-time guidance + 13-item acceptance criteria
- **Drift-checker pass-1 DRIFT** — 4 unscoped helpers (~42 LOC: `_SlippageConfig`, `_compute_initial_sl/tp`, `_pct_change`, `_unsubscribe`) + bloated module docstring (42 LOC) + dead `_apply_variant_overrides`. Refactor: drop SlippageConfig (4 ctor kwargs); move SL/TP into `seed_open_state` per T-511a `_apply_seed_open_state` design (eliminates `_compute_initial_sl/tp` + step 5 init-SL setup); inline `_pct_change`; trim docstrings.
- **Drift-checker pass-2 ON TRACK** with operator over-cap waiver (legitimate scope; plan budget miscalibrated +70%)
- **Brief-reviewer SHIP** (13/13 acceptance + 13/13 write-time guidance; §0.3 over-cap waiver verified in commit msg per L-014)
- **Math-validator VERIFIED** (3 BE/trail helpers byte-for-byte verbatim from lifecycle.py:233-268; truth-table parity dispatcher._derive_exec_type ✓; Decimal preservation realized_pnl end-to-end via TerminalEvent → kwargs → DB Numeric(20,4); plan/test fixture parameter divergence flagged but not blocking — implementation arithmetic matches test fixture math exactly)

### L-014 appended

Systematic plan-stage budget calibration miss for FSM-style execution-service tasks (shadow / risk / replay FSM): plan-reviewer should flag <300 LOC single-file budgets as optimistic when plan enumerates ≥3 helpers + ≥3 class methods + ≥1 closure factory + ≥1 dataclass; realistic budget 350-450 LOC. PE adapter deltas adding callback + dataclass + ctor kwarg + validation kwarg systematically run 50-70 LOC NOT 25. Brief-reviewer must verify §0.3 over-cap accompanied by explicit operator waiver in commit msg. Drift-checker must distinguish "scope drift" (DRIFT) from "plan-budget calibration miss" (ON TRACK with CONCERN noted).

### Watch-outs for next session

- **T-511b2 next reasonable pickup** — producer half of shadow runtime: `_on_parent_close` H-016 cancellation hook on `trade.closed.>` + `emit_post_commit_shadow_start_event` in `placement_persist.py:466` (open-side; mirror `emit_post_commit_events` pattern) + `main.py` ShadowWorker construction lifespan + per-bot YAML config plumbing + BRIEF §13.7 verbatim parity test `test_shadow_step_transitions_match_live_lifecycle`. Est ~110 LOC src + ~120 tests per backlog (apply L-014 calibration: realistic ~180-220 LOC src given enumerated members).
- **Critical-path gating task** — T-512 OHLC replay restart-recovery (H-023 owner; mandatory kill-during-variant integration test per E3 exit criterion). Heaviest remaining F5 task; UI tasks T-516/T-517 soft-blocked.

---

## 2026-05-08 (late-evening III — T-511a PE shadow-mode prereq shipped)

**F5 phase: 16/22 numbered tasks done (~73%).** Master HEAD `b6cac80`. Shadow runtime cluster 3/5 → 4/5 with T-511a (T-510a + T-510b + T-511a + T-514; T-511b + T-512 + T-513 remaining).

### T-511a delivered (16/22)

Plan-reviewer 2-pass APPROVE (REVISE → APPROVE on **ADR-0005 v2 BLOCKER** — `sl_type='be'` → `'trail'` fix in 5 plan-doc locations) → drift-checker SKIPPED → brief-reviewer single-pass SHIP → math-validator OUT OF SCOPE.

**Critical catch**: I had written `sl_type='be'` after partial_tp in 5 plan-doc places — **ADR-0005 v2 verbatim mandates `'trail'`, NOT `'be'`**. Live execution-service has 3-state vocabulary `protective / be / trail`: `'be'` comes from separate lifecycle BE-trigger path (price crosses `be_trigger`), `'trail'` comes from partial_tp dispatcher path. Shipping `'be'` would silently mislabel v2 trail state as BE in shadow lifecycle — direct H-024 v2 invariant regression.

**T-511a delivers (PE refactor only; T-511b shadow worker deferred)**:
- `seed_open_state` ctor kwarg + `_apply_seed_open_state` helper — pre-populates caches BEFORE NATS subscribe (eliminates place_market_order race on empty `_last_price`)
- `bus_unsubscribe_market_ohlc` async method — idempotent NATS/ReplayBus dual-path (H-016 ergonomic precondition)
- `sl_type` field on `_active_positions` — 3-state vocabulary; `set_trading_stop` initializes 'protective'; `_drain_partial_tp` promotes 'trail' per ADR-0005 v2
- 5 unit tests; 123 paper-suite pass (118 existing + 5 new); 0 regressions

### F5 cluster progress

- **Backtest harness (T-501..T-509)**: 9/9 = 100% (unchanged)
- **Shadow runtime (T-510..T-514)**: 4/5 done — T-510a + T-510b + **T-511a NEW** + T-514. **Remaining**: T-511b (shadow worker module; consumes refactored PE) + T-512 (OHLC replay restart-recovery; H-023 owner) + T-513 (rejected-signal observation)
- **UI extensions (T-515..T-517)**: 1/3 done (unchanged; T-516+T-517 soft-blocked on T-512)
- **Backend polish + ops (T-518..T-522)**: 0/5 done

### Watch-outs for next session

- **T-511b next reasonable pickup**: shadow-worker module consuming refactored PE per T-511a. Open question for T-511b plan stage: BE-trigger path in PE for shadow lifecycle (PE has no lifecycle BE-trigger today; either skip BE_HIT outcome OR add BE-trigger refactor to T-511b OR defer to follow-up). ~280 LOC src per backlog estimate.
- **Critical-path gating task**: T-512 OHLC replay restart-recovery (H-023 owner; mandatory kill-during-variant integration test per E3 exit criterion). Heaviest remaining F5 task; UI tasks soft-blocked
- **Today total**: 14 master commits (T-506 + chore + 3 chore(devx) + T-507a + chore + T-507b + chore + T-508 + chore + T-509 + chore + T-511a)

---

## 2026-05-08 (late-evening II — T-509 worker shipped; backtest harness cluster 9/9 complete)

**F5 phase: 15/22 numbered tasks done (~68%).** Master HEAD `850b94a`. **Backtest harness cluster T-501..T-509 = 9/9 (100% complete)** — F5 backtest goal delivered per BRIEF §12.2.

### T-509 delivered (15/22)

Plan-reviewer 2-pass APPROVE (REVISE → APPROVE on L-011/L-013 codec regression BLOCKER + missing Write-time guidance + 3 CONCERNs) → drift-checker SKIPPED → brief-reviewer single-pass SHIP (7 WG items verified) → math-validator OUT OF SCOPE.

**Key catches**:
- **L-011/L-013 codec regression BLOCKER**: T-507b `update_backtest_run_completion` was text-mode for CLI pool (no codec); analytics-api worker pool REGISTERS `_register_jsonb_codec` → would double-encode. Fix: `codec_registered: bool = False` kwarg flag (forward-pointer in `analytics.py:2038-2041` literally predicted this).
- **getattr sentinel for backwards-compat**: T-507b `main()` uses `external_run_id = getattr(args, 'run_id', None)` — preserves CLI argparse Namespace without the attr.
- **SKIP LOCKED race-safety verification**: env-gated real-PG concurrent claim test (2 coroutines proti seeded queued row → only 1 claims).

### Backtest harness cluster recap (T-501..T-509)

- T-501: backtest_runs migration 0013 + scoring_evaluations FK
- T-502: ReplayBus (in-process timestamp-ordered pub/sub)
- T-503: HistoricalOHLCSource (cursor-streamed OHLC replay; pace control)
- T-504: HistoricalSignalSource (signals replay)
- T-505: intra_candle generator (TradingView Replay path)
- T-506: PaperExchange replay-mode wiring (T-503 + T-505)
- T-507a: BusProtocol prereq + ReplayBus async subscribe + KV stubs
- T-507b: scripts/backtest.py CLI orchestrator + ReplayClock + ADR-0008 PF semantic
- T-508: --compare mode (aggregate diff + per-trade diff)
- **T-509: backtest worker connect (this) — analytics-api lifespan polls queue + dispatches to T-507b**

End-to-end backtest flow now operational: operator UI POST /api/backtests/ → T-407 creates queued row → T-509 worker claims (atomic SKIP LOCKED) → invokes T-507b main() with run_id → replays via T-502/T-503/T-504/T-506/strategy-engine/execution-service → writes summary + backtest_trades.

### F5 cluster progress

- **Backtest harness (T-501..T-509, 9 tasks)**: **9/9 done = 100% COMPLETE**
- **Shadow variants runtime (T-510..T-514, 5 tasks)**: 3/5 done (unchanged)
- **UI extensions (T-515..T-517, 3 tasks)**: 1/3 done (unchanged)
- **Backend polish + ops (T-518..T-522, 5 tasks)**: 0/5 done (unchanged)

### Watch-outs for next session

- **F5 critical-path bottleneck**: T-512 OHLC replay restart-recovery (H-023 owner; kill-during-variant integration test mandatory) is heaviest remaining task; T-516 + T-517 UI tasks soft-blocked on T-512 runtime
- **T-511 next reasonable pickup**: shadow-worker FSM (H-016 owner) — first shadow runtime task after T-510a/b infra layer
- **Today total**: 13 master commits (T-506 + chore + 3 chore(devx) + T-507a + chore + T-507b + chore + T-508 + chore + T-509 + chore)
- **Operator can now end-to-end test backtest CLI**: `BACKTEST_WORKER_ENABLED=true ... uv run uvicorn services.analytics_api.app.main:create_app --factory ...` + UI POST → worker picks up → T-507b replays → summary persisted

---

## 2026-05-08 (late-evening — T-508 compare mode shipped)

**F5 phase: 14/22 numbered tasks done (~64%).** Master HEAD `fcdc453`. T-508 is small additive read-only mode extending T-507b CLI; backtest harness cluster 8/9 → 9/9 (only T-509 worker connect remaining).

### T-508 delivered (14/22)

Plan-reviewer single-pass APPROVE → drift-checker SKIPPED → brief-reviewer 2-pass SHIP (FIX FIRST → SHIP on 3 ruff errors + sys.argv CONCERN) → math-validator OUT OF SCOPE.

- **scripts/backtest.py** (+194 LOC) — `--compare nargs=2` argparse flag; `cli_main()` dispatch; `main_compare()` composition root (read-only); `_format_aggregate_diff` + `_format_per_trade_diff` text-table helpers; `_parse_uuid` validator; mutex hard-fail per WG#1
- **packages/db/queries/analytics.py** (+128 LOC) — 3 read helpers (`select_backtest_run_summary`, `select_diverging_trades_for_compare` with `IS DISTINCT FROM` null-safe equality, `count_common_signals_for_compare` for WG#3 M=0 distinction) + `DivergingTradeRow` dataclass
- **tests/scripts/test_backtest_cli.py** (+151 LOC) — 8 new tests; 3 monkeypatch'd cli_main tests for auto-restore (CONCERN response avoiding cross-test argv leakage)
- 1912 → 1922 = +10 tests; no regressions

### F5 cluster progress

- **Backtest harness cluster (T-501..T-509, 9 tasks)**: 8/9 done — T-501..T-505 + T-506 + T-507a + T-507b + **T-508 NEW**. **Remaining**: T-509 (worker connect from analytics-api `/api/backtests/{id}` queue)
- **Shadow variants runtime (T-510..T-514, 5 tasks)**: 3/5 done (unchanged)
- **UI extensions (T-515..T-517, 3 tasks)**: 1/3 done (unchanged)
- **Backend polish + ops (T-518..T-522, 5 tasks)**: 0/5 done (unchanged); T-520 hardening 3/5 cherry-picked

### Watch-outs for next session

- **T-509 worker** is next in backtest cluster — analytics-api lifespan task that polls `backtest_runs WHERE status='queued'` + invokes `scripts.backtest.main()` programmatically with existing `run_id` (T-507b currently always creates fresh row; T-509 must accept external run_id). Est: ~220 LOC src + ~140 LOC tests
- **Today total**: 11 master commits (T-506 + chore + 3 chore(devx) dev-stack + T-507a + chore + T-507b + chore + T-508 + chore)

---

## 2026-05-08 (evening — T-507b CLI orchestrator shipped)

**F5 phase: 13/22 numbered tasks done (~59%).** Master HEAD `db2d282`. T-507b je najväčší F5 task — orchestruje 6 komponentov do single in-process backtest CLI per BRIEF §12.2:1949. 8 BLOCKERs + 7 CONCERNs surfaced cez 3 plan-reviewer + 2 brief-reviewer cykly; všetky resolved.

### T-507b delivered (13/22)

Plan-reviewer 3-pass APPROVE (REVISE → REVISE → APPROVE) → brief-reviewer 2-pass SHIP (FIX FIRST → SHIP) → math-validator VERIFIED (per-content financial-math invocation; 5 hand-computed §A-§E summary fixtures cross-check exactly).

- **`scripts/backtest.py`** (NEW, 510 LOC) — CLI orchestrator; argparse + composition root + `_compute_summary` + `_publish_signals` + `_load_bot_config_with_overrides`/`_apply_overrides` helpers
- **`packages/core/replay_clock.py`** (NEW, 51 LOC) — Belt-and-suspenders ReplayClock per OQ-D=C; virtual time advanced per OHLC bucket + per signal received_at
- **`packages/exchange/paper/adapter.py`** (+11 LOC) — `replay_clock` kwarg + advance call in `_process_replay_candle`
- **`packages/db/queries/analytics.py`** (+117 LOC) — 3 helpers (update_to_running + update_completion + copy_paper_trades_to_backtest)
- **Cascade BusProtocol retypes**: `services/execution/app/{lifecycle,placement,placement_persist}.py` (3 modules, 4 functions) extending T-507a Protocol scope so CLI ReplayBus injection at composition root is mypy-strict-clean
- **ADR-0008** PF=None semantic shipped
- **13 unit tests + 3 ReplayClock + 1 env-gated integration** (full-fidelity per OQ-B=B)

### Key BLOCKER catches across review cycles

1. **Plan-reviewer 1st cycle (4 BLOCKERs)**: bus typing → T-507a; FeatureResolver kv_get → T-507a; invented `scoring_config_hash` → raw bytes per OQ-A=A; L-006 framing → 14% acknowledged
2. **Plan-reviewer 2nd cycle (2 BLOCKERs)**: missing make_per_bot_handler subscription (would produce 0 trades silently); HistoricalSignalSource symbol_universe; SignalRow→SignalValidated reconstruction; composition variable order; signals.ttl_seconds
3. **Plan-reviewer 3rd cycle (2 BLOCKERs)**: max_signal_age_seconds + replay-clock semantic → ReplayClock per OQ-D=C; ExecutionSettings name → Settings alias
4. **Brief-reviewer 1st cycle (4 BLOCKERs)**: §N1 SQL NOW() → started_at param; mypy 4 errors (Action enum cast, SlippageModel annotation, ExecutionSettings call-arg, make_per_bot_handler bus typing — last forced cascade retype); ruff 19 errors; architectural arrow ReplayClock relocation z scripts/ do packages/core/

### F5 cluster progress

- **Backtest harness cluster (T-501..T-509, 9 tasks)**: 8/9 done — T-501..T-505 + T-506 + T-507a + **T-507b NEW**. **Remaining**: T-508 (compare mode) + T-509 (worker connect)
- **Shadow variants runtime (T-510..T-514, 5 tasks)**: 3/5 done (unchanged)
- **UI extensions (T-515..T-517, 3 tasks)**: 1/3 done (unchanged)
- **Backend polish + ops (T-518..T-522, 5 tasks)**: 0/5 done (unchanged)

### Watch-outs for next session

- **T-508 next** (compare mode `--compare run_A run_B`); independent of T-507b orchestration code-path; ~180 LOC src + ~140 LOC tests per T-500 backlog
- **T-509 worker** consumes T-507a BusProtocol + invokes T-507b main() programmatically with existing run_id
- **Today total**: 9 master commits (T-506 + chore(tasks) + 3 chore(devx) dev-stack + T-507a + chore(tasks) + T-507b + this chore(tasks))
- **Dev stack**: postgres + nats Docker + analytics-api + Vite (LAN-bound 0.0.0.0); operator can run T-507b CLI integration test locally via `BACKTEST_INTEGRATION=1 POSTGRES_TEST_DSN='...' uv run pytest tests/integration/scripts/test_backtest_integration.py`

---

## 2026-05-08 (afternoon — T-507a BusProtocol prereq shipped)

**F5 phase: 12/22 numbered tasks done (~55%).** Master HEAD `e4723e8`. T-507a was an unplanned prereq sub-task that emerged when T-507 plan-reviewer caught 4 BLOCKERs (consumer signature hard-typing + FeatureResolver bus.kv_get gap + invented BotConfig field + L-006 framing). Operator chose split T-507a (BusProtocol prereq, this) + T-507b (CLI; remaining).

### chore(devx) `b179e8d` + `df38a76` + `d164bbb` morning recap

3 chore(devx) commits earlier today exposed dev-stack lifecycle: `dev-up.sh`/`dev-down.sh` one-command wrapper + LAN-bind on postgres + nats (4222/8222) + analytics-api on `0.0.0.0` per operator-led trusted-LAN stance. All 5 service surfaces now reachable from second LAN PC without SSH tunnel. `docs/runbooks/dev_stack.md` documents workflow + revert recipe.

### T-507a delivered (12/22)

Plan-reviewer 2-pass APPROVE → drift-checker SKIPPED (small narrow scope) → brief-reviewer 2-pass SHIP (FIX FIRST → SHIP on 3 RUF100 + 1 E501) → math-validator OUT OF SCOPE.

- **packages/bus/protocol.py** (NEW, 71 LOC) — BusProtocol Protocol class (publish + subscribe + close + kv_get + kv_put + kv_update); `runtime_checkable` deliberately omitted per §0.8.
- **packages/bus/replay_bus.py** (modified) — `subscribe(...)` def → async (matches NatsClient + 12 await call-sites verified by grep); 3 KV stubs: `kv_get` returns None unconditionally with `@idempotent` decorator (FeatureResolver._try_kv falls back to _try_db per OQ-5=A — NO FeatureResolver modification needed); `kv_put`/`kv_update` raise NotImplementedError.
- **6 consumer-function signature retypes across 5 modules**: consumer.py 3 (handler + 2 publish helpers — handler delegates into both, mypy fail-cascades unless all 3 retype) + dispatcher.py 1 + reconcile.py 1 (`emit_post_commit_close_event` reaches replay path via dispatcher._process close-flow — caught by plan-reviewer concern) + paper/adapter.py 1 + scoring/resolver.py 1.
- **Tests**: 12 await mods + 1 def→async + 3 new KV stub tests in test_replay_bus.py + 4 new BusProtocol satisfaction tests (introspection-based per `test_protocol_conformance.py:50-68` precedent). 1889 → 1896 = +7 tests passing.
- **Other `bus: NatsClient` sites** (feature_engine pipeline, analytics_api SSE, market_data, signal_gateway webhook, alerting, rate_limiter, execution-service composition root) remain live-only per plan §"Out of replay scope" — explicit enumeration prevents T-507b accidental ReplayBus mount.

### T-507b remaining (CLI orchestrator)

Carries OQ-1=A single + OQ-2=A compose-direct + OQ-3=A post-replay-SQL-copy + OQ-6=A PF=None (with ADR-0008). Address all CONCERNs from prior T-507 REVISE: PF Decimal/float explicit cast, run_dispatcher_for_bot signature fix, --override syntax precision, §N3 helper annotations, env-gated integration test. Est: ~280 LOC src + ~220 LOC tests + ADR.

### F5 cluster progress (per T-500 backlog)

- **Backtest harness cluster (T-501..T-509, 9 tasks)**: 7/9 done — T-501..T-505 + T-506 + **T-507a NEW**. **Remaining**: T-507b + T-508 + T-509.
- **Shadow variants runtime cluster (T-510..T-514, 5 tasks)**: 3/5 done (unchanged). **Remaining**: T-511 + T-512 + T-513.
- **UI extensions (T-515..T-517, 3 tasks)**: 1/3 done (unchanged).
- **Backend polish + ops (T-518..T-522, 5 tasks)**: 0/5 done (unchanged); T-520 hardening 3/5 cherry-picked.

### Watch-outs for next session

- **T-507b** is next. Plan-doc rewrite needed (T-507.md was renamed to T-507a.md; T-507b plan-doc fresh write). 4 OQs from prior REVISE cycle answered (OQ-1/2/3/6 all=A); 1 ADR write (PF semantic).
- **Dev stack**: postgres + nats Docker + analytics-api uvicorn + Vite all running; LAN access live via `192.168.100.100`.
- **CI status**: pre-commit clean on master.

---

## 2026-05-08 (morning — T-506 PaperExchange replay-mode shipped + chore(devx) dev-stack wrapper)

**F5 phase: 11/22 numbered tasks done (~50%) + 3 T-520 hardening shortlist sub-commits unchanged + 1 chore(devx) dev-stack lifecycle wrapper.** Master HEAD `b179e8d`. T-506 + chore(tasks) + chore(devx) = 3 master commits this morning.

### chore(devx) `b179e8d` — dev-stack one-command lifecycle

`scripts/dev-up.sh` + `scripts/dev-down.sh` (NEW, 134 LOC bash) + `docs/runbooks/dev_stack.md` (NEW, 71 LOC) + `README.md` `## Local dev` section. **Why**: operator-asked after morning incident — `dev-up.sh` prvá skúška recreated postgres+nats BEZ overlay, stratila port-publish, broke analytics-api connection pool. Brief-reviewer FIX FIRST chytil 2 BLOCKERs (compose overlay missing + hardcoded password) + 4 CONCERNs (kill PGID silent no-op without setsid + health-poll missing fail-fast + hostname -I non-deterministic + runbook password-source clarity); všetky 6 adresované. Mid-review damage repaired live (recreate s overlay + restart analytics-api PID 337430 → 349407 pred commit-om). Workflow odteraz: `./scripts/dev-up.sh` štartuje compose overlay (postgres + nats) + setsid-nohup uvicorn + setsid-nohup pnpm vite; `./scripts/dev-down.sh` zhodí všetko cez kill -- -PGID + compose stop. Idempotent (PID-file checks); fail-fast na 30s healthcheck timeout; LAN IP cez `ip -4 -o addr show eno1`; DSN derives z `.env` POSTGRES_PASSWORD (fallback devpass). Mini-task pattern bez plan-reviewer per `chore(devx) 868e35b` precedent.

### T-506 delivered

- **Backtest harness cluster (T-501..T-509, 9 tasks)**: 6/9 done (was 5/9). T-506 PaperExchange replay-mode wired to HistoricalOHLCSource via T-505 intra-candle path expansion; 187 src LOC + 391 test LOC + 406 plan-doc; 12 new tests; live-mode 100% intact (106 existing tests unchanged). Plan-reviewer 2-pass APPROVE (REVISE → APPROVE on `_last_candle` BLOCKER caught at concern #6); brief-reviewer 2-pass SHIP (FIX FIRST → SHIP on inline comment line citation). **Remaining**: T-507 CLI orchestrator (top-of-DAG zostávajúce ne-T-520) + T-508 comparison mode + T-509 worker-connect.

### Key implementation details for T-507 hand-off

- **PaperExchange constructor** now accepts `mode: Literal["live","replay"]` + `historical_source: HistoricalOHLCSource | None`. Defaults preserve live-mode backwards compat.
- **Replay entry point**: `await paper.run_replay()` — iterates injected source to exhaustion. Returns None.
- **Intra-candle expansion**: each OHLCRow → 4 prices via T-505 `generate_intra_candle_path` → 3 sequential segments fed through new `_check_sl_tp_crosses_replay(symbol, low, high)`. Segment ranges `[min(seg_open, seg_close), max(seg_open, seg_close)]` are narrower than the full real candle, so SL/TP fire in chronological order (TradingView "Replay" semantics).
- **Drain-side caveat for T-507 CLI**: `_drain_sl_tp_fill` writes to live `paper_*` tables in replay mode (same drain path as live). T-507 CLI must run against dev DB; production replay sandbox out of scope per §0.8.
- **`_last_candle` cache** (BLOCKER fix from plan-reviewer concern #6): `_process_replay_candle` populates BOTH `_last_price` and `_last_candle` (synthesised `OhlcCandlePayload` with hardcoded `source='binance'` — schema lie contained because `_compute_slippage` reads only `candle.high`/`.low`, never `.source`). Without this, T-507 signal-driven `place_market_order` would `KeyError` on first call.

### F5 cluster progress (per T-500 backlog)

- **Backtest harness cluster (T-501..T-509, 9 tasks)**: 6/9 done — T-501 + T-502 + T-503 + T-504 + T-505 + **T-506 NEW**. **Remaining**: T-507 + T-508 + T-509.
- **Shadow variants runtime cluster (T-510..T-514, 5 tasks)**: 3/5 done (unchanged) — T-510a + T-510b + T-514. **Remaining**: T-511 + T-512 + T-513.
- **UI extensions cluster (T-515..T-517, 3 tasks)**: 1/3 done (unchanged) — T-515. **Remaining**: T-516 + T-517 (soft-blocked na T-512).
- **Backend polish + ops cluster (T-518..T-522, 5 tasks)**: 0/5 done (unchanged); T-520 hardening shortlist 3/5 cherry-picked sub-commits from yesterday.

### Active lessons (`docs/review-lessons.md`)

13 lessons L-001..L-013 unchanged (T-506 nedidal žiadne nové generalizable lesson — `_last_candle` BLOCKER bol task-specific cache parity, nie cross-task pattern).

L-006 (LOC overshoot acceptable on integration tasks) najviac uplatňované — T-506 src 187 = +50% nad plánom 125 (kvôli WG-required documentation blocks); ON TRACK per drift-checker.

### Watch-outs for next session

- **F5 phase pickup**: 11/22 numbered tasks remaining + 2 T-520 sub-items.
- **Top-of-DAG zostávajúce ne-T-520 / ne-T-512**: **T-507 PaperExchange CLI orchestrator** (~250 LOC src + ~180 LOC tests; pre-emptively split-flagged per L-007 — môže sa rozdeliť na T-507a orchestration + T-507b summary stats ak compute non-trivial; integruje T-503 + T-504 + T-502 + T-506 do single in-process CLI). T-518 Feature auto-backfill + T-516 shadow variants UI (soft-blocked na T-512) + T-519 hazard test audit (gating; late-F5).
- **Critical-path bottleneck**: T-512 OHLC replay restart-recovery (kill-during-variant integration test mandatory) zostáva najťažší F5 task.
- **Dev stack**: postgres + nats v Dockeri uptime ~24h (healthy); analytics-api + Vite procesy zomreli zo včera, neresetované (T-506 backend-only task, neboli potrebné).
- **CI status**: ci-fast + ci-full + e2e all green on master HEAD chains across yesterday's 33 commits + dnešný `a96df9e`.
- **Master HEAD trajectory**: yesterday `64cda81` (status) → today `a96df9e` (T-506).

---

## 2026-05-07 (evening session-end — F5 marathon: 10/22 tasks done + T-520 hardening shortlist 3/5)

**F5 phase: 10/22 numbered tasks done (~50%) + 3 T-520 hardening shortlist sub-commits + L-013 lesson generalizing pre-emptive _to_jsonable convention.** Master HEAD `426e873`. **Today total: 33 master commits** across morning F4 close + afternoon F5 marathon.

### F5 cluster progress (per T-500 backlog)

- **Backtest harness cluster (T-501..T-509, 9 tasks)**: 5/9 done — T-501 migration 0013 backtest_trades + T-502 ReplayBus + T-503 HistoricalOHLCSource + T-504 HistoricalSignalSource + T-505 intra-candle path generator. **Remaining**: T-506 PaperExchange replay-mode wiring + T-507 CLI orchestrator + T-508 comparison mode + T-509 worker-connect.
- **Shadow variants runtime cluster (T-510..T-514, 5 tasks)**: 3/5 done — T-510a migration 0014 shadow_variants/rejected schema + T-510b shadow.py read+write helpers + 2 StrEnums + T-514 shadow config schema. **Remaining**: T-511 shadow-worker FSM (H-016 owner) + T-512 OHLC replay restart-recovery (H-023 owner) + T-513 rejected-signal observation.
- **UI extensions cluster (T-515..T-517, 3 tasks)**: 1/3 done — T-515 YamlDiffView strategy editor diff. **Remaining**: T-516 shadow variants per-trade drill-down + T-517 aggregate + rejected explorer.
- **Backend polish + ops cluster (T-518..T-522, 5 tasks)**: 0/5 done. **Remaining**: T-518 feature auto-backfill + T-519 hazard test audit (E4 gating) + T-520 hardening shortlist (multi-commit; 3/5 sub-items done — see below) + T-521 final docs + T-522 close-out runbook.

### T-520 hardening shortlist progress (multi-commit umbrella)

3/5 sub-commits done in this session:

1. **`chore(ui)` ui nav persist** (`bb5d57b`) — Zustand `persist` middleware on `useNavStore.lastSelectedBotId` via localStorage namespaced key `scalper-v2-nav`; resolves F4 E1 smoke nit (per-bot + strategy nav links disabled after refresh until re-pick); `partialize` whitelist + `version=1`; 4/4 vitest tests passing.
2. **`chore(ci)` Playwright cache** (`bc1cab7`) — `actions/cache@v4` step in `.github/workflows/e2e.yml` keyed on `ui/pnpm-lock.yaml`; cache hit drops Playwright install ~10 min cold → ~30s; resolves F4 E1 watch-out from `chore(F4-E1-smoke)` `4caa3d0`.
3. **`fix(signal_gateway)` L-011 pre-emptive** (`9d1370e`) — `signal_gateway.insert_signal` payload serialised via `json.dumps(_to_jsonable(payload))` instead of `json.dumps(payload)`; mirror T-510b shadow.py B-mode; switch trigger documented; codec-immune convention regardless of future signal-gateway codec registration. 4/4 mock tests + 4/4 integration env-gated all passing.

**Remaining T-520 sub-items (2)**: audit pre-fix rows cleanup (id 1+2 from F4 E1 c241c15 intermediate fix; smoke residue, optional defensive cleanup) + T-401c symbol_map cleanup migration (defensive; dev DB already clean operator-side). Both punch-list items can be cherry-picked ad-hoc; not F5 close-out blockers.

### Today's master commits (33)

**Morning (F4 close, 7 commits)**: `c3c8a57` fix(T-413) BotSelector wire-up + `868e35b` chore(devx) Vite LAN-bind + `2968461` fix(deps) mako/pip CVE + `c241c15` fix(audit) intermediate `default=str` + `67e8c5f` fix(audit) double-encode proper + L-011 lesson + `4caa3d0` chore(F4-E1-smoke) sign-off + `0c086ae` chore(tasks) T-500 F5 backlog populate.

**Afternoon (F5 marathon, 26 commits)**:
- F5 implementation tasks: T-501 (`071576b`) + T-505 (`4ae8b39`) + L-012 fix (`7c5c025`) + T-510a (`8716cc0`) + T-504 (`2f22505`) + T-510b (`6df8859`) + T-503 (`2a040d4`) + T-502 (`c1fcae8`) + T-514 (`419b712`) + T-515 (`2c6ca4d`).
- T-520 cherry-picks: ui nav persist (`bb5d57b`) + Playwright cache (`bc1cab7`) + signal_gateway L-011 (`9d1370e`).
- Lessons: L-013 codec-state-immune JSONB convention (`426e873`).
- Plus 9 `chore(tasks)` follow-ups (one per F5-numbered done) + 1 `chore(F4-E1-smoke)` sign-off.

### Active lessons (`docs/review-lessons.md`)

13 lessons L-001..L-013 active:
- **L-013 NEW** — pre-emptive `_to_jsonable` wrapper as codec-state-immune JSONB-writer convention (generalizes T-510b + T-520 cherry-pick #3).
- **L-012 NEW** (this morning) — explicit revision targets in migration downgrade tests (caught T-501 ci-full regression).
- **L-011 NEW** (yesterday F4 E1 close) — JSONB double-encode under registered codec.

L-006 LOC overshoot acceptable on integration tasks + L-007 pre-emptive split discipline most exercised across F5 cohort (T-510a/T-510b explicit split per L-007).

### Watch-outs for next session

- **F5 phase pickup**: 12/22 numbered tasks remaining + 2 T-520 sub-items + L-013 active control needs codification in plan-reviewer subagent prompt.
- **Top-of-DAG zostávajúce ne-T-520**: T-506 PaperExchange replay-mode integration (~200 src; integrates T-503+T-505 with existing T-213b — drift risk on existing fill-semantics test suite) + T-518 Feature auto-backfill (~200 src; APScheduler integration; isolated from F5 cluster) + T-516 shadow variants UI (~250 src; needs T-512 runtime — soft-blocked) + T-519 hazard test audit (gating; late-F5).
- **Critical-path bottleneck**: T-512 OHLC replay restart-recovery (kill-during-variant integration test mandatory) is heaviest F5 task; T-516 + T-517 UI tasks soft-blocked on T-512 runtime.
- **Dev stack still up at session end**: postgres + nats + analytics-api (PID `66469`) + pnpm dev Vite (PID `13010`). Backgrounded; reusable next session.
- **CI status**: ci-fast + ci-full + e2e all green on master HEAD chains across today's 33 commits (verified across multiple `gh run watch` cycles); L-012 fix from morning unblocked T-501 cohort regression.
- **Master HEAD trajectory**: `4caa3d0` (F4 E1 close) → `0c086ae` (T-500) → ... → `426e873` (L-013).

### F5 close-out estimate (per OQ-3=A)

Per BRIEF §19:2575+ "est. 2-3 weeks" + operator OQ-3=A "2 weeks realistic": at current pace (10 tasks shipped 1 day after F5 unlock), F5 close-out plausibly ~5-7 days **if** session length normalizes. T-512 + T-519 are the heavy gating tasks. F5 close-out runbook (T-522) ships ~280 LOC mirror T-313/T-423.

---

## 2026-05-07 (session-end — F4 E1 smoke runbook executed + 5 master-fix commits)

**F4 phase exit-criteria E1 SIGN-OFF COMPLETE: PASS with 2 partials.** Master HEAD `67e8c5f`. Runbook `docs/runbooks/F4_E1_dashboard_smoke.md` ticked end-to-end with operator `luster` + sign-off timestamp `2026-05-07T15:26:32+00:00`. F4 phase truly closed — F5 unlock pending operator decision per §0.10.

### Today's master commits (5)

- `fix(T-413)` `c3c8a57` — Overview BotSelector multi-select → `useNavStore.setLastSelectedBotId` wire-up (3 LOC src; uncommitted from prior evening session shipped today).
- `chore(devx)` `868e35b` — Vite dev server LAN-bind (`host: "0.0.0.0"` per §16.2; backend stays on `127.0.0.1`). Operator can now browse `http://192.168.100.100:5173/` from LAN devices without SSH tunnel.
- `fix(deps)` `2968461` — bumped `mako 1.3.11→1.3.12` + `pip 26.0.1→26.1.1` for CVE-2026-44307 + CVE-2026-6357 (CVEs published 2026-05-05→2026-05-07 on existing transitive deps; ci-full was red on master before this commit).
- `fix(audit)` `c241c15` (intermediate, default=str) + `67e8c5f` (proper double-encode fix) — `audit_events.{before,after}_state` JSONB double-encode trap under analytics-api registered JSONB codec. Helper `audit.py:insert_audit_event` now passes Python dict directly to asyncpg (codec serialises once); UUID/datetime/Decimal pre-stringified via new `_to_jsonable(value)` helper that recurses dicts/lists. **L-011 lesson** added capturing the codec-asymmetry trap.

### F4 E1 smoke results

- **Cri 1** (navigate 9 sections) — ✓ FULL PASS (Steps 1-9 ticked; partials inside Steps 3 + 6 due to empty fixture acceptable).
- **Cri 2** (drill into trade end-to-end) — ✗ PARTIAL (no trades fixture; F4 scope is dashboard, not end-to-end ingest).
- **Cri 3** (scoring inspector per-rule breakdown) — ✓ FULL PASS (verified F3 dvoj-bot signal_id=3 with alpha=reject + beta=passthrough scoring evaluations from 2026-05-02).
- **Cri 4** (feature inspector chart) — ✗ PARTIAL (features table empty; OHLC ingest not active in F4).
- **Cri 5** (backtest lab POST 202 + new row) — ✓ FULL PASS (verified twice cross-fix; 2 backtest_run rows live in DB with `status=queued`).
- **Cri 6** (Playwright CI green on master HEAD) — ✓ FULL PASS (run 25504796848 on `67e8c5f` success in 1m5s, 3/3 chromium scenarios passed).

### Live-discovery audit-row data integrity

- `bot_config.apply` v2 (Event #3) drill panel renders `before_state (7 keys)` + `after_state (7 keys)` as pretty-printed JSON object — `applied_at` field has `+00:00` offset (§N1 ✓), `config_hash` 64-char hex preserved.
- `backtest_run.queued` #4 drill shows `after_state (11 keys)` with `id` UUID stringified (`f846180e-...`) + `started_at` + `date_range_start/end` all with `+00:00`.
- Pre-fix rows id 1 + 2 stored as JSON-string scalars (read-side `null`) — acceptable smoke tech-debt; new writes clean.

### Active lessons (docs/review-lessons.md)

11 lessons L-001..L-011. **L-011 NEW** — "JSONB double-encode under registered codec" — most recent and most operationally relevant. Active control: brief-reviewer must reject `json.dumps(state) if state is not None else None` patterns in JSONB-writer query helpers running under analytics-api or feature-engine (both register codec); tests must assert `isinstance(captured, dict)` AND emulate codec via `json.dumps(captured)` to prove no `TypeError`.

### Tech debt + follow-up candidates (NOT F5 blockers)

1. **`fix(T-401c)`** — symbol_map cleanup migration (`DELETE FROM symbol_map WHERE exchange NOT IN ('binance','bybit','custom')`); operator already DELETE-d 2 stale `tradingview` rows manually pre-runbook 2026-05-05.
2. **`chore(T-422)`** — Playwright cache in `e2e.yml` (`actions/cache@v4` on `~/.cache/ms-playwright`); cold install spiked once today (>13 min cancelled), subsequent runs ~1 min on warm runner cache.
3. **`chore(ui)`** — Zustand `persist` middleware on `useNavStore.lastSelectedBotId`; UX nit: per-bot + strategy left-nav links disabled after page refresh until operator re-picks bot.
4. **`fix(audit)`** — cleanup of pre-fix corrupted rows id 1 + 2 (UPDATE audit_events SET after_state = ..::jsonb WHERE jsonb_typeof(after_state) = 'string'); optional polish.
5. **`fix(signal_gateway)`** — apply same `_to_jsonable` pattern when/if signal-gateway service registers JSONB codec; currently safe-by-accident, latent flag in L-011.

### Watch-outs for next session pickup

- **F5 phase unlock decision** — F4 truly closed today; F5 (Shadow Variants + Backtest Harness + Finishing per BRIEF §19:2575+; est 2-3 weeks) unlock pending operator decision per §0.10 phase-gate.
- **Dev stack restart procedure**: laborka shell needs DSN + NATS_URL overrides because `.env` uses compose-internal hostnames (`postgres`, `nats`). Working invocation: `DATABASE_URL='postgresql://scalper:devpass@127.0.0.1:5432/scalper' NATS_URL='nats://127.0.0.1:4222' uv run uvicorn services.analytics_api.app.main:create_app --factory --host 127.0.0.1 --port 8000`. Vite reads `vite.config.ts`, no env override needed.
- **Vite LAN URL**: `http://192.168.100.100:5173/` from any LAN device (per `chore(devx)` `868e35b`); backend stays 127.0.0.1, Vite proxies `/api` + `/events` server-side.
- **Two background processes** still running at session end: pnpm dev (Vite) `bop4f8rx3` + uvicorn (analytics-api) `bz8o3mf8j`. Operator can leave them up or kill at will; no unsaved state.

---

## 2026-05-05 (evening session-end — F4 close-out + smoke runbook live demo)

**F4 phase exit-criteria E1 partial sign-off — operator-led runbook execution started; remote LAN access established via SSH tunnel; one ad-hoc UX bug found + fixed (UNCOMMITTED).** Master HEAD unchanged from afternoon session (`d161863` chore(tasks): T-423 done). Pending uncommitted: **`ui/src/routes/index.tsx`** (3-line wire-up: Overview BotSelector → `useNavStore.setLastSelectedBotId`).

### Live demo session events

- **Symbol map enum drift bug surfaced + worked-around** during runbook Step 4. Stale `tradingview` rows in `symbol_map` (left over from F3 dvoj-bot smoke; predate T-401b ExchangeSource StrEnum tightening to `binance|bybit|custom`). `/api/symbol-map/` returned 500 ValueError. **Fix applied in-place via SQL DELETE** of 2 stale rows. ROOT CAUSE: T-401b enum tightening had no DB cleanup migration. **Follow-up task candidate**: `fix(T-401c)` migration `DELETE FROM symbol_map WHERE exchange NOT IN ('binance', 'bybit', 'custom')` — defensive even though dev DB now clean.
- **Per-bot nav link UX bug**: Overview BotSelector (multi-mode) did NOT update `useNavStore.lastSelectedBotId`, so left-nav "Per-bot live view" stayed disabled until operator manually navigated to `/bot/<id>` URL. **Fixed in `routes/index.tsx`** (3-line: import `useNavStore`, hold `setLastSelectedBotId` ref, on multi-select pick first bot as last-selected). UNCOMMITTED — needs drift-checker + brief-reviewer next session before commit.
- **LAN access pattern verified**: SSH tunnel from secondary PC works (`ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 luster@laborka` then browse `http://localhost:5173`). Per BRIEF §16.6 LAN-only invariant — no wider exposure needed; SSH tunnel preserves "no public listener" stance.
- **CI run 25390558068 still in_progress at session end** (master push of T-422 from afternoon). Step 6 "Install Playwright chromium browser" running >13 min (no `actions/cache@v4` in `e2e.yml` workflow). Background task `by8nuoi6o` still polling — notification on completion will fire automatically. **Follow-up task candidate**: `chore(T-422): cache Playwright browsers in CI` — `actions/cache@v4` on `~/.cache/ms-playwright` + `~/.cache/apt` — reduces cold install ~10 min → ~30 s.

### Watch-outs for next session pickup

1. **First action: commit the uncommitted `ui/src/routes/index.tsx` wire-up.** Inspect via `git diff ui/src/routes/index.tsx` (3-line; in-scope of T-413 nav UX). Path: drift-checker → brief-reviewer → `fix(T-413): Overview BotSelector → useNavStore wire-up` commit. NO new task ID needed — bug regressed from original T-413 plan.
2. **Second action: triage CI run 25390558068 outcome.** If green → operator can tick runbook Step 10. If red → fetch artifact `playwright-report` (7-day retention).
3. **Third action: optional follow-up `chore(T-422)` Playwright cache** — operator-driven; F5 unlocking shouldn't wait on this.
4. **Fourth action: optional follow-up `fix(T-401c)` symbol_map cleanup migration** — only if operator wants belt-and-braces; current dev DB already clean.
5. **F4 E1 sign-off section** in `docs/runbooks/F4_E1_dashboard_smoke.md` — operator continues runbook ticks (Steps 4-9 visual / Step 10 CI-side); when 6 checkboxes done, fill ISO-8601 `+00:00` timestamp.
6. **F5 phase unlock** — pending operator decision per §0.10 phase-gate after E1 sign-off.

---

## 2026-05-05 (session-end)

**F4 phase COMPLETE: 24/24 numbered tasks + T-423 close-out runbook shipped (100% F4 scope delivered).** Master HEAD `8d6cfe9` (this commit), branch up-to-date with origin. Repo-wide pytest **1789 passed** (unchanged; F4 frontend cohort didn't add backend tests — backend was complete at T-409). Vitest **160 passed** (4 → 160 = +156 across T-410 scaffold + T-411 component lib + T-412..T-420 9 dashboard sections + T-422 api-client). Dashboard-query parametrizations **23** (CI-full gated; 0 → 2 → 23 from T-421). Playwright scenarios **3** (master-push gated; 0 → 3 from T-422). 47 pytest skipped (env-gated integration; unchanged), no regressions.

### F4 close-out summary — 12 tasks shipped this marathon session

- **T-412** (commit `39d7ea8`) — Section 1 Overview cross-bot dashboard route at `/`
- **T-413** (commit `0c56aac`) — Section 2 Per-bot live view at `/bot/$botId` + Zustand SSE store + useSSEStream hook + PnlChart Recharts wrapper + SignalFeed
- **T-414** (commit `5bb7cb2`) — Section 3 Trade explorer + drill-down at `/trades` + `/trades/$tradeId` (8 sections; 2 supported BRIEF tiers + 5 placeholder F4+/F5+) + format-time helpers + TimelineSection primitive
- **T-415** (commit `c093538`) — Section 4 Backtest lab at `/backtests` + `/backtests/$runId` + StatusBadge kind="backtest" extension
- **T-416** (commit `a688eb9`) — Section 5 Strategy editor at `/strategy/$botId` + useDebouncedValidation hook (500ms + AbortController)
- **T-417** (commit `91ced0d`) — Section 6 Feature inspector at `/features` + FeatureChart + StalenessDot (5min UX threshold)
- **T-418** (commit `ad84d9b`) — Section 7 Scoring inspector at `/scoring` + `/scoring/$signalId`; extracted ScoringBreakdownView from T-414 + new FeatureSnapshotTable
- **T-419** (commit `29bc4c8`) — Section 8 Audit log viewer at `/audit` + `?correlation_id=` URL search-param consumer; T-411 `as never` casts retired
- **T-420** (commit `2c1022e`) — Section 9 Settings at `/settings` (4 sections — Bot registry + Symbol map CRUD + 2 placeholders); **L-010 BLOCKER fix** apiFetch 204 No Content short-circuit + REAL fetch-path test coverage
- **T-421** (commit `4ca57d2`) — Grafana ops dashboards (4 NEW JSON: service-health + nats + pg + host) — first non-UI F4 task
- **T-422** (commit `7dd72c4`) — Playwright E2E critical journeys (3 scenarios + master-push CI workflow); first new pnpm dev-dep `@playwright/test@1.59.1` (L-009 active control re-tested — 0 new CVEs vs baseline)
- **T-423** (this commit) — F4 exit-criteria runbook close-out

### Critical events

- **F4 phase complete at T-423.** Runbook `docs/runbooks/F4_E1_dashboard_smoke.md` ships operator-runnable smoke checklist verifying BRIEF §19:2569-2570 5 exit criteria. F4 phase exit-criteria E1 verification PENDING operator-side runbook execution + sign-off.
- **L-010 lesson added** (T-420 brief-reviewer FIX FIRST): apiFetch 204 No Content fix — shared fetch wrapper that always calls `res.json()` silently breaks first 204/empty-body endpoint while mocked tests pass (T-420 DELETE /api/symbol-map/{id} would fail runtime). Active control: when introducing NEW DELETE/PUT-no-body/202-empty mutation, brief-reviewer MUST verify fetch wrapper handles 204 + empty Content-Length BEFORE res.json(); test must exercise REAL apiFetch via fetch-level mock, not apiFetch-level mock.
- **First F4 dep audit since T-411**: T-422 introduced `@playwright/test@1.59.1` — L-009 active control re-tested. Pre-existing 14 baseline vulnerabilities all from T-410 deps; Playwright adds 0 new CVEs (verified via `git stash` baseline comparison). Critical vulnerability `vitest` Remote Code Execution (GHSA-9crc-q9x8-hgqq) predates T-422 — separate fix task scope. Truthful "0 new CVEs vs baseline" framing locked across plan + README + commit message per WG#4.
- **T-411 explicit promise fulfilled by T-419**: `as never` casts on `CorrelationIdChip.NavigatingChip.navigate({to, search})` RETIRED post `/audit` route registration; TypeScript strict mode validates typed routing path.
- **9 dashboard routes + 9 left-nav links live**: Overview + Per-bot + Trade explorer + Backtest lab + Strategy editor + Feature inspector + Scoring inspector + Audit log + Settings. All 9 BRIEF §14.3 sections shipped per spec.

### LOC trend (F4 cumulative across this session)

T-412 -39%, T-413 +78%, T-414 +76%, T-415 +47%, T-416 +30%, T-417 +3%, T-418 +27.5%, T-419 -10%, T-420 +26%, T-421 (JSON exempt §0.3), T-422 (test/CI exempt §0.3), T-423 (docs exempt §0.3). F4 frontend cohort tolerance well-utilized; per L-006 cohort precedent acceptable.

### Active lessons (docs/review-lessons.md)

10 lessons L-001..L-010 platné. **L-010 NEW** (T-420 / apiFetch 204 No Content fix) — most recent + most operationally relevant for any future DELETE/PUT-no-body endpoint introduction. L-006 (LOC overshoot acceptable on integration tasks) najviac uplatňované celej F4 cohort. L-009 (pip-audit on new deps) re-tested at T-422 — active control disciplined.

### Watch-outs for next session

- **F5 phase pick-up** — per BRIEF §19:2575+ (Shadow Variants + Backtest Harness + Finishing; est 2-3 weeks). Phase gate not yet open — F5 unlock requires operator decision after F4 exit-criteria E1 sign-off.
- **F4 exit-criteria E1 verification pending**: operator must run `docs/runbooks/F4_E1_dashboard_smoke.md` end-to-end on dev host (analytics-api + Vite dev server running); tick 6 exit-criteria check-boxes (5 BRIEF + 1 Playwright CI green); sign-off section uses full ISO-8601 `+00:00` per §N1.
- **First master-push triggers `e2e.yml` workflow** — chromium browser install + 3 scenario run; if green → operator can tick Step 10 of runbook; if red → `playwright-report/` artifact retained 7 days for triage.
- **vitest critical CVE GHSA-9crc-q9x8-hgqq** predates F4 (from T-410 baseline) — separate fix task scope; not blocking F4 close-out but should be addressed in F5+ as dep-update opportunity.
- **F4+ deferred backend endpoints surface in dashboard placeholders**: virtual_balance + alert_count (T-412 placeholders) + 3 trade drill-down tiers (T-414 placeholders: order_events / executions / post_close_snapshots) + correlation_id audit filter (T-419 placeholder) + plugin registry + API key status (T-420 placeholders). Each is a candidate F4+ task; landing them turns existing UI placeholders functional without UI rewrites.

## 2026-05-04 (session-end)

**F4 marathon continues: 13/24 tasks shipped (T-400..T-411).** Master HEAD `2a5d2a6`, branch up-to-date with origin. Repo-wide pytest **1789 passed** (1713 → 1789 = +76 nových Python testov; T-407 +43 + T-408 +20 + T-409 +19 - posun -6 reportu kvôli premiestneniu skipped). Vitest **20 passed** (T-410 4 + T-411 16). 96 pytest skipped (no change), no regressions.

### Tasks completed this session (5 backend + 2 frontend = 7)

- **T-407** (commit `1a95b13`) — `/api/backtests/*` 3 endpoints + migration 0012 backtest_runs + BacktestStatus enum + atomic audit-tx; pgcrypto first repo-wide use
- **T-408** (commit `9294313`) — `/events/stream` SSE multiplexed endpoint + SSEMultiplexer lifespan singleton + 4 Settings knobs (env-tunable per L-001) + asgi-lifespan dev-dep
- **T-409** (commit `c9aad6e`) — `services/alerting/` skeleton + Telegram delivery via NATS system.alerts; 7th service (UID/GID 10007); jinja2 + PyYAML new deps
- **T-410** (commit `71dc3cf`) — `ui/` React 18 + Vite 5.4 + TS strict + Tailwind + 6 shadcn baseline + TanStack Router/Query + Zustand + Recharts + Vitest scaffold
- **T-411** (commit `369dac9`) — UI component library: 6 reusable components (DataTable + TimeRangePicker + BotSelector + StatusBadge + PriceDelta + CorrelationIdChip) + api-types mirror + showcase route

### Critical events

- **F4 backend complete** at T-409 — 11/11 backend tasks shipped; UI cohort began with T-410.
- **Toolchain bump mid-T-410**: Operator-led Node 18.19.1 → 20.19.6 via nvm. Required for `@tanstack/router-plugin@1.167+` (`unplugin@3` needs `import.meta.dirname` from Node 20.11+). pnpm 10.33.2 reinstalled under nvm prefix. Memory `ui_toolchain.md` records this — every shell must `. ~/.nvm/nvm.sh && nvm use --delete-prefix v20.19.6` before pnpm/node calls.
- **Post-merge hotfix on T-409 jinja2 CVEs**: ci-full pip-audit detected CVE-2024-56326 + CVE-2024-56201 + CVE-2025-27516 in jinja2==3.1.4. Bumped to 3.1.6 in `fix(T-409)` commit `9bec47a`. **L-009 lesson** (`docs/review-lessons.md` + commit `97a8208`) captures the gap: local pre-commit doesn't run pip-audit; only ci-full does. Active control — plan-reviewer must require "verified latest patch with no open CVEs" sentence in §0.9 for new deps; brief-reviewer should run `uv run pip-audit --skip-editable` on staged uv.lock.
- **T-410 ci-full failure historical**: T-410 chore commit ci-full ran against pre-fix lockfile (jinja2 still 3.1.4 from T-409). Failure superseded by `fix(T-409)` 3 minutes later. No T-410 action needed.

### LOC trend (F4 cumulative)

T-400 +23%, T-401a +6.5%, T-401b +28%, T-402 +26.5%, T-403 +43%, T-404 +6.75%, T-405 +142%, T-406 +106%, T-407 +87%, T-408 +50%, T-409 +154%, T-410 +60%, T-411 +123%. Frontend cohort tracking +60-123% (within F4 endpoint-group precedent).

### Active lessons (docs/review-lessons.md)

9 lessons L-001..L-009 platné. **L-009 NEW** (T-409 / pip-audit gap) — most recent + most operationally relevant. L-006 (LOC overshoot acceptable on integration tasks) najviac uplatňované celej F4 cohort.

### Watch-outs for next session

- **`pnpm` requires nvm-shimmed Node 20** — every shell must `. "$HOME/.nvm/nvm.sh" && nvm use --delete-prefix v20.19.6 >/dev/null 2>&1` before any pnpm/node command. `~/.bashrc` has nvm source line; `--delete-prefix` resolves conflict with legacy `~/.npm-global/bin/pnpm`.
- **Vite dev server requires backend running**: `pnpm dev` proxies `/api` + `/events` to `http://127.0.0.1:8000`. Operator must run `uv run uvicorn services.analytics_api.app.main:create_app --factory --host 127.0.0.1 --port 8000` in separate terminal else fetch fails ECONNREFUSED. README has happy-path.
- **shadcn/ui 7th baseline `<DropdownMenu>`**: T-411 used heavyweight `<Dialog>` for column visibility per WG#2 (no new shadcn primitive). T-412+ may add `<DropdownMenu>` baseline if column visibility UX feels too heavy in real usage.
- **TanStack Router strict typed routes**: `<CorrelationIdChip>` casts `to: "/audit" as never` because `/audit` route doesn't exist yet (T-419). Cast retires when T-419 lands.
- **API type drift**: `ui/src/lib/api-types.ts` is hand-maintained mirror of Pydantic models. T-412..T-420 will extend per consumer. F5+ may switch to `openapi-typescript` codegen if drift > 5 incidents.

## Next session pick-up — TOMORROW

**Phase: F4 (continuing).** 11/24 tasks remain (9 dashboard sections T-412..T-420 + 3 ops T-421..T-423).

### Recommended next task: T-412 — Section 1 Overview

**Per BRIEF §14.3:2060 + TASKS.md:128**: cross-bot dashboard tiles — open positions count, aggregate virtual balance, 24h P&L, signals received/accepted/rejected, alert count. Blocked by T-411 (✓ shipped) + T-401 (✓ /api/bots/) + T-402 (✓ /api/positions/, /api/trades/) + T-406 (✓ /api/analytics/expectancy + /api/analytics/pnl-series). Est: ~200 LOC src + ~150 LOC tests.

T-412 is a **pure consumer task** (uses existing T-411 components + T-401/402/406 endpoints; no new backend, no new components). Should be smaller than T-411 — first true dashboard route landing.

**OQ to consider before T-412 plan-reviewer**:
1. Top bar layout — bot selector position (left of presets vs right) + connection-status indicator (green dot if SSE connected; T-413 wires SSE so T-412 can render placeholder dot).
2. Tile aggregation — frontend-side (sum across `/api/positions/?bot_id=` per-bot fetches) vs backend-side (new `/api/analytics/overview` endpoint). Default A: frontend aggregation per BRIEF §0.8 anti-hypothetical (avoid premature backend additions).
3. Auto-refresh interval — TanStack Query `refetchInterval`? Default A: 30s (matches `staleTime`); per-tile override possible later.
4. Time-range scope — Overview uses 24h window per BRIEF §14.3:2060. TimeRangePicker visible but only "24h" preset effective in T-412 (rest grayed) OR full picker enabled (consumer slices)?

### After T-412

T-413 Per-bot live view (consumes T-408 SSE for live signals/positions) → T-414 Trade explorer drill-down → T-415 Backtest lab → T-416..T-420 remaining sections → T-421 Grafana → T-422 Playwright → T-423 F4 exit-criteria bundle.

### Useful refs (for tomorrow)

- `ui/src/routes/index.tsx` — placeholder showcase (T-412 replaces with Overview tiles)
- `ui/src/lib/api-types.ts` — extend with TradeRow / OpenPositionRow / AnalyticsExpectancyResponse interfaces
- `ui/src/components/` — DataTable + StatusBadge + PriceDelta + BotSelector + TimeRangePicker ready for consumption
- BRIEF §14.3:2060 — Overview spec verbatim
- `docs/plans/T-411.md` — pattern for next plan-reviewer cycle (component-consumer task)

---

## 2026-05-03 (session-end)

**F4 marathon: 8/24 tasks shipped (T-400 + T-401a + T-401b + T-402 + T-403 + T-404 + T-405 + T-406).** Master HEAD `459d41a`, branch up-to-date s origin. Repo-wide pytest **1713 passed** (1460 → 1713 = +253 nových testov), 85 skipped — žiadne regresie.

### Endpoints live (analytics-api)

15 endpointov vo 4 doménach:

- `/api/bots/*` (T-401a) — list + detail
- `/api/symbol-map/*` (T-401b) — 5× CRUD s atomic audit-tx
- `/api/positions/*` + `/api/trades/*` (T-402) — 3 endpointy
- `/api/signals/*` + `/api/scoring/by-signal/{id}` (T-403) — 3 endpointy
- `/api/features/{latest,history}` (T-404) — 2 endpointy
- `/api/configs/*` + `/api/audit/*` (T-405) — 7 endpointov + apply path s 5-helper same-conn tx
- `/api/analytics/*` (T-406) — 4 endpointy: expectancy + heatmap + pnl-series + Monte-Carlo s in-memory cache + asyncio.to_thread offload

### Patterns established for F4 endpoint groups

- StrEnum domain types (`BotStatus` / `ExchangeMode` / `ExchangeSource` / `TradeStatus` / `IngestionStatus` / `ScoringDecision`) v `packages/core/types.py` pre FastAPI Query auto-422
- Decimal-as-string per §5.3 (NUMERIC) vs float per §5.13 (DOUBLE PRECISION) — domain split rigorózne dodržaný
- Dynamic SQL builder pattern `_build_*_where_clause` s `$N` placeholders only per L-008 (žiadna interpolácia hodnôt)
- Atomic admin write tx pattern: T-401b 4-helper → T-405 5-helper (validate-before-tx + parse mimo tx, INSIDE `pool.acquire() + conn.transaction()`, audit emission v tej istej conn)
- `_register_jsonb_codec` per-pool init (T-401a load-bearing pre meta JSONB round-trip)
- Per-key `asyncio.Lock` anti-thundering-herd cache (T-406 mirror ADR-0006 D4)
- Mock at router import boundary (`monkeypatch.setattr("services.analytics_api.app.routers.<x>.<fn>", AsyncMock(...))`)

### LOC trend per task (vs §0.3 400 cap)

T-400 +23%, T-401a +6.5%, T-401b +28%, T-402 +26.5%, T-403 +43%, T-404 +6.75%, T-405 +142%, T-406 +106%. Endpoint groups konsistentne nad cap-om — pre-flagged + operator-acknowledged + L-006 active control. T-405/T-406 boli operátorom OQ-7=B/OQ-9=A schválené single-task ship rozhodnutia.

### Active lessons (docs/review-lessons.md)

8 lessons L-001..L-008 platné. L-006 (LOC overshoot acceptable on endpoint groups) + L-007 (pre-emptive split if migration adds) + L-008 (`$N` placeholders only — never SQL literal values) najviac uplatňované v tejto session.

### Watch-outs for next session

- **bandit `# noqa: S311` neplatí** — bandit potrebuje `# nosec B311` (T-406 prelude — pre-commit hook 2× failed kým som to zmenil). Pri ďalšej `random.Random` / `random.choices` použití použiť `# noqa: S311 # nosec B311 — <reason>` pattern (pozri `packages/exchange/bybit_v5/client.py:286`).
- **Pre-commit ruff-format reformatuje** — pravidelne stagnem znova po failure. Štandardný retry pattern.
- **Pydantic `use_enum_values=True`** je load-bearing pre StrEnum serialization v response models (T-401a regression caught).

## Next session pick-up — TOMORROW

**Phase: F4 (continuing).** 16/24 tasks zostáva.

### T-407 — backlog top per TASKS.md plan

`/api/backtests/*` endpoint group — list runs + trigger new run + status + results. Backtest execution backend deferred to F5 (T-509+); T-407 ships len API surface + minimal `backtest_runs` table per BRIEF §9.6:1629. Blocked by T-400 (shipped). Est: ~180 LOC src + ~150 LOC tests.

**OQ na uvažovanie pred štartom T-407:**
1. `backtest_runs` schema — minimal columns: id / bot_id / config_yaml_hash / from_at / to_at / status (queued|running|completed|failed) / created_at / started_at / completed_at / result_json. Default A: minimálny 9-column schema; postpone result_json columns extraction do F5+.
2. Trigger endpoint payload — `POST /api/backtests/` s body `{bot_id, from_at, to_at, config_yaml?}` → 202 Accepted (zaradené do queue, žiadny synchronný compute v F4). Default A: 202 + status=queued; F5 spustí background worker.
3. Status polling vs SSE — pre F4 default A: long-poll cez `GET /api/backtests/{id}`; SSE až v T-408.

### After T-407

T-408 (SSE multiplexed stream — komplexný backpressure ~2 dni) → T-409 (alerting service + Telegram) → T-410..T-423 UI tasks (backend complete after T-407 ships).

## 2026-05-02 (session-end)

**F3 PHASE CLOSED + F4 PHASE UNLOCKED.** Marathon session: 16/16 F3 tasks shipped + 2 F2 build regressions caught & fixed during T-313 smoke + F4 24-task plan drafted.

### F3 deliverables shipped this session

T-309 + T-310a + T-310b + T-308b + T-311 + T-312 + T-313. F3 §19:2546-2550 exit-criteria SATISFIED via dvoj-bot smoke run 2026-05-02T20:15:30+00:00 (correlation_id=`f3-e1-smoke-2`, signal_id=3, alpha=`reject` + beta=`passthrough` rozdielne rozhodnutia, 2 audit rows, oi_squeeze plugin loaded). Commits `3a0518f` … `548c0cc`.

### F2 build regressions fixed during smoke

`d1d3d45` (services/execution missing scalper-v2-exchange dep) + `a1112c1` (packages/exchange missing hatchling build config). Production Docker `uv sync --package <svc> --frozen --no-dev` path was broken; lokálne testy to maskovali workspace-wide syncom. Future Docker builds funkčné.

### F4 phase plan saved (commit `dec8c12`)

24 tasks T-400..T-423 per BRIEF §19:2552-2571 + §9.6 + §14, pre-emptively split per L-006/L-007. Master HEAD `dec8c12`, branch up-to-date with origin. 1440 tests passing locally.

### Operator-driven actions taken at session end

- `signabot.service` (paralelný v1 paper bot port 8000) — `sudo systemctl disable` permanentne
- `timescaledb` v1 Docker kontajner (port 5432) — stopped, nereštartovať
- scalper-v2 dev compose stack — `docker compose down` po smoke
- Memory updates: `sibling_bot_v1.md` + `deployment.md` reflektujú "v1 disabled" stav

## Next session pick-up — TOMORROW

**Phase: F4 Analytics API + Dashboard UI.** Start with T-400 (analytics-api skeleton).

### T-400: services/analytics_api/ skeleton

**Prereq**: žiadne (T-400 je foundational task; mirror T-309 strategy-engine + T-214 execution-service patterns).

**Scope per TASKS.md:108**:
- `services/analytics_api/app/main.py` — FastAPI factory + lifespan (asyncpg.Pool + NatsClient + structlog)
- `services/analytics_api/app/config.py` — Settings(BaseSettings); DATABASE_URL + NATS_URL + LOG_LEVEL + service_name
- `services/analytics_api/app/health.py` — `/health` + `/ready` (mirror execution T-214 verbatim)
- `services/analytics_api/app/deps.py` — FastAPI providers (get_pool, get_bus, get_settings, get_logger_dep)
- `services/analytics_api/app/__init__.py` + tests/__init__.py + py.typed
- `services/analytics_api/Dockerfile` — UID/GID **10006** (distinct from execution 10004 / feature-engine 10003 / market-data 10002 / signal-gateway 10001 / strategy-engine 10005)
- `services/analytics_api/pyproject.toml` — replace 4-line stub with hatchling config + 4 external deps (fastapi==0.136.0, pydantic-settings==2.13.1, uvicorn[standard]==0.45.0, uvloop==0.22.1) + 4 workspace deps (scalper-v2-bus, scalper-v2-core, scalper-v2-db, scalper-v2-observability)
- `services/analytics_api/tests/conftest.py` + test_app_factory.py + test_health.py + test_ready.py + test_config.py
- `compose.yaml` + `compose.dev.yaml` — analytics-api service block (mirror execution-service envelope; NO host port publish, internal-only per §16.6)

**Estimate**: ~150 LOC src + ~100 LOC tests = ~250 LOC total. Within §0.3 cap.

**Tests target**: ~12 tests (mirror T-309 structure). Repo-wide pytest 1440 → expected ~1452.

**Workflow tomorrow**:
1. **Session start guard** — read TASKS.md current state, 3 most recent ADRs, this status.md.
2. **Gate 1 plan-reviewer** — write `docs/plans/T-400.md` per CLAUDE.md §6.2 template (Purpose / Public interface / Scope / Hazards / Test strategy / §N invariants / §0.3 LOC budget / Hand verification / Open questions / Acceptance criteria / Out of scope), invoke plan-reviewer subagent for APPROVE.
3. **Implementation** — 6-step lifespan (pool create → bus connect → state attach → yield → bus.close → pool.close); reverse shutdown bus-before-pool per T-200 Q2 publish-after-persist precedent.
4. **Drift checkpoint** — drift-checker subagent after main.py reaches ~80 LOC and after first test passes.
5. **Gate 3 brief-reviewer** — pre-commit on staged diff.
6. **Gate 4 math-validator** — out-of-scope (analytics-api skeleton has zero arithmetic; CLAUDE.md Gate 4 list line 121 doesn't include `services/analytics_api/`).
7. **Commit + push** + chore(tasks) move T-400 from Next to Done newest-first.

**Watch-outs for T-400**:
- Dockerfile UID/GID 10006 — distinct from prior services per repo convention (per service Dockerfile blocks)
- Skipnutie `BOT_ID` env required (analytics-api is service-instance-singleton, not per-bot like strategy-engine T-309)
- Mirror execution-service `compose.yaml` envelope verbatim — NO host port publish (internal-only); analytics-api becomes externally accessible only via nginx + cloudflared in F5+ (per BRIEF §2.1 + §16.6)
- F4 backend ships incrementally — T-400 ship first, endpoints T-401..T-408 land per per-task plan-reviewer cycles
- Prerequisite for next session: F2 build regressions already fixed (`d1d3d45` + `a1112c1`); production Docker builds hardened. Should not surface again.

### F4 sub-phase tracking

After T-400, expected order of tasks (each with plan-reviewer Gate 1 cycle):
- T-401 → T-402 → T-403 → T-404 → T-405 → T-406 → T-407 (read endpoint groups; ~8-10 days)
- T-408 (SSE multiplexed stream; ~2 days; complex backpressure semantics — likely 2-pass plan-reviewer)
- T-409 (alerting-svc + Telegram) — can run parallel any time after T-400
- T-410 → T-411 (UI scaffold + components; ~3 days)
- T-412..T-420 (9 dashboard sections; can parallelize; ~7-10 days total)
- T-421 → T-422 → T-423 (operations + exit criteria; ~3-4 days)

Per BRIEF estimate F4 = 2-3 týždne. With per-task plan-reviewer Gate 1 cycles + L-006/L-007 LOC discipline + math-validator out-of-scope (UI/REST = no Decimal arithmetic), realistic 2-2.5 weeks at F2/F3 pace.

### Dependencies + risks for tomorrow

**No external dependencies for T-400** — purely scaffold work + docker compose extension. No Bybit credentials needed, no live OI feature pipeline needed.

**T-400 + T-401-T-407 read-endpoint LOC budget**: analytics-api accumulates ~1500 LOC across endpoint groups. CI test count grows from 1440 baseline → ~1700 expected after F4 backend complete. Watch for L-006 LOC drift on individual endpoint tasks; pre-emptive splits where any single task estimates >300 LOC src.

**T-410 UI scaffold gotchas**:
- shadcn/ui components copied to repo (not NPM deps) per BRIEF §14.1:2046
- pnpm package manager (not npm) per repo convention
- Vite dev server vs production build separate workflows
- TypeScript strict mode + Tailwind config + TanStack Router + Query setup is fragile; budget half day for first-time stack assembly

**T-422 Playwright E2E**: needs CI workflow update + browser cache; first-time setup adds ~100 LOC `.github/workflows/e2e.yml` + headless config. Slot post-T-413 + T-414 minimum; ideally after T-420.

## Useful refs (for tomorrow)

- TASKS.md F4 plan: `## Next` section lines 108-183 with full task list + dependencies graph
- BRIEF §9.6 analytics-api spec: `docs/CLAUDE_CODE_BRIEF.md:1617-1647`
- BRIEF §14 dashboard spec: `docs/CLAUDE_CODE_BRIEF.md:2041-2089`
- T-309 strategy-engine skeleton (pattern mirror for T-400): `services/strategy_engine/app/main.py` + `docs/plans/T-309.md`
- T-214 execution-service skeleton (deeper pattern reference): `services/execution/app/main.py` + `docs/plans/T-214.md`
- F3-close runbook (smoke setup gotchas): `docs/runbooks/F3_E1_dvoj_bot_smoke.md`
- Plan template: CLAUDE.md §6.2 module-design-doc structure
- Hazard-bound deferrals: TASKS.md `## Next` § "F4 hazard-bound deferrals (carry-over from F3)" — natural slots during T-409 + T-417

## Session-end action checklist (DONE)

- [x] T-313 + chore(F3-close) commits shipped (`813e6f0` + `663e0df` + `548c0cc`)
- [x] F2 build regressions fixed (`d1d3d45` + `a1112c1`)
- [x] Memory updates (`sibling_bot_v1.md` + `deployment.md`)
- [x] F4 phase unlock + 24-task plan in TASKS.md (`dec8c12`)
- [x] status.md updated for tomorrow's pick-up
- [x] Master pushed to origin
- [x] No uncommitted changes

Tomorrow: start fresh session with **"Session start"** preamble per CLAUDE.md, pick up T-400 plan-doc draft.
