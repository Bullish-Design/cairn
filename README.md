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

- **File-based task execution** - Run pre-written `.pym` scripts in isolated workspaces
- **LLM code generation** - Generate and execute code from natural language (via `cairn-llm` plugin)
- **Untrusted user scripts** - Execute user-submitted code safely
- **Preview environments** - Test code changes in isolation before merging
- **CI/CD workflows** - Run build/test scripts in sandboxed workspaces

## Read this first (canonical docs)

1. **README.md** (this file): install + quickstart.
2. **[CONCEPT.md](docs/CONCEPT.md)**: philosophy and constraints.
3. **[SPEC.md](docs/SPEC.md)**: runtime architecture and contracts.
4. **[PROVIDERS.md](docs/PROVIDERS.md)**: code provider reference.
5. **[MIGRATION.md](docs/MIGRATION.md)**: V2 migration overview.
6. **[TESTING.md](docs/TESTING.md)**: repository test commands.

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

### Queue work

**With file-based code provider (default):**
```bash
# Run a pre-written .pym script
uv run cairn spawn scripts/refactor_imports.pym
uv run cairn queue scripts/add_type_hints.pym
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

## Contributing

- Workflow conventions: [AGENT.md](AGENT.md)
- Architecture and contracts: [CONCEPT.md](docs/CONCEPT.md), [SPEC.md](docs/SPEC.md)
- Tests and local validation: [TESTING.md](docs/TESTING.md)
