# F4 E1 dashboard smoke runbook

**Phase:** F4 exit-criteria E1 (BRIEF §19:2569-2570)
**Mode:** dev (operator-host; production deploy F5+ per BRIEF §16.2)
**Owner:** operator (manual; T-423 ships this runbook as required deliverable per TASKS.md F4 close-out)

§19:2569-2570 verbatim — 5 exit criteria:
1. Operator can navigate all 9 sections, see live data, drill into a trade end-to-end.
2. Scoring inspector shows per-rule breakdown for any signal.
3. Feature inspector charts historical feature values.
4. Backtest lab can trigger a small backtest and show results.
5. Playwright smoke journeys pass in CI.

## Purpose

End-to-end UX smoke verifying all 9 dashboard sections + Playwright CI green on master HEAD. The 1789 pytest + 160 vitest + 23 dashboard-query parametrizations cover unit-level invariants; this runbook covers operator-runnable UX flow per BRIEF §14 dashboard convention.

## Prerequisites

- [ ] Project root `.env` populated with `DATABASE_URL` + `POSTGRES_PASSWORD`.
- [ ] `docker compose -f compose.yaml -f compose.dev.yaml up -d postgres nats nats-init` brings infra healthy.
- [ ] Alembic migrations applied (manual or via service-side init): `uv run alembic upgrade head`.
- [ ] At least 1 bot config in `configs/bots/` (alpha.yaml or similar) — needed for BotSelector + Strategy editor + Settings.
- [ ] Optional fixture data in DB (smoke renders empty placeholders if absent — runbook still tickable for navigation criteria).

## Operator host startup (2 terminals)

**Terminal A — analytics-api backend:**
```bash
uv run uvicorn services.analytics_api.app.main:create_app --factory --host 127.0.0.1 --port 8000
```

**Terminal B — UI Vite dev server:**
```bash
cd ui
pnpm dev
```

Vite serves on `http://127.0.0.1:5173`. Vite proxies `/api/*` + `/events/*` → `127.0.0.1:8000` per `ui/vite.config.ts`. Without backend running, every fetch fails ECONNREFUSED.

Operator browser: open `http://localhost:5173`.

---

## Step 1 — Open app + Overview renders 5 tiles

Navigate browser to `http://localhost:5173/`.

- [ ] 5 tile titles visible: **Open positions** / **Virtual balance** / **24h P&L** / **Signals (24h)** / **Alerts (24h)**
- [ ] Top bar visible: BotSelector multi-select + TimeRangePicker (1h / 24h / 7d / 30d / Custom) + ConnectionDot (gray "unknown" — Overview doesn't subscribe SSE)
- [ ] Left nav 9 links visible: Overview / Per-bot live view (disabled if no bot selected) / Trade explorer / Backtest lab / Strategy editor (disabled if no bot selected) / Feature inspector / Scoring inspector / Audit log / Settings

## Step 2 — Per-bot live view (bot selector → drill-in)

Pick a bot from BotSelector (top bar). Operator can also navigate directly to `/bot/<botId>` for deep-link.

- [ ] Route URL changes to `/bot/<botId>` (e.g., `/bot/alpha`)
- [ ] Top bar: BotSelector single-mode renders selected bot; TimeRangePicker visible; ConnectionDot transitions yellow "connecting" → green "connected" within ~2s (real SSE via T-413 useSSEStream)
- [ ] 3 panels render: **Open positions** (DataTable; "No open positions" if empty fixture) + **Live signals** (last-50 feed; "No signals yet" placeholder if empty) + **Cumulative P&L (24h)** (Recharts LineChart; "No P&L data" if empty)

## Step 3 — Trade explorer + drill-down (BRIEF §19:2569 "drill into a trade end-to-end")

Click "Trade explorer" in left nav. URL: `/trades`.

- [ ] DataTable renders with 7 columns (#, Bot, Symbol, Side, Status, Entry, Exit, Realized P&L, Close reason); empty-message "No trades match filters" if fixture absent
- [ ] 4 filters work: BotSelector + symbol Input + status dropdown (All/open/closed/error) + TimeRangePicker (tooltip "Filters by close time")
- [ ] status="open" OMITS `?from=`/`?to=` URL params (per T-414 BLOCKER #2 fix; verify via browser devtools Network tab)
- [ ] Custom Previous/Next pagination block visible below DataTable (NOT inline in DataTable)

If at least 1 trade exists, click row:

- [ ] Route URL changes to `/trades/$tradeId`
- [ ] Drill-down renders 8 sections: Trade summary header (close info folded in: close_reason / closed_at / exit_price / realized_pnl) + Signal details (T-403) + Scoring breakdown (T-403) + 5 placeholders (Order events / Fills / SL moves / Shadow variants / Post-close price snapshots — all "Coming F4+/F5+")
- [ ] Back-to-Trades link returns to `/trades`

## Step 4 — Backtest lab — trigger small backtest + see results (BRIEF §19:2569 "backtest lab can trigger a small backtest and show results")

Click "Backtest lab" in left nav. URL: `/backtests`.

- [ ] DataTable renders with 7 columns; "No backtests match filters" empty state
- [ ] "+ New backtest run" Card collapsible above DataTable
- [ ] Click "+ New backtest run" → form expands with 6 fields (name + Bot + datetime-local start/end + config_yaml textarea + notes)

Fill form (paste minimal YAML matching `configs/bots/alpha.yaml`):

- [ ] Date range start: `2026-01-01T00:00`; end: `2026-02-01T00:00`
- [ ] Submit → POST `/api/backtests/` returns 202 Accepted
- [ ] Modal closes; new row in DataTable with `status=queued` (StatusBadge yellow)
- [ ] Click row → drill-down at `/backtests/<runId>` renders 4 Cards: header (name + StatusBadge) + Run metadata + Config YAML collapsible + Summary "F5+ worker pending" placeholder

## Step 5 — Strategy editor — load + edit + apply (creates new bot_configs version)

Pick same bot from earlier; navigate `/strategy/<botId>`.

- [ ] Editor textarea pre-populated with current bot config_yaml
- [ ] Side-by-side diff Cards: "Current" + "New (editing)" both render YAML
- [ ] Validation panel renders: green "valid (parsed v<N>)" within 500ms after typing pause (T-416 useDebouncedValidation hook hits POST `/api/configs/validate`)
- [ ] Edit one harmless field (e.g., add comment line); validation stays green
- [ ] Apply button → modal opens with applied_by Input + notes textarea
- [ ] Type `applied_by="<your-name>"`; click Confirm apply
- [ ] POST `/api/configs/<bot_id>/apply` returns 201 Created; modal closes
- [ ] Versions DataTable refreshes; new row at top with version=N+1, applied_by=`<your-name>`, applied_at=now

## Step 6 — Feature inspector — chart historical values (BRIEF §19:2569 "feature inspector charts historical feature values")

Click "Feature inspector" in left nav. URL: `/features`.

- [ ] DataTable renders with 5 columns: Feature / Symbol / Value / Computed at / Status (StalenessDot)
- [ ] Type prefix in Input (e.g., `ind.btc`) → DataTable filters via `?prefix=ind.btc` URL param
- [ ] StalenessDot renders green for fresh (<5 min) or red for stale rows
- [ ] Click a numeric `value_num` row (e.g., RSI / EMA / MACD)
- [ ] Selected feature panel renders below DataTable with FeatureChart Recharts LineChart (24h history; X-axis = computed_at; Y-axis = value_num)
- [ ] Click a non-numeric `value_bool` / `value_json` row → "Not chartable" placeholder + history table renders instead

## Step 7 — Scoring inspector — select signal → per-rule breakdown (BRIEF §19:2569 "scoring inspector shows per-rule breakdown for any signal")

Click "Scoring inspector" in left nav. URL: `/scoring`.

- [ ] DataTable renders signals with 5 columns: # / Received at / Symbol / Action / Status (ingestion_status StatusBadge)
- [ ] 5 filters work: source + symbol Input + action dropdown (All/LONG/SHORT/CLOSE/CUSTOM) + ingestion_status dropdown (All/validated/duplicate/invalid) + TimeRangePicker
- [ ] Click a signal row → URL `/scoring/<signalId>`
- [ ] Drill-down renders 4 Card sections: header (Signal #N + StatusBadge) + Signal summary (8 fields) + Scoring breakdown (rule-by-rule list; each row: rule name + weight + applied_weight + result badge tone) + Feature snapshots (per-evaluation FeatureSnapshotTable; alphabetical key-value rows)
- [ ] At least 1 rule_results row visible with rule_name + weight + applied_weight + result (tone-mapped: True=green / False=muted / data_missing=red)

## Step 8 — Audit log viewer — chronological events + filter

Click "Audit log" in left nav. URL: `/audit`.

- [ ] DataTable renders audit_events chronologically (occurred_at DESC) with 6 columns: Occurred at / Actor / Action / Entity type / Entity ID / Correlation
- [ ] 4 filters work: actor Input + action_prefix Input + entity_type Input + TimeRangePicker
- [ ] Time-range filter via `.toISOString()` Z-suffix per §N1 (verify URL in devtools)
- [ ] Click a row → expand panel below DataTable with before_state + after_state JSON pretty-print + meta (if non-empty)
- [ ] Click CorrelationIdChip in any row → URL `/audit?correlation_id=<id>`; client-side filter notice renders + only rows with matching correlation_id visible
- [ ] "Clear" link in notice resets URL to `/audit`

## Step 9 — Settings — bot registry + symbol map CRUD

Click "Settings" in left nav. URL: `/settings`.

- [ ] 4 sections render in order: Bot registry / Symbol map / Plugin registry placeholder / API key status placeholder
- [ ] Bot registry DataTable: 6 columns (Bot ID / Display name / Status / Exchange mode / Config hash / Applied at); read-only (no admin actions per §0.8)
- [ ] Symbol map DataTable: 6 columns + Edit/Delete buttons inline
- [ ] Click "+ Add entry" → modal opens with 4 form fields (input_symbol + canonical_symbol + exchange_source dropdown + notes textarea)
- [ ] Fill + Submit → POST returns 201; modal closes; new row appears
- [ ] Click Edit on a row → modal pre-populated; input_symbol field disabled (URL path is PK per WG#3); change canonical_symbol; Submit → PUT 200; row refreshes
- [ ] Click Delete on a row → `window.confirm("Delete symbol_map entry X?")`; confirm → DELETE 204; row disappears
- [ ] Click Delete + Cancel → no DELETE fires (per §N3 + window.confirm guard)
- [ ] Plugin registry placeholder renders "Coming F4+ — no /api/plugins/ endpoint yet"
- [ ] API key status placeholder renders "H-022 — env-only" + NEVER displays key VALUES

## Step 10 — Playwright CI smoke green (BRIEF §19:2570 "Playwright smoke journeys pass in CI")

After T-423 commit lands on master, verify the latest `e2e.yml` workflow run on master HEAD is green (per WG#1 — operator ticks Step 10 as last action post-commit, NOT pre-commit; T-423 cannot block on its own future CI run).

- [ ] Visit `https://github.com/lusterier/scalper-v2/actions/workflows/e2e.yml`
- [ ] Latest run on `master` branch shows green checkmark
- [ ] Click latest run → 3 chromium scenarios passed: `overview.spec.ts` + `per-bot.spec.ts` + `trade-drill-down.spec.ts`

If failure: download `playwright-report/` artifact (7-day retention per `.github/workflows/e2e.yml` config); inspect `playwright-report/index.html` for trace + screenshot of failing assertion.

---

## Exit checklist (BRIEF §19:2569-2570 verbatim)

- [ ] **Criterion 1**: Operator can navigate all 9 sections (Steps 1-9 ticked)
- [ ] **Criterion 2**: Drill into trade end-to-end (Step 3 drill-down ticked)
- [ ] **Criterion 3**: Scoring inspector per-rule breakdown rendered (Step 7 drill-down ticked)
- [ ] **Criterion 4**: Feature inspector chart populated for at least 1 numeric feature (Step 6 chart ticked)
- [ ] **Criterion 5**: Backtest lab POST returns 202 + new versioned row in list (Step 4 ticked)
- [ ] **Criterion 6**: Playwright CI green on master HEAD (Step 10 ticked post-commit)

## Sign-off

Run timestamp: `<YYYY-MM-DDThh:mm:ss+00:00>` (full ISO-8601 with explicit `+00:00` offset per §N1; NOT `Z` suffix).

Operator: `<name>` (or `lan-operator` placeholder pre-auth).

Master HEAD at run: `<git rev-parse HEAD>`.

Result: `PASS` / `FAIL` (if fail, list failing criteria number + brief observation).

If all 6 exit criteria PASS → F4 phase exit-criteria E1 satisfied; F5 phase unlock pending operator decision per §0.10.
