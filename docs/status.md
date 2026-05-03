# Session status

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
