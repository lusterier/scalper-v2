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

- [ ] Project-root `.env` populated with `DATABASE_URL` + `POSTGRES_PASSWORD` + the signal-gateway `HMAC_SECRET`.
- [ ] Alembic migrations at head: `uv run alembic upgrade head`.
- [ ] A bot config in `configs/bots/` with a `shadow:` block per BRIEF §13.2 (`enabled: true` + ≥1 `variants:` entry + `max_duration_hours` large enough — e.g. 4 — that variants stay pending across the kill/restart window), and its `bots` row present.
- [ ] `docker compose -f compose.yaml -f compose.dev.yaml up -d postgres nats nats-init signal-gateway market-data-svc feature-engine execution-service strategy-engine-<bot>` → all healthy (`docker compose ps` shows none `unhealthy`).
- [ ] `ohlc_1m` seeded/flowing for the bot's symbol(s) so §13.4 replay has candles from `created_at`→now.

## Step 1 — Open a trade → shadow variants spawn (pending)

POST an ACCEPT-able signal via the signal-gateway webhook (HMAC, F3_E1 precedent):

```
SIGNATURE=$(printf '%s' "$SIGNAL_PAYLOAD" | openssl dgst -sha256 -hmac "$HMAC_SECRET" | awk '{print $2}')
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
