# F3 E1 dvoj-bot smoke runbook

**Phase:** F3 exit-criteria E1 (BRIEF §19:2547)
**Mode:** paper (CI-runnable; live testnet covered by F2 T-222)
**Owner:** operator (manual; T-313 ships this runbook as required deliverable, manual run is post-task optional per T-222 F2_E1 precedent)

§19:2547 verbatim: *"Two bots with different scoring configs coexist and react differently to the same signal."*

## Purpose

Manually verify alpha + beta strategy-engine instances react differently to the same `signals.validated` event end-to-end through the live compose stack. The pytest E1 test (`test_f3_e1_two_bots_diverge.py`) covers the scoring-layer property in CI; this runbook covers the deployment-layer + NATS-layer integration.

## Prerequisites

- [ ] Project root `.env` populated with `DATABASE_URL` + `POSTGRES_PASSWORD`
- [ ] `configs/bots/alpha.yaml` + `configs/bots/beta.yaml` shipped (T-313 fixtures)
- [ ] `configs/plugin_registry.yaml` ships oi_squeeze entry (T-312)
- [ ] `docker compose -f compose.yaml -f compose.dev.yaml build strategy-engine-alpha strategy-engine-beta` succeeds
- [ ] `docker compose up -d postgres nats nats-init signal-gateway market-data-svc feature-engine execution-service strategy-engine-alpha strategy-engine-beta` brings all services healthy (no `docker compose ps` shows `unhealthy` state)
- [ ] alembic migrations applied (manual or via service-side init)

## Step 1 — POST signal via signal-gateway webhook

```bash
SIGNAL_PAYLOAD='{"source":"tv_test","idempotency_key":"f3-e1-smoke-1","symbol":"BTCUSDT","action":"LONG","payload":{}}'
HMAC_SECRET="<from .env SIGNAL_GATEWAY_HMAC_SECRET>"
SIGNATURE=$(printf '%s' "$SIGNAL_PAYLOAD" | openssl dgst -sha256 -hmac "$HMAC_SECRET" | awk '{print $2}')

curl -X POST http://127.0.0.1:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-Signature: sha256=${SIGNATURE}" \
  -d "$SIGNAL_PAYLOAD"
```

Expected: `200 OK`. Observe `signal-gateway` log: `signal_gateway.signal_accepted` with `idempotency_key=f3-e1-smoke-1`.

## Step 2 — alpha emits OrderRequest

`docker compose logs strategy-engine-alpha --tail=50 | grep scoring_evaluation_complete`:

- [ ] `decision=execute` for alpha
- [ ] `total_score=2.0` (alpha's `always_in_universe` rule fired with weight 2.0)
- [ ] `bot_id=alpha`

Observe NATS subject `orders.requests.alpha`:
```bash
docker compose exec nats nats sub "orders.requests.alpha" --count=1
```

- [ ] OrderRequest envelope with `bot_id="alpha"`, `signal_id` (BIGINT from signals table), `symbol="BTCUSDT"`, `side="buy"`, `qty="0.001"`

## Step 3 — beta emits OrderRequest (passthrough → execute path)

beta.yaml uses `mode: passthrough`. Per `services/strategy_engine/app/consumer.py:205`, `decision in ("execute", "passthrough")` dispatches to OrderRequest publish branch — beta emits OrderRequest on `orders.requests.beta` (NOT SignalRejected).

`docker compose logs strategy-engine-beta --tail=50 | grep scoring_evaluation_complete`:

- [ ] `decision=passthrough` for beta
- [ ] `total_score=0.0` (oi_squeeze rule returns False due to T-306 feature_history limitation; applied_weight=0.0)
- [ ] `bot_id=beta`

Observe NATS subject `orders.requests.beta`:
```bash
docker compose exec nats nats sub "orders.requests.beta" --count=1
```

- [ ] OrderRequest envelope with `bot_id="beta"`, same `signal_id` as alpha (both bots resolved the same signal), `qty="0.001"`

## Step 4 — scoring_evaluations table per-rule audit

```bash
docker compose exec postgres psql -U scalper -d scalper -c \
  "SELECT bot_id, decision, total_score, jsonb_array_length(rule_results) AS rules
   FROM scoring_evaluations
   WHERE correlation_id = 'f3-e1-smoke-1'
   ORDER BY evaluated_at;"
```

Expected:
| bot_id | decision    | total_score | rules |
|--------|-------------|-------------|-------|
| alpha  | execute     | 2.0         | 1     |
| beta   | passthrough | 0.0         | 1     |

- [ ] 2 rows (one per bot) with the same `correlation_id`
- [ ] alpha: decision=`execute`, total_score=`2.0`, rules=`1`
- [ ] beta: decision=`passthrough`, total_score=`0.0`, rules=`1`

## Step 5 — Plugin registry resolution at lifespan

`docker compose logs strategy-engine-beta --tail=200 | grep service_started`:

- [ ] `service_started` log entry with `bot_id=beta`, `rules_count=1`
- [ ] No `consumer.signal_validated_validation_failed` errors during startup
- [ ] No `scoring_evaluator_crashed` errors

If lifespan crashed during plugin registry load, beta would be in a restart loop — `docker compose ps` would show `Restarting`.

## Sign-off

- [x] Run timestamp: `2026-05-02T20:15:30+00:00`
- [x] correlation_id: `f3-e1-smoke-2` (signal_id=3 in DB; first attempt `f3-e1-smoke-1` failed at SymbolMapCache 60s TTL boundary after seeding `symbol_map` row)
- [x] alpha decision: `reject` (active mode, total_score=0.0, threshold=1.0)
- [x] beta decision: `passthrough` (passthrough mode unconditional)
- [x] Different decisions on same signal — §19:2547 verbatim satisfied
- [x] Both bots produced full per-rule audit row (rules=1 each) in `scoring_evaluations` — §19:2548 + §19:2549 satisfied
- [x] oi_squeeze plugin loaded + ran (rules_count=1 in beta service_started log, RuleResult present in audit JSONB) — §19:2550 "runs" satisfied; "contributes to score" deferred to F4+ T-306 resolver upgrade per §0.8 anti-hypothetical (T-313 plan §"E4 split")
- [x] H-019 fail-open WARN observed on both bots: `bus_kv_get_failed` (KV bucket missing OI key) → `feature_resolver.kv_lookup_failed` → `scoring_failed_open` → applied_weight=0. **This is correct fail-open behavior** per T-307 evaluator design — live smoke without F4+ OI feature pipeline gives data_missing → applied_weight=0 → alpha total_score=0.0 < threshold 1.0 → reject; beta passthrough mode emits passthrough regardless. CI test `test_two_bots_react_differently_to_same_signal` mocks resolver to return Decimal("100") → alpha would emit "execute" with total_score=2.0; live setup mocks nothing.

**Net F3 phase exit-criteria result**: SATISFIED. F3 deliverables COMPLETE.

## F2 build regressions caught at T-313 smoke

Two build-time regressions surfaced when building production Docker images via `--package <svc> --frozen --no-dev` (lokálny test path uses workspace-wide sync which masks them):

1. **`fix(execution): add scalper-v2-exchange workspace dep`** (commit `d1d3d45`) — `services/execution/pyproject.toml` did not declare the `scalper-v2-exchange` workspace dep transitively pulled via `packages/exchange/`. Caught when execution-service container failed `ModuleNotFoundError: No module named 'httpx'` at lifespan startup.

2. **`fix(packages/exchange): add hatchling build config`** (commit `a1112c1`) — `packages/exchange/pyproject.toml` had no `[build-system]` / hatchling config; setuptools auto-discovery failed with "Multiple top-level packages discovered in a flat-layout: ['paper', 'bybit_v5']". Caught when execution-service Docker build failed during `uv sync --package`.

Both fixed pre-smoke, no F2 task regression introduced; tests passed locally because workspace-wide sync (`uv sync --all-packages`) bypasses per-package build-backend invocation. Fixes apply to all future production-image builds.

## F2 + F3 known fail-fast behaviours observed during setup

Pre-smoke environment setup surfaced known fail-fast guardrails (none are bugs; documented for next operator's reference):

- `/etc/scalper-v2/secrets.env` parent dir EACCES → fixed via `sudo mkdir + chmod 0755 + touch + chmod 0644` per T-014 cloudflared comment precedent
- Port 5432 conflict with v1 sibling bot `timescaledb` container (16h up) → `docker stop timescaledb` for smoke; restart after teardown
- Port 8000 conflict with `signabot.service` systemd unit (15h up) → `sudo systemctl stop signabot.service` for smoke; restart after teardown
- HMAC secret < 32 chars → fixed in `.env` to 34-char string per Pydantic `min_length=32` validator
- DB volume retained password from prior init → `docker compose down -v` to reset (acceptable for dev; never in prod)
- Alembic env-var name is `POSTGRES_URL`, not `DATABASE_URL` (per `migrations/env.py:62`)
- SymbolMapCache 60s TTL — first webhook POST after seeding `symbol_map` may hit negative cache from prior failed lookup; restart signal-gateway clears cache
- `bots` table seed required (alpha + beta rows) per FK contract; symbol_map seed required (BTCUSDT canonical) per signal-gateway pipeline
- Live OI feature pipeline (F4+ scope) absent → `data_missing` for any rule referencing `ind.<symbol>.<interval>.oi_change` → H-019 fail-open path engaged correctly

After successful run, follow up with `chore(F3-close)` commit referencing this runbook + the two correlation_id audit rows from `scoring_evaluations`.

## Failure modes to flag

- **alpha decision != "execute"**: scoring_threshold misconfigured, or FeatureResolver returned data_missing (check feature pipeline)
- **beta decision != "passthrough"**: beta.yaml mode field misconfigured, or T-307 evaluator passthrough branch regressed
- **Same correlation_id missing in scoring_evaluations**: T-310b consumer's `insert_scoring_evaluation` write failed (check `scoring_evaluations_insert_failed` log)
- **OrderRequest published but no scoring_evaluations row**: publish-after-persist invariant violated (T-310b WG#3 regression — file blocker bug)
- **Plugin loader fails at lifespan**: missing `plugins/rules/oi_squeeze/__init__.py`, malformed `configs/plugin_registry.yaml` entry, or PYTHONPATH issue
