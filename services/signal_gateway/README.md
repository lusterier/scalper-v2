# signal-gateway

TradingView webhook ingress for scalper-v2 (brief §9.1, §19 F0 bullet 8).

T-015a ships the FastAPI skeleton — `/health`, `/ready`, `/metrics` — so
the Prometheus scrape target (`infra/prometheus/prometheus.yml`) lights
up. The §9.1 validation pipeline (`POST /webhook`) lands in T-015b. Full
design in [`docs/modules/signal_gateway.md`](../../docs/modules/signal_gateway.md).

## Run

### Production (compose base)

```
docker compose up -d signal-gateway
```

Requires `/etc/scalper-v2/secrets.env` on the host with the runtime env
laid out in `.env.example`. The service listens on `:8000` inside the
`backend` compose network; external reach is through the nginx →
cloudflared chain (T-014). No host-side port publish.

### Dev (compose overlay)

```
docker compose -f compose.yaml -f compose.dev.yaml up -d signal-gateway
```

Publishes `127.0.0.1:8000` on the dev host for local `curl` probes.

### Local (without Docker)

```
export DATABASE_URL=postgresql://scalper:<password>@127.0.0.1:5432/scalper
export SIGNAL_GATEWAY_HMAC_SECRET=dev-placeholder
uv run --package scalper-v2-signal-gateway \
    python -m uvicorn services.signal_gateway.app.main:create_app \
    --factory --reload --port 8000
```

Requires `postgres` and `nats` from the dev compose overlay to be up.

## Test

From the repo root:

```
uv run pytest services/signal_gateway/ -q
```

Integration tests (full `/webhook` → NATS → PG round-trip via
testcontainers) land with T-015b and run under CI-full (T-016).

## Configuration

All env; typed Pydantic model in `app/config.py`. See `.env.example` for
the full list. Required at startup: `DATABASE_URL`,
`SIGNAL_GATEWAY_HMAC_SECRET`. Everything else has a documented default.

## Database-credential wiring (F0 minimum)

`DATABASE_URL` is a plaintext `postgresql://` DSN loaded from
`/etc/scalper-v2/secrets.env` via the compose `env_file` directive.
Docker-secret read-through (mount `/run/secrets/pg_password` + assemble
the DSN at startup) is deferred to F1, when a second service
(execution, analytics-api) also needs DB credentials and a shared
pattern is worth establishing. For T-015a's single consumer and a
`/ready` probe that acquires once per request, plaintext via env is the
minimal working shape and does not warrant an ADR per §0.6 (the choice
is service-local, not cross-module).
