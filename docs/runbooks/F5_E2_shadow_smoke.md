# F5 E2 shadow-restart smoke runbook

**Phase:** F5 exit-criteria — shadow variants restart recovery (BRIEF §19, the F5 "Exit criteria" bullet verbatim below)
**Mode:** dev (operator-host live compose stack; production deploy per BRIEF §16.2 / §18)
**Owner:** operator (manual; T-521 ships this runbook as a required F5 close-out deliverable; executed + signed off as part of T-522 E5/E6)

Verbatim BRIEF §19 F5 exit-criteria bullet this runbook verifies:

> - Shadow variants persist across restart (verified by killing execution-service mid-variant).

## Purpose

End-to-end operator smoke of the §13.4 shadow-variant **restart recovery
via OHLC replay** (H-023 "no more lost_on_restart"): open a trade on a bot
with `shadow.enabled: true`, kill `execution-service` while variants are
pending (mid-variant, before any terminal outcome), restart it, and confirm
the pending `shadow_variants` rows survive and are finalized/resumed via
`ohlc_1m` replay from `created_at` — NOT lost. The pytest integration test
`test_shadow_restart.py::test_shadow_variant_survives_restart_via_replay`
(T-512b) covers this in CI; this runbook is the operator-facing
deployment-layer confirmation, NOT a re-test.

## Prerequisites

- [ ] Project-root `.env` populated; source it: `set -a; . ./.env; set +a`. **D8:** the signal-gateway secret env var is `SIGNAL_GATEWAY_HMAC_SECRET` (NOT the un-prefixed name the prior runbook used); containerized signal-gateway compose default is `dev-hmac-secret-32-chars-padding!!`. **D4:** host-run connects `127.0.0.1`, not the `.env` docker-internal host.
- [ ] Alembic migrations at head: `POSTGRES_URL="postgresql://scalper:$POSTGRES_PASSWORD@127.0.0.1:5432/scalper" uv run alembic -c migrations/alembic.ini upgrade head` (**D1** — `alembic.ini` in `migrations/`; `migrations/env.py` reads `POSTGRES_URL`, no `.env` auto-load).
- [ ] Use the shipped `configs/bots/smoke.yaml` (**D7** — ships a `shadow:` block: `enabled: true` + 2 variants + `max_duration_hours: 4`; `scoring.mode: passthrough` so any signal accepts without the F4+-unimplemented `oi_change`). Seed its `bots` row: `docker exec scalper-v2-postgres-1 psql -U scalper -d scalper -c "INSERT INTO bots (bot_id,display_name,status,exchange_mode) VALUES ('smoke','Smoke fixture','active','paper') ON CONFLICT (bot_id) DO NOTHING;"`.
- [ ] **D10:** the execution-service paper adapter requires, for EVERY `bots` row, env `BOT_<ID>_PAPER_SEED_BALANCE` / `_SLIPPAGE_MODEL` (`fixed_pct`|`proportional_to_qty`|`half_spread`) / `_FEE_RATE` / `_SLIPPAGE_PARAMS_JSON` — e.g. `BOT_SMOKE_PAPER_SEED_BALANCE=10000`, `BOT_SMOKE_PAPER_SLIPPAGE_MODEL=fixed_pct`, `BOT_SMOKE_PAPER_FEE_RATE=0.00055`, `BOT_SMOKE_PAPER_SLIPPAGE_PARAMS_JSON='{"fixed_slippage_pct":"0"}'` (+ the same for alpha/beta or execution-service crash-loops on startup).
- [ ] **Honesty note (L-028 / T-522 close-out decision A):** the individual D-fixes in this runbook are close-out-RUN-verified-correct (commit `b16d41a` / `docs/status.md`), but the full F5_E2 live signal→kill→restart sequence was **NOT re-run end-to-end under T-540** — compose ships `strategy-engine-alpha`/`-beta` only; a `smoke` bot also needs a `strategy-engine-smoke` service (documented residual). **E3's sanctioned verification is CI-grade** (the 2 controlling restart tests green in CI-full + the T-519 §20 audit + the E4 hazard meta-test); this deployment smoke is the optional deeper confirmation, NOT the E3 sign-off gate.
- [ ] `docker compose -f compose.yaml -f compose.dev.yaml up -d postgres nats nats-init signal-gateway market-data-svc feature-engine execution-service strategy-engine-<bot>` → all healthy (`docker compose ps` shows none `unhealthy`).
- [ ] `ohlc_1m` seeded/flowing for the bot's symbol(s) so §13.4 replay has candles from `created_at`→now.

## Step 1 — Open a trade → shadow variants spawn (pending)

POST an ACCEPT-able signal via the signal-gateway webhook (HMAC, F3_E1 precedent):

```
# D11: symbol_map ships `BTCUSDT.P → BTCUSDT` — the webhook payload uses the
# TradingView-side alias `BTCUSDT.P` (the canonical `BTCUSDT` is unmapped → reject).
SIGNAL_PAYLOAD='{"source":"tv_test","idempotency_key":"f5-e2-smoke-1","symbol":"BTCUSDT.P","action":"LONG","payload":{}}'
SIGNATURE=$(printf '%s' "$SIGNAL_PAYLOAD" | openssl dgst -sha256 -hmac "$SIGNAL_GATEWAY_HMAC_SECRET" | awk '{print $2}')  # D8: env var is SIGNAL_GATEWAY_HMAC_SECRET
curl -X POST http://127.0.0.1:8000/webhook \
  -H "Content-Type: application/json" -H "X-Signature: sha256=${SIGNATURE}" \
  -d "$SIGNAL_PAYLOAD"
```

- [ ] Signal accepted (HTTP 200) → strategy-engine scores execute → execution-service opens a trade (`trades` row OPEN).
- [ ] execution-service published `shadow.start.<bot_id>` and the shadow-worker spawned per-variant sims (§13.3).
- [ ] `shadow_variants` rows exist for this trade with `created_at` set and the terminal outcome column still UNSET (pending — mid-variant; one row per configured variant): `SELECT id, created_at, outcome FROM shadow_variants WHERE bot_id = '<bot>' ORDER BY created_at DESC;`

## Step 2 — Kill execution-service mid-variant

While the variants are pending (BEFORE any terminal `sl_hit`/`be_hit`/`tp_trail`/`tp_full`/`timeout` fired — well within `max_duration_hours`):

```
docker compose -f compose.yaml -f compose.dev.yaml stop execution-service
```

- [ ] `docker compose ps` shows `execution-service` stopped/exited.
- [ ] The `shadow_variants` rows from Step 1 are STILL present in the DB (the kill did not delete them) with `created_at` unchanged, outcome still pending.

## Step 3 — Restart → §13.4 OHLC-replay recovery

```
docker compose -f compose.yaml -f compose.dev.yaml up -d execution-service
```

- [ ] `execution-service` returns healthy.
- [ ] §13.4 recovery: for each pending variant the worker queried `ohlc_1m` from `created_at`→now and replayed via the same `_step` as live. Re-query: `SELECT id, created_at, outcome FROM shadow_variants WHERE bot_id = '<bot>' ORDER BY created_at DESC;`
- [ ] The rows are NOT lost (H-023): each pending variant is either **finalized** (terminal `outcome` ∈ {sl_hit, be_hit, tp_trail, tp_full, timeout} — fired during replay) OR **resumed** (still pending but tracked, `created_at` is the original pre-kill timestamp — NOT a new row, NOT `lost_on_restart`).
- [ ] No `lost_on_restart` / dropped-variant state anywhere for this trade.

## Exit checklist (BRIEF §19 F5 exit-criteria — verbatim)

- [ ] **Shadow variants persist across restart (verified by killing execution-service mid-variant).** (Steps 1-3: variants spawned pending → execution-service killed mid-variant → restarted → the same `shadow_variants` rows [original `created_at`] survive and are finalized/resumed via OHLC replay, NOT lost — H-023.)

_(Note: the F5 §19 bullets are unlabelled; this runbook is the 2nd F5 close-out runbook by sequential naming [`F5_E2_…`] per the T-521 task-def — the filename's "E2" is not a §19-bullet index. The shadow-restart bullet above is the criterion this runbook verifies, quoted verbatim.)_

## Sign-off

_(Filled by the operator when this runbook is executed as part of T-522 E5/E6.)_

```
Run timestamp: `YYYY-MM-DDTHH:MM:SS+00:00`   (§N1 — explicit UTC offset)
Operator: `<name>`
Master HEAD at run: `<git-hash>`
Result: `PASS` / `PASS WITH N PARTIALS` / `FAIL` — <one-line summary>
```

Discoveries during run (any master-fix commits in the same session — list with `fix(T-NNN)` + hash, mirror the F4_E1 precedent). Tech-debt / follow-up candidates (NOT F5 blockers): list or "none".
