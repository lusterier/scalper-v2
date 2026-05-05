# `infra/grafana/`

Grafana operational notes for `compose.yaml` / `compose.dev.yaml`
(brief §15.4, §16.6). The directory holds file-provisioning config
under `provisioning/` (datasource + dashboard provider), dashboard
JSON under `dashboards/`, and this README. No secrets, no bootstrap
sidecar needed.

Image pin: `grafana/grafana:13.0.1` per §3.2 exact-pinning against
latest stable at pin time.

## Provisioning layout

Both halves land under `/etc/grafana/provisioning/` at container
start (read-only bind mount from `./infra/grafana/provisioning/`).
Grafana rescans the provisioning tree every 10s; dashboard JSON
edits hot-reload without a restart.

### Datasources

Single `datasources/prometheus.yml` declares the Prometheus
datasource with fixed `uid: ds_prom`. Dashboard JSON
(`overview.json`) references the datasource by UID — decouples
dashboard portability from Grafana's auto-generated IDs.

`editable: false` blocks UI edits to the provisioned datasource;
reconciliation lands via file change only, preventing drift
between committed YAML and runtime state.

### Dashboards

`dashboards/dashboards.yml` declares a file provider scanning
`/var/lib/grafana/dashboards/` (bind-mounted from
`./infra/grafana/dashboards/`). `overview.json` holds a stat
panel (targets-up count) and a table panel (per-target status
with last-scrape timestamp) over the Prometheus `up` metric.

`disableDeletion: true` + `allowUiUpdates: false` match the
datasource treatment: UI is read-only, committed file is source
of truth.

## Dashboards (T-421 ops dashboards inventory)

T-421 ships 4 ops dashboards alongside the F0 `overview.json`. Each
JSON file lives under `dashboards/` and provisions automatically via
the file provider (10s rescan; hot-reload). PromQL is validated in CI
by `tests/grafana/test_dashboard_queries.py` against zero-data
Prometheus — empty-metric placeholders are syntactically valid (status
"success") even when their exporter has not yet landed.

| Dashboard | UID | Exporter dep | Status today |
| --- | --- | --- | --- |
| Overview | `scalper-v2-overview` | none (F0 stub) | functional |
| Service Health | `scalper-v2-service-health` | none for default panels; spec'd metrics need service emission | functional for `up` / `process_*` panels; `open_positions` / `virtual_balance` / `ws_connected` / `db_pool_saturation` empty until services emit per BRIEF §15.3 |
| NATS | `scalper-v2-nats` | prometheus-nats-exporter (F5+ sidecar) | empty until sidecar lands |
| PostgreSQL | `scalper-v2-pg` | partial: `db_pool_saturation` from services (functional when emitted); `pg_stat_*` from postgres_exporter (F5+) | partial functional |
| Host | `scalper-v2-host` | node-exporter (F5+ sidecar) | empty until sidecar lands |

Operator search: `grep -l "F5+" infra/grafana/dashboards/*.json` lists
panels with deferred exporter dependencies.

## Auth posture

Anonymous `Viewer` role is enabled; the login form is kept
reachable (`GF_AUTH_DISABLE_LOGIN_FORM=false`) so admin API calls
still work. Admin credentials are the dev-default `admin`/`admin`.

Why no secret-backed admin password (diverging from the
`pg_password` Docker-secret pattern): Grafana never escapes the
compose backend network. T-014's nginx + `cloudflared` expose only
`signal-gateway` and (F3+) `analytics-api` — per §2.1 / §18.1 the
Grafana UI has no public ingress route. An attacker would need to
be on the backend network already, at which point `admin`/`admin`
is not a meaningful incremental defense.

If Grafana ever grows a public ingress (dashboard sharing with
collaborators, remote on-call), that's a scope change: an ADR
documents the new threat model and this file gains a
`GF_SECURITY_ADMIN_PASSWORD` secret alongside.

## Production pre-up (one-time per host)

```sh
sudo mkdir -p /mnt/data/grafana
sudo chown 472:0 /mnt/data/grafana
```

`grafana/grafana:13.0.1` runs as uid=472 gid=0 (non-root user in
the root group) — verified empirically against the pinned image
via `docker run --rm --entrypoint id <image>`, not from memory.
The uid/gid asymmetry is a Grafana packaging convention.

First boot on wrong ownership dies with a permission error in
`docker compose logs grafana` (`mkdir: can't create directory
'/var/lib/grafana/plugins': Permission denied`) — easy to
recognize; fix with the `chown` above and
`docker compose up -d grafana` to retry.

## Smoke verification

From a dev host after `docker compose -f compose.yaml -f
compose.dev.yaml up -d prometheus grafana`:

```sh
# Anonymous liveness probe (no auth required)
curl -s http://127.0.0.1:3000/api/health

# Provisioned datasource — expect uid ds_prom
curl -s -u admin:admin http://127.0.0.1:3000/api/datasources \
  | jq '.[0] | {name, type, uid}'

# Provisioned dashboard — expect uid scalper-v2-overview
curl -s -u admin:admin \
  "http://127.0.0.1:3000/api/search?query=Overview" \
  | jq '.[0] | {title, uid, type}'
```

Expected:
- `/api/health` → JSON with `"database":"ok"` and
  `"version":"13.0.1"`.
- datasource → `uid: "ds_prom"`, `type: "prometheus"`.
- dashboard → `uid: "scalper-v2-overview"`, `type: "dash-db"`.

## Forward references

- T-017: dashboard test harness (`tests/grafana/`) asserts panel
  queries parse against Prometheus; `overview.json` is the first
  provisioned JSON for it to target.
- F1+: `ds_ts_main` TimescaleDB datasource lands alongside a
  `postgres_exporter` scrape entry when Timescale-backed panels
  are needed.
