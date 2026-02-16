# Refactoring Step 8: Testing Infrastructure

## Overview
This step adds comprehensive test coverage including integration tests, concurrency tests, failure injection tests, and CLI tests. This validates all the refactoring work from previous steps and ensures the system works correctly end-to-end.

**Priority:** 🔴 CRITICAL
**Estimated Effort:** 10-12 hours (largest step)
**Dependencies:** All previous steps (Steps 1-7)

---

## Issues Addressed

### Test Coverage Gaps from CODE_REVIEW.md

**Critical Gaps:**
1. **No integration tests** - Missing end-to-end workflow tests
2. **No concurrency tests** - Race conditions not tested
3. **No failure injection** - Transient failure handling not tested
4. **No CLI tests** - Command-line interface not tested
5. **Missing plugin tests** - Plugin providers have minimal coverage

**Missing Test Scenarios:**
- Concurrent agent state transitions
- Orchestrator crash recovery
- Workspace merge conflicts
- Resource exhaustion
- Plugin failures
- State recovery after crash

---

## Detailed Implementation Steps

### 1. Integration Tests - End-to-End Workflows

**File:** `tests/integration/test_e2e_workflows.py` (NEW FILE)

```python
"""End-to-end integration tests for complete agent workflows."""

import pytest
import asyncio
from pathlib import Path

from cairn.orchestrator import CairnOrchestrator
from cairn.commands import QueueCommand, AcceptCommand, RejectCommand
from cairn.agent import AgentState


@pytest.fixture
async def orchestrator(tmp_path):
    """Create orchestrator instance for testing."""
    orch = CairnOrchestrator(
        project_root=tmp_path,
        cairn_home=tmp_path / ".cairn"
    )
    yield orch
    await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complete_agent_lifecycle(orchestrator, tmp_path):
    """Test complete agent lifecycle from queue to acceptance.

    This test validates:
    - Queueing an agent
    - Starting orchestrator
    - Agent progresses through all states
    - Accepting agent changes
    - Changes merged to stable workspace
    """
    # Queue an agent
    await orchestrator.handle_command(
        QueueCommand(
            agent_id="test-agent-001",
            task="Create a hello world function in hello.py",
            priority=5
        )
    )

    # Start orchestrator (in background)
    orchestrator_task = asyncio.create_task(orchestrator.start())

    # Wait for agent to complete (with timeout)
    max_wait = 30.0
    start_time = asyncio.get_event_loop().time()

    while True:
        lifecycle = await orchestrator.lifecycle.load("test-agent-001")

        if lifecycle and lifecycle.state in ["REVIEWING", "ACCEPTED", "REJECTED", "ERRORED"]:
            break

        if asyncio.get_event_loop().time() - start_time > max_wait:
            pytest.fail("Agent did not complete within timeout")

        await asyncio.sleep(0.5)

    # Stop orchestrator
    await orchestrator.stop()
    await orchestrator_task

    # Verify agent reached REVIEWING state
    lifecycle = await orchestrator.lifecycle.load("test-agent-001")
    assert lifecycle is not None
    assert lifecycle.state == "REVIEWING"
    assert lifecycle.error is None

    # Accept the agent
    await orchestrator.handle_command(
        AcceptCommand(agent_id="test-agent-001")
    )

    # Verify acceptance
    lifecycle = await orchestrator.lifecycle.load("test-agent-001")
    assert lifecycle.state == "ACCEPTED"

    # Verify file exists in stable workspace
    hello_file = tmp_path / "hello.py"
    assert hello_file.exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_agent_rejection_workflow(orchestrator):
    """Test rejecting an agent's changes."""
    await orchestrator.handle_command(
        QueueCommand(
            agent_id="test-agent-002",
            task="Test task",
            priority=5
        )
    )

    # Run until reviewing
    orchestrator_task = asyncio.create_task(orchestrator.start())
    # ... wait for REVIEWING state ...
    await orchestrator.stop()

    # Reject the agent
    await orchestrator.handle_command(
        RejectCommand(
            agent_id="test-agent-002",
            reason="Test rejection"
        )
    )

    lifecycle = await orchestrator.lifecycle.load("test-agent-002")
    assert lifecycle.state == "REJECTED"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_multiple_agents_sequential(orchestrator):
    """Test multiple agents processed sequentially."""
    # Queue multiple agents
    agent_ids = []
    for i in range(3):
        agent_id = f"agent-{i:03d}"
        agent_ids.append(agent_id)
        await orchestrator.handle_command(
            QueueCommand(
                agent_id=agent_id,
                task=f"Task {i}",
                priority=5
            )
        )

    # Run orchestrator
    orchestrator_task = asyncio.create_task(orchestrator.start())

    # Wait for all to complete
    # ... wait logic ...

    await orchestrator.stop()
    await orchestrator_task

    # Verify all agents completed
    for agent_id in agent_ids:
        lifecycle = await orchestrator.lifecycle.load(agent_id)
        assert lifecycle is not None
        assert lifecycle.state in ["REVIEWING", "ACCEPTED"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_agent_error_handling(orchestrator):
    """Test agent that encounters an error during execution."""
    # Create agent with task that will fail
    await orchestrator.handle_command(
        QueueCommand(
            agent_id="failing-agent",
            task="This task will cause an error",
            priority=5
        )
    )

    # Mock provider to cause error
    # ... setup error condition ...

    orchestrator_task = asyncio.create_task(orchestrator.start())
    # ... wait for completion ...
    await orchestrator.stop()

    lifecycle = await orchestrator.lifecycle.load("failing-agent")
    assert lifecycle.state == "ERRORED"
    assert lifecycle.error is not None
```

### 2. Concurrency Tests

**File:** `tests/integration/test_concurrency.py` (NEW FILE)

```python
"""Tests for concurrent agent execution and race conditions."""

import pytest
import asyncio
from cairn.orchestrator import CairnOrchestrator
from cairn.commands import QueueCommand


@pytest.mark.asyncio
@pytest.mark.slow
async def test_concurrent_agent_execution(tmp_path):
    """Test multiple agents executing concurrently."""
    orchestrator = CairnOrchestrator(
        project_root=tmp_path,
        config=OrchestratorSettings(max_concurrent_agents=3)
    )

    # Queue many agents
    agent_ids = [f"concurrent-agent-{i:03d}" for i in range(10)]
    for agent_id in agent_ids:
        await orchestrator.handle_command(
            QueueCommand(
                agent_id=agent_id,
                task="Concurrent test task",
                priority=5
            )
        )

    # Run orchestrator
    orchestrator_task = asyncio.create_task(orchestrator.start())

    # Monitor concurrent execution
    max_concurrent = 0
    while orchestrator.queue.size() > 0 or len(orchestrator.active_agents) > 0:
        current_concurrent = len(orchestrator.active_agents)
        max_concurrent = max(max_concurrent, current_concurrent)
        await asyncio.sleep(0.1)

    await orchestrator.stop()
    await orchestrator_task

    # Verify concurrency was limited
    assert max_concurrent <= 3
    assert max_concurrent > 0  # At least some concurrency

    # Verify all agents completed
    for agent_id in agent_ids:
        lifecycle = await orchestrator.lifecycle.load(agent_id)
        assert lifecycle is not None


@pytest.mark.asyncio
@pytest.mark.slow
async def test_lifecycle_concurrent_updates(tmp_path):
    """Test concurrent updates to same lifecycle record."""
    from cairn.lifecycle import LifecycleStore, LifecycleRecord
    from cairn.exceptions import VersionConflictError

    store = LifecycleStore(tmp_path / "lifecycle.db")

    # Create initial record
    record = LifecycleRecord(agent_id="concurrent-test", state="QUEUED")
    await store.save(record)

    # Attempt concurrent updates
    async def update_state(new_state: str) -> bool:
        """Try to update state, return success."""
        try:
            await store.update_atomic(
                "concurrent-test",
                lambda r: setattr(r, "state", new_state)
            )
            return True
        except VersionConflictError:
            return False

    # Run multiple updates concurrently
    results = await asyncio.gather(
        update_state("GENERATING"),
        update_state("EXECUTING"),
        update_state("SUBMITTING"),
        return_exceptions=True
    )

    # At least one should succeed
    successes = sum(1 for r in results if r is True)
    assert successes >= 1

    # Final record should have one of the states
    final = await store.load("concurrent-test")
    assert final.state in ["GENERATING", "EXECUTING", "SUBMITTING"]


@pytest.mark.asyncio
@pytest.mark.slow
async def test_workspace_merge_conflicts(tmp_path):
    """Test handling of workspace merge conflicts."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)

    # Queue two agents that modify same file
    await orchestrator.handle_command(
        QueueCommand(
            agent_id="agent-a",
            task="Modify shared.py line 1",
            priority=5
        )
    )

    await orchestrator.handle_command(
        QueueCommand(
            agent_id="agent-b",
            task="Modify shared.py line 2",
            priority=5
        )
    )

    # Run both
    orchestrator_task = asyncio.create_task(orchestrator.start())
    # ... wait for both to complete ...
    await orchestrator.stop()

    # Accept first agent
    await orchestrator.handle_command(AcceptCommand(agent_id="agent-a"))

    # Accept second agent - may have conflicts
    await orchestrator.handle_command(AcceptCommand(agent_id="agent-b"))

    # Verify conflict handling
    # (exact behavior depends on merge strategy)
```

### 3. Failure Injection Tests

**File:** `tests/integration/test_failure_injection.py` (NEW FILE)

```python
"""Tests for handling transient failures with retry logic."""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock

from cairn.orchestrator import CairnOrchestrator
from cairn.exceptions import RecoverableError, WorkspaceError


@pytest.mark.asyncio
async def test_retry_on_workspace_failure(tmp_path):
    """Test retry logic for workspace operations."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)

    call_count = 0

    async def failing_workspace_op(*args, **kwargs):
        """Fail twice, then succeed."""
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RecoverableError("Transient workspace error")
        return "success"

    # Patch workspace operation
    with patch.object(
        orchestrator.workspace_manager,
        'open_workspace',
        side_effect=failing_workspace_op
    ):
        # This should retry and succeed
        # ... test agent execution ...
        pass

    # Verify retries occurred
    assert call_count >= 2


@pytest.mark.asyncio
async def test_retry_on_provider_failure(tmp_path):
    """Test retry logic for code provider failures."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)

    call_count = 0

    async def failing_provider(*args, **kwargs):
        """Fail once, then succeed."""
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise RecoverableError("Network timeout")
        return "# Generated code"

    orchestrator.code_provider.fetch_code = failing_provider

    # Queue and run agent
    # ... agent execution ...

    # Verify retry occurred
    assert call_count == 2


@pytest.mark.asyncio
async def test_lifecycle_persistence_retry(tmp_path):
    """Test retry on lifecycle persistence failures."""
    from cairn.lifecycle import LifecycleStore, LifecycleRecord
    from cairn.exceptions import VersionConflictError

    store = LifecycleStore(tmp_path / "lifecycle.db")

    # Create record
    record = LifecycleRecord(agent_id="test", state="QUEUED")
    await store.save(record)

    # Simulate version conflict that resolves
    conflict_count = 0

    original_save = store._write_record

    async def mock_save(rec):
        nonlocal conflict_count
        if conflict_count < 2:
            conflict_count += 1
            # Increment version to simulate concurrent update
            existing = await store.load(rec.agent_id)
            if existing:
                existing.version += 1
                await original_save(existing)
            raise VersionConflictError("Simulated conflict")
        return await original_save(rec)

    store._write_record = mock_save

    # Update should retry and succeed
    updated = await store.update_atomic(
        "test",
        lambda r: setattr(r, "state", "GENERATING")
    )

    assert updated.state == "GENERATING"
    assert conflict_count >= 1
```

### 4. CLI Tests

**File:** `tests/test_cli.py` (NEW FILE)

```python
"""Tests for CLI commands."""

import pytest
from typer.testing import CliRunner

from cairn.typer_cli import app


runner = CliRunner()


def test_cli_queue_command():
    """Test 'queue' CLI command."""
    result = runner.invoke(
        app,
        ["queue", "--agent-id", "test-cli-agent", "--task", "Test task", "--priority", "5"]
    )

    assert result.exit_code == 0
    assert "Queued agent" in result.stdout or "queued" in result.stdout.lower()


def test_cli_list_command():
    """Test 'list' CLI command."""
    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    # Should display agent list (may be empty)


def test_cli_inspect_command():
    """Test 'inspect' CLI command."""
    # First queue an agent
    runner.invoke(
        app,
        ["queue", "--agent-id", "inspect-test", "--task", "Test", "--priority", "5"]
    )

    # Then inspect it
    result = runner.invoke(app, ["inspect", "inspect-test"])

    assert result.exit_code == 0
    assert "inspect-test" in result.stdout


def test_cli_accept_command():
    """Test 'accept' CLI command."""
    result = runner.invoke(app, ["accept", "test-agent"])

    # May fail if agent doesn't exist, but command should parse
    assert result.exit_code in [0, 1]


def test_cli_reject_command():
    """Test 'reject' CLI command."""
    result = runner.invoke(
        app,
        ["reject", "test-agent", "--reason", "Test rejection"]
    )

    # May fail if agent doesn't exist, but command should parse
    assert result.exit_code in [0, 1]


def test_cli_start_command():
    """Test 'start' CLI command parsing."""
    # Don't actually start, just verify command parses
    result = runner.invoke(app, ["start", "--help"])

    assert result.exit_code == 0
    assert "start" in result.stdout.lower()


def test_cli_invalid_command():
    """Test CLI with invalid command."""
    result = runner.invoke(app, ["invalid-command"])

    assert result.exit_code != 0
```

### 5. Crash Recovery Tests

**File:** `tests/integration/test_crash_recovery.py` (NEW FILE)

```python
"""Tests for orchestrator crash recovery."""

import pytest
import asyncio
import signal
import os

from cairn.orchestrator import CairnOrchestrator
from cairn.commands import QueueCommand


@pytest.mark.asyncio
@pytest.mark.slow
async def test_orchestrator_restart_recovery(tmp_path):
    """Test orchestrator recovers state after restart.

    This simulates:
    1. Starting orchestrator
    2. Queueing agents
    3. Stopping orchestrator (simulated crash)
    4. Restarting orchestrator
    5. Verifying agents resume from correct state
    """
    # Create and start first orchestrator instance
    orch1 = CairnOrchestrator(
        project_root=tmp_path,
        cairn_home=tmp_path / ".cairn"
    )

    # Queue some agents
    agent_ids = ["recovery-test-1", "recovery-test-2"]
    for agent_id in agent_ids:
        await orch1.handle_command(
            QueueCommand(
                agent_id=agent_id,
                task="Recovery test task",
                priority=5
            )
        )

    # Start and let some agents begin
    task = asyncio.create_task(orch1.start())
    await asyncio.sleep(1.0)  # Let agents start

    # Abruptly stop (simulate crash)
    await orch1.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Create new orchestrator instance (restart)
    orch2 = CairnOrchestrator(
        project_root=tmp_path,
        cairn_home=tmp_path / ".cairn"
    )

    # Check lifecycle records were persisted
    for agent_id in agent_ids:
        lifecycle = await orch2.lifecycle.load(agent_id)
        assert lifecycle is not None
        assert lifecycle.state in ["QUEUED", "GENERATING", "EXECUTING", "SUBMITTING", "REVIEWING"]

    # Resume processing
    await orch2.start()
    # ... wait for completion ...
    await orch2.stop()


@pytest.mark.asyncio
async def test_partial_execution_recovery(tmp_path):
    """Test recovery when agent partially executed before crash."""
    orch1 = CairnOrchestrator(project_root=tmp_path)

    await orch1.handle_command(
        QueueCommand(
            agent_id="partial-exec",
            task="Test task",
            priority=5
        )
    )

    # Start execution
    task = asyncio.create_task(orch1.start())

    # Wait until agent is EXECUTING
    while True:
        lifecycle = await orch1.lifecycle.load("partial-exec")
        if lifecycle and lifecycle.state == "EXECUTING":
            break
        await asyncio.sleep(0.1)

    # Crash during execution
    await orch1.stop()
    task.cancel()

    # Restart
    orch2 = CairnOrchestrator(project_root=tmp_path)

    # Verify state preserved
    lifecycle = await orch2.lifecycle.load("partial-exec")
    assert lifecycle.state == "EXECUTING"

    # Should be able to resume or restart
    # (exact behavior depends on design choice)
```

### 6. Resource Exhaustion Tests

**File:** `tests/integration/test_resource_limits.py` (NEW FILE)

```python
"""Tests for resource limit enforcement."""

import pytest
import asyncio

from cairn.orchestrator import CairnOrchestrator
from cairn.commands import QueueCommand
from cairn.exceptions import ResourceLimitError, TimeoutError


@pytest.mark.asyncio
async def test_queue_size_limit(tmp_path):
    """Test queue rejects tasks when full."""
    orchestrator = CairnOrchestrator(
        project_root=tmp_path,
        config=OrchestratorSettings(max_queue_size=10)
    )

    # Fill queue
    for i in range(10):
        await orchestrator.handle_command(
            QueueCommand(
                agent_id=f"agent-{i}",
                task="Test task",
                priority=5
            )
        )

    # Next one should fail
    with pytest.raises(ResourceLimitError, match="Queue is full"):
        await orchestrator.handle_command(
            QueueCommand(
                agent_id="agent-overflow",
                task="Test task",
                priority=5
            )
        )


@pytest.mark.asyncio
async def test_execution_timeout_enforcement(tmp_path):
    """Test agent execution times out if too slow."""
    orchestrator = CairnOrchestrator(
        project_root=tmp_path,
        executor_settings=ExecutorSettings(max_execution_time=1.0)
    )

    # Create agent with slow task
    await orchestrator.handle_command(
        QueueCommand(
            agent_id="slow-agent",
            task="Sleep for 10 seconds",  # Will timeout
            priority=5
        )
    )

    # Mock slow execution
    # ... setup mock that sleeps ...

    task = asyncio.create_task(orchestrator.start())
    # ... wait ...
    await orchestrator.stop()

    # Verify timeout occurred
    lifecycle = await orchestrator.lifecycle.load("slow-agent")
    assert lifecycle.state == "ERRORED"
    assert "timeout" in lifecycle.error.lower()


@pytest.mark.asyncio
async def test_memory_limit_enforcement(tmp_path):
    """Test agent execution fails if memory limit exceeded."""
    orchestrator = CairnOrchestrator(
        project_root=tmp_path,
        executor_settings=ExecutorSettings(max_memory_bytes=10 * 1024 * 1024)  # 10MB
    )

    # Create agent that would use too much memory
    await orchestrator.handle_command(
        QueueCommand(
            agent_id="memory-hog",
            task="Allocate large array",
            priority=5
        )
    )

    # ... execute ...

    lifecycle = await orchestrator.lifecycle.load("memory-hog")
    # Should either complete within limit or error
    assert lifecycle.state in ["REVIEWING", "ERRORED"]


@pytest.mark.asyncio
async def test_workspace_cache_eviction(tmp_path):
    """Test workspace cache evicts old items."""
    from cairn.workspace_cache import WorkspaceCache

    cache = WorkspaceCache(max_size=5)

    # Add items beyond cache size
    for i in range(10):
        await cache.put(f"ws-{i}", {"data": i})

    # Cache should be at max size
    assert cache.size() == 5

    # Old items should be evicted
    assert await cache.get("ws-0") is None
    assert await cache.get("ws-9") is not None
```

### 7. Performance Regression Tests

**File:** `tests/performance/test_benchmarks.py` (EXPAND EXISTING)

```python
"""Performance benchmarks and regression tests."""

import pytest
import time
import asyncio

from cairn.orchestrator import CairnOrchestrator
from cairn.commands import QueueCommand


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_agent_throughput_benchmark(tmp_path, benchmark):
    """Benchmark agent processing throughput."""

    async def process_agents(count: int):
        """Process N agents and return elapsed time."""
        orch = CairnOrchestrator(project_root=tmp_path)

        # Queue agents
        for i in range(count):
            await orch.handle_command(
                QueueCommand(
                    agent_id=f"bench-{i}",
                    task="Benchmark task",
                    priority=5
                )
            )

        # Process
        start = time.time()
        await orch.start()
        # ... wait for completion ...
        await orch.stop()
        elapsed = time.time() - start

        return elapsed

    # Benchmark processing 10 agents
    result = benchmark(lambda: asyncio.run(process_agents(10)))

    # Assert reasonable performance
    # (adjust threshold based on your requirements)
    assert result < 30.0  # Should process 10 agents in under 30 seconds


@pytest.mark.benchmark
async def test_queue_operations_performance():
    """Benchmark queue operations."""
    from cairn.queue import TaskQueue, QueuedTask

    queue = TaskQueue()

    # Benchmark enqueue
    start = time.time()
    for i in range(1000):
        await queue.enqueue(
            QueuedTask(
                agent_id=f"agent-{i}",
                task="test",
                priority=5,
                created_at=time.time()
            )
        )
    enqueue_time = time.time() - start

    # Benchmark dequeue
    start = time.time()
    for i in range(1000):
        await queue.dequeue()
    dequeue_time = time.time() - start

    # Assert performance
    assert enqueue_time < 1.0  # 1000 enqueues in under 1 second
    assert dequeue_time < 1.0  # 1000 dequeues in under 1 second
```

---

## Test Organization

### Directory Structure

```
tests/
├── __init__.py
├── conftest.py                      # Shared fixtures
├── test_agent.py                    # Existing
├── test_lifecycle.py                # Existing
├── test_orchestrator.py             # Existing (expand)
├── test_cli.py                      # NEW
├── test_exceptions.py               # NEW (from Step 1)
├── test_constants.py                # NEW (from Step 1)
├── test_types.py                    # NEW (from Step 2)
├── test_workspace_manager.py        # NEW (from Step 3)
├── test_error_formatting.py         # NEW (from Step 3)
├── test_retry_utils.py              # NEW (from Step 4)
├── test_lifecycle_retry.py          # NEW (from Step 4)
├── test_regex_utils.py              # NEW (from Step 5)
├── test_secrets_detection.py        # NEW (from Step 5)
├── test_lifecycle_locking.py        # NEW (from Step 6)
├── test_workspace_cache.py          # NEW (from Step 6)
├── test_orchestrator_phases.py      # NEW (from Step 7)
│
├── integration/                     # NEW
│   ├── __init__.py
│   ├── test_e2e_workflows.py
│   ├── test_concurrency.py
│   ├── test_failure_injection.py
│   ├── test_crash_recovery.py
│   └── test_resource_limits.py
│
└── performance/                     # EXPAND
    ├── __init__.py
    └── test_benchmarks.py
```

### Pytest Configuration

**File:** `pyproject.toml` (UPDATE)

```toml
[tool.pytest.ini_options]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: marks tests as integration tests",
    "benchmark: marks tests as performance benchmarks",
]
testpaths = ["tests"]
asyncio_mode = "auto"

# Coverage settings
[tool.coverage.run]
source = ["cairn"]
omit = [
    "*/tests/*",
    "*/test_*.py",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```

### Shared Fixtures

**File:** `tests/conftest.py` (EXPAND)

```python
"""Shared pytest fixtures for all tests."""

import pytest
import asyncio
from pathlib import Path
import tempfile
import shutil

from cairn.orchestrator import CairnOrchestrator
from cairn.lifecycle import LifecycleStore


@pytest.fixture
def tmp_project(tmp_path):
    """Create temporary project directory."""
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.fixture
async def orchestrator(tmp_project):
    """Create orchestrator instance."""
    orch = CairnOrchestrator(
        project_root=tmp_project,
        cairn_home=tmp_project / ".cairn"
    )
    yield orch
    await orch.shutdown()


@pytest.fixture
async def lifecycle_store(tmp_path):
    """Create lifecycle store instance."""
    store = LifecycleStore(tmp_path / "lifecycle.db")
    yield store
    await store.close()


@pytest.fixture
def mock_code_provider():
    """Create mock code provider."""
    class MockProvider:
        async def fetch_code(self, agent_id: str) -> str:
            return f"# Generated code for {agent_id}\npass"

    return MockProvider()


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
```

---

## Testing Requirements Summary

### Unit Tests Coverage Goals
- ✅ All new modules have >80% coverage
- ✅ Critical paths have >95% coverage
- ✅ Error paths tested
- ✅ Edge cases covered

### Integration Tests Coverage
- ✅ Complete agent lifecycle
- ✅ Concurrent execution
- ✅ Failure scenarios
- ✅ Recovery scenarios
- ✅ Resource limits

### Performance Tests
- ✅ Throughput benchmarks
- ✅ Latency measurements
- ✅ Resource usage tracking
- ✅ Regression detection

---

## Validation Criteria

### Success Criteria
- ✅ All unit tests pass
- ✅ All integration tests pass
- ✅ CLI tests pass
- ✅ Code coverage >85%
- ✅ No flaky tests
- ✅ Performance benchmarks establish baseline
- ✅ Tests run in CI successfully

### Test Execution
```bash
# Run all tests
pytest

# Run only fast tests
pytest -m "not slow"

# Run integration tests
pytest tests/integration/

# Run with coverage
pytest --cov=cairn --cov-report=html

# Run benchmarks
pytest -m benchmark

# Run specific test file
pytest tests/integration/test_e2e_workflows.py -v
```

---

## Notes for Implementer

### Time Estimates
- Integration tests: 4 hours
- Concurrency tests: 2 hours
- Failure injection tests: 2 hours
- CLI tests: 1 hour
- Crash recovery tests: 2 hours
- Resource limit tests: 1.5 hours
- Performance tests: 1.5 hours
- Test fixtures and utilities: 1 hour
- **Total: 15 hours**

### Testing Best Practices

1. **Arrange-Act-Assert:** Structure tests clearly
2. **One assertion per test:** Keep tests focused
3. **Descriptive names:** Test names explain what they test
4. **Fast tests:** Keep unit tests fast, mark slow tests
5. **Isolated tests:** Tests don't depend on each other
6. **Clean up:** Use fixtures for setup and teardown
7. **Mock external dependencies:** Don't hit real services

---

## References

- CODE_REVIEW.md - Section 5 (Testing Analysis)
- CODE_REVIEW.md - Section 5.3 (Missing Test Scenarios)
- All previous refactoring steps (validates their changes)
