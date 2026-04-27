# syntax=docker/dockerfile:1.9
#
# execution-service image (§9.5, §18.1, ADR-0002).
#
# Two-stage:
#   1. `builder` — install the frozen workspace environment into
#      /app/.venv using the pinned uv CLI. No runtime Python imports yet.
#   2. `runtime` — slim image, non-root, no build tooling. uvicorn
#      launches the factory from services.execution.app.main.
#
# Image pins inherit feature-engine's verified set (T-109, 2026-04-26;
# itself inherited from market-data-svc T-100, 2026-04-25; signal-gateway
# T-015a, 2026-04-22): python:3.12.13-slim-trixie + ghcr.io/astral-sh/uv:0.11.7.
# Bare semver per §0.8 / §16.7 (digest pinning deferred per T-009/T-012/
# T-013a/T-014/T-015a/T-100/T-109 precedent).

FROM python:3.12.13-slim-trixie AS builder

# uv donor image — bare-semver tag matched to github.com/astral-sh/uv
# latest. Only /uv + /uvx are forwarded; the stage is otherwise clean.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Workspace metadata first so the resolve step caches cleanly on
# source-only edits.
COPY pyproject.toml uv.lock ./

# packages/ carries __init__.py + every member's source (recursive COPY
# includes the repo-root packages/__init__.py; ADR-0002 "surprise 2").
# services/__init__.py is load-bearing for the runtime import path
# services.execution.app.main:create_app and must be copied
# explicitly — the execution subdir alone is not enough.
COPY packages/ ./packages/
COPY services/__init__.py ./services/__init__.py
COPY services/execution/ ./services/execution/

# --package scope keeps the image minimal: only scalper-v2-execution
# and its declared workspace + external deps. --no-dev excludes the
# dev-group (pytest, ruff, mypy, alembic, httpx).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package scalper-v2-execution --frozen --no-dev

# -----------------------------------------------------------------------------

FROM python:3.12.13-slim-trixie AS runtime

# Non-root service user. UID/GID 10004 — distinct from signal-gateway's
# 10001, market-data-svc's 10002, and feature-engine's 10003 so per-
# service log attribution on a shared host bind mount (if F1+ adds one)
# does not conflate the services. The shared /var/log/scalper deferral
# from §18.1 still holds.
RUN groupadd --system --gid 10004 execution \
 && useradd --system --uid 10004 --gid execution --no-create-home --shell /usr/sbin/nologin execution

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/packages /app/packages
COPY --from=builder /app/services/__init__.py /app/services/__init__.py
COPY --from=builder /app/services/execution /app/services/execution

USER execution:execution

EXPOSE 8000

# `python -m uvicorn` so sys.path[0] == /app and dotted imports resolve:
# services.execution.app.main is a regular package (services/ and
# services/execution/ both ship __init__.py). --factory tells
# uvicorn to call create_app() at startup. --loop uvloop per §3.1.
CMD ["python", "-m", "uvicorn", \
     "services.execution.app.main:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--loop", "uvloop"]
