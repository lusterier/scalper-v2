# Session status

## 2026-05-05 (session-end)

**F4 phase COMPLETE: 24/24 numbered tasks + T-423 close-out runbook shipped (100% F4 scope delivered).** Master HEAD `<T-423-commit>` (this commit), branch up-to-date with origin. Repo-wide pytest **1789 passed** (unchanged; F4 frontend cohort didn't add backend tests — backend was complete at T-409). Vitest **160 passed** (4 → 160 = +156 across T-410 scaffold + T-411 component lib + T-412..T-420 9 dashboard sections + T-422 api-client). Dashboard-query parametrizations **23** (CI-full gated; 0 → 2 → 23 from T-421). Playwright scenarios **3** (master-push gated; 0 → 3 from T-422). 47 pytest skipped (env-gated integration; unchanged), no regressions.

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
