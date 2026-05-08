#!/usr/bin/env bash
# Tear down the dev stack started by dev-up.sh: stop Vite + analytics-api
# native processes (via PID files), then stop postgres + nats compose
# services. Idempotent — missing PID files or already-stopped services
# are no-ops.
#
# Usage:
#   ./scripts/dev-down.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="/tmp/scalper-v2"

VITE_PID_FILE="${STATE_DIR}/vite.pid"
ANALYTICS_PID_FILE="${STATE_DIR}/analytics-api.pid"

stop_pid_file() {
  local label="$1"
  local pid_file="$2"
  if [[ ! -f "${pid_file}" ]]; then
    echo "[dev-down] ${label}: no pid file at ${pid_file} — skipping"
    return
  fi
  local pid
  pid="$(cat "${pid_file}")"
  if kill -0 "${pid}" 2>/dev/null; then
    echo "[dev-down] ${label}: stopping pid ${pid}..."
    # Kill the process group so child shells / node workers also exit.
    kill -- "-${pid}" 2>/dev/null || kill "${pid}" 2>/dev/null || true
    for _ in {1..10}; do
      if ! kill -0 "${pid}" 2>/dev/null; then break; fi
      sleep 0.5
    done
    if kill -0 "${pid}" 2>/dev/null; then
      echo "[dev-down] ${label}: pid ${pid} did not exit cleanly — sending SIGKILL"
      kill -9 "${pid}" 2>/dev/null || true
    fi
  else
    echo "[dev-down] ${label}: pid ${pid} not running — stale pid file"
  fi
  rm -f "${pid_file}"
}

stop_pid_file "vite" "${VITE_PID_FILE}"
stop_pid_file "analytics-api" "${ANALYTICS_PID_FILE}"

echo "[dev-down] stopping docker infra (postgres + nats)..."
cd "${REPO_ROOT}"
# Overlay required so we operate on the same compose project as dev-up.sh.
docker compose -f compose.yaml -f compose.dev.yaml stop postgres nats >/dev/null
docker compose -f compose.yaml -f compose.dev.yaml ps postgres nats --format 'table {{.Service}}\t{{.Status}}'

echo "[dev-down] done. Data volumes preserved (use 'docker compose down -v' to wipe)."
