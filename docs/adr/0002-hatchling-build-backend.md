# ADR-0002: Use hatchling as the workspace build backend

Status: accepted
Date: 2026-04-19
Deciders: operator, Claude Code

## Context

§3.1 names `uv` as the package manager and dependency resolver for the monorepo but leaves the PEP 517 build backend unspecified. The workspace is declared at the repo root via `[tool.uv.workspace]` with members in `services/*` and `packages/*`, and uses a flat layout (`packages/core/__init__.py`, `packages/observability/__init__.py`, …) rather than the `src/` convention.

T-006 (`packages/observability`) was the first workspace member to declare external runtime dependencies (`structlog`, `prometheus-client`). Local mypy then failed to import those modules, which surfaced the underlying gap: `uv sync --group dev --frozen` installs only root-project dependencies, not the runtime dependencies of workspace members. Making `uv sync --all-packages --group dev` work requires that each member with deps be *installable* — which, in PEP 517 terms, means it must declare a `[build-system]`. Every future member with external deps (T-007 asyncpg, T-008 nats-py, and the services in later phases) will hit the same wall; picking a backend once now avoids solving the same problem three more times.

Options considered:
- `hatchling`
- `setuptools`
- `poetry-core`

## Decision

Use **`hatchling`** as the PEP 517 build backend for every workspace member that declares external dependencies (starting with `packages/core` and `packages/observability`). Members with no external deps stay "virtual" (no `[build-system]`) until their first external dep is added.

## Rationale

- **`uv` default.** Choosing the tool's own default minimises configuration drift and means `uv init` / `uv build` behave as documented without per-project overrides.
- **PEP 621 native.** Metadata (`name`, `version`, `description`, `dependencies`, …) stays in the single `[project]` table already used by each member — no parallel `[tool.*]` metadata block to keep in sync.
- **Zero-config for flat layout.** For `packages/observability/__init__.py` the only configuration needed is a single `[tool.hatch.build.targets.wheel] packages = ["observability"]` line (and analogous for `core`); hatchling then builds a wheel that installs the module at the expected import path. Verified empirically per §0.8 by building a wheel with `uv build` and inspecting it with `python -m zipfile -l dist/*.whl` before merge.
- **Installable-but-minimal.** Adding `[build-system]` only where it is needed (i.e., where the member has external runtime deps) keeps the repo's build footprint as small as the current scale demands, while leaving the same pattern ready to apply to future members without another ADR.

## Consequences

Positive:
- `uv sync --all-packages --group dev --frozen` installs each workspace member as an editable wheel, so runtime imports (`import structlog`, `import prometheus_client`) resolve during local mypy and in CI without further glue.
- The same pattern — one `[build-system]` stanza + one `[tool.hatch.build.targets.wheel] packages = […]` line — copies verbatim to every future member that gains external deps (T-007, T-008, services).
- No JVM, no extra tooling process: hatchling is a pure-Python backend already pulled in transitively by `uv`.

Negative / trade-offs:
- Each member that grows a runtime dep must now remember to add the three-line `[build-system]` + hatchling packages stanza; forgetting it is a silent "virtual member" regression. Mitigated by the pattern being uniform and the CI-fast `uv sync --all-packages` failing loudly if a declared dep can't be resolved for a member that ought to be installable.
- We are coupled to hatchling's specific quirks around flat-layout discovery; if we later move to a `src/` layout or adopt a different backend, every member's `pyproject.toml` changes. Considered acceptable because the change is mechanical and the current flat layout is the brief's chosen shape.
- `uv build` of the full workspace now produces one wheel per installable member, slightly increasing CI artefact surface; not shipped anywhere in F0 so the cost is local only.

## Alternatives considered

- **`setuptools`.** Ubiquitous and well-understood; every Python developer has touched it. Rejected: its modern PEP 621 configuration still carries legacy edges around flat-layout auto-discovery (`packages = find:` vs `[tool.setuptools.packages.find]`), needs more boilerplate than hatchling for the same result, and is not uv's default — choosing it would mean deliberately swimming against the tool's grain for no gain at our scale.

- **`poetry-core`.** A reasonable, modern backend with a mature ecosystem around it. Rejected: it assumes Poetry-flavored pyproject.toml shape (`[tool.poetry]` metadata tables) that conflicts with the PEP 621 `[project]` tables `uv` expects and that we already use across every member. Mixing the two styles inside one workspace would be a constant source of drift; picking hatchling keeps every member on a single, standard metadata schema.

## Scaffold pattern for future members

Amendment date: 2026-04-19. The simpler sketch in the "Rationale" bullet about "a single `[tool.hatch.build.targets.wheel] packages = […]` line" was approximate; the authoritative pattern every member copies is below. Two surprises surfaced during T-007 (`packages/db`, the first member with a subpackage) and are captured here so future members do not re-derive them.

### Exact pyproject snippet

Every workspace member under `packages/<name>/` uses this scaffold verbatim, edited only for `name`/`description`/`dependencies`/`[tool.uv.sources]`:

```toml
[project]
name = "scalper-v2-<name>"
version = "0.1.0"
description = "<one-line>"
requires-python = ">=3.12"
dependencies = [
    # runtime deps; use "scalper-v2-core" etc. for workspace deps
]

[tool.uv.sources]
# one line per workspace dep:
# scalper-v2-core = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

# Hatch scaffold per ADR-0002: flat-layout source files live directly in
# packages/<name>/; sources remap adds the `packages/<name>/` wheel prefix
# so imports resolve as `from packages.<name> import ...`. Repo-root
# packages/__init__.py is excluded from every member wheel (PEP 420 at
# install time). dev-mode-dirs = ["."] bypasses hatchling's editable-mode
# detection, which trips on sources-remap + subpackages (pfmoore/editables#20).
[tool.hatch.build.targets.wheel]
include = ["*.py", "py.typed"]
exclude = ["tests/**", "**/__pycache__/**"]
dev-mode-dirs = ["."]

[tool.hatch.build.targets.wheel.sources]
"" = "packages/<name>"
```

### Why `dev-mode-dirs = ["."]` (surprise 1)

The sources-remap semantics are "ADD a prefix to every wheel path" — the key `""` matches all source files, and the value `"packages/<name>"` is the prefix placed in front of each file's wheel path. Without this, files like `__init__.py` and `pool.py` would land at the wheel root; with it, they land at `packages/<name>/__init__.py` etc., which is what `from packages.<name> import ...` expects.

Hatchling's editable-mode detection (`build_editable_detection` at `hatchling/builders/wheel.py:540-566`) is incompatible with sources-remap-adds-prefix once a member has a subpackage. For each included file it computes `relative_path` (project-root-relative, e.g. `queries/__init__.py` in `packages/db/`) and `distribution_path` (wheel path, `packages/db/queries/__init__.py` — longer because the sources remap prepends). Single-component paths (`__init__.py`, `pool.py`) short-circuit on line 545 and pass. Multi-component paths fall through to line 553, which calls `relative_path.index(distribution_path)`. Because `distribution_path` is strictly longer than `relative_path`, the substring search always raises `ValueError`, which hatchling re-raises as:

> `Dev mode installations are unsupported when any path rewrite in the "sources" option changes a prefix rather than removes it, see: https://github.com/pfmoore/editables/issues/20`

Flat-only members (`packages/core`, `packages/observability`) never hit this because every file is single-component. The first subpackage (`packages/db/queries/`) does.

`dev-mode-dirs = ["."]` routes the build through `build_editable_explicit` (`wheel.py:610-638`) instead, which writes a single `.pth` file containing the member's project-root directory (e.g. `/home/luster/scalper-v2/packages/db`) and skips detection entirely. The `.pth` value is **not** functionally used for imports — those resolve via the repo-root `packages/__init__.py` discussed below — but hatchling needs a value to honour its editable-build contract. Applying the setting uniformly across all three current members keeps the pattern consistent so the next member with a subpackage (T-008 `packages/bus/handlers/`, T-014 `packages/features/builtin/`, …) does not hit the wall in isolation.

### Why repo-root `packages/__init__.py` stays (surprise 2)

`packages/__init__.py` at the repo root (one-line docstring, no code) is retained deliberately. It is not in any member wheel — every member's `include = ["*.py", …]` glob is evaluated relative to that member's project root (`packages/<name>/`), so the repo-root file is outside every glob. Verified on T-007 close-out via `python -m zipfile -l dist/scalper_v2_{core,observability,db}-*.whl`; none of the three wheels contain `packages/__init__.py`.

At install time, this means `packages` is a PEP 420 namespace package in `site-packages/` — exactly the behaviour this ADR specified in the original Rationale.

At dev time, the file is load-bearing. `pytest` collects each test file by walking up through consecutive `__init__.py` directories and stopping at the first one that lacks one; it then inserts that directory into `sys.path` and constructs the test module name from the walk. With `packages/__init__.py` present, the walk stops at the repo root, the repo root is inserted, and `from packages.<name>.<module> import …` in any test or collected source resolves normally. Without it, the walk stops at `packages/` itself, `packages/` (not its parent) is inserted, and `from packages.<name> import …` becomes unfindable — removal triggers collection errors `ModuleNotFoundError: No module named 'packages'` across every package's tests. Verified empirically during the T-007 close-out investigation.

Summary: regular package at dev tree, PEP 420 namespace at install time. Do not delete the repo-root file. Do not include it in any member wheel.

## Follow-up tasks

- T-006 unpark: once T-020 merges to master, `git stash pop` resumes T-006 with the observability modules + a now-working `uv sync --all-packages --group dev --frozen`; the stashed code carries the `[build-system]` stanza for `packages/observability` that T-020 establishes, so the resume is a no-conflict pop-and-finish. (Resolved in T-006.)
- T-007 (`packages/db`, asyncpg pool factory): first member with a subpackage; surfaced the two amendments above. Closes with `dev-mode-dirs` applied to all three current members so the pattern is correct at source for every future copy-paste.
- T-008 (`packages/bus`, NATS client wrapper): copies the snippet under "Scaffold pattern for future members" verbatim, adjusts `name`/`description`/`dependencies`/`[tool.uv.sources]` only.
- Future services (T-015 signal-gateway and later): each adopts the same snippet at the point it declares its first external runtime dep.
