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

- [ ] Run timestamp: `___________________`
- [ ] correlation_id: `f3-e1-smoke-1`
- [ ] alpha realized_pnl after close (paper-mode trade lifecycle): `___________________`
- [ ] beta realized_pnl after close: `___________________`
- [ ] Any unexpected log keys observed: `___________________`

After successful run, follow up with `chore(F3-close)` commit referencing this runbook + the two correlation_id audit rows from `scoring_evaluations`.

## Failure modes to flag

- **alpha decision != "execute"**: scoring_threshold misconfigured, or FeatureResolver returned data_missing (check feature pipeline)
- **beta decision != "passthrough"**: beta.yaml mode field misconfigured, or T-307 evaluator passthrough branch regressed
- **Same correlation_id missing in scoring_evaluations**: T-310b consumer's `insert_scoring_evaluation` write failed (check `scoring_evaluations_insert_failed` log)
- **OrderRequest published but no scoring_evaluations row**: publish-after-persist invariant violated (T-310b WG#3 regression — file blocker bug)
- **Plugin loader fails at lifespan**: missing `plugins/rules/oi_squeeze/__init__.py`, malformed `configs/plugin_registry.yaml` entry, or PYTHONPATH issue
