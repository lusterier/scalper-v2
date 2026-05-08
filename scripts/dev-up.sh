#!/usr/bin/env bash
# Bring up the dev stack: docker infra (postgres + nats) + analytics-api uvicorn
# + Vite dev server. Idempotent — safe to re-run; existing healthy components
# are left alone. PID files and logs in /tmp/scalper-v2/.
#
# Usage:
#   ./scripts/dev-up.sh
#
# Tear down with ./scripts/dev-down.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="/tmp/scalper-v2"
mkdir -p "${STATE_DIR}"

VITE_PID_FILE="${STATE_DIR}/vite.pid"
VITE_LOG_FILE="${STATE_DIR}/vite.log"
ANALYTICS_PID_FILE="${STATE_DIR}/analytics-api.pid"
ANALYTICS_LOG_FILE="${STATE_DIR}/analytics-api.log"

# Source .env if present so DSN derives from the same POSTGRES_PASSWORD that
# compose.dev.yaml substitutes. Fallback "devpass" matches the historical dev
# convention; still a single source of truth when .env is set.
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "${REPO_ROOT}/.env"
  set +a
fi
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-devpass}"
DATABASE_URL="postgresql://scalper:${POSTGRES_PASSWORD}@127.0.0.1:5432/scalper"
NATS_URL='nats://127.0.0.1:4222'

is_running() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null
}

echo "[dev-up] starting docker infra (postgres + nats)..."
cd "${REPO_ROOT}"
# Overlay compose.dev.yaml is REQUIRED — base compose.yaml does NOT publish
# 5432/4222 to host, uses Docker secrets for password, and bind-mounts
# /mnt/data which dev hosts don't have. Overlay flips to host-port-publish +
# .env-substituted password + named volumes per dev convention (matches
# F3/F4 smoke runbooks).
# --no-recreate: leave already-running containers untouched (config drift
# would otherwise drop live connections).
docker compose -f compose.yaml -f compose.dev.yaml up -d --no-recreate postgres nats >/dev/null
docker compose -f compose.yaml -f compose.dev.yaml ps postgres nats --format 'table {{.Service}}\t{{.Status}}'

echo "[dev-up] waiting for postgres + nats healthy..."
pg_healthy=0
nats_healthy=0
for _ in {1..30}; do
  pg_healthy=$(docker compose -f compose.yaml -f compose.dev.yaml ps postgres --format json 2>/dev/null | grep -c '"Health":"healthy"' || true)
  nats_healthy=$(docker compose -f compose.yaml -f compose.dev.yaml ps nats --format json 2>/dev/null | grep -c '"Health":"healthy"' || true)
  if [[ "${pg_healthy}" == "1" && "${nats_healthy}" == "1" ]]; then break; fi
  sleep 1
done
if [[ "${pg_healthy}" != "1" || "${nats_healthy}" != "1" ]]; then
  echo "[dev-up] ERROR: postgres or nats not healthy after 30s — check 'docker compose logs postgres nats'" >&2
  exit 1
fi

if is_running "${ANALYTICS_PID_FILE}"; then
  echo "[dev-up] analytics-api already running (pid $(cat "${ANALYTICS_PID_FILE}")) — skipping"
else
  echo "[dev-up] starting analytics-api uvicorn on 127.0.0.1:8000..."
  # setsid puts the child in its own session + process group so dev-down can
  # signal the whole group via `kill -- -PGID`. Plain `nohup ... &` in a
  # non-interactive script (job control off under set -e) leaves the child
  # in the script's PGID and orphans children on dev-down kill.
  DATABASE_URL="${DATABASE_URL}" NATS_URL="${NATS_URL}" \
    setsid nohup uv run uvicorn services.analytics_api.app.main:create_app \
    --factory --host 127.0.0.1 --port 8000 \
    >"${ANALYTICS_LOG_FILE}" 2>&1 &
  echo $! >"${ANALYTICS_PID_FILE}"
  echo "[dev-up] analytics-api pid $(cat "${ANALYTICS_PID_FILE}") (log: ${ANALYTICS_LOG_FILE})"
fi

if is_running "${VITE_PID_FILE}"; then
  echo "[dev-up] vite already running (pid $(cat "${VITE_PID_FILE}")) — skipping"
else
  echo "[dev-up] starting vite dev server on 0.0.0.0:5173..."
  # nvm + Node 20.19.6 + pnpm 10.33.2 toolchain per docs/runbooks/dev_stack.md.
  # shellcheck disable=SC1091
  . "${HOME}/.nvm/nvm.sh"
  nvm use --delete-prefix v20.19.6 >/dev/null
  cd "${REPO_ROOT}/ui"
  setsid nohup pnpm dev >"${VITE_LOG_FILE}" 2>&1 &
  echo $! >"${VITE_PID_FILE}"
  echo "[dev-up] vite pid $(cat "${VITE_PID_FILE}") (log: ${VITE_LOG_FILE})"
fi

# LAN interface name is operator convention (eno1 on this host); change here
# if the LAN interface differs. `hostname -I` enumerates docker bridge IPs
# alongside LAN, so order is non-deterministic — explicit interface beats
# heuristics.
LAN_IP="$(ip -4 -o addr show eno1 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
LAN_IP="${LAN_IP:-127.0.0.1}"

cat <<EOF

[dev-up] stack ready:
  postgres:        127.0.0.1:5432
  nats:            127.0.0.1:4222
  analytics-api:   http://127.0.0.1:8000   (log: ${ANALYTICS_LOG_FILE})
  vite (LAN):      http://${LAN_IP}:5173
  vite (local):    http://127.0.0.1:5173   (log: ${VITE_LOG_FILE})

Tear down with: ./scripts/dev-down.sh
EOF
