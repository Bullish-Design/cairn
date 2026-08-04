# Cairn

Cairn is a workspace-aware orchestration runtime for sandboxed code execution with copy-on-write isolation and explicit human integration control.

## What is Cairn?

Cairn provides:
- **Safe execution of untrusted code** in sandboxed environments
- **Isolated workspace management** with copy-on-write overlays
- **Human-controlled integration** via explicit accept/reject gates
- **Pluggable code providers** for sourcing code from files, LLMs, git repos, registries, or custom sources
- **Preview environments** for inspecting changes before integration

## Use Cases

- **File-based task execution** - Run pre-written Python scripts in isolated bwrap sandboxes
- **LLM code generation** - Generate and execute code from natural language (via `cairn-llm` plugin)
- **Untrusted user scripts** - Execute user-submitted code safely
- **Preview environments** - Test code changes in isolation before merging
- **CI/CD workflows** - Run build/test scripts in sandboxed workspaces
- **Workspace inspection** - Read-only inspection of workspace contents via `WorkspaceInspector`
- **Agent state persistence** - Typed state management for agents via `AgentStateManager`

## Read this first (canonical docs)

1. **README.md** (this file): install + quickstart.
2. **[CONCEPT.md](docs/CONCEPT.md)**: philosophy and constraints.
3. **[SPEC.md](docs/SPEC.md)**: runtime architecture and contracts.
4. **[PROVIDERS.md](docs/PROVIDERS.md)**: code provider reference.
5. **[MIGRATION.md](docs/MIGRATION.md)**: V2 migration overview.
6. **[TESTING.md](docs/TESTING.md)**: repository test commands.
7. **[CHANGELOG.md](CHANGELOG.md)**: version history.

> **Source-of-truth note:** `docs/SPEC.md` defines runtime contracts; when implementation changes in `src/cairn/*`, update `docs/SPEC.md` in the same PR.

## Installation

```bash
uv sync --all-extras
```

## Quickstart

Run these commands from the repository root.

### Start the orchestrator

```bash
uv run cairn up
```

`cairn up` runs as a daemon that owns the databases; a second `cairn up` in
the same `CAIRN_HOME` is refused.  To run a single task without a daemon:

```bash
uv run cairn run scripts/task.py
```

### Queue work

**With file-based code provider (default):**
```bash
# Run a pre-written Python script
uv run cairn spawn scripts/refactor_imports.py
uv run cairn queue scripts/add_type_hints.py
```

**With LLM code provider (requires `cairn-llm` plugin):**
```bash
# Generate and execute code from natural language
uv run cairn spawn "Add docstrings to public functions" --provider llm
uv run cairn queue "Refactor watcher tests" --provider llm
```

### Inspect state

```bash
uv run cairn list-agents
uv run cairn status agent-<id>
```

### Resolve review

```bash
uv run cairn accept agent-<id>
# or
uv run cairn reject agent-<id>
```

Accept reports the merged file count and, when the sandbox deleted files in
stable, how many deletions were applied (fsdantic overlay tombstones).

## Sandbox deletions (tombstones)

Deletions made inside the sandbox are re-imported into the agent overlay as
**tombstones** (fsdantic >= 0.7.0 `overlay.tombstone`): files that exist only
in stable can be deleted by an agent, and the accept merge replays the
markers against stable (`MergeResult.tombstones_applied`).  A file
re-created in the overlay after deletion overrides its tombstone.

## Read-only inspection

Inspection commands (`cairn-cli workspace info`, `files list|read|search|tree`,
`preview changes|file`) open workspaces read-only (`readonly=True`), so they
never modify the databases they inspect.

## Development

- **Environment**: `devenv` (Nix) manages the toolchain; `devenv test` runs
the full gate: `uv lock --check`, `ruff check` + `ruff format --check`,
`ty check`, and pytest including the real bubblewrap sandbox tests with a
75% coverage floor.  `devenv.lock` and `uv.lock` are tracked for
reproducibility.
- **CI**: `.github/workflows/ci.yml` runs the same gate on push/PR, plus a
separate non-blocking benchmark job.  It runs identically on GitHub hosted
runners and locally with [`act`](https://github.com/nektos/act) (see
`.actrc` for the runner image, privileged mode, and the persisted `/nix`
volume; steady-state local runs ~2 minutes).
- **Benchmarks**: timing benchmarks are deselected by default
(`-m "not benchmark"`); run them with `pytest -m benchmark`, or strictly with
`CAIRN_STRICT_BENCHMARKS=1 pytest -m benchmark`.  CI runs them in a separate
`continue-on-error` job with relaxed thresholds so flakiness never blocks
merges.

## Contributing

- Workflow conventions: [AGENT.md](AGENT.md)
- Architecture and contracts: [CONCEPT.md](docs/CONCEPT.md), [SPEC.md](docs/SPEC.md)
- Tests and local validation: [TESTING.md](docs/TESTING.md)
