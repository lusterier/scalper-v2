# Dev stack lifecycle

Local development orchestration: PostgreSQL + NATS (Docker Compose) + analytics-api (native uvicorn) + Vite dev server (native pnpm). One command up, one command down.

## Quick start

```bash
./scripts/dev-up.sh    # bring up infra + services
./scripts/dev-down.sh  # tear down (preserves DB volumes)
```

After `dev-up.sh` — **all services LAN-bound 0.0.0.0** per operator decision 2026-05-08 (home LAN trusted; no router port-forward; no untrusted devices):

- PostgreSQL — `192.168.100.100:5432` (user `scalper`, password from `.env` `POSTGRES_PASSWORD` — fallback `devpass`, DB `scalper`)
- NATS client — `192.168.100.100:4222` (NATS protocol; for `nats` CLI / SDK)
- NATS HTTP monitor — `http://192.168.100.100:8222` (JSON endpoints `/varz`, `/connz`, `/streamsz`, `/healthz`; no UI — browse JSON or run `prometheus-nats-exporter` against it)
- analytics-api — `http://192.168.100.100:8000` (FastAPI + SSE; OpenAPI docs at `/docs`)
- Vite — `http://192.168.100.100:5173` (per chore(devx) `868e35b` LAN-bind)

Vite proxies `/api` + `/events` server-side to `127.0.0.1:8000` (per `ui/vite.config.ts`); analytics-api listening on `0.0.0.0` keeps that path working unchanged.

BRIEF §16.6 LAN-only invariant is **preserved**: nothing publicly exposed (no router port-forward, no cloudflared on dev). LAN-bound services are operator discretion based on the trusted-LAN constraint — guest WiFi or port-forward would invalidate this stance.

## Remote access from another LAN PC

All four service surfaces are reachable from any host on the home LAN without SSH tunnels.

### pgAdmin / DBeaver / psql (PostgreSQL)

- Host: `192.168.100.100` (verify with `ip -4 addr show eno1` if DHCP shifted)
- Port: `5432`
- Database: `scalper` · Username: `scalper` · Password: `.env` `POSTGRES_PASSWORD` (typically `devpass`)

### nats CLI

```bash
nats --server=nats://192.168.100.100:4222 stream ls
nats --server=nats://192.168.100.100:4222 sub 'market.ohlc.1m.>'
```

### NATS HTTP monitor

Browse JSON endpoints in any browser:
- `http://192.168.100.100:8222/varz` — server stats
- `http://192.168.100.100:8222/connz` — active connections
- `http://192.168.100.100:8222/streamsz` — JetStream stream list
- `http://192.168.100.100:8222/healthz` — health probe

(No built-in UI; run `prometheus-nats-exporter` against `:8222` if Grafana visualization is needed — flagged as F1+ follow-up in `compose.dev.yaml`.)

### analytics-api (FastAPI)

- OpenAPI docs: `http://192.168.100.100:8000/docs`
- Endpoints: `http://192.168.100.100:8000/api/...` (bots, trades, signals, scoring, features, audit, configs, analytics, backtests, symbol-map)
- SSE stream: `http://192.168.100.100:8000/events/stream`

### Vite UI

- Dashboard: `http://192.168.100.100:5173/`

**Security note**: this trusted-LAN stance assumes no untrusted devices on the LAN and no router port-forward. If the trust model changes (guest WiFi, port-forward), revert each service to `127.0.0.1` bind in `compose.dev.yaml` (postgres + nats) and `scripts/dev-up.sh` (uvicorn `--host 127.0.0.1`), then use SSH tunnel from the remote PC (pgAdmin Tunnel tab; or `ssh -L` for the rest).

## Why a wrapper instead of full compose

Vite + analytics-api stay native (not containerized) because:
- HMR file-watching via bind-mount on Linux is flaky (inotify limits) and slower than native.
- Node toolchain (20.19.6) + pnpm (10.33.2) is operator-led per project memory `ui_toolchain.md` (operator-led bump 2026-05-04 during T-410); containerizing hides the toolchain version behind an image build.
- Compose still owns infra (postgres + nats) where containerization is the right answer (data volumes, healthchecks).

## What the scripts do

### `dev-up.sh`
1. Sources `.env` (if present) so DSN derives from the same `POSTGRES_PASSWORD` that `compose.dev.yaml` substitutes; falls back to `devpass`.
2. `docker compose -f compose.yaml -f compose.dev.yaml up -d --no-recreate postgres nats`. The overlay is **required** — base `compose.yaml` does NOT publish 5432/4222 to host, uses Docker secrets, and bind-mounts `/mnt/data` (which dev hosts don't have). The overlay flips to host-port-publish + `.env`-substituted password + named volumes per dev convention (matches existing F3/F4 smoke runbooks).
3. Polls until both healthy (30s timeout); fail-fast `exit 1` on timeout so callers don't see a misleading "stack ready" banner when DB isn't actually up.
4. `setsid nohup uv run uvicorn services.analytics_api.app.main:create_app --factory --host 127.0.0.1 --port 8000` in background; PID written to `/tmp/scalper-v2/analytics-api.pid`, stdout/stderr to `analytics-api.log`. `setsid` puts the child in its own session + process group so `dev-down` can signal the whole group.
5. Sources nvm + `nvm use --delete-prefix v20.19.6` (per `ui_toolchain.md`), then `setsid nohup pnpm dev` in background; PID to `/tmp/scalper-v2/vite.pid`, log to `vite.log`.
6. Idempotent: if a PID file points at a running process, that step is skipped (re-running `dev-up.sh` is safe).

### `dev-down.sh`
1. Reads `/tmp/scalper-v2/{vite,analytics-api}.pid`; sends SIGTERM to the process group via `kill -- "-${pid}"` (correctly targets the `setsid`-created group, so child node workers / esbuild forks also exit); 5 s grace then SIGKILL if still alive.
2. `docker compose -f compose.yaml -f compose.dev.yaml stop postgres nats` (overlay must match `dev-up`; NOT `down` — preserves volumes; use `docker compose down -v` manually to wipe).
3. Removes the PID files.

### State files

```
/tmp/scalper-v2/
├── vite.pid             # Vite dev-server PID (parent shell wrapper)
├── vite.log             # Vite stdout/stderr
├── analytics-api.pid    # uvicorn worker PID
└── analytics-api.log    # uvicorn stdout/stderr
```

The `/tmp` location means logs + PIDs vanish on host reboot — intentional, replay-safe.

## Troubleshooting

**"vite already running — skipping" but I killed it manually.** Stale PID file: `rm /tmp/scalper-v2/vite.pid` and re-run `dev-up.sh`. Same recipe for analytics-api.

**Postgres unhealthy at startup.** Check `docker compose logs postgres`; usually port 5432 already taken (sibling v1 bot or system Postgres). Stop the conflicting service or change the published port in `compose.yaml`.

**Can't reach `http://192.168.100.100:5173/` from another PC on the LAN.** Verify the host's current LAN IP with `ip -4 addr show eno1` (could have shifted on DHCP renewal); ping the host from the remote PC; if ping works but browser doesn't, check firewall (`sudo ufw status` — port 5173 must be reachable on the LAN interface).

**HMR not picking up changes.** Confirm Vite is the native process (`ps -ef | grep vite`), not a stale containerized variant. Native HMR uses inotify directly; if you hit `inotify watch limit reached`, raise `fs.inotify.max_user_watches` (check `cat /proc/sys/fs/inotify/max_user_watches`).

**Tail logs for live debugging.** `tail -f /tmp/scalper-v2/vite.log` and `tail -f /tmp/scalper-v2/analytics-api.log`.

## Cross-references

- `ui_toolchain.md` (project memory) — Node 20.19.6 + pnpm 10.33.2 toolchain pinning, nvm-shim requirement.
- BRIEF §16.6 — LAN-only invariant (no public listener; backend stays loopback, Vite optionally LAN).
- `chore(devx) 868e35b` — Vite LAN-bind (host: 0.0.0.0); precedent for native Vite dev server.
- `compose.yaml` — full multi-service stack used in CI / production simulation; dev wrapper deliberately uses only `postgres` + `nats` from it.
