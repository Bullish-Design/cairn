# Cairn

Cairn is an orchestration runtime for AI code agents with isolated fsdantic workspaces and explicit human accept/reject control.

## Read this first (canonical docs)

1. **README.md** (this file): install + quickstart.
2. **[CONCEPT.md](CONCEPT.md)**: philosophy and constraints.
3. **[SPEC.md](SPEC.md)**: runtime architecture and contracts.
4. **[TESTING.md](TESTING.md)**: repository test commands.

> **Source-of-truth note:** `SPEC.md` defines runtime contracts; when implementation changes in `src/cairn/*`, update `SPEC.md` in the same PR.

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

```bash
uv run cairn spawn "Add docstrings to public functions"
# or normal-priority queueing
uv run cairn queue "Refactor watcher tests"
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
- Architecture and contracts: [CONCEPT.md](CONCEPT.md), [SPEC.md](SPEC.md)
- Tests and local validation: [TESTING.md](TESTING.md)
