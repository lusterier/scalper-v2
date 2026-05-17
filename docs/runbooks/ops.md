# Ops runbook — scalper-v2

Tento runbook číta Claude Code pri každej ops úlohe. Pokrýva celú prevádzku platformy:
správu botov, servisov, databázy, analytiku a diagnostiku problémov.

---

## Bezpečnostné pravidlá (VŽDY platné)

1. **Secrets** – `/etc/scalper-v2/secrets.env` smieš čítať a používať v príkazoch, nikdy
   nevypisuj hodnoty do terminálu ani do chatu. Ak musíš ukázať konfig, maskuj: `API_KEY=***`.
2. **Destruktívne akcie** (DELETE z DB, docker compose down, restart live bota) – vždy najprv
   oznám čo chceš spraviť a počkaj na potvrdenie operátora, pokiaľ nie je explicitne povedané
   „rob to".
3. **Live mode** – pred akýmkoľvek príkazom na live bota skontroluj `exchange_mode` v DB.
   Ak je `live`, buď extra opatrný.
4. **DB zmeny** – DDL (ALTER, DROP, TRUNCATE) NIKDY bez explicitného súhlasu. DML (INSERT,
   UPDATE, DELETE) len na non-financial tabuľky (bots, bot_configs) bez súhlasu; na trades,
   paper_trades, position_state vždy so súhlasom.

---

## Prostredie

```bash
# Stack príkazy
docker compose -f /home/luster/scalper-v2/compose.yaml PRÍKAZ

# DB shell
docker compose exec postgres psql -U scalper scalper

# Logy služby (live tail)
docker compose logs -f --tail=100 SLUŽBA

# Stav všetkých kontajnerov
docker compose ps

# Analytics API (OpenAPI docs)
http://192.168.100.100:8000/docs

# NATS monitor
http://192.168.100.100:8222/varz
```

Secrets sa načítavajú z `/etc/scalper-v2/secrets.env` automaticky cez `env_file` v compose.yaml.

---

## Schéma ako zdroj pravdy

Runbook môže zaostávať za kódom. Pred každou prácou s bot konfiguráciou skontroluj
aktuálne Pydantic modely – tie sú vždy správne.

```bash
# Aktuálna BotConfig schéma so všetkými sekciami
grep -A 300 "^class BotConfig" packages/scoring/types.py | head -80

# RiskSection – risk management parametre
grep -A 60 "^class RiskSection" packages/scoring/types.py

# SizingSection – position sizing parametre
grep -A 80 "^class SizingSection" packages/scoring/types.py

# ShadowConfig – shadow variants
grep -A 30 "^class ShadowConfig" packages/scoring/types.py

# Referenčná šablóna (production konfig)
cat configs/bots/alpha.yaml
```

**Postup pri pridávaní bota:**
1. Prečítaj schému vyššie
2. Porovnaj s `alpha.yaml`
3. Ak vidíš sekciu v schéme ktorá nie je v runbooku (napr. nové polia), použi default
   z `Field(default=...)` a aktualizuj príslušnú sekciu tohto runbooku

**Validácia konfigurácie pred uložením:**

```bash
# Vždy validuj YAML pred aplikovaním – API vráti 422 ak niečo nesedí
curl -s -X POST http://localhost:8000/api/configs/validate \
  -H "Content-Type: application/json" \
  -d "{\"bot_id\": \"BOTID\", \"yaml_text\": $(cat configs/bots/BOTID.yaml | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')}" \
  | python3 -m json.tool
```

Ak `valid: true` – pokračuj. Ak `valid: false` – `errors` pole povie čo treba opraviť.

---

## Správa botov

### Pridanie nového bota

> **Prerekvizity (zistené ops 2026-05-17):**
> - **DB schéma musí existovať.** Migrácie nie sú v compose ani v žiadnom
>   init servise — sú manuálny krok. Na čerstvej DB (žiadna tabuľka `bots`)
>   spusti z hosta cez container bridge IP postgresu:
>   `POSTGRES_URL=postgresql://scalper:<pg_password>@<pg_container_ip>:5432/scalper uv run alembic -c migrations/alembic.ini upgrade head`
>   (`migrations/env.py` číta `POSTGRES_URL`, NIE `DATABASE_URL`, neload-uje `.env`;
>   postgres nepublikuje 5432 na host → container IP cez `docker inspect`).
> - **`compose.dev.yaml` je aktívne mergnuté** (env_file `!reset null`,
>   `DATABASE_URL`/secrety interpolované z project-root `.env`). Nové env vars
>   čítané službami pod dev overlayom musia ísť aj do project-root `.env`,
>   nielen do `/etc/scalper-v2/secrets.env`.
> - **Live bot:** per-bot `BOT_<ID>_BYBIT_API_KEY/SECRET` + `BOT_CONFIRM_LIVE`
>   sa do `execution-service` ešte **neprepájajú (T-215, neimplementované)**.
>   `execution-service` číta `select_active_bots` (len `status='active'`) a pre
>   `exchange_mode='live'` vyžaduje `BOT_CONFIRM_LIVE=yes` vo svojom env — inak
>   RuntimeError a pád celej služby. **Live bota drž `status='paused'`** v DB
>   kým T-215 nie je hotové; inak zhodíš order execution pre celú platformu.

**Krok 1 – YAML konfig**

Skopíruj šablónu a uprav:

```bash
cp configs/bots/alpha.yaml configs/bots/BOTID.yaml
```

Povinné polia na zmenu:
```yaml
bot_id: BOTID          # lowercase, bez medzier
exchange:
  mode: paper          # paper | testnet | live
  account: sub_BOTID
  api_key_env: BOT_BOTID_BYBIT_API_KEY
  api_secret_env: BOT_BOTID_BYBIT_API_SECRET
trading:
  universe: [BTCUSDT]
execution:
  qty: "0.001"         # statický qty – ignorovaný ak je sekcia sizing:
  leverage: 20
  sl_pct: "0.01"
  tp_pct: "0.01"
  tp_qty_pct: "0.5"
  be_trigger: "0.005"
  be_sl_level: "0.003"
  trail_pct: "0.005"
  fee_rate: "0.00055"

# Risk management – numerické hodnoty 0 = vypnuté
risk:
  cooldown_after_loss_minutes: 0
  cooldown_after_streak_n_losses: 0
  cooldown_after_streak_n_losses_minutes: 0
  max_open_trades_per_bot: 0
  max_open_trades_global: 0
  daily_loss_limit_usd: "0"
  max_drawdown_pct: "0"
  # H-005 opposite-side guard (T-542): bool, default true = blokuje nový vstup
  # ak má bot otvorenú pozíciu na opačnej strane pre rovnaký symbol (signál
  # sa ticho preskočí pred scoringom – signal_blocked_opposite_side). NIE je
  # 0=vypnuté konvencia; nastav false ak chceš guard vypnúť pre tohto bota.
  block_opposite_side: true

# Position sizing (voliteľné – ak chýba, použije sa execution.qty)
# Odkomentuj a nakonfiguruj ak chceš balance-driven sizing:
# sizing:
#   method: tier          # tier | risk_per_sl
#   max_notional_per_symbol:
#     default: "500"      # max notional USD per pozíciu
#   tiers:                # tier-based sizing podľa equity
#     - balance_min: "0"
#       size: "100"
#   score_multipliers:    # voliteľné – škáluje size podľa scoring skóre
#     "1": "1.0"
#   # Pre method: risk_per_sl:
#   # risk_pct: "0.01"   # rizikuj 1% equity na trade

# Signal acceptance (voliteľné – ak chýba, akceptujú sa všetky zdroje)
# source_filter: allowlist zdrojov signálu (SignalValidated.source).
#   null / chýba = akceptuj všetky; signál zo zdroja mimo zoznamu sa ticho
#   preskočí (signal_outside_source_filter v trading.log – žiadny order/scoring).
# signals:
#   source_filter: ["tv_prod"]   # napr. len signály z tohto TradingView zdroja

# Scoring – konfiguruj podľa potrieb bota
scoring:
  mode: active           # active | passthrough
  trigger_threshold: 1.0
  rules:
    - name: rule_name
      weight: 1.0
      feature: ind.${signal.symbol}.15m.ema_20
      condition:
        type: gt
        feature: ind.${signal.symbol}.15m.ema_20
        value: "0"
```

**Skontroluj aktuálnu schému** – ak si neistý či nejaký parameter existuje:
```bash
grep -A 300 "^class BotConfig" packages/scoring/types.py | head -100
```

**Krok 2 – Riadok v DB**

```sql
INSERT INTO bots (bot_id, display_name, created_at, status, exchange_mode, config_hash, config_applied_at)
VALUES (
  'BOTID',
  'Popis bota',
  NOW() AT TIME ZONE 'UTC',
  'active',
  'paper',          -- musí sedieť s exchange.mode v YAML
  'pending',
  NOW() AT TIME ZONE 'UTC'
);
```

**Krok 3 – Zaregistruj konfig cez API**

```bash
curl -s -X POST http://localhost:8000/api/configs/BOTID/apply \
  -H "Content-Type: application/json" \
  -d "{
    \"yaml_text\": $(cat configs/bots/BOTID.yaml | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),
    \"applied_by\": \"operator\",
    \"notes\": \"initial config\"
  }"
```

**Krok 4 – Env vars do secrets.env**

Pre paper bot:
```
BOT_BOTID_PAPER_SEED_BALANCE=10000
BOT_BOTID_PAPER_FEE_RATE=0.00055
BOT_BOTID_PAPER_SLIPPAGE_MODEL=fixed
BOT_BOTID_PAPER_SLIPPAGE_PARAMS_JSON={"slippage_pct": "0.0001"}
```

Pre live/testnet bot:
```
BOT_BOTID_BYBIT_API_KEY=...
BOT_BOTID_BYBIT_API_SECRET=...
BOT_BOTID_BYBIT_SUB_ACCOUNT=sub_BOTID
BOT_BOTID_CONFIRM_LIVE=yes    # len ak mode=live
```

**Krok 5 – compose.yaml blok**

Pridaj za posledný `strategy-engine-*` blok:

```yaml
  strategy-engine-BOTID:
    build:
      context: .
      dockerfile: services/strategy_engine/Dockerfile
    image: scalper-v2/strategy-engine:dev
    restart: unless-stopped
    networks: [backend]
    env_file:
      - path: /etc/scalper-v2/secrets.env
        required: false
    environment:
      SERVICE_NAME: strategy-engine
      LOG_LEVEL: INFO
      NATS_URL: nats://nats:4222
      BOT_ID: BOTID
      BOT_CONFIG_DIR: /app/configs/bots
      PLUGIN_REGISTRY_PATH: /app/configs/plugin_registry.yaml
      BOT_CONFIRM_LIVE: ${BOT_BOTID_CONFIRM_LIVE:-}
    depends_on:
      postgres:
        condition: service_healthy
      nats:
        condition: service_healthy
      nats-init:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2); sys.exit(0)"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 15s
```

**Krok 6 – Spusti**

```bash
docker compose up -d strategy-engine-BOTID
docker compose restart execution
```

Overenie:
```bash
docker compose ps strategy-engine-BOTID
docker compose logs --tail=30 strategy-engine-BOTID
```

### Webhook kontrakt (signal-gateway)

`POST /webhook` (verejne cez nginx `/webhook,/health,/ready` → `signal-gateway:8000`;
nginx host port `127.0.0.1:8080`; externe cez systemd cloudflared tunnel `signabot`).

- Body model `SignalEnvelope` (`services/signal_gateway/app/models.py`):
  povinné `symbol`, `action` (`Literal["LONG","SHORT","CLOSE"]`), `source`,
  `idempotency_key`; extra top-level kľúče sa absorbujú do `payload`.
- `symbol` musí byť **input alias zo `symbol_map`**, nie kanonický. Seed (mig.
  0001): `BTCUSDT.P→BTCUSDT`, `ETHUSDT.P→ETHUSDT`. Neznámy alias → `422
  symbol_unknown`.
- `source` musí byť v `signals.source_filter` bota (ak je nastavený), inak sa
  signál ticho preskočí.
- HMAC: hlavička **`X-Signature`** = hex HMAC-SHA256 raw request body pod
  `SIGNAL_GATEWAY_HMAC_SECRET`. Chýbajúci/zlý → `401 {"reason":"hmac_invalid"}`.

---

### Aktualizácia konfigurácie bota

> Pred editáciou skontroluj aktuálnu schému: `grep -A 50 "^class RiskSection\|^class SizingSection" packages/scoring/types.py`

```bash
# Uprav YAML
nano configs/bots/BOTID.yaml

# Aplikuj cez API (verzionované, auditované)
curl -s -X POST http://localhost:8000/api/configs/BOTID/apply \
  -H "Content-Type: application/json" \
  -d "{
    \"yaml_text\": $(cat configs/bots/BOTID.yaml | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),
    \"applied_by\": \"operator\",
    \"notes\": \"DÔVOD ZMENY\"
  }"

# Reštart strategy-engine pre daného bota (načíta nový konfig)
docker compose restart strategy-engine-BOTID
```

---

### Pozastavenie / obnovenie bota

```bash
# Pozastaviť (bot nebude reagovať na signály)
docker compose stop strategy-engine-BOTID

# Aktualizovať status v DB
docker compose exec postgres psql -U scalper scalper -c \
  "UPDATE bots SET status='paused' WHERE bot_id='BOTID';"

# Obnoviť
docker compose exec postgres psql -U scalper scalper -c \
  "UPDATE bots SET status='active' WHERE bot_id='BOTID';"
docker compose start strategy-engine-BOTID
```

---

## Správa servisov

### Prehľad

| Servis | Obsluhuje | Reštart ovplyvní |
|--------|-----------|-----------------|
| `strategy-engine-*` | Per-bot scoring | Zastaví signál processing pre daného bota |
| `execution` | Ordery, pozície, SL/TP | Aktívne pozície nie sú ohrozené (T-221 reconcile pri reštarte) |
| `signal-gateway` | TradingView webhooky | Signály počas reštartu sa stratia |
| `market-data` | OHLC dáta | Feature engine stratí live data; dopočíta z DB |
| `feature-engine` | Indikátory | Krátky výpadok feature store; fail-open scoring |
| `analytics-api` | Dashboard REST API | Dashboard nedostupný |
| `alerting` | Telegram | Žiadne alerting |

### Bežné príkazy

```bash
# Status všetkých
docker compose ps

# Reštart jednej služby
docker compose restart SERVIS

# Zastavenie a spustenie (tvrdší reset)
docker compose stop SERVIS && docker compose start SERVIS

# Rebuild a deploy (po zmene kódu)
docker compose up -d --build SERVIS

# Logy (live)
docker compose logs -f --tail=200 SERVIS

# Logy s timestamps
docker compose logs -f -t --tail=100 SERVIS

# Reštart celého stacku (OPATRNE – ovplyvní všetky boty)
docker compose restart
```

### Healthcheck

```bash
# Rýchly healthcheck všetkých služieb
for s in signal-gateway execution strategy-engine-demo strategy-engine-smoke feature-engine market-data analytics-api alerting; do
  status=$(docker compose exec $s python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" 2>&1 && echo OK || echo FAIL)
  echo "$s: $status"
done

# NATS healthcheck
curl -s http://192.168.100.100:8222/healthz

# DB healthcheck
docker compose exec postgres pg_isready -U scalper
```

---

## Databáza – bežné dotazy

### Pripojenie

```bash
docker compose exec postgres psql -U scalper scalper
```

### Prehľad botov a ich stavu

```sql
-- Zoznam všetkých botov
SELECT bot_id, display_name, status, exchange_mode, config_applied_at
FROM bots ORDER BY bot_id;

-- Aktuálna verzia konfigurácie
SELECT b.bot_id, b.status, bc.version, bc.applied_at, bc.notes
FROM bots b
LEFT JOIN bot_configs bc ON bc.bot_id = b.bot_id
  AND bc.version = (SELECT MAX(version) FROM bot_configs WHERE bot_id = b.bot_id)
ORDER BY b.bot_id;
```

### Aktívne pozície

```sql
-- Všetky otvorené pozície
SELECT ps.bot_id, ps.symbol, ps.side, ps.remaining_qty,
       ps.entry_price, ps.sl_price, ps.sl_type,
       ps.running_pnl, ps.mfe_pct, ps.mae_pct,
       ps.opened_at
FROM position_state ps
ORDER BY ps.opened_at DESC;

-- Pozície konkrétneho bota
SELECT * FROM position_state WHERE bot_id = 'BOTID';
```

### Paper trading – prehľad

```sql
-- Otvorené paper trades
SELECT bot_id, symbol, side, qty, entry_price, sl_price, tp_price,
       fees_paid, opened_at
FROM paper_trades
WHERE status = 'open'
ORDER BY opened_at DESC;

-- Uzavreté paper trades za posledných 24h
SELECT bot_id, symbol, side, qty, entry_price, exit_price,
       realized_pnl, fees_paid, close_reason,
       opened_at, closed_at
FROM paper_trades
WHERE status = 'closed'
  AND closed_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '24 hours'
ORDER BY closed_at DESC;
```

### Signály

```sql
-- Posledné prijaté signály
SELECT received_at, symbol, side, source, signal_id
FROM signals
ORDER BY received_at DESC
LIMIT 20;

-- Scoring evaluations pre posledný signál
SELECT bot_id, decision, total_score, trigger_threshold,
       rule_evaluations, created_at
FROM scoring_evaluations
WHERE signal_id = (SELECT MAX(signal_id) FROM signals)
ORDER BY bot_id;
```

### Equity snapshots

```sql
-- Posledný equity snapshot
SELECT bot_id, snapshot_at,
       total_equity, wallet_balance, available_balance,
       unrealized_pnl
FROM bot_equity_snapshots
WHERE snapshot_at = (SELECT MAX(snapshot_at) FROM bot_equity_snapshots)
ORDER BY bot_id;
```

---

## Analytika

### P&L prehľad

```sql
-- Celkový P&L podľa bota (paper)
SELECT
  bot_id,
  COUNT(*) FILTER (WHERE status = 'closed') AS total_trades,
  COUNT(*) FILTER (WHERE status = 'closed' AND realized_pnl > 0) AS wins,
  COUNT(*) FILTER (WHERE status = 'closed' AND realized_pnl < 0) AS losses,
  ROUND(
    COUNT(*) FILTER (WHERE status = 'closed' AND realized_pnl > 0)::numeric
    / NULLIF(COUNT(*) FILTER (WHERE status = 'closed'), 0) * 100, 2
  ) AS win_rate_pct,
  ROUND(SUM(realized_pnl) FILTER (WHERE status = 'closed'), 4) AS total_pnl,
  ROUND(AVG(realized_pnl) FILTER (WHERE status = 'closed'), 4) AS avg_pnl,
  ROUND(SUM(fees_paid), 4) AS total_fees
FROM paper_trades
GROUP BY bot_id
ORDER BY bot_id;
```

```sql
-- P&L po symboloch (paper, posledných 7 dní)
SELECT
  bot_id, symbol,
  COUNT(*) FILTER (WHERE status = 'closed') AS trades,
  ROUND(SUM(realized_pnl) FILTER (WHERE status = 'closed'), 4) AS pnl,
  ROUND(AVG(realized_pnl) FILTER (WHERE status = 'closed'), 4) AS avg_pnl
FROM paper_trades
WHERE closed_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '7 days'
GROUP BY bot_id, symbol
ORDER BY bot_id, pnl DESC;
```

```sql
-- Denný P&L (paper, TimescaleDB time_bucket)
SELECT
  bot_id,
  time_bucket('1 day', closed_at) AS day,
  COUNT(*) AS trades,
  ROUND(SUM(realized_pnl), 4) AS daily_pnl
FROM paper_trades
WHERE status = 'closed'
  AND closed_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '30 days'
GROUP BY bot_id, day
ORDER BY bot_id, day DESC;
```

### Scoring analýza

```sql
-- Distribúcia rozhodnutí podľa bota
SELECT
  bot_id,
  decision,
  COUNT(*) AS count,
  ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER (PARTITION BY bot_id) * 100, 1) AS pct
FROM scoring_evaluations
WHERE created_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '7 days'
GROUP BY bot_id, decision
ORDER BY bot_id, count DESC;
```

```sql
-- Priemerný score pri execute vs reject
SELECT
  bot_id,
  decision,
  ROUND(AVG(total_score::numeric), 3) AS avg_score,
  MIN(total_score::numeric) AS min_score,
  MAX(total_score::numeric) AS max_score,
  COUNT(*) AS count
FROM scoring_evaluations
WHERE created_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '7 days'
GROUP BY bot_id, decision
ORDER BY bot_id, decision;
```

### SL/TP analýza

```sql
-- Distribúcia close_reason (paper)
SELECT
  bot_id,
  close_reason,
  COUNT(*) AS count,
  ROUND(AVG(realized_pnl), 4) AS avg_pnl,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM paper_trades
WHERE status = 'closed'
GROUP BY bot_id, close_reason
ORDER BY bot_id, count DESC;
```

```sql
-- MFE/MAE distribúcia (max favorable/adverse excursion)
SELECT
  bot_id,
  ROUND(AVG(mfe_pct) * 100, 2) AS avg_mfe_pct,
  ROUND(AVG(mae_pct) * 100, 2) AS avg_mae_pct,
  ROUND(MAX(mfe_pct) * 100, 2) AS max_mfe_pct,
  ROUND(MAX(mae_pct) * 100, 2) AS max_mae_pct
FROM paper_trades
WHERE status = 'closed'
  AND mfe_pct IS NOT NULL
GROUP BY bot_id;
```

### Feature analýza

```sql
-- Posledné hodnoty featur pre symbol
SELECT
  feature_name,
  value_num,
  value_json,
  computed_at
FROM features
WHERE symbol = 'BTCUSDT'
  AND computed_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '1 hour'
ORDER BY feature_name, computed_at DESC;
```

```sql
-- História EMA 20 (TimescaleDB time_bucket)
SELECT
  time_bucket('15 minutes', computed_at) AS bucket,
  AVG(value_num) AS ema_20
FROM features
WHERE feature_name = 'ind.btcusdt.15m.ema_20'
  AND computed_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '24 hours'
GROUP BY bucket
ORDER BY bucket DESC
LIMIT 48;
```

### Funding fees

```sql
-- Funding fees za posledný týždeň
SELECT
  bot_id, symbol,
  SUM(funding) AS total_funding,
  COUNT(*) AS settlements,
  MIN(settled_at) AS first,
  MAX(settled_at) AS last
FROM funding_fees
WHERE settled_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '7 days'
GROUP BY bot_id, symbol
ORDER BY total_funding;
```

---

## Diagnostika problémov

### Postup pri probléme

1. **Identifikuj** – aký servis/bot je ovplyvnený
2. **Logy** – `docker compose logs -f --tail=200 SERVIS`
3. **DB stav** – skontroluj position_state, paper_trades, trading_events
4. **NATS** – `http://192.168.100.100:8222/connz` (aktívne spojenia)
5. **Healthcheck** – `curl http://localhost:8000/ready`

### Časté problémy

**Bot neobchoduje (žiadne ordery)**
```bash
# Skontroluj czy bot dostáva signály
docker compose logs --tail=50 strategy-engine-BOTID | grep -E "signal|decision|execute|reject"

# Skontroluj scoring evaluations
docker compose exec postgres psql -U scalper scalper -c \
  "SELECT decision, total_score, created_at FROM scoring_evaluations
   WHERE bot_id='BOTID' ORDER BY created_at DESC LIMIT 10;"

# Skontroluj cooldown/caps gates
docker compose logs --tail=100 strategy-engine-BOTID | grep -E "blocked|cooldown|cap"
```

**Execution servis nereaguje**
```bash
docker compose logs --tail=100 execution | grep -E "ERROR|WARNING|exception"

# Skontroluj aktívne pozície vs position_state
docker compose exec postgres psql -U scalper scalper -c \
  "SELECT bot_id, symbol, remaining_qty, sl_type FROM position_state;"
```

**SL/TP nie je nastavený (sleduj watchdog)**
```bash
docker compose logs --tail=100 execution | grep -E "sl_watchdog|SL|sl_missing"
```

**Feature engine zastaví**
```bash
docker compose logs --tail=100 feature-engine | grep -E "ERROR|stale|fail"

# Skontroluj posledné features
docker compose exec postgres psql -U scalper scalper -c \
  "SELECT feature_name, computed_at FROM features
   ORDER BY computed_at DESC LIMIT 5;"
```

**DB spojenie padá**
```bash
docker compose exec postgres pg_isready -U scalper

# Aktívne spojenia
docker compose exec postgres psql -U scalper scalper -c \
  "SELECT application_name, state, count(*)
   FROM pg_stat_activity
   GROUP BY application_name, state
   ORDER BY count DESC;"
```

**NATS problémy**
```bash
# Stav streamov
curl -s http://192.168.100.100:8222/streamsz | python3 -m json.tool | grep -E "name|msgs|bytes"

# Aktívne subscriptions
curl -s http://192.168.100.100:8222/subsz | python3 -m json.tool
```

### Kill-switch (manuálne zastavenie bota)

```bash
# Soft stop – bot prestane reagovať na signály (pozície ostanú otvorené)
docker compose stop strategy-engine-BOTID

# Tvrdý stop – zastaví aj execution (pozície ostanú otvorené na exchange)
# OPATRNE – iba ak vieš čo robíš
docker compose stop strategy-engine-BOTID execution
```

Pre manuálne uzavretie pozície – urob to priamo na Bybit UI; systém to detekuje a zosynchronizuje (T-221 reconcile).

### Audit log

```sql
-- Posledné auditné záznamy
SELECT occurred_at, actor, action, entity_type, entity_id
FROM audit_events
ORDER BY occurred_at DESC
LIMIT 20;

-- Zmeny konfigurácie konkrétneho bota
SELECT occurred_at, actor, action, notes
FROM audit_events
JOIN bot_configs bc ON bc.id::text = entity_id
WHERE entity_type = 'bot_config'
  AND entity_id LIKE '%BOTID%'
ORDER BY occurred_at DESC;

-- Trading eventy (SL watchdog, overwrite detection, ...)
SELECT event_type, bot_id, trade_id, occurred_at, payload
FROM trading_events
WHERE occurred_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '1 hour'
ORDER BY occurred_at DESC;
```

---

## Backtest

### Spustenie backtesta (CLI)

```bash
# Základný backtest
docker compose exec strategy-engine-alpha \
  uv run python scripts/backtest.py \
    --bot-config configs/bots/BOTID.yaml \
    --from 2026-01-01 \
    --to 2026-05-01

# Backtest s override parametrami
docker compose exec strategy-engine-alpha \
  uv run python scripts/backtest.py \
    --bot-config configs/bots/BOTID.yaml \
    --from 2026-01-01 --to 2026-05-01 \
    --override execution.sl_pct=0.008 \
    --override execution.tp_pct=0.015
```

### Porovnanie dvoch backtest runov

```bash
docker compose exec strategy-engine-alpha \
  uv run python scripts/backtest.py \
    --compare RUN_UUID_A RUN_UUID_B
```

### Výsledky backtesta v DB

```sql
-- Zoznam backtest runov
SELECT id, bot_id, status, created_at, completed_at,
       summary->>'total_trades' AS trades,
       summary->>'profit_factor' AS pf,
       summary->>'win_rate' AS win_rate
FROM backtest_runs
ORDER BY created_at DESC
LIMIT 10;

-- Detail konkrétneho runu
SELECT * FROM backtest_runs WHERE id = 'UUID';
```

---

## Monitorovanie

### Prometheus metriky

```bash
# Metriky execution servisu
curl -s http://localhost:8001/metrics | grep -E "signals_|trades_|equity_"

# Metriky strategy-engine pre bota
curl -s http://localhost:8002/metrics | grep signals_blocked
```

### Grafana dashboardy

Dashboardy sú v `infra/grafana/dashboards/`:
- `service-health.json` – stav všetkých servisov
- `nats.json` – NATS JetStream metriky
- `pg.json` – PostgreSQL metriky
- `host.json` – CPU, RAM, disk

---

## Rýchle príkazy (cheat sheet)

```bash
# Stack stav
docker compose ps

# Všetky logy (posledných 5 minút)
docker compose logs --since=5m

# Bot logy
docker compose logs -f strategy-engine-BOTID

# Execution logy
docker compose logs -f execution

# Aktívne pozície
docker compose exec postgres psql -U scalper scalper -c \
  "SELECT bot_id, symbol, side, remaining_qty, entry_price, sl_price, running_pnl FROM position_state;"

# Paper P&L (dnes)
docker compose exec postgres psql -U scalper scalper -c \
  "SELECT bot_id, COUNT(*) trades, SUM(realized_pnl) pnl FROM paper_trades
   WHERE closed_at >= CURRENT_DATE AND status='closed' GROUP BY bot_id;"

# Posledné signály
docker compose exec postgres psql -U scalper scalper -c \
  "SELECT received_at, symbol, side FROM signals ORDER BY received_at DESC LIMIT 10;"

# NATS health
curl -s http://192.168.100.100:8222/healthz

# Reštart execution (bezpečný – T-221 reconcile pri štarte)
docker compose restart execution

# Pridanie bota – spusti podľa sekcie "Pridanie nového bota" vyššie
```
