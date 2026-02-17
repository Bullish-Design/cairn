# Cairn Domain Reorganization Guide (Proposal A + B)

This guide combines Proposal A (root grouping) and Proposal B (domain-focused source layout). It outlines a concrete target layout and the full set of updates needed to complete the reorganization.

## Target Structure

```
.
├── docs/
│   ├── CONCEPT.md
│   ├── SPEC.md
│   ├── PROVIDERS.md
│   ├── TESTING.md
│   ├── MIGRATION.md
│   └── CLI_README.md
├── extensions/
│   ├── cairn-git/
│   ├── cairn-llm/
│   └── cairn-registry/
├── scripts/
├── src/
│   └── cairn/
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── cli.py
│       │   └── commands.py
│       ├── orchestrator/
│       │   ├── __init__.py
│       │   ├── orchestrator.py
│       │   ├── lifecycle.py
│       │   ├── queue.py
│       │   └── signals.py
│       ├── providers/
│       │   ├── __init__.py
│       │   └── providers.py
│       ├── runtime/
│       │   ├── __init__.py
│       │   ├── agent.py
│       │   ├── external_functions.py
│       │   └── settings.py
│       ├── watcher/
│       │   ├── __init__.py
│       │   └── watcher.py
│       └── nvim/
├── stubs/
├── tests/
├── README.md
├── pyproject.toml
├── devenv.nix
├── devenv.yaml
└── uv.lock
```

## Step-by-Step Reorganization Plan

1. Create `docs/` and move documentation files.
2. Create `extensions/` and move `cairn-*` extension directories.
3. Create domain folders under `src/cairn/`.
4. Move Python modules into domain folders.
5. Add `__init__.py` files for new packages.
6. Update Python import paths in `src/` and `tests/`.
7. Update CLI entry points and any console script config.
8. Update docs and diagrams to match new paths.
9. Run tests and fix any path-related failures.

## Files and References to Update

### Root-Level Moves

- Move docs into `docs/`:
  - `CLI_README.md`
  - `CONCEPT.md`
  - `MIGRATION.md`
  - `PROVIDERS.md`
  - `SPEC.md`
  - `TESTING.md`
- Move extensions into `extensions/`:
  - `cairn-git/`
  - `cairn-llm/`
  - `cairn-registry/`

### Source Tree Moves

- `src/cairn/cli.py` -> `src/cairn/cli/cli.py`
- `src/cairn/commands.py` -> `src/cairn/cli/commands.py`
- `src/cairn/orchestrator.py` -> `src/cairn/orchestrator/orchestrator.py`
- `src/cairn/lifecycle.py` -> `src/cairn/orchestrator/lifecycle.py`
- `src/cairn/queue.py` -> `src/cairn/orchestrator/queue.py`
- `src/cairn/signals.py` -> `src/cairn/orchestrator/signals.py`
- `src/cairn/providers.py` -> `src/cairn/providers/providers.py`
- `src/cairn/agent.py` -> `src/cairn/runtime/agent.py`
- `src/cairn/external_functions.py` -> `src/cairn/runtime/external_functions.py`
- `src/cairn/settings.py` -> `src/cairn/runtime/settings.py`
- `src/cairn/watcher.py` -> `src/cairn/watcher/watcher.py`
- Keep `src/cairn/nvim/` in place

### New Package Initializers

- `src/cairn/cli/__init__.py`
- `src/cairn/orchestrator/__init__.py`
- `src/cairn/providers/__init__.py`
- `src/cairn/runtime/__init__.py`
- `src/cairn/watcher/__init__.py`

### Python Import Updates

Search and update import paths for moved modules in:

- `src/cairn/**/*.py`
- `tests/**/*.py`
- `scripts/**`
- `extensions/**`

Expected import changes include:

- `from cairn.cli import ...` -> `from cairn.cli.cli import ...`
- `from cairn.commands import ...` -> `from cairn.cli.commands import ...`
- `from cairn.orchestrator import ...` -> `from cairn.orchestrator.orchestrator import ...`
- `from cairn.lifecycle import ...` -> `from cairn.orchestrator.lifecycle import ...`
- `from cairn.queue import ...` -> `from cairn.orchestrator.queue import ...`
- `from cairn.signals import ...` -> `from cairn.orchestrator.signals import ...`
- `from cairn.providers import ...` -> `from cairn.providers.providers import ...`
- `from cairn.agent import ...` -> `from cairn.runtime.agent import ...`
- `from cairn.external_functions import ...` -> `from cairn.runtime.external_functions import ...`
- `from cairn.settings import ...` -> `from cairn.runtime.settings import ...`
- `from cairn.watcher import ...` -> `from cairn.watcher.watcher import ...`

### Entry Points and Tooling

Update any references that point at moved modules, including:

- `pyproject.toml` console scripts or entrypoints
- `devenv.nix`/`devenv.yaml` if they reference module paths
- `scripts/**` shell or Python helpers

### Documentation Updates

Update any file references in:

- `README.md`
- `docs/CONCEPT.md`
- `docs/SPEC.md`
- `docs/PROVIDERS.md`
- `docs/TESTING.md`
- `docs/CLI_README.md`
- `docs/MIGRATION.md`

## Optional Follow-Ups

- Add a short `docs/README.md` index if the docs set grows.
- Add a `src/cairn/README.md` with module ownership and entry points.
- Add a small script in `scripts/` to validate expected layout.
