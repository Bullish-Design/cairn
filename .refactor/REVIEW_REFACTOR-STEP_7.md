# Refactoring Step 7: Code Structure Refactoring

## Overview
This step improves code organization by breaking down large methods into smaller, focused functions and improving overall code structure. This is pure refactoring that doesn't change behavior but makes the code more maintainable, testable, and easier to understand.

**Priority:** 🟢 MEDIUM
**Estimated Effort:** 4-5 hours
**Dependencies:** All previous steps should be complete

---

## Issues Addressed

### Issue #5: Long Method - _run_agent()
**Location:** `orchestrator.py:334-419` (85 lines)
**Severity:** MEDIUM

**Problem:**
- Single method handles generation, validation, execution, and submission
- Hard to test individual phases
- Difficult to understand the flow
- Violates Single Responsibility Principle

**Solution:**
Extract to separate methods for each phase:
- `_generate_code()`
- `_validate_code()`
- `_execute_script()`
- `_submit_results()`

### Large Orchestrator Class
**Location:** `orchestrator.py` (487 lines)
**Concern:** Approaching single responsibility violation

**Solution:**
Consider extracting some responsibilities to helper classes or modules without major restructuring.

---

## Detailed Implementation Steps

### 1. Extract Agent Execution Phases

**File:** `cairn/orchestrator.py`

**Before:**
```python
async def _run_agent(self, agent_id: str) -> None:
    """Run a single agent task."""
    ctx = None
    try:
        await self._semaphore.acquire()

        ctx = self.active_agents.get(agent_id)
        if ctx is None:
            ctx = await self._create_agent_context(agent_id)
            self.active_agents[agent_id] = ctx

        # Generation phase
        ctx.state = AgentState.GENERATING
        await self._save_lifecycle_record(ctx)
        code = await self.code_provider.fetch_code(agent_id)
        script_path = self.agentfs_dir / agent_id / "script.pym"
        script_path.write_text(code)
        ctx.script_path = script_path

        # Execution phase
        ctx.state = AgentState.EXECUTING
        await self._save_lifecycle_record(ctx)
        script = _load_grail_script(script_path)
        tools = self.tools_factory(ctx.agent_id, ctx.agent_fs, ctx.stable_fs)
        result = await script.run(inputs={"task_description": ctx.task}, externals=tools)
        ctx.result = result

        # Submission phase
        ctx.state = AgentState.SUBMITTING
        await self._save_lifecycle_record(ctx)
        # ... submission logic

    except Exception as exc:
        if ctx:
            ctx.error = str(exc)
            ctx.state = AgentState.ERRORED
            await self._save_lifecycle_record(ctx)
    finally:
        self._semaphore.release()
```

**After:**
```python
async def _run_agent(self, agent_id: str) -> None:
    """Run a single agent task through all execution phases.

    This orchestrates the agent lifecycle:
    1. Context setup
    2. Code generation
    3. Validation (if applicable)
    4. Script execution
    5. Result submission

    Args:
        agent_id: Unique agent identifier

    The method ensures proper resource cleanup even on errors.
    """
    ctx = None

    try:
        # Acquire execution slot
        await self._semaphore.acquire()

        # Setup agent context
        ctx = await self._setup_agent_context(agent_id)

        # Execute agent phases
        await self._execute_agent_phases(ctx)

    except Exception as exc:
        await self._handle_agent_error(ctx, agent_id, exc)

    finally:
        # Always release semaphore
        self._semaphore.release()

        # Cleanup agent resources
        await self._cleanup_completed_agent(agent_id)


async def _setup_agent_context(self, agent_id: str) -> AgentContext:
    """Setup agent execution context.

    Args:
        agent_id: Agent identifier

    Returns:
        Initialized agent context

    Raises:
        AgentError: If context setup fails
    """
    ctx = self.active_agents.get(agent_id)

    if ctx is None:
        ctx = await self._create_agent_context(agent_id)
        self.active_agents[agent_id] = ctx

    return ctx


async def _execute_agent_phases(self, ctx: AgentContext) -> None:
    """Execute all agent phases in sequence.

    Args:
        ctx: Agent execution context

    Raises:
        AgentError: If any phase fails
    """
    # Phase 1: Generate code
    await self._generate_code(ctx)

    # Phase 2: Validate code (optional)
    if self.config.validate_code:
        await self._validate_code(ctx)

    # Phase 3: Execute script
    await self._execute_script(ctx)

    # Phase 4: Submit results
    await self._submit_results(ctx)


async def _generate_code(self, ctx: AgentContext) -> None:
    """Generate agent code from provider.

    Args:
        ctx: Agent execution context

    Updates:
        - ctx.state -> GENERATING
        - ctx.script_path -> path to generated script

    Raises:
        ProviderError: If code generation fails
    """
    ctx.state = AgentState.GENERATING
    await self._save_lifecycle_record(ctx)

    logger.info(
        f"Generating code for agent: {ctx.agent_id}",
        extra={"agent_id": ctx.agent_id, "task": ctx.task[:100]}
    )

    try:
        # Fetch code from provider
        code = await self.code_provider.fetch_code(ctx.agent_id)

        # Write to script file
        script_path = self.agentfs_dir / ctx.agent_id / "script.pym"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(code, encoding="utf-8")

        ctx.script_path = script_path

        logger.debug(
            f"Code generated successfully: {script_path}",
            extra={
                "agent_id": ctx.agent_id,
                "script_path": str(script_path),
                "code_size": len(code)
            }
        )

    except Exception as exc:
        raise ProviderError(
            format_agent_error(
                "Code generation failed",
                agent_id=ctx.agent_id,
                state=ctx.state.value,
            ),
            error_code="CODE_GENERATION_FAILED",
            context={"agent_id": ctx.agent_id}
        ) from exc


async def _validate_code(self, ctx: AgentContext) -> None:
    """Validate generated code (if validation enabled).

    Args:
        ctx: Agent execution context

    Raises:
        ValidationError: If validation fails
    """
    logger.info(
        f"Validating code for agent: {ctx.agent_id}",
        extra={"agent_id": ctx.agent_id}
    )

    try:
        # Load script to verify it's valid
        script = await self._load_grail_script_with_retry(ctx.script_path)

        # Additional validation checks could go here
        # - Check for required inputs
        # - Check for required outputs
        # - Static analysis, etc.

        logger.debug(
            f"Code validation passed: {ctx.agent_id}",
            extra={"agent_id": ctx.agent_id}
        )

    except Exception as exc:
        raise ValidationError(
            format_agent_error(
                "Code validation failed",
                agent_id=ctx.agent_id,
                state=ctx.state.value,
            ),
            error_code="CODE_VALIDATION_FAILED",
            context={"agent_id": ctx.agent_id, "script_path": str(ctx.script_path)}
        ) from exc


async def _execute_script(self, ctx: AgentContext) -> None:
    """Execute agent script with resource limits.

    Args:
        ctx: Agent execution context

    Updates:
        - ctx.state -> EXECUTING
        - ctx.result -> execution result

    Raises:
        AgentExecutionError: If execution fails
        ResourceLimitError: If resource limits exceeded
        TimeoutError: If execution times out

    Note: This method was updated in Step 5 to include resource limits.
    """
    ctx.state = AgentState.EXECUTING
    await self._save_lifecycle_record(ctx)

    logger.info(
        f"Executing script for agent: {ctx.agent_id}",
        extra={"agent_id": ctx.agent_id}
    )

    try:
        # Load script
        script = await self._load_grail_script_with_retry(ctx.script_path)

        # Create tools for agent
        tools = self.tools_factory(
            ctx.agent_id,
            ctx.agent_fs,
            ctx.stable_fs
        )

        # Execute with resource limits (from Step 5)
        limiter = ResourceLimiter(
            timeout_seconds=self.executor_settings.max_execution_time,
            max_memory_bytes=self.executor_settings.max_memory_bytes,
        )

        async with limiter.limit():
            result = await run_with_timeout(
                script.run(
                    inputs={"task_description": ctx.task},
                    externals=tools
                ),
                timeout_seconds=self.executor_settings.max_execution_time
            )

        ctx.result = result

        logger.info(
            f"Script execution completed: {ctx.agent_id}",
            extra={"agent_id": ctx.agent_id}
        )

    except (ResourceLimitError, CairnTimeoutError) as exc:
        ctx.error = str(exc)
        ctx.state = AgentState.ERRORED
        await self._save_lifecycle_record(ctx)
        raise

    except Exception as exc:
        ctx.error = str(exc)
        ctx.state = AgentState.ERRORED
        await self._save_lifecycle_record(ctx)
        raise AgentExecutionError(
            format_agent_error(
                "Script execution failed",
                agent_id=ctx.agent_id,
                state=ctx.state.value,
            ),
            error_code="SCRIPT_EXECUTION_FAILED",
            context={"agent_id": ctx.agent_id}
        ) from exc


async def _submit_results(self, ctx: AgentContext) -> None:
    """Submit agent results and queue for review.

    Args:
        ctx: Agent execution context

    Updates:
        - ctx.state -> SUBMITTING
        - ctx.submission_path -> path to submission

    Raises:
        WorkspaceMergeError: If submission fails
        SecretsDetectedError: If secrets detected (from Step 5)
    """
    ctx.state = AgentState.SUBMITTING
    await self._save_lifecycle_record(ctx)

    logger.info(
        f"Submitting results for agent: {ctx.agent_id}",
        extra={"agent_id": ctx.agent_id}
    )

    try:
        # Scan for secrets before submission (from Step 5)
        secrets = await scan_workspace_for_secrets(
            ctx.agent_fs,
            exclude_patterns=["*.md", "test_*", "*_test.py"]
        )
        validate_no_secrets(secrets)

        # Create submission
        submission_dir = self.submissions_dir / ctx.agent_id
        submission_dir.mkdir(parents=True, exist_ok=True)

        # Copy agent workspace to submission
        await self._copy_workspace_to_submission(ctx.agent_fs, submission_dir)

        ctx.submission_path = submission_dir

        # Update state to reviewing
        ctx.state = AgentState.REVIEWING
        await self._save_lifecycle_record(ctx)

        logger.info(
            f"Results submitted successfully: {ctx.agent_id}",
            extra={
                "agent_id": ctx.agent_id,
                "submission_path": str(submission_dir)
            }
        )

    except SecretsDetectedError as exc:
        ctx.error = str(exc)
        ctx.state = AgentState.ERRORED
        await self._save_lifecycle_record(ctx)
        raise

    except Exception as exc:
        ctx.error = str(exc)
        ctx.state = AgentState.ERRORED
        await self._save_lifecycle_record(ctx)
        raise WorkspaceMergeError(
            format_agent_error(
                "Result submission failed",
                agent_id=ctx.agent_id,
                state=ctx.state.value,
            ),
            error_code="SUBMISSION_FAILED",
            context={"agent_id": ctx.agent_id}
        ) from exc


async def _handle_agent_error(
    self,
    ctx: AgentContext | None,
    agent_id: str,
    error: Exception,
) -> None:
    """Handle agent execution error.

    Args:
        ctx: Agent context (may be None if error during setup)
        agent_id: Agent identifier
        error: Exception that occurred

    This method ensures proper error recording and logging.
    """
    if ctx:
        ctx.error = str(error)
        ctx.state = AgentState.ERRORED
        await self._save_lifecycle_record(ctx)

    # Log error with appropriate level
    if isinstance(error, (RecoverableError, CairnTimeoutError)):
        logger.warning(
            f"Agent failed with recoverable error: {agent_id}",
            extra={
                "agent_id": agent_id,
                "error_type": type(error).__name__,
                "error": str(error)
            }
        )
    else:
        logger.error(
            f"Agent failed with error: {agent_id}",
            extra={
                "agent_id": agent_id,
                "error_type": type(error).__name__,
            },
            exc_info=error
        )
```

### 2. Extract Helper Functions for Complex Logic

**File:** `cairn/orchestrator_helpers.py` (NEW FILE)

```python
"""Helper functions for orchestrator operations.

This module contains utility functions extracted from the orchestrator
to improve code organization and testability.
"""

from pathlib import Path
import shutil
from typing import Any

from cairn.exceptions import WorkspaceError


async def copy_workspace_to_submission(
    workspace: Any,
    destination: Path,
) -> None:
    """Copy workspace contents to submission directory.

    Args:
        workspace: Source workspace
        destination: Destination directory path

    Raises:
        WorkspaceError: If copy fails
    """
    try:
        # List all files in workspace
        files = await workspace.list_files("**/*")

        for file_path in files:
            # Read from workspace
            content = await workspace.read_file(file_path)

            # Write to destination
            dest_file = destination / file_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_text(content, encoding="utf-8")

    except Exception as exc:
        raise WorkspaceError(
            f"Failed to copy workspace to submission: {destination}",
            error_code="WORKSPACE_COPY_FAILED",
            context={"destination": str(destination)}
        ) from exc


def calculate_priority_score(priority: int, created_at: float) -> tuple[int, float]:
    """Calculate sort key for priority queue.

    Args:
        priority: Task priority (1-10, higher is more urgent)
        created_at: Task creation timestamp

    Returns:
        Tuple of (negative_priority, timestamp) for heap sorting

    This ensures higher priority tasks come first, with FIFO
    ordering within the same priority level.
    """
    return (-int(priority), created_at)
```

### 3. Improve Queue Implementation Structure

**File:** `cairn/queue.py`

```python
"""Task queue implementation with clear structure."""

import heapq
import asyncio
from dataclasses import dataclass, field
from typing import Any

from cairn.constants import DEFAULT_QUEUE_PRIORITY
from cairn.exceptions import ResourceLimitError


@dataclass(order=True)
class QueuedTask:
    """A task in the priority queue.

    Tasks are ordered by priority (higher first) and then by
    creation time (FIFO within priority).
    """

    _sort_key: tuple[int, float] = field(init=False, repr=False)
    priority: int
    agent_id: str = field(compare=False)
    task: str = field(compare=False)
    created_at: float = field(compare=False)

    def __post_init__(self) -> None:
        """Initialize sort key for heap ordering."""
        self._sort_key = self._calculate_sort_key()

    def _calculate_sort_key(self) -> tuple[int, float]:
        """Calculate sort key for priority ordering.

        Returns:
            Tuple of (negative priority, timestamp)

        The negative priority ensures higher priorities come first.
        The timestamp provides FIFO ordering within priority levels.
        """
        return (-int(self.priority), self.created_at)


class TaskQueue:
    """Thread-safe priority queue for agent tasks.

    Features:
    - Priority-based ordering (1-10, higher is more urgent)
    - FIFO within same priority level
    - Size limit with backpressure
    - Thread-safe operations
    """

    def __init__(self, max_size: int = 1000):
        """Initialize task queue.

        Args:
            max_size: Maximum queue size (0 = unlimited)
        """
        self._queue: list[QueuedTask] = []
        self.max_size = max_size
        self._lock = asyncio.Lock()

    async def enqueue(self, task: QueuedTask) -> None:
        """Add task to queue.

        Args:
            task: Task to enqueue

        Raises:
            ResourceLimitError: If queue is full
        """
        async with self._lock:
            if self.is_full():
                raise ResourceLimitError(
                    f"Queue is full: {len(self._queue)}/{self.max_size}",
                    error_code="QUEUE_FULL",
                    context={
                        "current_size": len(self._queue),
                        "max_size": self.max_size,
                    }
                )

            heapq.heappush(self._queue, task)

    async def dequeue(self) -> QueuedTask | None:
        """Remove and return highest priority task.

        Returns:
            Next task or None if queue is empty
        """
        async with self._lock:
            if not self._queue:
                return None
            return heapq.heappop(self._queue)

    async def peek(self) -> QueuedTask | None:
        """View highest priority task without removing.

        Returns:
            Next task or None if queue is empty
        """
        async with self._lock:
            if not self._queue:
                return None
            return self._queue[0]

    def size(self) -> int:
        """Get current queue size.

        Returns:
            Number of tasks in queue
        """
        return len(self._queue)

    def is_empty(self) -> bool:
        """Check if queue is empty.

        Returns:
            True if queue has no tasks
        """
        return len(self._queue) == 0

    def is_full(self) -> bool:
        """Check if queue is at capacity.

        Returns:
            True if queue is at max_size
        """
        return self.max_size > 0 and len(self._queue) >= self.max_size

    async def list_all(self) -> list[QueuedTask]:
        """List all tasks in queue (unordered).

        Returns:
            List of all queued tasks

        Note: This is a snapshot and order is not guaranteed.
        """
        async with self._lock:
            return list(self._queue)

    async def clear(self) -> None:
        """Remove all tasks from queue."""
        async with self._lock:
            self._queue.clear()
```

---

## Testing Requirements

### Unit Tests for Extracted Methods

**File:** `tests/test_orchestrator_phases.py` (NEW FILE)

```python
"""Tests for individual orchestrator phase methods."""

import pytest
from cairn.orchestrator import CairnOrchestrator
from cairn.exceptions import ProviderError, ValidationError


@pytest.mark.asyncio
async def test_generate_code_phase(tmp_path):
    """Test code generation phase in isolation."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)
    ctx = await orchestrator._setup_agent_context("test-agent")

    await orchestrator._generate_code(ctx)

    assert ctx.script_path is not None
    assert ctx.script_path.exists()
    assert ctx.state == AgentState.GENERATING


@pytest.mark.asyncio
async def test_generate_code_handles_provider_error(tmp_path):
    """Test code generation handles provider errors."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)
    ctx = await orchestrator._setup_agent_context("test-agent")

    # Mock provider to raise error
    orchestrator.code_provider.fetch_code = async_mock_that_raises(ProviderError)

    with pytest.raises(ProviderError):
        await orchestrator._generate_code(ctx)


@pytest.mark.asyncio
async def test_validate_code_phase(tmp_path):
    """Test code validation phase in isolation."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)
    ctx = await orchestrator._setup_agent_context("test-agent")

    # Generate code first
    await orchestrator._generate_code(ctx)

    # Validate
    await orchestrator._validate_code(ctx)

    # Should not raise


@pytest.mark.asyncio
async def test_execute_script_phase(tmp_path):
    """Test script execution phase in isolation."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)
    ctx = await orchestrator._setup_agent_context("test-agent")

    # Setup script
    await orchestrator._generate_code(ctx)

    # Execute
    await orchestrator._execute_script(ctx)

    assert ctx.result is not None
    assert ctx.state == AgentState.EXECUTING
```

---

## Files to Create

1. `cairn/orchestrator_helpers.py` - Extracted helper functions
2. `tests/test_orchestrator_phases.py` - Phase-specific tests

---

## Files to Modify

1. `cairn/orchestrator.py` - Extract methods, improve structure
2. `cairn/queue.py` - Improve structure and documentation

---

## Validation Criteria

### Success Criteria
- ✅ `_run_agent()` method under 30 lines
- ✅ Each phase is a separate method
- ✅ All phase methods have clear docstrings
- ✅ All existing tests still pass
- ✅ New phase-specific tests pass
- ✅ Code coverage maintained or improved
- ✅ No behavior changes (pure refactoring)

### Breaking Changes
- None - this is pure refactoring
- All APIs remain unchanged
- Behavior is identical

---

## Notes for Implementer

### Time Estimates
- Extract agent phases: 2 hours
- Extract helper functions: 1 hour
- Improve queue structure: 1 hour
- Write new tests: 2 hours
- Validation: 1 hour
- **Total: 7 hours**

### Key Principles

1. **Single Responsibility:** Each method does one thing
2. **Clear Names:** Method names describe what they do
3. **Testability:** Each phase can be tested independently
4. **Documentation:** Every extracted method has a docstring
5. **No Behavior Change:** This is pure refactoring

---

## References

- CODE_REVIEW.md - Issue #5 (Long Method)
- CODE_REVIEW.md - Section 1.1 (Architecture Review)
- orchestrator.py:334-419 (Long _run_agent method)
