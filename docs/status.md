# Session status

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
