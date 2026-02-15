# Cairn Refactoring Plan: Built on Fsdantic and Grail

## Executive Summary

This document outlines the complete refactoring of Cairn from its current implementation to a simpler, more powerful architecture built on top of two foundational libraries:

- **fsdantic**: Workspace-first, async Python library providing type-safe, Pydantic-based interface for AgentFS
- **grail**: Pydantic-native wrapper around Monty for executing untrusted Python code in sandboxed environments

The refactoring will significantly simplify Cairn's codebase by leveraging built-in functionality from these libraries, while maintaining all core features and improving type safety, observability, and extensibility.

**Key Benefits:**
- 40-60% reduction in custom code
- Better type safety throughout with Pydantic models
- Built-in observability and metrics
- Resumable execution for long-running agents
- More robust error handling
- Cleaner separation of concerns

---

## Current Architecture Overview

### Current Cairn Components

**Storage Layer:**
- Direct `agentfs-sdk` usage with manual database operations
- Custom KV models (`kv_models.py`) for metadata
- Manual overlay management
- Custom workspace materialization

**Execution Layer:**
- Direct `pydantic-monty` usage
- Manual external function injection
- Custom resource limit enforcement
- Manual validation and error handling

**Orchestration Layer:**
- `orchestrator.py`: Agent lifecycle management
- `queue.py`: Priority-based task queue
- `code_generator.py`: LLM-based code generation
- `executor.py`: Sandboxed execution wrapper
- `lifecycle.py`: Lifecycle state persistence
- `workspace.py`: Workspace materialization
- `watcher.py`: File system sync
- `signals.py`: Signal file handling
- `commands.py`: Command models
- `external_functions.py`: Agent API functions

### Current Dependencies
- `agentfs-sdk`: Low-level AgentFS operations
- `fsdantic`: Some high-level operations (OverlayOperations, Materializer)
- `pydantic-monty`: Sandboxed execution
- `llm`: LLM provider abstraction
- `watchfiles`: Filesystem watching
- `pydantic-settings`: Configuration

---

## New Architecture Overview

### New Cairn Components (Post-Refactor)

**Storage Layer:**
- **fsdantic Workspace**: Single abstraction for all storage
  - `workspace.files`: All file operations
  - `workspace.kv`: All metadata storage
  - `workspace.overlay`: Overlay management
  - `workspace.materialize`: Preview and diff

**Execution Layer:**
- **grail MontyContext**: Complete sandboxed execution
  - Tool registry for agent capabilities
  - Resource policies and limits
  - Type-safe input/output with Pydantic
  - Built-in observability and metrics
  - Resumable execution support

**Orchestration Layer (Simplified):**
- `orchestrator.py`: Simplified lifecycle management
- `queue.py`: Task queue (minimal changes)
- `agent_tools.py`: Tool definitions for grail (replaces external_functions.py)
- `code_generator.py`: LLM code generation (updated prompts)
- `lifecycle.py`: Using TypedKVRepository (simplified)
- `watcher.py`: Workspace sync (updated for fsdantic)
- `signals.py`: Signal handling (minimal changes)
- `commands.py`: Command models (minimal changes)

**Removed Components:**
- `executor.py`: Replaced by grail MontyContext
- `external_functions.py`: Replaced by agent_tools.py with grail
- `kv_models.py`: Replaced by TypedKVRepository
- `workspace.py`: Replaced by workspace.materialize

---

## Detailed Refactoring Plan

### Phase 1: Replace Execution Layer with Grail

**Goal:** Replace `pydantic-monty` and `executor.py` with grail's `MontyContext`

#### 1.1 Create Agent Tools Registry (`agent_tools.py`)

**Current:** `external_functions.py` defines functions injected into Monty namespace

**New:** Define Pydantic-based tools for grail's tool registry

```python
# agent_tools.py
from pydantic import BaseModel, Field
from grail import Tool, ToolRegistry
from fsdantic import Workspace

# Input/Output models
class ReadFileInput(BaseModel):
    path: str = Field(description="Path to file to read")

class ReadFileOutput(BaseModel):
    content: str
    success: bool = True

class WriteFileInput(BaseModel):
    path: str
    content: str

class WriteFileOutput(BaseModel):
    success: bool = True

# ... similar for other operations

# Tool implementations using workspace
def create_agent_tools(workspace: Workspace) -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register_tool(
        name="read_file",
        description="Read a file from the workspace",
        input_model=ReadFileInput,
        output_model=ReadFileOutput
    )
    async def read_file(input: ReadFileInput) -> ReadFileOutput:
        content = await workspace.files.read(input.path)
        return ReadFileOutput(content=content)

    @registry.register_tool(
        name="write_file",
        description="Write a file to the workspace",
        input_model=WriteFileInput,
        output_model=WriteFileOutput
    )
    async def write_file(input: WriteFileInput) -> WriteFileOutput:
        await workspace.files.write(input.path, input.content)
        return WriteFileOutput()

    # ... register other tools: list_dir, file_exists, search_files,
    # search_content, ask_llm, submit_result, log

    return registry
```

**Benefits:**
- Type-safe inputs/outputs with Pydantic validation
- Automatic error handling by grail
- Observable tool calls (metrics, logging)
- Easy to extend with new tools

#### 1.2 Replace Executor with MontyContext

**Current:** `executor.py` wraps pydantic_monty with custom limits

**New:** Use grail's MontyContext with resource policies

```python
# In orchestrator.py or new execution.py module
from grail import MontyContext, ResourcePolicy, FilesystemPermissions
from grail.models import ExecutionInput, ExecutionOutput

async def execute_agent_code(
    agent_id: str,
    code: str,
    workspace: Workspace,
    settings: Settings
) -> ExecutionOutput:
    # Create tool registry for this agent
    tools = create_agent_tools(workspace)

    # Configure resource policy
    policy = ResourcePolicy(
        max_execution_time=settings.AGENT_TIMEOUT_SECONDS,
        max_memory_bytes=settings.AGENT_MAX_MEMORY,
        max_recursion_depth=1000
    )

    # Configure filesystem permissions (read-only, sandbox only)
    fs_perms = FilesystemPermissions(
        allow_read=False,  # No direct file access
        allow_write=False,
        allow_execute=False
    )

    # Create context
    ctx = MontyContext(
        tool_registry=tools,
        resource_policy=policy,
        filesystem_permissions=fs_perms,
        enable_metrics=True,
        enable_logging=True
    )

    # Execute
    input_data = ExecutionInput(code=code)
    result = await ctx.execute(input_data)

    return result
```

**Benefits:**
- Built-in resource enforcement (no manual limits)
- Automatic metrics collection
- Type-safe execution results
- Resumable execution support for future use
- Better error reporting

#### 1.3 Update Code Generator Prompts

**Current:** Prompts mention specific external functions by name

**New:** Update to reference grail tools

```python
# code_generator.py - update prompt template
AGENT_PROMPT_TEMPLATE = """
You are a Python code agent. Write code to accomplish this task:

{task}

Available tools (call via tool registry):
- read_file(path: str) -> str
- write_file(path: str, content: str) -> None
- list_dir(path: str) -> list[str]
- file_exists(path: str) -> bool
- search_files(pattern: str) -> list[str]
- search_content(pattern: str, path: str = ".") -> list[dict]
- ask_llm(question: str) -> str
- log(message: str) -> None
- submit_result(summary: str, changed_files: list[str]) -> None

Requirements:
- Use tools from the registry (already available in scope)
- No imports allowed
- Must call submit_result() when done
- Keep code simple and focused

Return only Python code.
"""
```

**Changes Required:**
- `code_generator.py`: Update prompt template
- Add validation for grail tool calls instead of function calls

---

### Phase 2: Leverage Fsdantic Workspace Abstraction

**Goal:** Replace direct agentfs-sdk usage with fsdantic Workspace

#### 2.1 Replace Lifecycle Store with TypedKVRepository

**Current:** `lifecycle.py` uses custom `TypedKVRepository` from `kv_models.py`

**New:** Use fsdantic's built-in `TypedKVRepository`

```python
# lifecycle.py - refactored
from fsdantic import Workspace, TypedKVRepository
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class LifecycleRecord(BaseModel):
    agent_id: str
    task: str
    priority: int
    state: str
    created_at: datetime
    updated_at: datetime
    db_path: Optional[str] = None
    submission: Optional[dict] = None
    error: Optional[str] = None

class LifecycleStore:
    def __init__(self, workspace: Workspace):
        self.repo: TypedKVRepository[LifecycleRecord] = workspace.kv.typed(
            model=LifecycleRecord,
            prefix="lifecycle:"
        )

    async def save(self, record: LifecycleRecord) -> None:
        await self.repo.put(record.agent_id, record)

    async def get(self, agent_id: str) -> Optional[LifecycleRecord]:
        return await self.repo.get(agent_id)

    async def list_all(self) -> list[LifecycleRecord]:
        return await self.repo.list()

    async def delete(self, agent_id: str) -> None:
        await self.repo.delete(agent_id)
```

**Benefits:**
- No custom KV implementation needed
- Type-safe with Pydantic validation
- Automatic serialization/deserialization
- Built-in batch operations
- Query support if needed

#### 2.2 Use Workspace for Overlay Operations

**Current:** Manual overlay creation and merging via agentfs-sdk

**New:** Use `workspace.overlay` operations

```python
# In orchestrator.py
async def create_agent_overlay(
    agent_id: str,
    stable_workspace: Workspace
) -> Workspace:
    # Create overlay workspace
    overlay = await stable_workspace.overlay.create(
        overlay_id=agent_id,
        description=f"Agent {agent_id} workspace"
    )
    return overlay

async def merge_agent_changes(
    agent_id: str,
    stable_workspace: Workspace
) -> None:
    # Merge overlay into stable
    await stable_workspace.overlay.merge(
        overlay_id=agent_id,
        conflict_strategy="overlay_wins"  # Agent changes take precedence
    )

async def discard_agent_changes(
    agent_id: str,
    stable_workspace: Workspace
) -> None:
    # Delete overlay
    await stable_workspace.overlay.delete(overlay_id=agent_id)
```

**Benefits:**
- Clean overlay lifecycle management
- Built-in conflict resolution strategies
- Change tracking and diff support
- Less error-prone than manual operations

#### 2.3 Use Workspace Materialization

**Current:** Custom `workspace.py` with manual file copying

**New:** Use `workspace.materialize` for preview

```python
# Replace workspace.py entirely
async def materialize_agent_workspace(
    agent_id: str,
    overlay_workspace: Workspace,
    preview_dir: Path
) -> None:
    # Materialize overlay to disk for preview
    await overlay_workspace.materialize.to_disk(
        target_path=preview_dir,
        include_patterns=["**/*"],
        exclude_patterns=[".git/**", "__pycache__/**"]
    )

async def get_agent_diff(
    agent_id: str,
    overlay_workspace: Workspace,
    stable_workspace: Workspace
) -> str:
    # Get unified diff between overlay and stable
    diff = await overlay_workspace.materialize.diff(
        other=stable_workspace,
        format="unified"
    )
    return diff
```

**Benefits:**
- Robust file copying with error handling
- Built-in diff generation
- Pattern-based filtering
- Less custom code

#### 2.4 Update File Watcher

**Current:** `watcher.py` syncs to stable.db via agentfs-sdk

**New:** Use workspace.files for syncing

```python
# watcher.py - refactored
from fsdantic import Workspace
from watchfiles import awatch

class FileWatcher:
    def __init__(self, workspace: Workspace, project_root: Path):
        self.workspace = workspace
        self.project_root = project_root

    async def watch(self):
        async for changes in awatch(
            self.project_root,
            ignore_paths=[".agentfs", ".git", "__pycache__", "node_modules"]
        ):
            for change_type, path in changes:
                rel_path = Path(path).relative_to(self.project_root)

                if change_type == "added" or change_type == "modified":
                    # Read from disk and write to workspace
                    content = Path(path).read_text()
                    await self.workspace.files.write(str(rel_path), content)

                elif change_type == "deleted":
                    # Delete from workspace
                    await self.workspace.files.delete(str(rel_path))
```

**Benefits:**
- Cleaner integration with workspace abstraction
- Type-safe file operations
- Better error handling

---

### Phase 3: Simplify Orchestrator

**Goal:** Streamline orchestrator using new abstractions

#### 3.1 Refactor Orchestrator Structure

**Current:** Large `orchestrator.py` with manual state management

**New:** Simplified using workspace and grail

```python
# orchestrator.py - refactored structure
from fsdantic import Workspace
from grail import MontyContext
from .agent_tools import create_agent_tools
from .lifecycle import LifecycleStore, LifecycleRecord
from .queue import TaskQueue
from .code_generator import generate_agent_code

class Orchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stable_workspace: Workspace = None
        self.lifecycle_store: LifecycleStore = None
        self.queue = TaskQueue()
        self.active_agents: dict[str, AgentContext] = {}
        self.semaphore = asyncio.Semaphore(settings.AGENT_MAX_CONCURRENT)

    async def start(self):
        # Initialize stable workspace
        self.stable_workspace = await Workspace.open(
            db_path=self.settings.state_dir / "stable.db"
        )

        # Initialize lifecycle store
        self.lifecycle_store = LifecycleStore(self.stable_workspace)

        # Recover from crash
        await self.recover()

        # Start workers
        await asyncio.gather(
            self.worker_loop(),
            self.command_loop(),
            self.file_watcher_loop()
        )

    async def spawn_agent(self, task: str, priority: int):
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"

        # Create lifecycle record
        record = LifecycleRecord(
            agent_id=agent_id,
            task=task,
            priority=priority,
            state="QUEUED",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        await self.lifecycle_store.save(record)

        # Add to queue
        await self.queue.enqueue(agent_id, task, priority)

        return agent_id

    async def run_agent(self, agent_id: str):
        async with self.semaphore:
            # Get lifecycle record
            record = await self.lifecycle_store.get(agent_id)

            # Create overlay workspace
            overlay = await self.stable_workspace.overlay.create(agent_id)

            # Update state: GENERATING
            await self.update_state(agent_id, "GENERATING")

            # Generate code
            code = await generate_agent_code(record.task, self.settings)

            # Update state: EXECUTING
            await self.update_state(agent_id, "EXECUTING")

            # Execute with grail
            result = await execute_agent_code(
                agent_id=agent_id,
                code=code,
                workspace=overlay,
                settings=self.settings
            )

            if result.success:
                # Update state: REVIEWING
                await self.update_state(agent_id, "REVIEWING")

                # Materialize for preview
                preview_dir = self.settings.state_dir / "workspaces" / agent_id
                await overlay.materialize.to_disk(preview_dir)

                # Store overlay reference
                record.overlay_id = agent_id
                await self.lifecycle_store.save(record)
            else:
                # Update state: ERRORED
                record.error = result.error
                await self.update_state(agent_id, "ERRORED")

    async def accept_agent(self, agent_id: str):
        # Merge overlay into stable
        await self.stable_workspace.overlay.merge(
            overlay_id=agent_id,
            conflict_strategy="overlay_wins"
        )

        # Update state
        await self.update_state(agent_id, "ACCEPTED")

        # Cleanup
        await self.cleanup_agent(agent_id)

    async def reject_agent(self, agent_id: str):
        # Discard overlay
        await self.stable_workspace.overlay.delete(agent_id)

        # Update state
        await self.update_state(agent_id, "REJECTED")

        # Cleanup
        await self.cleanup_agent(agent_id)
```

**Benefits:**
- Cleaner separation of concerns
- Less boilerplate
- Type-safe throughout
- Easier to test

#### 3.2 Remove Redundant Components

**Files to Remove:**
- `executor.py` → Replaced by grail MontyContext usage
- `external_functions.py` → Replaced by `agent_tools.py`
- `kv_models.py` → Replaced by fsdantic TypedKVRepository
- `workspace.py` → Replaced by workspace.materialize

**Files to Significantly Simplify:**
- `orchestrator.py` → Use workspace and grail abstractions
- `lifecycle.py` → Use TypedKVRepository
- `watcher.py` → Use workspace.files

**Files with Minimal Changes:**
- `queue.py` → Keep as is
- `commands.py` → Keep as is
- `signals.py` → Keep as is
- `cli.py` → Minor updates for new orchestrator API
- `settings.py` → Minor updates for new configuration

---

### Phase 4: Add Advanced Features from Grail

**Goal:** Leverage grail's advanced capabilities

#### 4.1 Add Observability

**New:** Use grail's built-in metrics and logging

```python
# Add metrics collection
from grail.observability import MetricsCollector, LogCollector

class Orchestrator:
    def __init__(self, settings: Settings):
        # ... existing init
        self.metrics = MetricsCollector()
        self.logs = LogCollector()

    async def run_agent(self, agent_id: str):
        # ... existing code

        # Collect execution metrics
        execution_metrics = result.metrics
        await self.metrics.record(
            agent_id=agent_id,
            duration=execution_metrics.duration_seconds,
            tool_calls=execution_metrics.tool_call_count,
            memory_peak=execution_metrics.peak_memory_bytes
        )

        # Store logs
        await self.logs.save(agent_id, result.logs)

    async def get_agent_metrics(self, agent_id: str) -> dict:
        return await self.metrics.get(agent_id)

    async def get_agent_logs(self, agent_id: str) -> list[str]:
        return await self.logs.get(agent_id)
```

**CLI Updates:**
```bash
# New commands
cairn logs agent-abc123
cairn metrics agent-abc123
```

#### 4.2 Add Resumable Execution (Future)

**New:** Support for long-running agents with human-in-the-loop

```python
# For future: agents that pause for human input
from grail import SnapshotManager

async def run_resumable_agent(self, agent_id: str):
    snapshot_mgr = SnapshotManager(storage_path=self.settings.state_dir / "snapshots")

    # Check for existing snapshot
    if await snapshot_mgr.exists(agent_id):
        # Resume from snapshot
        result = await ctx.resume(agent_id)
    else:
        # Start fresh
        result = await ctx.execute(input_data)

        # If agent requests human input, save snapshot
        if result.status == "paused":
            await snapshot_mgr.save(agent_id, result.snapshot)
```

**Benefits for Future:**
- Agents can pause for human clarification
- Long-running agents can be resumed after crashes
- Better support for complex multi-step tasks

#### 4.3 Add Retry Policies

**New:** Use grail's retry capabilities

```python
from grail import RetryPolicy, RetryStrategy

# Configure retry for transient errors
retry_policy = RetryPolicy(
    strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
    max_attempts=3,
    initial_delay=1.0,
    max_delay=10.0,
    retryable_errors=["NetworkError", "TimeoutError"]
)

ctx = MontyContext(
    tool_registry=tools,
    resource_policy=policy,
    retry_policy=retry_policy
)
```

**Benefits:**
- Automatic retry for network errors
- Configurable backoff strategies
- Better resilience

---

## Implementation Strategy

### Phase 1: Foundation (Week 1-2)
**Goal:** Replace execution layer with grail

Tasks:
1. Create `agent_tools.py` with Pydantic-based tool definitions
2. Update `code_generator.py` prompt templates
3. Replace `executor.py` with grail MontyContext usage
4. Update tests for new execution model
5. Remove `executor.py`

**Success Criteria:**
- All existing tests pass with grail execution
- Agent code runs with new tool registry
- No regressions in functionality

### Phase 2: Storage Abstraction (Week 3-4)
**Goal:** Leverage fsdantic Workspace throughout

Tasks:
1. Refactor `lifecycle.py` to use TypedKVRepository
2. Update orchestrator to use `workspace.overlay` operations
3. Replace `workspace.py` with `workspace.materialize` usage
4. Update `watcher.py` to use `workspace.files`
5. Remove `kv_models.py`, `workspace.py`, `external_functions.py`

**Success Criteria:**
- All storage operations go through workspace
- Overlay lifecycle managed by fsdantic
- Materialization uses workspace.materialize
- File watching syncs via workspace.files

### Phase 3: Orchestrator Simplification (Week 5)
**Goal:** Streamline orchestrator using new abstractions

Tasks:
1. Refactor orchestrator to use simplified APIs
2. Update CLI for any API changes
3. Update documentation
4. Remove redundant code

**Success Criteria:**
- 40%+ reduction in orchestrator code
- Cleaner separation of concerns
- All existing features work

### Phase 4: Advanced Features (Week 6)
**Goal:** Add observability and future capabilities

Tasks:
1. Add metrics collection from grail
2. Add log collection and viewing
3. Add CLI commands for logs/metrics
4. Document new features
5. (Optional) Add resumable execution support

**Success Criteria:**
- Metrics visible via CLI
- Logs accessible per agent
- Documentation updated

### Phase 5: Testing & Documentation (Week 7)
**Goal:** Comprehensive testing and docs

Tasks:
1. Update all tests for new architecture
2. Add integration tests for grail + fsdantic
3. Update README, CONCEPT, SPEC, AGENT docs
4. Add migration guide (if needed)
5. Performance benchmarking

**Success Criteria:**
- 100% test coverage maintained
- All docs updated
- Performance meets targets

---

## Testing Strategy

### Unit Tests
**Update Required:**
- `test_executor.py` → Test grail MontyContext integration
- `test_lifecycle.py` → Test TypedKVRepository usage
- `test_workspace.py` → Test workspace.materialize usage
- `test_agent_tools.py` (NEW) → Test tool registry

### Integration Tests
**Update Required:**
- `test_orchestrator.py` → Test full agent lifecycle with new architecture
- `test_overlay.py` → Test overlay operations via workspace
- `test_watcher.py` → Test file sync with workspace.files

### E2E Tests
**Update Required:**
- Test complete workflows: spawn → execute → review → accept
- Test error handling with grail
- Test metrics and logging
- Test concurrent agent execution

### Performance Tests
**New Benchmarks:**
- Agent spawn time (target: <1s)
- Code generation time
- Execution time for common tasks
- Preview materialization time (target: <100ms)
- Accept/reject time (target: <50ms)
- Memory usage per agent

---

## Migration Considerations

### Breaking Changes
**None for users** - The refactor is internal, CLI and behavior remain the same

**For contributors:**
- `external_functions.py` API removed → Use `agent_tools.py` tool registry
- `executor.py` removed → Use grail MontyContext
- `kv_models.py` removed → Use fsdantic TypedKVRepository
- `workspace.py` removed → Use workspace.materialize

### Data Migration
**Not required** - AgentFS database format remains the same

**State Migration:**
If TypedKVRepository format differs:
1. Read old lifecycle records
2. Convert to new format
3. Write via new TypedKVRepository
4. Provide migration script if needed

### Rollback Plan
1. Keep old code in `legacy/` branch during refactor
2. Feature flag for new vs old execution (if needed)
3. Ability to rollback to previous version

---

## Code Size Impact

### Estimated Line Count Changes

**Before:**
- `orchestrator.py`: ~800 lines
- `executor.py`: ~150 lines
- `external_functions.py`: ~300 lines
- `lifecycle.py`: ~200 lines
- `workspace.py`: ~150 lines
- `kv_models.py`: ~100 lines
- `watcher.py`: ~100 lines
- **Total: ~1800 lines**

**After:**
- `orchestrator.py`: ~400 lines (-50%)
- `agent_tools.py`: ~200 lines (NEW)
- `lifecycle.py`: ~80 lines (-60%)
- `watcher.py`: ~60 lines (-40%)
- Removed: ~700 lines (executor, external_functions, workspace, kv_models)
- **Total: ~740 lines**

**Net Reduction: ~1060 lines (59% reduction)**

---

## Dependencies Update

### Remove
- Direct `pydantic-monty` dependency (replaced by grail)
- Direct `agentfs-sdk` low-level usage (via fsdantic)

### Add
- `grail` - Sandboxed execution with Pydantic
- Updated `fsdantic` - Workspace abstraction

### Keep
- `llm` - LLM provider abstraction
- `watchfiles` - Filesystem watching
- `pydantic-settings` - Configuration

### New `pyproject.toml`
```toml
[project]
name = "cairn"
version = "0.2.0"
dependencies = [
    "fsdantic>=0.2.0",
    "grail>=0.1.0",
    "llm>=0.15.0",
    "watchfiles>=0.21.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "click>=8.1.0",
]
```

---

## Benefits Summary

### Code Quality
- **59% reduction** in code size
- **Type-safe** throughout with Pydantic models
- **Less custom code** - leverage battle-tested libraries
- **Better error handling** - built into grail and fsdantic
- **Cleaner separation of concerns** - workspace, execution, orchestration

### Features
- **Observability**: Built-in metrics and logging from grail
- **Resumability**: Support for long-running agents (future)
- **Retry policies**: Automatic retry for transient errors
- **Better diffs**: Rich diff support from fsdantic
- **Query support**: KV repository queries from fsdantic

### Developer Experience
- **Easier to extend**: Add new tools via simple registry
- **Easier to test**: Type-safe mocks with Pydantic
- **Better debugging**: Metrics and logs built-in
- **Less boilerplate**: Libraries handle common patterns

### Performance
- **Same or better** - fsdantic and grail are optimized
- **Better resource management** - grail's resource policies
- **Efficient storage** - fsdantic's batch operations

### Maintenance
- **Less code to maintain** - 59% reduction
- **Fewer bugs** - less custom code, more library code
- **Easier onboarding** - simpler architecture
- **Better docs** - grail and fsdantic are well-documented

---

## Risks and Mitigations

### Risk 1: Breaking Changes in Libraries
**Mitigation:** Pin versions, monitor releases, contribute upstream

### Risk 2: Performance Regression
**Mitigation:** Comprehensive benchmarking before/after, optimize hot paths

### Risk 3: Unexpected Complexity
**Mitigation:** Incremental refactoring, keep old code until validated

### Risk 4: Testing Gaps
**Mitigation:** Maintain 100% test coverage, add integration tests

### Risk 5: Documentation Debt
**Mitigation:** Update docs in parallel with code changes

---

## Success Metrics

### Code Metrics
- [ ] 50%+ reduction in total line count
- [ ] 100% type coverage with Pydantic
- [ ] Zero direct agentfs-sdk usage in orchestrator
- [ ] Zero direct pydantic-monty usage

### Functional Metrics
- [ ] All existing tests pass
- [ ] All existing features work
- [ ] New observability features added

### Performance Metrics
- [ ] Agent spawn time ≤ 1s
- [ ] Preview materialization ≤ 100ms
- [ ] Accept/reject ≤ 50ms
- [ ] Memory per agent ≤ 150MB

### Quality Metrics
- [ ] Test coverage ≥ 90%
- [ ] Zero critical bugs in first month
- [ ] Documentation complete and accurate

---

## Timeline Summary

| Phase | Duration | Key Deliverables |
|-------|----------|------------------|
| Phase 1: Foundation | 2 weeks | Grail execution layer integrated |
| Phase 2: Storage | 2 weeks | Fsdantic workspace throughout |
| Phase 3: Orchestrator | 1 week | Simplified orchestrator |
| Phase 4: Advanced | 1 week | Observability features |
| Phase 5: Testing | 1 week | Complete tests and docs |
| **Total** | **7 weeks** | **Production-ready refactor** |

---

## Conclusion

This refactoring will transform Cairn from a custom-built orchestration system to a lean, focused orchestrator built on robust foundation libraries. By leveraging fsdantic for storage abstraction and grail for sandboxed execution, we can:

1. **Reduce code by 59%** while maintaining all features
2. **Improve type safety** with Pydantic throughout
3. **Add observability** with built-in metrics and logging
4. **Simplify maintenance** with less custom code
5. **Enable future features** like resumable execution

The refactoring is low-risk due to incremental approach, comprehensive testing, and clear rollback plan. The result will be a more maintainable, extensible, and powerful Cairn library that better serves its goal of enabling safe AI agent orchestration.
