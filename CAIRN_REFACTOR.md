# Cairn Library Refactoring Plan

**Date:** 2026-02-16
**Status:** Draft
**Purpose:** Complete refactor of Cairn to leverage newly refactored FSdantic and Grail libraries

---

## Executive Summary

Cairn needs a complete refactor to take advantage of the newly redesigned FSdantic and Grail libraries. Both underlying libraries have been significantly simplified to reduce cognitive overhead, and Cairn should follow suit.

**Key Changes:**
- Replace old FSdantic API with new workspace-first API
- Replace old Grail/Monty integration with new `.pym` file-based approach
- Simplify agent code generation to produce inspectable `.pym` files
- Leverage new FSdantic managers (files, kv, overlay, materialize)
- Use Grail's pre-flight validation (`grail check`)
- Reduce cognitive overhead and complexity throughout

**Benefits:**
- Cleaner, more maintainable codebase
- Better developer experience with inspectable agent code
- Pre-flight validation catches errors before execution
- Simplified APIs throughout
- Better alignment with underlying library philosophies

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [FSdantic Integration Changes](#2-fsdantic-integration-changes)
3. [Grail Integration Changes](#3-grail-integration-changes)
4. [Agent Code Generation Redesign](#4-agent-code-generation-redesign)
5. [Orchestrator Refactoring](#5-orchestrator-refactoring)
6. [File Structure Changes](#6-file-structure-changes)
7. [Migration Path](#7-migration-path)
8. [Testing Strategy](#8-testing-strategy)
9. [Implementation Phases](#9-implementation-phases)

---

## 1. Current State Analysis

### 1.1 Current FSdantic Usage Issues

**Problem:** Using deprecated API patterns

```python
# Current (OLD API):
self.stable = await Fsdantic.open_with_options(
    AgentFSOptions(path=str(self.agentfs_dir / "stable.db"))
)

# Uses raw AgentFS instead of workspace managers
self.file_ops = FileOperations(agent_fs.raw, base_fs=stable_fs.raw)
```

**Impact:**
- Not using new workspace manager pattern
- Accessing `.raw` escape hatch unnecessarily
- Missing out on cleaner error handling
- Not leveraging batch APIs or new convenience methods

### 1.2 Current Grail Usage Issues

**Problem:** Using old Grail v1 MontyContext

```python
# Current (OLD API):
from grail import MontyContext

# Code generated as strings
code = await self.llm.generate(task)

# Complex MontyContext setup (13 parameters)
context = MontyContext(
    input_model=EmptyInput,
    tools=tools,
    limits=limits,
    # ... many more parameters
)
```

**Impact:**
- No IDE support for agent code (strings, not files)
- No pre-flight validation
- No inspectable generated code
- Complex setup process
- No type stubs for external functions
- Errors discovered only at runtime

### 1.3 Agent Code Generation Issues

**Problem:** LLM generates code as opaque strings

```python
# Current approach:
code = await llm.generate(task)  # Returns Python string
# Agent code is never written to disk
# No way to inspect what the agent will run
# No validation before execution
```

**Impact:**
- Debugging is difficult (no source file to inspect)
- No pre-flight validation
- LLM can generate invalid Monty code
- Users can't review agent code before execution
- No artifact preservation for debugging

### 1.4 Tool Registration Issues

**Problem:** Manual tool registration with duplicated signatures

```python
# Tools defined as methods:
async def read_file(self, path: str) -> str:
    ...

# Then manually registered
tools = [read_file, write_file, list_dir, ...]

# Signatures duplicated in prompt template
PROMPT_TEMPLATE = """Available tools:
- read_file(path: str) -> str
- write_file(path: str, content: str) -> bool
...
"""
```

**Impact:**
- Signature drift between implementation and documentation
- Manual maintenance burden
- No automatic type stub generation
- Monty doesn't get type information

---

## 2. FSdantic Integration Changes

### 2.1 Replace Workspace Opening

**Before:**
```python
self.stable = await Fsdantic.open_with_options(
    AgentFSOptions(path=str(self.agentfs_dir / "stable.db"))
)
```

**After:**
```python
self.stable = await Fsdantic.open(path=str(self.agentfs_dir / "stable.db"))
```

**Benefits:**
- Simpler API
- Workspace object has managers built-in
- No need for AgentFSOptions wrapper

### 2.2 Use Workspace Managers

**Before:**
```python
# Direct raw access
content = await agent_fs.raw.fs.read_file(path)
await agent_fs.raw.fs.write_file(path, content)

# Manual FileOperations setup
ops = FileOperations(agent_fs.raw, base_fs=stable_fs.raw)
```

**After:**
```python
# Use workspace.files manager
content = await agent_fs.files.read(path)
await agent_fs.files.write(path, content)

# Query support
results = await agent_fs.files.query(
    ViewQuery(path_pattern="**/*.py", include_stats=True)
)

# Built-in search
files = await agent_fs.files.search("**/*.txt")
```

**Benefits:**
- No need for FileOperations wrapper class
- Cleaner API surface
- Better error messages
- Type safety throughout

### 2.3 Use KV Managers

**Before:**
```python
# Manual repository creation
submission_repo = agent_fs.kv.repository(prefix="", model_type=SubmissionRecord)
await submission_repo.save(SUBMISSION_KEY, submission_record)
```

**After:**
```python
# Direct KV manager usage (same pattern, just cleaner)
repo = agent_fs.kv.repository(prefix="submissions:", model_type=SubmissionRecord)
await repo.save(agent_id, submission_record)

# Or simpler KV operations
await agent_fs.kv.set(f"submissions:{agent_id}", submission_data)
result = await agent_fs.kv.get(f"submissions:{agent_id}")
```

**Benefits:**
- Namespacing encourages better organization
- Batch operations available
- Cleaner repository pattern

### 2.4 Use Overlay Manager for Accept/Reject

**Before:**
```python
# Manual overlay manipulation
# (Current implementation likely does manual file copying)
```

**After:**
```python
# Accept agent changes
result = await stable.overlay.merge(
    agent_fs,
    strategy=MergeStrategy.OVERWRITE
)

# Preview changes first
changes = await stable.overlay.list_changes(agent_fs, path="/")

# Reject (reset overlay)
removed = await agent_fs.overlay.reset(paths=["/"])
```

**Benefits:**
- Built-in conflict detection
- Multiple merge strategies
- Clear change tracking
- Safer operations

### 2.5 Use Materialization Manager for Previews

**Before:**
```python
# Manual workspace materialization
# (Current implementation likely copies files manually)
```

**After:**
```python
# Generate preview workspace
preview_dir = self.cairn_home / "previews" / agent_id
result = await agent_fs.materialize.to_disk(
    preview_dir,
    base=stable,
    clean=True,
    allow_root=self.cairn_home / "previews"
)

# Or just compute diff
diff = await agent_fs.materialize.diff(stable)
for change in diff:
    print(f"{change.change_type}: {change.path}")
```

**Benefits:**
- Safe materialization with staging
- Automatic cleanup
- Change tracking
- Error recovery

### 2.6 Use Batch APIs

**New capability:**
```python
# Read multiple files efficiently
file_paths = ["/a.txt", "/b.txt", "/c.txt"]
results = await agent_fs.files.read_many(file_paths)

for item in results.items:
    if item.ok:
        print(f"{item.key_or_path}: {item.value}")
    else:
        print(f"Failed: {item.error}")

# Batch KV operations
kv_results = await agent_fs.kv.get_many(
    ["settings:theme", "settings:tz"],
    default="UTC"
)
```

**Benefits:**
- Better performance
- Deterministic ordering
- Graceful partial failures

---

## 3. Grail Integration Changes

### 3.1 Replace MontyContext with grail.load()

**Before:**
```python
from grail import MontyContext

context = MontyContext(
    input_model=EmptyInput,
    tools=tools,
    limits=limits,
    filesystem=...,
    # ... many more parameters
)

result = await context.execute_async(code)
```

**After:**
```python
import grail

# Load .pym file
script = grail.load(f".grail/agents/{agent_id}/task.pym")

# Check for errors
check_result = script.check()
if not check_result.valid:
    # Handle validation errors
    pass

# Run with inputs and externals
result = await script.run(
    inputs={"task_description": task},
    externals=external_functions,
)
```

**Benefits:**
- Simpler API (3 parameters vs 13)
- Pre-flight validation
- Inspectable code in `.pym` file
- Better error messages
- Type checking before execution

### 3.2 Use .pym Files for Agent Code

**Before:**
```python
# Code as string
code = """
files = await search_files("*.py")
for f in files:
    content = await read_file(f)
    # ... process
await submit_result(summary="Done", changed_files=[])
"""
```

**After:**
```python
# .grail/agents/{agent_id}/task.pym
from grail import external, Input
from typing import Any

# Inputs
task_description: str = Input("task_description")

# External functions
@external
async def read_file(path: str) -> str:
    """Read file from workspace."""
    ...

@external
async def write_file(path: str, content: str) -> bool:
    """Write file to workspace."""
    ...

@external
async def search_files(pattern: str) -> list[str]:
    """Search for files by pattern."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit task result."""
    ...

# Agent code (executable section)
files = await search_files("*.py")
for f in files:
    content = await read_file(f)
    # ... process

await submit_result(summary="Done", changed_files=[])

# Return value
{"status": "complete"}
```

**Benefits:**
- Full IDE support (syntax highlighting, autocomplete)
- Type checking
- Inspectable code
- Automatic stub generation
- Pre-flight validation
- Debugging support

### 3.3 Use grail check for Validation

**New capability:**
```python
# Before running agent
script = grail.load(pym_file)
check_result = script.check()

if not check_result.valid:
    for error in check_result.errors:
        print(f"Line {error.lineno}: {error.code} - {error.message}")
    # Transition to ERRORED state
    return

# Only run if validation passes
result = await script.run(...)
```

**Benefits:**
- Catch Monty incompatibilities before runtime
- Detect missing type annotations
- Find unused external functions
- Validate input declarations
- Better error messages

### 3.4 Simplify Resource Limits

**Before:**
```python
# Complex policy inheritance (if using old Grail v1)
policy = ResourcePolicy(...)
limits = policy.resolve()
```

**After:**
```python
# Simple dict
limits = {
    "max_memory": "16mb",
    "max_duration": "5s",
    "max_recursion": 200,
}

# Or use presets
limits = grail.STRICT  # or grail.DEFAULT, grail.PERMISSIVE

script = grail.load(pym_file, limits=limits)
```

**Benefits:**
- No complex inheritance
- Clear, explicit values
- Named presets for convenience

---

## 4. Agent Code Generation Redesign

### 4.1 Generate .pym Files Instead of Strings

**Before:**
```python
class CodeGenerator:
    async def generate(self, task: str) -> str:
        # Returns code as string
        prompt = self.PROMPT_TEMPLATE.format(task=task)
        response = self.model.prompt(prompt)
        return self.extract_code(response.text())
```

**After:**
```python
class CodeGenerator:
    async def generate_pym(self, task: str, agent_id: str) -> Path:
        """Generate .pym file for agent task.

        Returns:
            Path to generated .pym file
        """
        # Generate code using LLM
        prompt = self.build_pym_prompt(task)
        response = await self.model.prompt(prompt)
        code = self.extract_code(response.text())

        # Write to .pym file
        pym_file = Path(f".grail/agents/{agent_id}/task.pym")
        pym_file.parent.mkdir(parents=True, exist_ok=True)
        pym_file.write_text(code)

        return pym_file

    def build_pym_prompt(self, task: str) -> str:
        """Build prompt that generates valid .pym file."""
        return f"""Generate a .pym file for Monty to accomplish this task:
{task}

The .pym file must follow this structure:

```python
from grail import external, Input
from typing import Any

# Input
task_description: str = Input("task_description")

# External functions (copy these exactly)
@external
async def read_file(path: str) -> str:
    \"\"\"Read file from workspace.\"\"\"
    ...

@external
async def write_file(path: str, content: str) -> bool:
    \"\"\"Write file to workspace.\"\"\"
    ...

@external
async def list_dir(path: str = ".") -> list[str]:
    \"\"\"List directory contents.\"\"\"
    ...

@external
async def file_exists(path: str) -> bool:
    \"\"\"Check if file exists.\"\"\"
    ...

@external
async def search_files(pattern: str) -> list[str]:
    \"\"\"Search for files by glob pattern.\"\"\"
    ...

@external
async def search_content(pattern: str, path: str = ".") -> list[dict[str, Any]]:
    \"\"\"Search file contents by pattern.\"\"\"
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    \"\"\"Submit the task result.\"\"\"
    ...

@external
async def log(message: str) -> bool:
    \"\"\"Log a message.\"\"\"
    ...

# Your task code here (use the external functions)
# ...

# Must call submit_result at the end
await submit_result(summary="...", changed_files=[...])

# Return value (final expression)
{{"status": "complete"}}
```

Requirements:
- Start with grail imports
- Declare all external functions with @external decorator
- Write task code using only external functions
- Call submit_result() at the end
- No imports except from grail and typing
- No classes, generators, or with statements
- Final expression is the return value

Respond with ONLY the .pym file code.
"""
```

**Benefits:**
- Generated code is inspectable
- Can be reviewed before execution
- Preserved for debugging
- IDE can show it
- grail check can validate it

### 4.2 Two-Phase Generation: Template + LLM

**Alternative approach for more reliability:**

```python
class CodeGenerator:
    PYM_TEMPLATE = '''from grail import external, Input
from typing import Any

# Input
task_description: str = Input("task_description")

# External functions
@external
async def read_file(path: str) -> str:
    """Read file from workspace."""
    ...

@external
async def write_file(path: str, content: str) -> bool:
    """Write file to workspace."""
    ...

@external
async def list_dir(path: str = ".") -> list[str]:
    """List directory contents."""
    ...

@external
async def file_exists(path: str) -> bool:
    """Check if file exists."""
    ...

@external
async def search_files(pattern: str) -> list[str]:
    """Search for files by glob pattern."""
    ...

@external
async def search_content(pattern: str, path: str = ".") -> list[dict[str, Any]]:
    """Search file contents by pattern."""
    ...

@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    """Submit the task result."""
    ...

@external
async def log(message: str) -> bool:
    """Log a message."""
    ...

# Task code
{task_code}
'''

    async def generate_pym(self, task: str, agent_id: str) -> Path:
        # Generate only the task-specific code
        task_code = await self.generate_task_code(task)

        # Insert into template
        pym_content = self.PYM_TEMPLATE.format(task_code=task_code)

        # Write to file
        pym_file = Path(f".grail/agents/{agent_id}/task.pym")
        pym_file.parent.mkdir(parents=True, exist_ok=True)
        pym_file.write_text(pym_content)

        return pym_file
```

**Benefits:**
- External declarations are always correct
- LLM only generates task logic
- Less prone to LLM hallucination
- Consistent structure

---

## 5. Orchestrator Refactoring

### 5.1 Simplify Agent Execution Flow

**Before (complex):**
```python
async def _execute_agent(self, ctx: AgentContext):
    # Many state transitions
    # Manual code generation
    # Manual Monty setup
    # Manual error handling
    # Manual cleanup
```

**After (simplified):**
```python
async def _execute_agent(self, ctx: AgentContext):
    """Execute agent task through complete lifecycle."""
    try:
        # GENERATING phase
        await self._transition_state(ctx, AgentState.GENERATING)
        pym_file = await self.code_generator.generate_pym(
            ctx.task,
            ctx.agent_id
        )

        # EXECUTING phase
        await self._transition_state(ctx, AgentState.EXECUTING)
        script = grail.load(str(pym_file))

        # Check before running
        check_result = script.check()
        if not check_result.valid:
            await self._handle_validation_errors(ctx, check_result)
            return

        # Run the script
        external_functions = self._create_external_functions(ctx)
        result = await script.run(
            inputs={"task_description": ctx.task},
            externals=external_functions,
        )

        # SUBMITTING phase
        await self._transition_state(ctx, AgentState.SUBMITTING)
        await self._collect_submission(ctx)

        # REVIEWING phase
        await self._transition_state(ctx, AgentState.REVIEWING)
        await self._materialize_preview(ctx)

    except grail.ExecutionError as e:
        await self._handle_execution_error(ctx, e)
    except grail.InputError as e:
        await self._handle_input_error(ctx, e)
    except Exception as e:
        await self._handle_unexpected_error(ctx, e)
```

**Benefits:**
- Clear phase transitions
- Proper error handling per error type
- Validation before execution
- Cleaner code flow

### 5.2 Simplify External Function Creation

**Before:**
```python
def create_agent_tools(...) -> list[Callable]:
    # Many individual function definitions
    # Manual registration
    ext = CairnAgentTools(...)

    async def read_file(path: str) -> str:
        return await ext.read_file(...)

    # ... repeat for all tools

    return [read_file, write_file, ...]
```

**After:**
```python
def _create_external_functions(self, ctx: AgentContext) -> dict[str, Callable]:
    """Create external functions for agent execution."""
    agent_fs = ctx.agent_fs
    stable_fs = self.stable

    return {
        "read_file": lambda path: agent_fs.files.read(path),
        "write_file": lambda path, content: agent_fs.files.write(path, content),
        "list_dir": lambda path=".": agent_fs.files.list_dir(path, output="name"),
        "file_exists": lambda path: agent_fs.files.exists(path),
        "search_files": lambda pattern: agent_fs.files.search(pattern),
        "search_content": self._make_search_content_fn(agent_fs, stable_fs),
        "submit_result": self._make_submit_result_fn(ctx),
        "log": lambda msg: self._log_agent_message(ctx.agent_id, msg),
    }

def _make_search_content_fn(self, agent_fs, stable_fs):
    """Create search_content function with fallthrough."""
    async def search_content(pattern: str, path: str = ".") -> list[dict]:
        # Search agent workspace
        results = await agent_fs.files.query(
            ViewQuery(
                path_pattern=self._normalize_search_path(path),
                content_regex=pattern,
                recursive=True,
                include_content=True,
            )
        )
        # Return formatted results
        return [
            {"file": r.path, "line": ..., "text": ...}
            for r in results
        ]
    return search_content

def _make_submit_result_fn(self, ctx: AgentContext):
    """Create submit_result function for this agent."""
    async def submit_result(summary: str, changed_files: list[str]) -> bool:
        submission = SubmissionRecord(
            agent_id=ctx.agent_id,
            submission={"summary": summary, "changed_files": changed_files}
        )
        repo = ctx.agent_fs.kv.repository(
            prefix="submissions:",
            model_type=SubmissionRecord
        )
        await repo.save(ctx.agent_id, submission)
        return True
    return submit_result
```

**Benefits:**
- Simpler, more direct
- Uses workspace managers
- Clear separation of concerns
- Easier to test

### 5.3 Simplify Preview Generation

**Before:**
```python
# Manual file copying/materialization
```

**After:**
```python
async def _materialize_preview(self, ctx: AgentContext):
    """Generate preview workspace for review."""
    preview_dir = self.cairn_home / "previews" / ctx.agent_id

    result = await ctx.agent_fs.materialize.to_disk(
        preview_dir,
        base=self.stable,
        clean=True,
        allow_root=self.cairn_home / "previews"
    )

    if result.errors:
        # Log materialization errors
        for path, error in result.errors:
            print(f"Materialization error {path}: {error}")

    # Store preview metadata
    await ctx.agent_fs.kv.set(
        f"preview:{ctx.agent_id}",
        {
            "path": str(preview_dir),
            "files_written": result.files_written,
            "bytes_written": result.bytes_written,
        }
    )
```

**Benefits:**
- Uses built-in materialization
- Safe with staging
- Automatic error handling
- Metadata tracking

### 5.4 Simplify Accept/Reject

**Before:**
```python
async def accept_agent(self, agent_id: str):
    # Manual overlay merging
    # Manual cleanup
```

**After:**
```python
async def accept_agent(self, agent_id: str):
    """Accept agent changes and merge into stable."""
    ctx = self.active_agents.get(agent_id)
    if not ctx:
        raise KeyError(f"Agent {agent_id} not found")

    if ctx.state != AgentState.REVIEWING:
        raise ValueError(f"Agent {agent_id} not in REVIEWING state")

    # Merge changes into stable
    result = await self.stable.overlay.merge(
        ctx.agent_fs,
        strategy=MergeStrategy.OVERWRITE
    )

    if result.conflicts:
        # Log conflicts (shouldn't happen with OVERWRITE)
        for conflict in result.conflicts:
            print(f"Conflict: {conflict.path}")

    if result.errors:
        raise RuntimeError(f"Merge failed: {result.errors}")

    # Transition to ACCEPTED
    await self._transition_state(ctx, AgentState.ACCEPTED)

    # Cleanup
    await self._cleanup_agent(ctx)

async def reject_agent(self, agent_id: str):
    """Reject agent changes and discard overlay."""
    ctx = self.active_agents.get(agent_id)
    if not ctx:
        raise KeyError(f"Agent {agent_id} not found")

    if ctx.state != AgentState.REVIEWING:
        raise ValueError(f"Agent {agent_id} not in REVIEWING state")

    # Just transition and cleanup (overlay is discarded)
    await self._transition_state(ctx, AgentState.REJECTED)
    await self._cleanup_agent(ctx)
```

**Benefits:**
- Uses overlay manager
- Clear error handling
- Proper conflict detection
- Simple cleanup

---

## 6. File Structure Changes

### 6.1 New Directory Structure

```
cairn/
├── src/cairn/
│   ├── __init__.py
│   ├── orchestrator.py      # Simplified orchestrator
│   ├── agent.py              # Agent models (minimal changes)
│   ├── code_generator.py     # Generates .pym files
│   ├── external_functions.py # NEW: External function factory
│   ├── lifecycle.py          # Lifecycle management
│   ├── queue.py              # Task queue
│   ├── commands.py           # Command models
│   ├── settings.py           # Settings
│   ├── signals.py            # Signal handling
│   ├── watcher.py            # File watching
│   └── cli.py                # CLI
│
├── .grail/                   # NEW: Grail artifacts directory
│   └── agents/
│       └── {agent_id}/
│           ├── task.pym      # Generated agent code
│           ├── stubs.pyi     # Generated stubs
│           ├── check.json    # Validation results
│           ├── externals.json
│           ├── inputs.json
│           └── run.log
│
├── .agentfs/                 # AgentFS databases
│   ├── stable.db
│   ├── bin.db
│   └── agent-{id}.db
│
└── $CAIRN_HOME/              # ~/.cairn
    ├── previews/
    │   └── {agent_id}/       # Materialized previews
    ├── signals/
    └── state/
```

### 6.2 Removed Files

- `agent_tools.py` - Replaced by simpler external_functions.py
- `external_models.py` - No longer needed (using @external declarations)
- Old Grail/Monty integration code

### 6.3 New Files

- `external_functions.py` - Factory for creating external function dicts
- `.grail/` directory - Grail artifacts

---

## 7. Migration Path

### 7.1 Phase 1: Update Dependencies

```toml
# pyproject.toml
[project]
dependencies = [
    "fsdantic>=0.3.0",  # New version
    "grail>=2.0.0",     # New version
    "pydantic>=2.0.0",
    "llm>=0.13.0",
]
```

### 7.2 Phase 2: Update FSdantic Integration

1. Replace `Fsdantic.open_with_options()` with `Fsdantic.open()`
2. Replace `FileOperations` usage with `workspace.files`
3. Update KV operations to use `workspace.kv`
4. Update overlay operations to use `workspace.overlay`
5. Add materialization using `workspace.materialize`

### 7.3 Phase 3: Update Grail Integration

1. Remove `MontyContext` imports
2. Add `grail.load()` for .pym files
3. Update external function registration
4. Add `grail check` validation

### 7.4 Phase 4: Update Code Generation

1. Modify `CodeGenerator` to produce `.pym` files
2. Update prompts for .pym format
3. Add validation step
4. Store generated files in `.grail/`

### 7.5 Phase 5: Simplify Orchestrator

1. Refactor agent execution flow
2. Simplify external function creation
3. Update accept/reject logic
4. Improve error handling

### 7.6 Phase 6: Update Tests

1. Update test fixtures for new APIs
2. Add tests for .pym generation
3. Add tests for grail check integration
4. Add tests for new workspace manager usage
5. Update integration tests

---

## 8. Testing Strategy

### 8.1 Unit Tests

**New Test Files:**
- `test_external_functions.py` - Test external function factory
- `test_pym_generation.py` - Test .pym file generation
- `test_workspace_integration.py` - Test FSdantic workspace usage

**Updated Test Files:**
- `test_orchestrator.py` - Update for new execution flow
- `test_code_generator.py` - Update for .pym generation
- `test_agent_lifecycle.py` - Update for new APIs

### 8.2 Integration Tests

```python
# Test full agent lifecycle with new stack
async def test_agent_lifecycle_e2e():
    orch = CairnOrchestrator()
    await orch.initialize()

    # Queue task
    agent_id = await orch.spawn_agent("Add docstrings to public functions")

    # Wait for REVIEWING state
    await wait_for_state(orch, agent_id, AgentState.REVIEWING)

    # Verify .pym file exists
    pym_file = Path(f".grail/agents/{agent_id}/task.pym")
    assert pym_file.exists()

    # Verify grail check passed
    check_file = Path(f".grail/agents/{agent_id}/check.json")
    assert check_file.exists()
    check_data = json.loads(check_file.read_text())
    assert check_data["valid"] is True

    # Verify preview exists
    preview_dir = Path(f"~/.cairn/previews/{agent_id}").expanduser()
    assert preview_dir.exists()

    # Accept
    await orch.accept_agent(agent_id)

    # Verify state
    ctx = orch.active_agents.get(agent_id)
    assert ctx.state == AgentState.ACCEPTED
```

### 8.3 Validation Tests

```python
# Test that grail check catches errors
async def test_grail_check_catches_invalid_code():
    # Generate intentionally invalid .pym
    pym_file = Path(".grail/test/invalid.pym")
    pym_file.write_text("""
from grail import external

# Invalid: class definition
class Foo:
    pass
""")

    script = grail.load(str(pym_file))
    check_result = script.check()

    assert not check_result.valid
    assert any(e.code == "E001" for e in check_result.errors)
```

---

## 9. Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Update dependencies to new FSdantic and Grail
- [ ] Create new directory structure (.grail/)
- [ ] Update basic FSdantic usage (open, managers)
- [ ] Run existing tests, fix breaking changes

### Phase 2: Grail Integration (Week 2)
- [ ] Create external_functions.py
- [ ] Update CodeGenerator to produce .pym files
- [ ] Add grail.load() integration
- [ ] Add grail check validation
- [ ] Test .pym generation and execution

### Phase 3: Orchestrator Refactor (Week 3)
- [ ] Refactor agent execution flow
- [ ] Update accept/reject to use overlay manager
- [ ] Update preview generation to use materialize manager
- [ ] Improve error handling with new exception types
- [ ] Update lifecycle transitions

### Phase 4: Advanced Features (Week 4)
- [ ] Add batch API usage where beneficial
- [ ] Optimize file operations
- [ ] Add better logging and observability
- [ ] Performance testing and optimization

### Phase 5: Testing & Documentation (Week 5)
- [ ] Complete test coverage
- [ ] Update all documentation
- [ ] Update SPEC.md to reflect new architecture
- [ ] Add migration guide for users
- [ ] Create examples with new stack

### Phase 6: Polish & Release (Week 6)
- [ ] Code review and cleanup
- [ ] Performance benchmarks
- [ ] Final integration testing
- [ ] Documentation review
- [ ] Release v0.2.0

---

## 10. Benefits Summary

### 10.1 Developer Experience

**Before:**
- Agent code is invisible (strings)
- No IDE support
- Errors found at runtime
- Complex setup
- Difficult debugging

**After:**
- Agent code in .pym files (visible)
- Full IDE support
- Pre-flight validation
- Simple setup
- Easy debugging

### 10.2 Code Quality

**Before:**
- Manual tool registration
- Signature drift risk
- No type checking
- Complex abstractions

**After:**
- Declarative @external functions
- Automatic stub generation
- Type checking built-in
- Simple, clear APIs

### 10.3 Reliability

**Before:**
- Runtime errors
- No validation
- Manual error handling
- Complex state management

**After:**
- Pre-flight validation
- grail check catches issues
- Structured error hierarchy
- Simplified state management

### 10.4 Maintainability

**Before:**
- Complex orchestrator (500+ lines)
- Multiple abstraction layers
- Difficult to understand
- Hard to extend

**After:**
- Simplified orchestrator (~300 lines)
- Clear separation of concerns
- Easy to understand
- Simple to extend

---

## 11. Cognitive Overhead Reduction

### 11.1 Fewer Concepts

**Removed:**
- `AgentFSOptions` wrapper
- `FileOperations` manual setup
- `MontyContext` 13-parameter setup
- Manual tool registration
- String-based code generation
- Manual stub generation

**Added:**
- `Workspace` managers (files, kv, overlay, materialize)
- `grail.load()` simple API
- `.pym` file format
- `@external` decorator
- Pre-flight validation

**Net:** Fewer total concepts, each simpler

### 11.2 Clearer Mental Model

**Before:**
```
User → Orchestrator → CodeGenerator → LLM → String
                    ↓
                    MontyContext (13 params)
                    ↓
                    Monty execution
                    ↓
                    Manual cleanup
```

**After:**
```
User → Orchestrator → CodeGenerator → LLM → .pym file
                                            ↓
                                        grail.load()
                                            ↓
                                        grail.check()
                                            ↓
                                        script.run()
                                            ↓
                                        workspace managers
```

### 11.3 Better Error Messages

**Before:**
```
RuntimeError: Execution failed
```

**After:**
```
grail.ExecutionError: task.pym:22 — NameError: name 'undefined_var' is not defined

  20 |     total = sum(item["amount"] for item in items)
  21 |
> 22 |     if total > undefined_var:
  23 |         custom = await get_custom_budget(user_id=uid)

Context: This variable is not defined in the script and is not a declared Input().
```

---

## 12. Risk Mitigation

### 12.1 Risks

1. **Breaking Changes:** Complete API overhaul
2. **Migration Complexity:** Existing users need to update
3. **LLM Adaptation:** LLM needs to generate .pym format
4. **Testing Burden:** All tests need updates

### 12.2 Mitigation Strategies

1. **Version Bump:** Release as v0.2.0 or v2.0.0, clear breaking change
2. **Migration Guide:** Comprehensive documentation with examples
3. **LLM Testing:** Extensive testing of .pym generation prompts
4. **Incremental Testing:** Test each phase before moving to next
5. **Backward Compatibility:** Not a concern (Cairn is brand new)

---

## Conclusion

This refactor simplifies Cairn significantly while adding powerful capabilities. By leveraging the newly refactored FSdantic and Grail libraries, Cairn becomes:

- **Simpler** - Fewer concepts, clearer APIs
- **More Powerful** - Better validation, inspection, debugging
- **More Reliable** - Pre-flight checks, better error handling
- **More Maintainable** - Clean separation of concerns

The refactor aligns Cairn with the philosophy of its underlying libraries: **reduce cognitive overhead, make complexity visible, and provide simple, powerful primitives.**

**Recommendation:** Proceed with refactor. The benefits far outweigh the migration cost, especially given that Cairn is a new library with minimal existing users.
