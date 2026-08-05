# Agent Instructions for Cairn Development

This document provides guidance for AI agents (like Claude, ChatGPT, or Cairn agents themselves!) working on the Cairn codebase.

## Project Overview

**Cairn** is a workspace-aware orchestration runtime for sandboxed code execution that provides isolated workspace overlays, pluggable code providers, and explicit human control over integration.

### Key Components

1. **Cairn Orchestrator** (`src/cairn/orchestrator/`) - Task orchestration, execution, and lifecycle management
2. **Code Providers** (`src/cairn/providers/providers.py`) - Pluggable code sourcing (file, inline, LLM, git, registry)
3. **Sandbox Runtime** (`src/cairn/runtime/sandbox/`) - BwrapExecutor (materialize → sandbox run → re-import) and the sandbox API (`boot.py`)
4. **Workspace APIs** (`src/cairn/runtime/`) - open_workspace, WorkspaceInspector, AgentStateManager, WorkspaceManager
5. **CLI** (`src/cairn/cli/`) - Thin-client `cairn` (argparse); socket transport + lifecycle mirror
6. **Documentation** - README, CONCEPT, SPEC, PROVIDERS, MIGRATION, and the agent skills in `.agents/skills/`

### Technology Stack

- **Nix/devenv** - Development environment management
- **Python 3.13+** - Orchestrator and libraries
- **FSdantic 0.7+** - Type-safe workspace management with overlay semantics and tombstones
- **bubblewrap** - Kernel-namespace sandbox for stock CPython execution
- **BwrapExecutor** - Materialize → sandbox run → re-import changeset
- **Turso (libSQL/pyturso)** - WAL/MVCC-concurrent workspace storage
- **pydantic / pydantic-settings / typer / rich / watchfiles** - models, config, CLIs, file watching
- **Git** - version control (jj is no longer used; see Git Workflow)

**Optional plugins:**
- **cairn-git** - Git repository code sourcing via `GitCodeProvider`
- **cairn-registry** - Registry-based code sourcing via `RegistryCodeProvider`

## Development Philosophy

### Design Principles

1. **Simple beats complex** - Copy instead of merge, files instead of abstractions
2. **Composable layers** - Storage, execution, orchestration are independent
3. **Safe by default** - Sandboxing, overlays, validation
4. **Performance matters** - <1s agent spawn, <100ms preview open
5. **MVP first** - Prove concept before adding features

### What We Value

- **Clarity** - Code should be obvious, not clever
- **Testability** - Small functions, clear interfaces
- **Documentation** - Explain *why*, not just *what*
- **Pragmatism** - Ship working code, refactor later

### What We Avoid

- **Over-engineering** - Don't build for hypothetical future needs
- **Magic** - No hidden state, implicit behavior, or action-at-a-distance
- **Complexity** - When in doubt, simpler

## Codebase Structure

```
cairn/
├── src/cairn/               # Main orchestrator library (V2 layout)
│   ├── cli/
│   │   ├── cli.py           # argparse CLI (thin client: signals + mirror)
│   │   ├── typer_cli.py     # Typer CLI (workspace/files/agent/preview groups)
│   │   └── commands.py      # typed command models + parse/dispatch
│   ├── core/
│   │   ├── constants.py     # all magic numbers / limits
│   │   ├── exceptions.py    # typed error hierarchy with error codes
│   │   └── types.py         # TypedDicts (SubmissionData, ...)
│   ├── orchestrator/
│   │   ├── orchestrator.py  # CairnOrchestrator: lifecycle + accept/reject/undo
│   │   ├── lifecycle.py     # LifecycleStore, records, mirror, retention
│   │   ├── queue.py         # priority TaskQueue
│   │   ├── signals.py       # SignalHandler + write_signal
│   │   ├── daemon.py        # pidfile claim/liveness
│   │   └── orchestrator_helpers.py
│   ├── providers/
│   │   └── providers.py     # CodeProvider protocol, file/inline, entry points
│   ├── runtime/
│   │   ├── agent.py         # AgentState, AgentContext, VALID_TRANSITIONS
│   │   ├── settings.py      # Orchestrator/Executor/Paths settings (CAIRN_* env)
│   │   ├── workspace_manager.py  # open_workspace, WorkspaceManager
│   │   ├── inspection.py    # WorkspaceInspector, WorkspaceStats
│   │   ├── state.py         # AgentStateManager (namespaced KV)
│   │   ├── workspace_cache.py    # LRU workspace cache with pinning
│   │   └── sandbox/
│   │       ├── sandbox.py   # BwrapExecutor: materialize → run → re-import
│   │       └── boot.py      # sandbox API + rlimits (shipped into the sandbox)
│   ├── utils/
│   │   ├── retry.py         # with_retry decorator, RetryStrategy
│   │   └── error_formatting.py
│   ├── repo.py             # ProjectFilter, capture_manifest, materialize
│   ├── driver.py           # WorkspaceCapability, IterativeDriver, ProjectView
│   └── integration.py      # IntegrationLock (flock)
│
├── .agentfs/                # Metadata databases (never a repo mirror)
│   ├── bin.db               # lifecycle KV + undo snapshots + journal
│   └── agent-{id}.db        # per-agent metadata KV (run record, submission)
│
├── tests/
│   ├── cairn/               # unit-ish tests (async)
│   │   └── integration/     # end-to-end / real-sandbox tests
│   └── fixtures/            # sample project trees
│
├── .agents/
│   ├── AGENTS.md            # this file
│   └── skills/              # agent skills (SKILL.md per directory)
│
├── .context/                # Historical context (archived)
│
├── docs/                    # CONCEPT.md, SPEC.md, PROVIDERS.md, MIGRATION.md,
│                            # TESTING.md, CLI_README.md, REFACTOR_PLAN.md
├── README.md
├── devenv.nix               # Nix/dev shell + sandbox runtime declaration
├── pyproject.toml
└── uv.lock
```

## Common Tasks

### Adding a New Feature

1. **Read relevant SKILL docs** - Understand the subsystem
2. **Check SPEC.md** - Ensure feature aligns with architecture
3. **Write tests first** - TDD when possible
4. **Implement minimally** - MVP, then iterate
5. **Update docs** - README, SPEC, SKILL guides as needed

### Fixing a Bug

1. **Reproduce** - Write failing test
2. **Locate** - Use SKILL docs to understand subsystem
3. **Fix** - Minimal change
4. **Verify** - Test passes, no regressions
5. **Document** - Add comment explaining *why* if non-obvious

### Refactoring

1. **Tests first** - Ensure behavior is covered
2. **Small steps** - One refactor at a time
3. **No behavior changes** - Refactor OR feature, not both
4. **Verify** - Tests still pass
5. **Update docs** - If interfaces changed

## Development Workflow

### Setting Up

```bash
# 1. Clone repository
git clone <repo-url>
cd cairn

# 2. Enter devenv shell
#    (declares bubblewrap + the sandbox interpreter runtime, CAIRN_EXECUTOR_*)
devenv shell

# 3. Install Python dependencies
uv sync --all-extras

# 4. Run tests
uv run pytest
```

### Running Tests

```bash
# Python tests (from the repo root)
uv run pytest

# Targeted: orchestrator + workspace flow
uv run pytest tests/cairn/test_orchestrator.py tests/cairn/test_workspace.py

# Repo snapshots + driver + orchestrator
uv run pytest tests/cairn/test_repo.py tests/cairn/test_driver.py tests/cairn/test_orchestrator.py

# Performance-marked tests (deselected by default)
uv run pytest -m benchmark tests/cairn/test_performance.py

# The full repository gate (lockfile, ruff, ty, pytest + coverage floor)
devenv test
```

### Code Style

**Python:**
- Follow PEP 8 (ruff, line length 120)
- Use type hints everywhere
- Prefer async/await for I/O
- Use Pydantic for validation
- Magic numbers live in `src/cairn/core/constants.py`; typed errors with
  error codes in `src/cairn/core/exceptions.py`

**Nix:**
- Follow nixpkgs conventions
- Use descriptive attribute names
- Comment complex expressions
- Keep modules focused

### Git Workflow

The repository uses **git** (a `.git` directory is tracked):

```bash
# Create a branch / feature change
git checkout -b feat/workspace-materialization

# Stage and commit
git add src tests
git commit -m "feat: add on-demand workspace materialization for agent previews"

# Push and open a PR
git push -u origin feat/workspace-materialization
```

## Subsystem Guides (skills)

Agent skills live in `.agents/skills/<name>/SKILL.md` (Agent Skills format:
`name` + `description` frontmatter; pi discovers directories containing
`SKILL.md`, root-level `.md` files are ignored). Skills load on demand — the
description tells an agent when to read the full file. See also the cross-links
at the bottom of each skill.

- [cairn-architecture](skills/cairn-architecture/SKILL.md) — mental model,
  layers, data layout, lifecycle, invariants. Read first when orienting.
- [cairn-task-code](skills/cairn-task-code/SKILL.md) — writing sandbox task
  scripts (the 9 sandbox helpers, limits, submission contract).
- [cairn-code-providers](skills/cairn-code-providers/SKILL.md) — CodeProvider
  protocol, built-ins, plugin entry points.
- [cairn-library-api](skills/cairn-library-api/SKILL.md) — open_workspace,
  WorkspaceInspector, AgentStateManager, TaskQueue, embedding the orchestrator.
- [cairn-operations](skills/cairn-operations/SKILL.md) — daemon, CLI
  commands, signals/mirror, accept/reject/undo, troubleshooting.
- [cairn-contribution](skills/cairn-contribution/SKILL.md) — the dev gate,
  tests, conventions, security model, pitfalls when modifying the repo.

## Common Pitfalls

### FSdantic Workspaces

❌ **Don't:** Use old API patterns
```python
# Old API (deprecated)
content = await agent_fs.raw.fs.read_file("file.txt")
ops = FileOperations(agent_fs.raw, base_fs=stable_fs.raw)
```

✅ **Do:** Use workspace managers
```python
# New API
content = await agent_fs.files.read("file.txt")
results = await agent_fs.files.query(ViewQuery(path_pattern="**/*.py"))
```

### Sandbox execution (bwrap)

❌ **Don't:** Execute agent code in-process
```python
# Runs untrusted code inside the orchestrator — no isolation
exec(provider_code)
```

✅ **Do:** Run through the sandbox executor
```python
# Materialize → sandbox run → re-import changeset
executor = BwrapExecutor(agent_id=..., workdir=..., agent_fs=..., stable=..., settings=...)
result = await executor.run(code=code, task=task)
```

The sandbox runs stock CPython (stdlib only — no site-packages inside the
sandbox); the sandbox API (`read_file`, `write_file`, `list_dir`,
`file_exists`, `delete_file`, `search_files`, `search_content`,
`submit_result`, `log`) is injected as globals by the bootstrap script.
Imports are allowed — what the code can import is limited by the read-only
runtime mounts, not by a language subset.

### Async/event loop

❌ **Don't:** Block the event loop with sync file/db I/O
```python
content = path.read_bytes()  # blocking in async context
```

✅ **Do:** Delegate blocking I/O to a thread
```python
content = await asyncio.to_thread(path.read_bytes)
```

### Version control

❌ **Don't:** Assume jj commands work
```bash
jj describe -m "message"  # jj is not used in this repo
```

✅ **Do:** Use git commands
```bash
git commit -m "message"
```

## API Stability

### Stable APIs (Don't break without major version bump)

- **Workspace APIs** - open_workspace, WorkspaceManager, WorkspaceInspector, AgentStateManager
- **Sandbox API** - read_file/write_file/list_dir/file_exists/delete_file/search_files/search_content/submit_result/log
- **CLI commands** - `cairn up/run/spawn/queue/list-agents/status/accept/reject/undo/logs`; `cairn` groups
- **Environment variables** - `CAIRN_*` (including `CAIRN_ORCHESTRATOR_`, `CAIRN_EXECUTOR_`, `CAIRN_PATHS_` prefixes)
- **CodeProvider protocol** - `get_code` / `validate_code`

### Unstable APIs (Can change)

- Internal orchestrator functions
- Signal file payload format (transport is stable, payload may evolve)
- Config file format (until 1.0)
- CLI output format

## Documentation Standards

### Code Comments

```python
def materialize_workspace(agent_id: str) -> Path:
    """Copy agent overlay to disk for preview/testing.

    Creates a directory at ~/.cairn/workspaces/{agent_id}/ containing
    all files from the agent's overlay. Unchanged files are hardlinked
    to stable layer for efficiency.

    Args:
        agent_id: Agent UUID

    Returns:
        Path to materialized workspace

    Raises:
        AgentNotFoundError: If agent_id doesn't exist
    """
```

### README Updates

When adding features visible to users:

1. Update Quick Start if workflow changes
2. Add to "Features" or "Usage" section
3. Include code example
4. Update Configuration section if new options

### SPEC Updates

When changing architecture:

1. Update relevant diagram
2. Update data flow
3. Update performance targets
4. Update security model if applicable

## Testing Guidelines

### Unit Tests

```python
# tests/unit/test_queue.py

import pytest
from cairn.queue import TaskQueue, AgentTask, TaskPriority

@pytest.mark.asyncio
async def test_enqueue_dequeue():
    """Test basic queue operations"""
    queue = TaskQueue(mock_stable_fs)

    task = AgentTask(
        id="task-1",
        description="Add docstrings",
        priority=TaskPriority.NORMAL,
        created_at=time.time()
    )

    await queue.enqueue(task)
    next_task = await queue.dequeue()

    assert next_task.id == "task-1"
```

### Integration Tests

```python
# tests/cairn/integration/test_e2e_workflows.py

@pytest.mark.asyncio
async def test_full_agent_lifecycle(tmp_path):
    """Test agent from spawn to accept"""
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        config=OrchestratorSettings(max_concurrent_agents=1),
        code_provider=InlineCodeProvider(),
        executor_factory=lambda **kw: StubExecutor("hello.py", "done", **kw),
    )
    await orch.initialize()

    # Spawn
    agent_id = await orch.spawn_agent("Add docstrings")

    # Wait for REVIEWING
    await _wait_for_state(orch, agent_id, {AgentState.REVIEWING})

    # Accept
    await orch.accept_agent(agent_id)
    assert await orch.stable.files.exists("hello.py")

    await orch.shutdown()
```

### E2E Tests

```bash
#!/bin/bash
# tests/cairn/integration/test_e2e_workflows.py is the in-suite equivalent.
# For a manual end-to-end check against the CLI:

# Start the daemon
cairn up &
ORCH_PID=$!

# Queue a task (socket request; result returns synchronously)
cairn queue scripts/task.py

# Wait for it to reach REVIEWING
sleep 5

# Inspect
cairn list-agents
cairn status agent-<id>

# Accept
cairn accept agent-<id>

# Cleanup
kill $ORCH_PID
```

## Performance Guidelines

### Critical Paths

These must be optimized:

1. **Manifest capture + materialize**: proportional to tree size (CoW where
   supported)
2. **Agent spawn**: <1s
3. **Preview open**: <100ms
4. **Accept/reject**: <50ms

### Non-Critical Paths

These can be slower:

1. **Code generation** (LLM): <5s is fine
2. **Workspace materialization**: <500ms is fine
3. **GC scan**: <10ms is fine but runs infrequently

### Optimization Checklist

- [ ] Use async for I/O operations
- [ ] Avoid blocking the event loop
- [ ] Cache expensive operations
- [ ] Use indexes for database queries
- [ ] Profile before optimizing

## Security Guidelines

### Threat Model

**Assume:**
- Agent-generated code is malicious
- LLM output is attacker-controlled
- User files may contain injection attempts

**Ensure:**
- bwrap sandbox prevents filesystem/network access
- Overlays can't corrupt stable layer
- No SQL injection in AgentFS queries
- No shell injection in subprocess calls

### Checklist

When handling agent code or user input:

- [ ] Never use `eval()` or `exec()` on untrusted input
- [ ] Always use subprocess with list args, not shell strings
- [ ] Validate paths before filesystem operations
- [ ] Use the bwrap sandbox for all agent code execution
- [ ] Limit resource usage (time, memory, disk)

## Release Process

### Versioning

We use semantic versioning: MAJOR.MINOR.PATCH

- **MAJOR**: Breaking API changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes

### Checklist

Before releasing:

- [ ] All tests pass (devenv test)
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
- [ ] Version bumped in pyproject.toml
- [ ] Tagged in git
- [ ] Published to PyPI (cairn only)

## Getting Help

### Questions?

1. Check relevant SKILL guide
2. Check SPEC.md for architecture
3. Check CONCEPT.md for philosophy
4. Search existing issues
5. Ask in discussions

### Found a Bug?

1. Check if already reported
2. Provide minimal reproduction
3. Include environment info
4. Include error messages/logs

### Want a Feature?

1. Check if already requested
2. Describe use case (not just solution)
3. Explain why current approach doesn't work
4. Consider if it fits project philosophy

## Specific Guidance for AI Agents

### When Reading This Codebase

1. **Start with CONCEPT.md** - Understand the philosophy
2. **Then SPEC.md** - Understand the architecture
3. **Then relevant SKILL docs** - Understand specific subsystems
4. **Finally code** - With context from docs

### When Writing Code

1. **Read tests first** - Understand expected behavior
2. **Check existing patterns** - Follow established conventions
3. **Keep changes small** - One logical change at a time
4. **Document non-obvious** - Explain *why*, not *what*

### When Fixing Bugs

1. **Reproduce first** - Write failing test
2. **Understand root cause** - Don't just patch symptoms
3. **Fix minimally** - Change only what's necessary
4. **Verify** - Run full test suite

### When Adding Features

1. **Check SPEC** - Does it fit architecture?
2. **MVP first** - Simplest version that works
3. **Tests** - Write tests before or alongside code
4. **Docs** - Update README, SPEC, relevant SKILL doc

### Communication Style

When reporting work:

✅ **Good:**
```
Fixed workspace materialization race condition.

Problem: Multiple agents materializing simultaneously caused
file corruption due to non-atomic directory creation.

Solution: Added file locking around workspace creation.
Tested with 10 concurrent materializations.

Files changed:
- src/cairn/orchestrator/orchestrator.py: Added FileLock usage
- tests/cairn/integration/test_concurrent.py: Added test
```

❌ **Bad:**
```
Fixed a bug in the orchestrator.
```

## Appendix: Useful Commands

### Development

```bash
# Enter devenv
devenv shell

# Run the daemon
cairn up

# Run a single task inline (no daemon)
cairn run scripts/task.py

# Full gate (lockfile, lint, types, tests + coverage)
devenv test
```

### Testing

```bash
# Run all tests
uv run pytest

# Run a specific test
uv run pytest tests/cairn/test_orchestrator.py::test_accept_agent_flow

# Run with coverage
uv run pytest --cov=cairn --cov-report=html

# Type checking
uv run ty check
```

### Debugging

```bash
# Read an agent's sandbox run log
cairn logs agent-<id>

# The log file on disk (kept for errored agents)
cat ~/.cairn/workspaces/agent-<id>/.cairn/run.log

# Inspect an agent overlay read-only via the Typer CLI
cairn preview changes agent-<id>
cairn files list agent-<id>

# Inspect the lifecycle mirror (CLI query path)
cat ~/.cairn/state/lifecycle.json

# Daemon pidfile
cat ~/.cairn/state/orchestrator.pid
```

---

**Remember:** You're working on a system designed to help developers collaborate with AI agents. The code you write will be executed inside a bwrap sandbox, reviewed by humans, and iterated on by future agents (maybe even yourself!). Make it clear, make it safe, make it simple.

**Welcome to the pile. Add your stones carefully.**
