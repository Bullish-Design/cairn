# Agent Instructions for Cairn Development

This document provides guidance for AI agents (like Claude, ChatGPT, or Cairn agents themselves!) working on the Cairn codebase.

## Project Overview

**Cairn** is an orchestration system for AI code agents that provides isolated workspace overlays, sandboxed execution, and explicit human control over integration.

### Key Components

1. **Cairn Orchestrator** (`src/cairn/`) - Agent spawning, execution, and management
2. **Neovim Plugin** (`src/cairn/nvim/`) - UI for reviewing and accepting agent changes
3. **Nix Modules** (`modules/`) - devenv.sh integration (optional)
4. **Documentation** - README, CONCEPT, SPEC, TESTING guides

### Technology Stack

- **Nix/devenv** - Development environment management
- **Python 3.11+** - Orchestrator and libraries
- **AgentFS** - SQLite-based filesystem with overlays
- **Monty** - Sandboxed Python interpreter
- **llm library** - Pluggable LLM providers
- **Neovim + Lua** - Editor integration
- **TMUX** - Workspace management
- **Jujutsu** - Version control integration

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
├── src/cairn/               # Main orchestrator library
│   ├── orchestrator.py      # Main process
│   ├── agent.py             # Agent state models
│   ├── queue.py             # Task queue
│   ├── executor.py          # Monty sandbox executor
│   ├── code_generator.py    # LLM-based code generation
│   ├── external_functions.py # Functions exposed to agents
│   ├── lifecycle.py         # Lifecycle storage
│   ├── workspace.py         # Preview materialization
│   ├── cli.py               # Command-line interface
│   ├── commands.py          # Command models
│   ├── settings.py          # Configuration
│   ├── signals.py           # Signal file handling
│   ├── watcher.py           # File system watching
│   └── nvim/                # Neovim plugin
│       ├── plugin/cairn.lua
│       ├── lua/cairn/
│       │   ├── init.lua
│       │   ├── commands.lua
│       │   ├── config.lua
│       │   ├── watcher.lua
│       │   ├── tmux.lua
│       │   └── ghost.lua
│       ├── doc/cairn.txt
│       └── tests/
│
├── modules/                 # Nix modules (optional)
│   ├── agentfs.nix
│   └── cairn.nix
│
├── tests/                   # Test suite
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── .context/                # Historical context (archived)
│
├── README.md
├── CONCEPT.md
├── SPEC.md
├── AGENT.md                 # This file
├── TESTING.md
├── devenv.nix
└── pyproject.toml
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

# 2. Enter devenv shell (if using Nix)
devenv shell

# 3. Install Python dependencies
uv sync

# 4. Run tests
pytest
```

### Running Tests

```bash
# All tests (see TESTING.md for details)
test

# Specific test categories
test-unit         # Unit tests only
test-integration  # Integration tests
test-performance  # Performance benchmarks
test-cov          # Full coverage report

# Neovim plugin tests
cd src/cairn/nvim/tests
nvim --headless -c "PlenaryBustedDirectory ."
```

### Code Style

**Python:**
- Follow PEP 8
- Use type hints everywhere
- Prefer async/await for I/O
- Use Pydantic for validation
- Max line length: 100 characters

**Lua:**
- Follow Neovim style guide
- Use `local` for all variables
- Prefer functional style
- Document exported functions
- Max line length: 100 characters

**Nix:**
- Follow nixpkgs conventions
- Use descriptive attribute names
- Comment complex expressions
- Keep modules focused

### Git Workflow

We use **Jujutsu** (jj), not git:

```bash
# Create a new change
jj new -m "feat: add workspace materialization"

# Edit files
# ...

# Describe change
jj describe -m "Add on-demand workspace materialization for agent previews"

# Squash into parent (if needed)
jj squash

# Create PR
jj git push --branch my-feature
```

## Subsystem Guides

For detailed information on specific subsystems, see:

### [SKILL-AGENTFS.md](docs/skills/SKILL-AGENTFS.md)

- AgentFS SDK usage
- Overlay semantics
- File operations
- KV store operations
- Performance considerations

### [SKILL-MONTY.md](docs/skills/SKILL-MONTY.md)

- Monty sandbox constraints
- External function interface
- Code generation prompts
- Error handling
- Security model

### [SKILL-TMUX.md](docs/skills/SKILL-TMUX.md)

- TMUX session management
- Pane layouts
- Neovim integration
- Programmatic control
- .tmuxp.yaml configuration

### [SKILL-NEOVIM.md](docs/skills/SKILL-NEOVIM.md)

- Plugin architecture
- Lua module structure
- FS event watching
- Ghost text rendering
- User commands

### [SKILL-JJ.md](docs/skills/SKILL-JJ.md)

- Jujutsu concepts
- Change management
- Agent-to-change mapping
- Squash/abandon operations
- Working copy materialization

### [SKILL-DEVENV.md](docs/skills/SKILL-DEVENV.md)

- Nix module structure
- Environment variables
- Process management
- Script helpers
- Imports and composition

## Common Pitfalls

### AgentFS

❌ **Don't:** Assume file exists in overlay
```python
content = await agent_fs.fs.read_file("file.txt")  # Might not exist
```

✅ **Do:** Check or handle exception
```python
try:
    content = await agent_fs.fs.read_file("file.txt")
except FileNotFoundError:
    # Fall back to stable or handle
    pass
```

### Monty

❌ **Don't:** Use stdlib functions in agent code
```python
# This will fail - no imports allowed
code = """
import json
data = json.loads(content)
"""
```

✅ **Do:** Provide as external function
```python
# Add to external_functions
def parse_json(text: str) -> dict:
    import json
    return json.loads(text)
```

### Neovim

❌ **Don't:** Block the event loop
```lua
while true do
    check_previews()  -- Blocks forever
end
```

✅ **Do:** Use timers or FS events
```lua
local timer = vim.loop.new_timer()
timer:start(0, 500, vim.schedule_wrap(check_previews))
```

### Jujutsu

❌ **Don't:** Assume git commands work
```bash
git commit -m "message"  # Wrong VCS
```

✅ **Do:** Use jj commands
```bash
jj describe -m "message"
```

## API Stability

### Stable APIs (Don't break without major version bump)

- **AgentFS SDK** - File operations, KV operations
- **Monty external functions** - Signature and behavior
- **Neovim commands** - `:Cairn*` commands
- **Environment variables** - `CAIRN_*`, `AGENTFS_*`

### Unstable APIs (Can change)

- Internal orchestrator functions
- Lua helper functions (not exported)
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
# tests/integration/test_agent_lifecycle.py

@pytest.mark.asyncio
async def test_full_agent_lifecycle():
    """Test agent from spawn to accept"""
    orch = CairnOrchestrator()
    await orch.initialize()

    # Spawn
    agent_id = await orch.spawn_agentlet("Add docstrings")
    assert agent_id in orch.active_agents

    # Wait for completion
    await asyncio.sleep(10)

    # Accept
    await orch.accept_agent(agent_id)
    assert agent_id not in orch.active_agents
```

### E2E Tests

```bash
#!/bin/bash
# tests/e2e/test_workflow.sh

# Start orchestrator
cairn up &
ORCH_PID=$!

# Queue task
nvim --headless -c "CairnQueue 'Add docstrings'" -c "qa"

# Wait for completion
sleep 5

# Check preview exists
test -d ~/.cairn/workspaces/agent-*

# Accept
nvim --headless -c "CairnAccept" -c "qa"

# Cleanup
kill $ORCH_PID
```

## Performance Guidelines

### Critical Paths

These must be optimized:

1. **File sync** (inotify → stable.db): <10ms
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
- Monty sandbox prevents filesystem access
- Overlays can't corrupt stable layer
- No SQL injection in AgentFS queries
- No shell injection in subprocess calls

### Checklist

When handling agent code or user input:

- [ ] Never use `eval()` or `exec()` on untrusted input
- [ ] Always use subprocess with list args, not shell strings
- [ ] Validate paths before filesystem operations
- [ ] Use Monty sandbox for all agent code execution
- [ ] Limit resource usage (time, memory, disk)

## Release Process

### Versioning

We use semantic versioning: MAJOR.MINOR.PATCH

- **MAJOR**: Breaking API changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes

### Checklist

Before releasing:

- [ ] All tests pass
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
- [ ] Version bumped in pyproject.toml
- [ ] Tagged in git/jj

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
- cairn/orchestrator.py: Added FileLock usage
- tests/integration/test_concurrent.py: Added test
```

❌ **Bad:**
```
Fixed a bug in the orchestrator.
```

## Appendix: Useful Commands

### Development

```bash
# Enter devenv (if using Nix)
devenv shell

# Run orchestrator
cairn up

# Run in foreground with debug logging
CAIRN_LOG_LEVEL=debug cairn up

# Check AgentFS status
agentfs-info
```

### Testing

```bash
# Run all tests
pytest

# Run specific test
pytest tests/unit/test_queue.py::test_priority

# Run with coverage
pytest --cov=cairn --cov-report=html

# Type checking
mypy cairn/
```

### Debugging

```bash
# Watch orchestrator logs
tail -f ~/.cairn/logs/orchestrator.log

# Inspect agent database
sqlite3 .agentfs/agent-abc123.db "SELECT * FROM tool_calls;"

# Check tmux sessions
tmux ls
tmux attach -t cairn

# LLM debugging
llm logs
```

---

**Remember:** You're working on a system designed to help developers collaborate with AI agents. The code you write will be executed by Monty, reviewed by humans, and iterated on by future agents (maybe even yourself!). Make it clear, make it safe, make it simple.

**Welcome to the pile. Add your stones carefully.**
