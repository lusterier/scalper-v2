# Session status

## 2026-05-02 (session-end)

**F3 PHASE CLOSED.** Marathon session — 16/16 F3 tasks shipped + 2 F2 build regressions caught & fixed during T-313 smoke.

### F3 deliverables shipped this session

- T-309 strategy-engine skeleton (composition root + lifespan + /health/ready/metrics)
- T-310a BotConfig sections (Exchange/Signals/ExecutionSection) + SignalRejected schema + select_signal_id_by_idempotency_key helper
- T-310b per-bot signal consumer body (closes §9.4 main loop steps 3a-3h)
- T-308b ScoringRule field-level Pydantic validation hardening (deferred CONCERN from T-308)
- T-311 per-bot strategy-engine compose service variants (alpha + beta) per §18.1 verbatim
- T-312 oi_squeeze reference plugin per §10.6 verbatim
- T-313 F3 exit-criteria integration bundle (E1+E2+E3+E4) + alpha.yaml/beta.yaml fixtures + F3_E1 dvoj-bot smoke runbook

### Smoke run sign-off (T-313 E1)

Live `docker compose up` stack tested 2026-05-02T20:15:30+00:00:
- HTTP 200 webhook, signal_id=3, correlation_id=`f3-e1-smoke-2`
- alpha decision=`reject` (active mode, score=0.0 < threshold 1.0; H-019 fail-open WARN observed correctly because no live OI feature pipeline)
- beta decision=`passthrough` (passthrough mode unconditional)
- 2 audit rows in `scoring_evaluations` with full per-rule JSONB
- oi_squeeze plugin loaded (rules_count=1 in beta service_started log) + ran through pipeline (RuleResult present in audit, applied_weight=0.0 due to T-306 limitation per §0.8)

§19:2547 + §19:2548 + §19:2549 + §19:2550 verbatim **SATISFIED**.

### F2 build regressions caught + fixed during smoke

Lokálne `pytest` running pomocou workspace-wide sync (`uv sync --all-packages`) **maskoval** dva production-image build bugs:

- `d1d3d45` `fix(execution)`: `services/execution/pyproject.toml` chýbala `scalper-v2-exchange` workspace dep → execution-service container failed `ModuleNotFoundError: No module named 'httpx'`
- `a1112c1` `fix(packages/exchange)`: `packages/exchange/pyproject.toml` chýbal `[build-system]` block → setuptools auto-discovery failed pri Docker `uv sync --package`

Obidva fix-y aplikované pre-smoke; production Docker build path teraz funkčný pre všetky services.

### Master state

- Master HEAD `548c0cc` (`chore(F3-close)`)
- Branch up-to-date with origin
- 1440 tests passing locally (no regressions)
- F3 deliverables complete; F4 unlock pending operator decision

### Operator-driven actions taken at session end

- `signabot.service` (paralelný v1 paper bot na port 8000) — `sudo systemctl disable` (permanentne, "nebudeme zapinat")
- `timescaledb` v1 Docker kontajner (port 5432) — stopped + nepôjde sa reštartovať
- scalper-v2 dev compose stack — `docker compose down` po smoke
- Memory updates: `sibling_bot_v1.md` + `deployment.md` reflektuje "v1 disabled" stav

## Next session pick-up

**Operator decision needed: F4 unlock alebo backlog cleanup?**

### Option A — F4 phase unlock (analytics-api + dashboard UI)

Per BRIEF §19:2552-2563. Veľký scope (~2-3 týždne):
- `analytics-api` service skeleton + endpoint categories
- React UI scaffold (TanStack Router/Query, Zustand, Tailwind, shadcn/ui)
- 9 dashboard sections
- SSE streaming
- Component library
- Playwright E2E
- Grafana ops dashboards

Začať `chore(tasks): unlock F4 phase + add task plan` keď operátor schváli.

### Option B — Hazard-bound deferrals from F3 (smaller)

- **T-F3+ live-mode safeguard runtime check** — lifespan fail-fast when `bot_config.exchange.mode == "live"` AND `BOT_CONFIRM_LIVE != "yes"` + `LIVE MODE ENGAGED` warning + Telegram alert. Compose-level env passthrough already shipped in T-311; runtime check ~15 LOC + tests deferred per §0.8. Brief §16.5:2245.
- **F4+ T-306 feature_history population** — T-303 series conditions + T-312 oi_squeeze plugin will start contributing to score once resolver populates history. Currently both return data_missing.
- **F4+ built-in `oi_change` feature** in `packages/features/builtins/` — brief §9.3:1488 declares it as built-in alongside EMA/SMA/RSI/etc.
- **F4+ risk-based position sizing** — replace per-bot fixed `execution.qty: Decimal` with `sizing.tiers` block from §B.1:3006-3025.

### Option C — F2 follow-ups (operator-deferred during F2/F3)

- **T-F2+ Bybit V5 instruments-info step-size cache** (TASKS.md backlog) — T-216a left raw qty pass-through with TODO marker
- **T-F2+ Hazard catalog orchestrator test** — F2 E5 verification per §19:2531; ~80 LOC
- **F2 E1 testnet smoke** — manual operator runbook `docs/runbooks/F2_E1_testnet_smoke.md`; needs Bybit testnet credentials

## Watch-outs for next session

- **`signabot.service` permanently disabled** — port 8000 + 5432 voľné pre v2 dev stack; v1 sibling bot už nehedguje (operátorské rozhodnutie 2026-05-02)
- **Live OI feature pipeline absent** — F3 paper-mode smoke ukázal H-019 fail-open na oboch bot-och kvôli `data_missing` resolver outcomes. CI tests mock resolver pre Decimal("100") OI value → alpha by emitoval `execute` so score 2.0; live setup emituje `reject`. Toto NIE je regresia, ale operator awareness gap.
- **Dev stack environment setup gotchas** zaznamenané v `docs/runbooks/F3_E1_dvoj_bot_smoke.md` (HMAC ≥32 chars, alembic `POSTGRES_URL` not `DATABASE_URL`, SymbolMapCache 60s TTL, bots+symbol_map seed required).
- **Memory sync** — 2 files updated reflecting v1 disable + F3 close: `sibling_bot_v1.md`, `deployment.md`.

## Useful refs

- F3 plans: `docs/plans/T-309.md` ... `T-313.md` (cross-link from each chore(tasks) Done entry)
- F3 runbook: `docs/runbooks/F3_E1_dvoj_bot_smoke.md` (sign-off + setup gotchas)
- F4 spec: BRIEF §19:2552-2563 + §9.6 analytics-api
- Hazard-bound deferrals: T-311 plan §"§16.5 Live-mode safeguard"; T-312/T-313 plans § "Out of scope" T-306 deferral
- F2 build path lessons: commits `d1d3d45` + `a1112c1` describe the masking pattern (workspace-sync vs --package --no-dev)
