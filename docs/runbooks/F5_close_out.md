# F5 close-out runbook + E1..E6 sign-off (Live-ready MVP)

**Phase:** F5 close-out — BRIEF §19 F5 "Exit criteria" (all 6 bullets verbatim below); Live-ready MVP scope per ADR-0011
**Mode:** dev (operator-host; production deploy per BRIEF §16.2 / §18)
**Owner:** operator (manual; T-522 ships this runbook + the pre-filled E1..E6 evidence trace — the **operator** executes the verification steps and signs §A + §B; the author does not pre-fill or self-sign)

Verbatim BRIEF §19 F5 exit-criteria (the 6 bullets; the `E1..E6` labels are per the TASKS.md "F5 exit-criteria trace" + ADR-0011 — they map 1:1 to these 6 bullets in order):

> - **E1**: Backtest on a 30-day historical window completes and reports aggregates.
> - **E2**: Two backtests with different configs compared side-by-side.
> - **E3**: Shadow variants persist across restart (verified by killing execution-service mid-variant).
> - **E4**: All hazards in §20 have an associated test that passes.
> - **E5**: Operator signs off on the **Live-ready MVP** scope. *(Renamed from "Plný MVP" per ADR-0011 — production-ready semantic includes pre-live operational hardening cluster T-524..T-536.)*
> - **E6**: All hardening tasks (T-524..T-536) shipped + integration tests green + Live-ready deployment runbook executed.

## Purpose

The single F5 close-out gate: verify + sign off all 6 F5 exit criteria, completing the F5 phase — §A paper-feature-complete (E1..E5) + §B Live-ready (E6). This runbook **orchestrates and cites** the evidence + per-criterion verification; it does **NOT re-derive** the prior close-out-tail audits — T-519 (E4 §20-hazard audit), T-521 (E1/E2/E3 operator smokes), T-539 (task-ledger reconciliation) already produced the underlying evidence on their own commits; T-522 ties them together for the operator sign-off. Sign-off is on an **informed known-residual basis** (see "Deliberate residual").

## Pre-flight

- [ ] Dev stack up per `docs/runbooks/dev_stack.md`; project-root `.env` populated + sourced (`set -a; . ./.env; set +a`); `POSTGRES_URL="postgresql://scalper:$POSTGRES_PASSWORD@127.0.0.1:5432/scalper" uv run alembic -c migrations/alembic.ini upgrade head` (**D1** — `alembic.ini` lives in `migrations/`; `migrations/env.py` reads `POSTGRES_URL` and does NOT auto-load `.env`; host-run DSN uses `127.0.0.1`).
- [ ] Record master HEAD at run: `git rev-parse HEAD` (captured in the §A/§B sign-off blocks).
- [ ] **Ledger trust precondition**: T-539 `docs/audit/f5-task-completion.md` verdict is **F5 LEDGER VERIFIED** (counter `64/66` correct + gapless; every E1..E6 owning task green; no forgotten/uncounted task). E6's "all hardening shipped" leans on that audit — confirm it before signing.

## E1..E6 evidence trace + operator verification

Evidence is commit-cited (feat SHA + controlling test / re-runnable command), not prose — mirrors the T-539 `docs/audit/f5-task-completion.md` model. "completing" feat is cited for L-007-split clusters; the full per-leaf trail is in the T-539 report.

### E1 — Backtest on a 30-day historical window completes and reports aggregates

- **Owners** (TASKS.md F5 exit-criteria trace): T-507 (CLI/orchestrator) + T-509 (worker connect).
- **Evidence**: feat `e4723e8` (T-507a BusProtocol + ReplayBus subscribe), `db2d282` (T-507b `scripts/backtest.py` CLI orchestrator + ReplayClock + analytics helpers), `850b94a` (T-509 backtest worker connect). Replay determinism + intra-candle + 1-week seeded backtest covered by unit/integration tests (BRIEF §12.2 "Tests required").
- **Verify**: run `docs/runbooks/F5_E1_backtest_smoke.md` **Step 1** → expected: the corrected `python -m scripts.backtest --config-path configs/bots/smoke.yaml …` (**D2/D3/D4**) exits 0, a `run_id` printed, `backtest_runs.finished_at` non-NULL, `backtest_runs.summary` has the 5 aggregates (total trades / WR / P&L / PF / MDD). T-540-verified runnable (close-out RUN `b16d41a` + re-run).
- [ ] **E1 verified** (F5_E1 Step 1 PASS).

### E2 — Two backtests with different configs compared side-by-side

- **Owners**: T-508 (CLI `--compare`) + T-516 (UI variant view).
- **Evidence**: feat `fcdc453` (T-508 `scripts/backtest.py --compare` aggregate + per-trade diff), `47bb7d0` / `8b76db5` / `4b8ff86` (T-516a1/a2/b paper-trade analytics backend + UI drill-down).
- **Verify**: `docs/runbooks/F5_E1_backtest_smoke.md` **Step 2** → expected: two distinct `run_id`s; `--compare` emits an aggregate-metrics diff + a per-trade diff; exits 0. (UI variant drill-down renders in the dashboard.)
- [ ] **E2 verified** (F5_E1 Step 2 PASS).

### E3 — Shadow variants persist across restart (verified by killing execution-service mid-variant)

- **Owners**: T-511 (FSM + paper seed) + T-512a (replay infra) + T-512b (variant kill-test) + T-513b1 (rejected replay infra) + T-513b2 (rejected kill-test). **FULLY SATISFIED 2026-05-08** per the TASKS.md exit-criteria trace.
- **Evidence**: feat `067ad6f` (T-512a §13.4 restart-recovery via OHLC replay, H-023), `83e0c4d` (T-512b mandatory kill-during-variant test), `d8d54de` (T-513b1 rejected-signal restart-recovery), `709ca81` (T-513b2 rejected kill-test). Controlling tests: `services/execution/tests/integration/test_shadow_restart.py::test_shadow_variant_survives_restart_via_replay` + `services/execution/tests/integration/test_rejected_observation_restart.py::test_rejected_signal_shadow_survives_restart_via_replay`.
- **Verify (D6 — CORRECTED):** the 2 controlling tests `test_shadow_restart.py::test_shadow_variant_survives_restart_via_replay` + `test_rejected_observation_restart.py::test_rejected_signal_shadow_survives_restart_via_replay` are **CI-full / testcontainer-gated** — the integration conftest skips them unless `NATS_TEST_URL` + `POSTGRES_TEST_DSN` are set, and running them ad-hoc against the shared dev-stack NATS is NOT a valid harness (request-reply times out — an environment mismatch, not a shadow-restart defect). **Sanctioned E3 verification:** (a) confirm both tests green in **CI-full** on the signed master HEAD, OR (b) run `docs/runbooks/F5_E2_shadow_smoke.md` (deployment-layer smoke — see its L-028 honesty note re the `strategy-engine-smoke` residual). Per the T-522 close-out **decision A**, E3's basis is the CI-grade evidence: these tests shipped green CI-full 2026-05-08 (T-512b/T-513b2) + the T-519 §20 audit + the E4 hazard meta-test (all resolve H-016/H-023). Replay recovery (H-023): pre-kill `shadow_variants` survive (original `created_at`) and are finalized/resumed — NOT `lost_on_restart`.
- [ ] **E3 verified** (2 named tests pass + F5_E2 PASS).

### E4 — All hazards in §20 have an associated test that passes

- **Owner**: T-519 (§20 hazard test audit; F5 E4 owner).
- **Evidence**: `docs/audit/hazard-test-coverage.md` (T-519, feat `3d4fc38`) — verdict **35/36 hazards have a resolvable, pytest-collected, passing test; H-005 explicitly DEFERRED** (operator-acknowledged residual-risk T-F5+ carve-out). Permanent CI guard `tests/test_hazard_catalog_coverage.py` parses live §20 + resolves every `**Test.**` citation via `pytest --collect-only`.
- **Verify**: `uv run pytest tests/test_hazard_catalog_coverage.py` → expected: passes (every §20 `**Test.**` citation resolves; the `_KNOWN_DEFERRED={5}` H-005 whitelist invariant holds — a new deferral OR H-005 losing DEFERRED fails this test).
- [ ] **E4 verified** (meta-test passes; 35/36 + H-005 DEFERRED — see Deliberate residual).

### E5 — Operator signs off on the Live-ready MVP scope (paper-feature-complete section)

- **Owner**: T-522 (this runbook).
- **Evidence**: E1..E4 verified above = paper-feature-complete (backtest harness + comparison + shadow restart + §20 hazard coverage).
- **Action**: operator reviews E1..E4 results and signs **§A** below.
- [ ] **E5** — operator sign-off recorded in §A.

### E6 — All hardening (T-524..T-536) shipped + integration tests green + Live-ready deployment runbook executed

- **Owners**: T-524..T-536 + T-522.
- **Evidence**: T-539 `docs/audit/f5-task-completion.md` (**F5 LEDGER VERIFIED** — the entire hardening cluster shipped+merged+counter-closed, F5 `64/66` correct + gapless). Representative completing feats: risk-mgmt T-524 `3ef5265` / T-525a1 `e93f8cf` / T-525a2 `061f1d3` / T-525b `7a4459b` / T-526 `c1363f4`; sizing T-527 `96fae4f` / T-528 `610c8b4` / T-529 `f90c382`; balance-equity T-530 `93faa9e` / T-531 `78e74cc` / T-532 `2bfc6a3`; FSM T-533 `e87aee7`; SL/TP T-534 `fe631d4`+`195f7a6`+`f513fde` / T-535 `8c29c52` / T-536 `dbae961`. (Full per-leaf trail: the T-539 report.)
- **Verify**: (a) **hardening shipped** — confirm `docs/audit/f5-task-completion.md` verdict line reads "F5 LEDGER VERIFIED"; (b) **integration tests green** — `uv run pytest` exits 0 (or the CI-equivalent integration suite green on master HEAD); (c) **Live-ready deployment runbook executed** — bring the full stack up per `docs/runbooks/dev_stack.md` and confirm `docker compose ps` shows no `unhealthy`.
- [ ] **E6 verified** (ledger VERIFIED + integration green + deployment up healthy).

## Deliberate residual (informed sign-off basis — L-026)

- **E4 residual: H-005 DEFERRED.** Per T-519 + operator-acknowledged carve-out: the `opposite_side_open` scoring rule is code-verified non-existent; §20 H-005 Policy+Test are DEFERRED, tracked in a NEW T-F5+ backlog ticket ("opposite_side_open scoring condition + H-005 test"). E4 is therefore **35/36 + H-005 explicitly DEFERRED** — the Live-ready sign-off is on this **known-residual basis, NOT a false 100%**. The CI guard `tests/test_hazard_catalog_coverage.py` enforces the H-005-DEFERRED whitelist.
- **[Forward annotation — T-542, 2026-05-17]** H-005 was subsequently resolved post-MVP in **F6/T-542** as the `risk.block_opposite_side` consumer pre-scoring gate (ADR-0016); E4 is now **36/36**. This does **NOT** retro-change the F5 §A/§B sign-off below — that sign-off was correctly made on the then-true **35/36 + H-005 DEFERRED** informed-residual basis (the signed Result/timestamp lines are point-in-time and byte-unchanged per ADR-0015:35). F6 is additive post-MVP; the F5 Live-ready attestation stands as signed.
- **[Forward annotation — T-544, 2026-05-17]** the `strategy-engine-smoke` deployment-smoke residual (referenced in BOTH the signed §A Result `:91` and §B Result `:106` below as a basis for E3 staying CI-grade) was subsequently resolved post-MVP in **F6/T-544**: `compose.yaml` now ships a `strategy-engine-smoke` service so the full local F5_E2 deployment smoke is runnable (once its D7/D10/`ohlc_1m` prerequisites are met). This does **NOT** retro-change the signed §A/§B sign-off — both signed Result lines are point-in-time and byte-unchanged per ADR-0015:35; E3's sign-off basis stays the CI-grade evidence per T-522 decision A. F6 is additive post-MVP.
- T-522 **references, does not re-derive**, the T-519 / T-521 / T-539 artifacts — this runbook is the orchestration + sign-off gate; the underlying audits stand on their own commits.
- **F5+ opportunistic backlog** (post-Live-ready, NOT F5 blockers): per TASKS.md "## Backlog → ### F5+ opportunistic" — explicitly out of this F5 sign-off.
- **T-540 corrected this runbook's command/prereq drift** (D1–D11 from the close-out RUN `b16d41a`; plan `docs/plans/T-540.md`). Deliberate NOT-touched boundary (L-026): **D9** (native analytics-api `SERVICE_NAME` mislabel) → separate `fix(...)` (code bug, different class); `configs/bots/alpha.yaml`/`beta.yaml` (real bots; the `oi_change` scoring dependency is the separate F4+ "built-in oi_change feature" ticket — `smoke.yaml` is the runnable fixture instead); closed-phase runbooks `F3_E1_dvoj_bot_smoke.md` / `F2_E1_testnet_smoke.md` (carry the same D8/D11-class patterns but are F2/F3-closed operator docs, out of F5-corrective scope).

## Sign-off

### §A — Paper-feature-complete (E1..E5)

_(Operator fills on execution. Strict ISO-8601 `+00:00` per §N1.)_

```
E1 [x]   E2 [x]   E3 [x]   E4 [x] (35/36 + H-005 DEFERRED)
Run timestamp:        `2026-05-17T05:17:35+00:00`
Operator:             `luster`
Master HEAD at run:   `c024f21d46337279abe22ec30173af4438c69dd4`
Result:               `PASS WITH 3 PARTIALS` — E1 verified (30-day backtest completes + 5-aggregate summary; T-540-re-verified). E2 partial-depth (empty-window: `--compare` two-section machinery exercised, no differential trades — F4_E1 PASS-WITH-PARTIALS precedent). E3 accepted on **CI-grade** basis per the T-522 close-out **decision A** (2 controlling restart tests green CI-full 2026-05-08 [T-512b/T-513b2] + the T-519 §20 audit + the E4 hazard meta-test; the full F5_E2 deployment smoke was NOT executed — blocked by the D1–D11 chain / `strategy-engine-smoke` residual). E4 35/36 + H-005 DEFERRED (operator-acknowledged T-F5+ carve-out). Informed known-residual sign-off per this runbook's "Deliberate residual" basis.

E5 — Live-ready MVP scope, PAPER-FEATURE-COMPLETE section SIGNED OFF:
  operator: luster   @ 2026-05-17T05:17:35+00:00
```

### §B — Live-ready (E6)

_(Operator fills on execution. Strict ISO-8601 `+00:00` per §N1.)_

```
E6 [x]  (hardening T-524..T-536 shipped per docs/audit/f5-task-completion.md + integration tests green + dev_stack deployment executed)
Run timestamp:        `2026-05-17T05:24:14+00:00`
Operator:             `luster`
Master HEAD at run:   `37e1805f967557e4e63154cc3dcb4e497da99e2f`
Result:               `PASS WITH 3 PARTIALS` — hardening T-524..T-536 shipped: **VERIFIED** (T-539 ledger audit `docs/audit/f5-task-completion.md` = "F5 LEDGER VERIFIED"). Integration tests green: **CI-grade** — the hardening cluster shipped green via per-task gates + CI-full; NOT independently re-run on the current (markdown-only) HEAD (same basis as E3). Live-ready deployment runbook executed: **PARTIAL** — the 2026-05-17 close-out RUN built + ran the full containerized stack healthy + a live market-data feed (real deployment evidence); the full F5_E2 kill/restart smoke stayed CI-grade per the T-522 close-out **decision A**; the D1–D11 runbook drifts were corrected by T-540. H-005 DEFERRED carve-out unchanged. Informed Live-ready sign-off on this runbook's "Deliberate residual" basis — NOT a clean PASS.

E6 — Live-ready MVP scope SIGNED OFF:
  operator: luster   @ 2026-05-17T05:24:14+00:00
```

Discoveries during run (any master-fix commits in the same session — list `fix(T-NNN)` + hash, mirror the F4_E1 precedent). Tech-debt / follow-up candidates (NOT F5 blockers): list or "none".

## On sign-off complete

When §A + §B are both signed `PASS`, F5 phase closes. The T-522 `chore(tasks)` then bumps the F5 counter **`64/66 → 66/66`** — E5 + E6 are the 2 documented sign-off slots (authority: `docs/audit/f5-task-completion.md`, the T-539 reconciliation that established `66−64=2 = T-522 two sign-off sections`) — and marks **F5 COMPLETE** in `docs/status.md`. This is the one task where the TASKS.md counter line is legitimately edited; the citation distinguishes the authorized close-bump from counter drift.
