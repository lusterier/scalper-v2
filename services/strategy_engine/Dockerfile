# syntax=docker/dockerfile:1.9
#
# strategy-engine service image (§9.4, §18.1, ADR-0002).
#
# Two-stage:
#   1. `builder` — install the frozen workspace environment into
#      /app/.venv using the pinned uv CLI. No runtime Python imports yet.
#   2. `runtime` — slim image, non-root, no build tooling. uvicorn
#      launches the factory from services.strategy_engine.app.main.
#
# Image pins inherit execution-service's verified set (T-214, 2026-04-29;
# itself inherited from feature-engine T-109 / market-data-svc T-100 /
# signal-gateway T-015a): python:3.12.13-slim-trixie + ghcr.io/astral-sh/uv:0.11.7.
# Bare semver per §0.8 / §16.7 (digest pinning deferred per prior service
# task precedent).

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
# services.strategy_engine.app.main:create_app and must be copied
# explicitly — the strategy_engine subdir alone is not enough.
COPY packages/ ./packages/
COPY services/__init__.py ./services/__init__.py
COPY services/strategy_engine/ ./services/strategy_engine/

# --package scope keeps the image minimal: only scalper-v2-strategy-engine
# and its declared workspace + external deps. --no-dev excludes the
# dev-group (pytest, ruff, mypy, alembic, httpx).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package scalper-v2-strategy-engine --frozen --no-dev

# -----------------------------------------------------------------------------

FROM python:3.12.13-slim-trixie AS runtime

# Non-root service user. UID/GID 10005 — distinct from signal-gateway's
# 10001, market-data-svc's 10002, feature-engine's 10003, and execution-
# service's 10004 so per-service log attribution on a shared host bind
# mount (if F1+ adds one) does not conflate the services. The shared
# /var/log/scalper deferral from §18.1 still holds.
RUN groupadd --system --gid 10005 strategy \
 && useradd --system --uid 10005 --gid strategy --no-create-home --shell /usr/sbin/nologin strategy

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/packages /app/packages
COPY --from=builder /app/services/__init__.py /app/services/__init__.py
COPY --from=builder /app/services/strategy_engine /app/services/strategy_engine

# configs/bots/<bot_id>.yaml + configs/plugin_registry.yaml are read at
# startup per Settings.bot_config_dir + Settings.plugin_registry_path
# defaults. plugins/ ships entry_point dotted-paths resolved by
# importlib.import_module (§10.6); must be on sys.path[0] = /app.
# At current repo state both are .gitkeep-only — runtime fail surfaces
# only when BOT_ID is set without a corresponding YAML, which is the
# expected fail-fast (Pydantic ValidationError / FileNotFoundError).
COPY configs/ /app/configs/
COPY plugins/ /app/plugins/

USER strategy:strategy

EXPOSE 8000

# `python -m uvicorn` so sys.path[0] == /app and dotted imports resolve:
# services.strategy_engine.app.main is a regular package (services/ and
# services/strategy_engine/ both ship __init__.py). --factory tells
# uvicorn to call create_app() at startup. --loop uvloop per §3.1.
CMD ["python", "-m", "uvicorn", \
     "services.strategy_engine.app.main:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--loop", "uvloop"]
