# Refactoring Step 3: Error Handling & Resource Cleanup

## Overview
This step improves error handling robustness and ensures proper resource cleanup through finally blocks and context managers. It addresses workspace cleanup issues, improves error message consistency, and ensures resources are properly released even in error paths.

**Priority:** 🔴 CRITICAL
**Estimated Effort:** 4-5 hours
**Dependencies:** Step 1 (uses new exception hierarchy)

---

## Issues Addressed

### Issue #7: Missing Workspace Cleanup
**Location:** `orchestrator.py:286`
**Severity:** MEDIUM

**Problem:**
```python
await ctx.agent_fs.close()
```
No try/finally guarantee - workspace may leak if exception occurs before close.

### Issue #9: Inconsistent Error Messages
**Severity:** MEDIUM

**Examples:**
- Good: `f"Failed to merge agent overlay: {merge_errors}"`
- Bad: `"Agent DB missing after restart"` (missing agent_id context)

### Silent Failures in Watcher
**Location:** `watcher.py:40-46`

```python
def should_ignore(self, path: Path) -> bool:
    try:
        rel_parts = path.relative_to(self.project_root).parts
    except ValueError:
        return True  # Silently ignores non-relative paths
```

---

## Detailed Implementation Steps

### 1. Add Async Context Manager for Workspace

**File:** `cairn/workspace_manager.py` (NEW FILE)

```python
"""Workspace lifecycle management with automatic cleanup.

This module provides context managers and utilities for ensuring proper
workspace resource cleanup even in error scenarios.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from pathlib import Path

from cairn.exceptions import WorkspaceError
from cairn.constants import DEFAULT_EXECUTION_TIMEOUT_SECONDS

# Assuming Workspace is from fsdantic or similar
from fsdantic import Workspace


class WorkspaceManager:
    """Manages workspace lifecycle with automatic cleanup."""

    def __init__(self):
        self._active_workspaces: set[Workspace] = set()
        self._closed = False

    @asynccontextmanager
    async def open_workspace(
        self,
        path: Path | str,
        *,
        readonly: bool = False,
    ) -> AsyncIterator[Workspace]:
        """Open a workspace with automatic cleanup.

        Args:
            path: Path to workspace
            readonly: If True, open in read-only mode

        Yields:
            Workspace object

        Raises:
            WorkspaceError: If workspace cannot be opened

        Example:
            async with manager.open_workspace("/path/to/ws") as ws:
                await ws.read_file("test.py")
            # Workspace automatically closed here
        """
        workspace = None
        try:
            # Open workspace (adjust API to match your fsdantic version)
            workspace = await Workspace.open(path, readonly=readonly)
            self._active_workspaces.add(workspace)
            yield workspace
        except Exception as exc:
            raise WorkspaceError(
                f"Failed to open workspace: {path}",
                error_code="WORKSPACE_OPEN_FAILED",
                context={"path": str(path), "readonly": readonly}
            ) from exc
        finally:
            if workspace is not None:
                try:
                    await workspace.close()
                except Exception as exc:
                    # Log but don't raise - cleanup should be best-effort
                    import logging
                    logging.warning(
                        f"Failed to close workspace: {path}",
                        exc_info=exc,
                        extra={"path": str(path)}
                    )
                finally:
                    self._active_workspaces.discard(workspace)

    async def close_all(self) -> None:
        """Close all active workspaces.

        This is a cleanup method for shutdown scenarios.
        """
        if self._closed:
            return

        self._closed = True
        errors = []

        # Close all workspaces in parallel
        close_tasks = [
            ws.close()
            for ws in list(self._active_workspaces)
        ]

        if close_tasks:
            results = await asyncio.gather(*close_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    errors.append(result)

        self._active_workspaces.clear()

        if errors:
            import logging
            logging.warning(
                f"Errors during workspace cleanup: {len(errors)} workspaces failed to close",
                extra={"error_count": len(errors)}
            )
```

### 2. Update Orchestrator to Use Workspace Context Manager

**File:** `cairn/orchestrator.py`

```python
from cairn.workspace_manager import WorkspaceManager
from cairn.exceptions import WorkspaceError, AgentExecutionError

class CairnOrchestrator:
    def __init__(self, ...):
        # Add workspace manager
        self.workspace_manager = WorkspaceManager()
        # ... existing init code

    async def _run_agent(self, agent_id: str) -> None:
        """Run a single agent task with proper resource cleanup."""
        ctx = None
        try:
            # Acquire semaphore
            await self._semaphore.acquire()

            # Get or create context
            ctx = self.active_agents.get(agent_id)
            if ctx is None:
                ctx = await self._create_agent_context(agent_id)
                self.active_agents[agent_id] = ctx

            # Use context manager for workspace
            async with self.workspace_manager.open_workspace(
                self.agentfs_dir / agent_id / "workspace"
            ) as agent_ws:
                ctx.agent_fs = agent_ws

                # Load stable workspace
                async with self.workspace_manager.open_workspace(
                    self.agentfs_dir / "stable.db",
                    readonly=True
                ) as stable_ws:
                    ctx.stable_fs = stable_ws

                    # Run agent phases
                    await self._execute_agent_phases(ctx)

        except WorkspaceError as exc:
            if ctx:
                ctx.error = str(exc)
                ctx.state = AgentState.ERRORED
                await self._save_lifecycle_record(ctx)
            raise

        except Exception as exc:
            if ctx:
                ctx.error = str(exc)
                ctx.state = AgentState.ERRORED
                await self._save_lifecycle_record(ctx)
            # Log unexpected errors
            import logging
            logging.exception(
                "Unexpected error in agent execution",
                extra={"agent_id": agent_id}
            )
            raise

        finally:
            # Always release semaphore
            self._semaphore.release()

            # Remove from active agents
            if agent_id in self.active_agents:
                del self.active_agents[agent_id]

            # Workspace cleanup handled by context manager

    async def _execute_agent_phases(self, ctx: AgentContext) -> None:
        """Execute agent generation, validation, execution, and submission.

        This method is extracted from _run_agent to simplify the main flow.
        """
        # Generate phase
        await self._generate_code(ctx)

        # Validation phase (if needed)
        await self._validate_code(ctx)

        # Execution phase
        await self._execute_script(ctx)

        # Submission phase
        await self._submit_results(ctx)

    async def shutdown(self) -> None:
        """Shutdown orchestrator and cleanup resources."""
        # Stop accepting new work
        self._running = False

        # Wait for active agents to complete (with timeout)
        if self.active_agents:
            import asyncio
            from cairn.constants import DEFAULT_EXECUTION_TIMEOUT_SECONDS

            active_tasks = list(self._running_tasks)
            if active_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*active_tasks, return_exceptions=True),
                        timeout=DEFAULT_EXECUTION_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    import logging
                    logging.warning(
                        "Some agent tasks did not complete before shutdown timeout",
                        extra={"active_count": len(self.active_agents)}
                    )

        # Close all workspaces
        await self.workspace_manager.close_all()

        # Close lifecycle store
        if hasattr(self, 'lifecycle') and self.lifecycle:
            await self.lifecycle.close()
```

### 3. Improve Error Message Consistency

**File:** `cairn/error_formatting.py` (NEW FILE)

```python
"""Utilities for consistent error message formatting.

This module provides helper functions for creating informative error messages
with consistent context information.
"""

from typing import Any


def format_agent_error(
    message: str,
    agent_id: str,
    *,
    state: str | None = None,
    task: str | None = None,
    **context: Any,
) -> str:
    """Format error message with agent context.

    Args:
        message: Base error message
        agent_id: Agent identifier
        state: Current agent state
        task: Task description
        **context: Additional context fields

    Returns:
        Formatted error message with context

    Example:
        >>> format_agent_error(
        ...     "Workspace merge failed",
        ...     agent_id="agent-123",
        ...     state="SUBMITTING",
        ...     conflicts=["file1.py", "file2.py"]
        ... )
        "Workspace merge failed [agent_id=agent-123, state=SUBMITTING, conflicts=2]"
    """
    parts = [message]
    ctx_parts = [f"agent_id={agent_id}"]

    if state:
        ctx_parts.append(f"state={state}")

    if task:
        # Truncate long tasks
        task_preview = task[:50] + "..." if len(task) > 50 else task
        ctx_parts.append(f"task={task_preview!r}")

    for key, value in context.items():
        if isinstance(value, list):
            ctx_parts.append(f"{key}={len(value)}")
        elif isinstance(value, dict):
            ctx_parts.append(f"{key}={len(value)} items")
        else:
            ctx_parts.append(f"{key}={value}")

    parts.append(f"[{', '.join(ctx_parts)}]")
    return " ".join(parts)


def format_workspace_error(
    message: str,
    workspace_path: str,
    *,
    operation: str | None = None,
    **context: Any,
) -> str:
    """Format error message with workspace context.

    Args:
        message: Base error message
        workspace_path: Path to workspace
        operation: Operation that failed
        **context: Additional context fields

    Returns:
        Formatted error message with context
    """
    parts = [message]
    ctx_parts = [f"workspace={workspace_path}"]

    if operation:
        ctx_parts.append(f"operation={operation}")

    for key, value in context.items():
        ctx_parts.append(f"{key}={value}")

    parts.append(f"[{', '.join(ctx_parts)}]")
    return " ".join(parts)


def format_lifecycle_error(
    message: str,
    agent_id: str,
    *,
    version: int | None = None,
    **context: Any,
) -> str:
    """Format error message with lifecycle context.

    Args:
        message: Base error message
        agent_id: Agent identifier
        version: Record version
        **context: Additional context fields

    Returns:
        Formatted error message with context
    """
    parts = [message]
    ctx_parts = [f"agent_id={agent_id}"]

    if version is not None:
        ctx_parts.append(f"version={version}")

    for key, value in context.items():
        ctx_parts.append(f"{key}={value}")

    parts.append(f"[{', '.join(ctx_parts)}]")
    return " ".join(parts)
```

**Update error messages in orchestrator.py:**

```python
from cairn.error_formatting import format_agent_error, format_workspace_error

# OLD:
raise ValueError("Agent DB missing after restart")

# NEW:
raise AgentError(
    format_agent_error(
        "Agent database missing after restart",
        agent_id=agent_id,
        state=ctx.state.value if ctx else "UNKNOWN"
    ),
    error_code="AGENT_DB_MISSING",
    context={"agent_id": agent_id}
)

# OLD:
raise RuntimeError(f"Failed to merge agent overlay: {merge_errors}")

# NEW:
raise WorkspaceMergeError(
    format_agent_error(
        "Failed to merge agent overlay",
        agent_id=agent_id,
        state=ctx.state.value,
        conflicts=merge_errors
    ),
    error_code="WORKSPACE_MERGE_FAILED",
    context={
        "agent_id": agent_id,
        "conflicts": merge_errors,
        "conflict_count": len(merge_errors)
    }
)
```

### 4. Fix Silent Failures in Watcher

**File:** `cairn/watcher.py`

```python
from cairn.exceptions import PathValidationError
import logging

logger = logging.getLogger(__name__)


def should_ignore(self, path: Path) -> bool:
    """Check if path should be ignored.

    Args:
        path: Path to check

    Returns:
        True if path should be ignored
    """
    try:
        rel_parts = path.relative_to(self.project_root).parts
    except ValueError as exc:
        # Log warning instead of silently ignoring
        logger.warning(
            "Path outside project root, ignoring",
            extra={
                "path": str(path),
                "project_root": str(self.project_root),
            }
        )
        return True

    # Check ignore patterns
    for part in rel_parts:
        if part in self.ignore_patterns:
            return True

    return False
```

### 5. Add Try-Finally for All Resource Operations

**File:** `cairn/signals.py`

```python
async def process_signals_once(self) -> None:
    """Process all pending signals with proper error handling."""
    signal_files = list(self.signals_dir.glob("*.json"))

    for signal_file in signal_files:
        try:
            # Read signal
            signal_data = signal_file.read_text(encoding="utf-8")
            signal = json.loads(signal_data)

            # Process signal
            await self._process_signal(signal)

        except json.JSONDecodeError as exc:
            logger.error(
                "Invalid signal JSON",
                extra={"file": str(signal_file), "error": str(exc)}
            )
        except Exception as exc:
            logger.exception(
                "Error processing signal",
                extra={"file": str(signal_file)}
            )
        finally:
            # Always try to remove processed signal
            try:
                signal_file.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(
                    "Failed to remove signal file",
                    extra={"file": str(signal_file), "error": str(exc)}
                )
```

---

## Testing Requirements

### Unit Tests to Add/Update

**File:** `tests/test_workspace_manager.py` (NEW FILE)

```python
"""Tests for workspace lifecycle management."""

import pytest
from pathlib import Path
from cairn.workspace_manager import WorkspaceManager
from cairn.exceptions import WorkspaceError


@pytest.mark.asyncio
async def test_workspace_context_manager_success(tmp_path):
    """Test workspace opens and closes successfully."""
    manager = WorkspaceManager()

    async with manager.open_workspace(tmp_path) as ws:
        assert ws is not None
        assert ws in manager._active_workspaces

    # Workspace should be closed after context exit
    assert len(manager._active_workspaces) == 0


@pytest.mark.asyncio
async def test_workspace_context_manager_exception(tmp_path):
    """Test workspace closes even on exception."""
    manager = WorkspaceManager()

    with pytest.raises(RuntimeError, match="Test error"):
        async with manager.open_workspace(tmp_path) as ws:
            assert ws in manager._active_workspaces
            raise RuntimeError("Test error")

    # Workspace should be closed despite exception
    assert len(manager._active_workspaces) == 0


@pytest.mark.asyncio
async def test_workspace_close_all(tmp_path):
    """Test close_all closes all active workspaces."""
    manager = WorkspaceManager()

    # Open multiple workspaces
    ws1_path = tmp_path / "ws1"
    ws2_path = tmp_path / "ws2"
    ws1_path.mkdir()
    ws2_path.mkdir()

    async with manager.open_workspace(ws1_path) as ws1:
        async with manager.open_workspace(ws2_path) as ws2:
            assert len(manager._active_workspaces) == 2

            # Close all
            await manager.close_all()
            assert len(manager._active_workspaces) == 0


@pytest.mark.asyncio
async def test_workspace_open_invalid_path():
    """Test opening invalid workspace raises WorkspaceError."""
    manager = WorkspaceManager()

    with pytest.raises(WorkspaceError, match="Failed to open workspace"):
        async with manager.open_workspace("/nonexistent/path") as ws:
            pass
```

**File:** `tests/test_error_formatting.py` (NEW FILE)

```python
"""Tests for error message formatting."""

import pytest
from cairn.error_formatting import (
    format_agent_error,
    format_workspace_error,
    format_lifecycle_error,
)


def test_format_agent_error_basic():
    """Test basic agent error formatting."""
    msg = format_agent_error("Task failed", agent_id="agent-123")
    assert "Task failed" in msg
    assert "agent_id=agent-123" in msg


def test_format_agent_error_with_state():
    """Test agent error formatting with state."""
    msg = format_agent_error(
        "Execution failed",
        agent_id="agent-123",
        state="EXECUTING"
    )
    assert "agent_id=agent-123" in msg
    assert "state=EXECUTING" in msg


def test_format_agent_error_with_context():
    """Test agent error formatting with additional context."""
    msg = format_agent_error(
        "Merge failed",
        agent_id="agent-123",
        state="SUBMITTING",
        conflicts=["file1.py", "file2.py"],
        retry_count=3
    )
    assert "agent_id=agent-123" in msg
    assert "state=SUBMITTING" in msg
    assert "conflicts=2" in msg  # List length
    assert "retry_count=3" in msg


def test_format_agent_error_long_task():
    """Test task description is truncated."""
    long_task = "A" * 100
    msg = format_agent_error(
        "Error",
        agent_id="agent-123",
        task=long_task
    )
    assert "..." in msg  # Truncated
    assert len(msg) < len(long_task) + 50  # Significantly shorter


def test_format_workspace_error():
    """Test workspace error formatting."""
    msg = format_workspace_error(
        "Merge failed",
        workspace_path="/path/to/ws",
        operation="merge"
    )
    assert "Merge failed" in msg
    assert "workspace=/path/to/ws" in msg
    assert "operation=merge" in msg


def test_format_lifecycle_error():
    """Test lifecycle error formatting."""
    msg = format_lifecycle_error(
        "Version conflict",
        agent_id="agent-123",
        version=5
    )
    assert "Version conflict" in msg
    assert "agent_id=agent-123" in msg
    assert "version=5" in msg
```

### Integration Test Scenarios

**File:** `tests/test_resource_cleanup_integration.py` (NEW FILE)

```python
"""Integration tests for resource cleanup."""

import pytest
from cairn.orchestrator import CairnOrchestrator


@pytest.mark.asyncio
async def test_orchestrator_cleanup_on_error(tmp_path):
    """Test orchestrator cleans up resources on error."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)

    # Queue an agent that will fail
    await orchestrator.handle_command(
        QueueCommand(
            agent_id="failing-agent",
            task="This will fail",
            priority=5
        )
    )

    # Start orchestrator (will fail)
    try:
        await orchestrator.start()
    except Exception:
        pass

    # Verify cleanup
    assert len(orchestrator.workspace_manager._active_workspaces) == 0
    assert orchestrator._semaphore._value == orchestrator.config.max_concurrent_agents


@pytest.mark.asyncio
async def test_orchestrator_shutdown_cleanup(tmp_path):
    """Test orchestrator shutdown cleans up all resources."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)

    # Start orchestrator
    await orchestrator.start()

    # Queue some agents
    await orchestrator.handle_command(
        QueueCommand(agent_id="agent-1", task="task 1", priority=5)
    )

    # Shutdown
    await orchestrator.shutdown()

    # Verify cleanup
    assert len(orchestrator.workspace_manager._active_workspaces) == 0
    assert not orchestrator._running
```

---

## Files to Create

1. `cairn/workspace_manager.py` - Workspace lifecycle management
2. `cairn/error_formatting.py` - Error message formatting utilities
3. `tests/test_workspace_manager.py` - Workspace manager tests
4. `tests/test_error_formatting.py` - Error formatting tests
5. `tests/test_resource_cleanup_integration.py` - Integration tests

---

## Files to Modify

1. `cairn/orchestrator.py`
   - Use WorkspaceManager for all workspace operations
   - Add proper finally blocks
   - Add shutdown method
   - Improve error messages with context

2. `cairn/watcher.py`
   - Log warnings instead of silent failures
   - Add error context

3. `cairn/signals.py`
   - Add try-finally for signal file cleanup
   - Improve error handling

4. Any other files with resource management

---

## Validation Criteria

### Success Criteria
- ✅ All workspaces use context managers
- ✅ Semaphore always released in finally block
- ✅ All error messages include context
- ✅ No silent failures (all logged)
- ✅ All tests pass
- ✅ Resource leak tests pass
- ✅ Shutdown cleanup verified

### Breaking Changes
- None - this is internal refactoring
- API unchanged

### Rollback Plan
If issues arise, revert all changes in this step.

---

## Notes for Implementer

### Time Estimates
- Create workspace_manager.py: 1.5 hours
- Create error_formatting.py: 0.5 hours
- Update orchestrator.py: 1.5 hours
- Update watcher.py, signals.py: 0.5 hours
- Write tests: 1.5 hours
- Integration testing: 0.5 hours
- **Total: 6 hours**

---

## References

- CODE_REVIEW.md - Issue #7 (Missing Workspace Cleanup)
- CODE_REVIEW.md - Issue #9 (Inconsistent Error Messages)
- orchestrator.py:286 (Workspace close without finally)
- watcher.py:40-46 (Silent failures)
