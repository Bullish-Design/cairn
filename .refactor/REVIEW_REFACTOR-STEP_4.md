# Refactoring Step 4: Retry Logic Integration

## Overview
This step integrates the existing but unused `RetryStrategy` module into critical operations throughout the codebase. The retry module already exists with a complete implementation, but it's never actually used. This step adds retry logic to workspace operations, code provider fetches, lifecycle persistence, and signal processing to improve reliability.

**Priority:** 🟡 HIGH
**Estimated Effort:** 4-6 hours
**Dependencies:**
- Step 1 (uses RecoverableError exception hierarchy)
- Step 3 (proper resource cleanup)

---

## Issues Addressed

### Issue #3: RetryStrategy Module Unused
**Severity:** MEDIUM
**File:** `retry.py` (entire module)

**Problem:**
- Complete retry implementation exists with `RetryStrategy` class
- Module is never imported or used anywhere in codebase
- Critical operations lack retry logic for transient failures

**Critical operations needing retries:**
1. Lifecycle persistence (database writes)
2. Code provider fetches (network operations)
3. Workspace operations (I/O operations)
4. Grail script loading
5. Signal processing

---

## Detailed Implementation Steps

### 1. Review Existing Retry Module

First, let's understand what we have:

**File:** `cairn/retry.py` (existing)

The module should contain:
- `RetryStrategy` class with exponential backoff
- Configuration for max attempts, initial delay, backoff factor
- Exception filtering (which exceptions to retry)
- Timeout handling

If the existing implementation needs any adjustments, document them here.

### 2. Create Retry Decorators for Common Patterns

**File:** `cairn/retry_utils.py` (NEW FILE)

```python
"""Retry utilities and decorators for common retry patterns.

This module provides convenient decorators and helpers built on top of
the RetryStrategy class for common retry scenarios.
"""

import asyncio
import functools
import logging
from typing import Any, Awaitable, Callable, TypeVar

from cairn.retry import RetryStrategy
from cairn.exceptions import RecoverableError, TimeoutError as CairnTimeoutError
from cairn.constants import (
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_INITIAL_DELAY_SECONDS,
    DEFAULT_RETRY_BACKOFF_FACTOR,
    DEFAULT_RETRY_MAX_DELAY_SECONDS,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_retry(
    *,
    max_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    initial_delay: float = DEFAULT_RETRY_INITIAL_DELAY_SECONDS,
    backoff_factor: float = DEFAULT_RETRY_BACKOFF_FACTOR,
    max_delay: float = DEFAULT_RETRY_MAX_DELAY_SECONDS,
    retry_on: tuple[type[Exception], ...] = (RecoverableError,),
    operation_name: str | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator to add retry logic to async functions.

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        backoff_factor: Multiplier for delay after each attempt
        max_delay: Maximum delay between retries
        retry_on: Tuple of exception types to retry on
        operation_name: Name for logging (defaults to function name)

    Returns:
        Decorated function with retry logic

    Example:
        @with_retry(max_attempts=3, retry_on=(IOError, TimeoutError))
        async def fetch_data():
            # This will retry on IOError or TimeoutError
            return await network_call()
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            strategy = RetryStrategy(
                max_attempts=max_attempts,
                initial_delay=initial_delay,
                backoff_factor=backoff_factor,
                max_delay=max_delay,
            )

            name = operation_name or func.__name__
            attempt = 0
            last_exception = None

            async for attempt_num in strategy:
                attempt = attempt_num
                try:
                    result = await func(*args, **kwargs)
                    if attempt > 1:
                        logger.info(
                            f"Operation '{name}' succeeded after {attempt} attempts"
                        )
                    return result

                except retry_on as exc:
                    last_exception = exc
                    logger.warning(
                        f"Operation '{name}' failed on attempt {attempt}/{max_attempts}: {exc}",
                        extra={
                            "operation": name,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "exception_type": type(exc).__name__,
                        }
                    )

                    if attempt >= max_attempts:
                        logger.error(
                            f"Operation '{name}' failed after {max_attempts} attempts",
                            extra={
                                "operation": name,
                                "attempts": max_attempts,
                            }
                        )
                        raise

                    # Calculate delay for this attempt
                    delay = strategy.get_delay(attempt)
                    logger.debug(f"Retrying '{name}' in {delay:.2f}s")
                    await asyncio.sleep(delay)

            # Should not reach here, but just in case
            if last_exception:
                raise last_exception
            raise RuntimeError(f"Retry logic exhausted for {name}")

        return wrapper

    return decorator


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    initial_delay: float = DEFAULT_RETRY_INITIAL_DELAY_SECONDS,
    backoff_factor: float = DEFAULT_RETRY_BACKOFF_FACTOR,
    retry_on: tuple[type[Exception], ...] = (RecoverableError,),
    operation_name: str = "operation",
) -> T:
    """Retry an async operation with exponential backoff.

    Args:
        operation: Async callable to retry
        max_attempts: Maximum number of attempts
        initial_delay: Initial delay before first retry
        backoff_factor: Multiplier for delay after each attempt
        retry_on: Tuple of exception types to retry on
        operation_name: Name for logging

    Returns:
        Result of the operation

    Raises:
        Last exception if all retries exhausted

    Example:
        result = await retry_async(
            lambda: fetch_from_network(),
            max_attempts=3,
            retry_on=(NetworkError, TimeoutError)
        )
    """
    strategy = RetryStrategy(
        max_attempts=max_attempts,
        initial_delay=initial_delay,
        backoff_factor=backoff_factor,
    )

    last_exception = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()

        except retry_on as exc:
            last_exception = exc
            logger.warning(
                f"{operation_name} failed on attempt {attempt}/{max_attempts}: {exc}"
            )

            if attempt >= max_attempts:
                logger.error(
                    f"{operation_name} failed after {max_attempts} attempts"
                )
                raise

            delay = initial_delay * (backoff_factor ** (attempt - 1))
            await asyncio.sleep(delay)

    if last_exception:
        raise last_exception
    raise RuntimeError("Retry logic error")


# Predefined retry configurations for common scenarios

def workspace_retry() -> dict[str, Any]:
    """Get retry configuration for workspace operations."""
    return {
        "max_attempts": 3,
        "initial_delay": 0.1,
        "backoff_factor": 2.0,
        "retry_on": (IOError, OSError, RecoverableError),
    }


def network_retry() -> dict[str, Any]:
    """Get retry configuration for network operations."""
    return {
        "max_attempts": 3,
        "initial_delay": 1.0,
        "backoff_factor": 2.0,
        "retry_on": (ConnectionError, TimeoutError, RecoverableError),
    }


def database_retry() -> dict[str, Any]:
    """Get retry configuration for database operations."""
    return {
        "max_attempts": 5,
        "initial_delay": 0.1,
        "backoff_factor": 2.0,
        "retry_on": (RecoverableError,),
    }
```

### 3. Add Retry to Lifecycle Persistence

**File:** `cairn/lifecycle.py`

```python
from cairn.retry_utils import with_retry, database_retry
from cairn.exceptions import VersionConflictError, LifecycleError, RecoverableError


class LifecycleStore:
    """Lifecycle record storage with retry logic."""

    @with_retry(
        **database_retry(),
        operation_name="lifecycle_save"
    )
    async def save(self, record: LifecycleRecord) -> None:
        """Save lifecycle record with retry on transient failures.

        Args:
            record: Lifecycle record to save

        Raises:
            VersionConflictError: If version conflict occurs (retriable)
            LifecycleError: If save fails after retries
        """
        try:
            await self._save_impl(record)
        except Exception as exc:
            raise LifecycleError(
                f"Failed to save lifecycle record: {record.agent_id}",
                error_code="LIFECYCLE_SAVE_FAILED",
                context={"agent_id": record.agent_id, "version": record.version}
            ) from exc

    @with_retry(
        **database_retry(),
        operation_name="lifecycle_load"
    )
    async def load(self, agent_id: str) -> LifecycleRecord | None:
        """Load lifecycle record with retry on transient failures.

        Args:
            agent_id: Agent identifier

        Returns:
            Lifecycle record or None if not found

        Raises:
            LifecycleError: If load fails after retries
        """
        try:
            return await self._load_impl(agent_id)
        except Exception as exc:
            raise LifecycleError(
                f"Failed to load lifecycle record: {agent_id}",
                error_code="LIFECYCLE_LOAD_FAILED",
                context={"agent_id": agent_id}
            ) from exc

    @with_retry(
        max_attempts=3,
        initial_delay=0.1,
        backoff_factor=2.0,
        retry_on=(VersionConflictError,),
        operation_name="lifecycle_update"
    )
    async def update_with_version_check(
        self,
        agent_id: str,
        update_fn: Callable[[LifecycleRecord], LifecycleRecord],
    ) -> LifecycleRecord:
        """Update record with optimistic locking and retry.

        Args:
            agent_id: Agent identifier
            update_fn: Function to update the record

        Returns:
            Updated lifecycle record

        Raises:
            VersionConflictError: If version conflicts persist after retries
        """
        # Load current record
        current = await self.load(agent_id)
        if not current:
            raise LifecycleError(
                f"Cannot update non-existent record: {agent_id}",
                error_code="LIFECYCLE_NOT_FOUND",
                context={"agent_id": agent_id}
            )

        # Apply update
        updated = update_fn(current)
        updated.version = current.version + 1

        # Try to save with version check
        try:
            await self._save_with_version_check(updated, expected_version=current.version)
            return updated
        except VersionConflictError:
            # Will be retried by decorator
            raise
```

### 4. Add Retry to Code Provider Operations

**File:** `cairn/providers.py`

```python
from cairn.retry_utils import with_retry, network_retry
from cairn.exceptions import ProviderError, RecoverableError


class FileCodeProvider:
    """File-based code provider with retry logic."""

    @with_retry(
        **workspace_retry(),
        operation_name="provider_fetch_code"
    )
    async def fetch_code(self, agent_id: str) -> str:
        """Fetch agent code from file with retry.

        Args:
            agent_id: Agent identifier

        Returns:
            Agent code content

        Raises:
            ProviderError: If fetch fails after retries
        """
        try:
            return await self._fetch_code_impl(agent_id)
        except Exception as exc:
            raise ProviderError(
                f"Failed to fetch code for agent: {agent_id}",
                error_code="PROVIDER_FETCH_FAILED",
                context={"agent_id": agent_id, "provider": "file"}
            ) from exc


class PluginCodeProvider:
    """Plugin-based code provider with retry for network operations."""

    @with_retry(
        **network_retry(),
        operation_name="plugin_fetch_code"
    )
    async def fetch_code(self, agent_id: str) -> str:
        """Fetch agent code from plugin with retry.

        Args:
            agent_id: Agent identifier

        Returns:
            Agent code content

        Raises:
            ProviderError: If fetch fails after retries
        """
        try:
            return await self._plugin_fetch_impl(agent_id)
        except Exception as exc:
            raise ProviderError(
                f"Failed to fetch code from plugin: {agent_id}",
                error_code="PLUGIN_FETCH_FAILED",
                context={"agent_id": agent_id, "plugin": self.plugin_name}
            ) from exc
```

### 5. Add Retry to Workspace Operations

**File:** `cairn/workspace_manager.py`

```python
from cairn.retry_utils import with_retry, workspace_retry


class WorkspaceManager:
    """Workspace manager with retry logic for I/O operations."""

    @with_retry(
        **workspace_retry(),
        operation_name="workspace_merge"
    )
    async def merge_workspace(
        self,
        source_ws: Workspace,
        target_ws: Workspace,
    ) -> MergeResult:
        """Merge source workspace into target with retry.

        Args:
            source_ws: Source workspace
            target_ws: Target workspace

        Returns:
            Merge result with success status and conflicts

        Raises:
            WorkspaceMergeError: If merge fails after retries
        """
        try:
            return await self._merge_impl(source_ws, target_ws)
        except Exception as exc:
            raise WorkspaceMergeError(
                "Workspace merge failed",
                error_code="WORKSPACE_MERGE_FAILED",
                context={
                    "source": str(source_ws.path),
                    "target": str(target_ws.path),
                }
            ) from exc
```

### 6. Add Retry to Orchestrator Grail Script Loading

**File:** `cairn/orchestrator.py`

```python
from cairn.retry_utils import with_retry, workspace_retry


class CairnOrchestrator:
    """Orchestrator with retry logic for critical operations."""

    @with_retry(
        **workspace_retry(),
        operation_name="load_grail_script"
    )
    async def _load_grail_script_with_retry(self, script_path: Path) -> Any:
        """Load Grail script with retry on transient failures.

        Args:
            script_path: Path to Grail script

        Returns:
            Loaded Grail script object

        Raises:
            RecoverableError: If load fails but may succeed on retry
            FatalError: If load fails permanently
        """
        try:
            return _load_grail_script(script_path)
        except (IOError, OSError) as exc:
            # I/O errors are recoverable
            raise RecoverableError(
                f"Failed to load Grail script: {script_path}",
                error_code="GRAIL_LOAD_IO_ERROR",
                context={"path": str(script_path)}
            ) from exc
        except Exception as exc:
            # Other errors are likely permanent (syntax errors, etc.)
            raise FatalError(
                f"Failed to load Grail script: {script_path}",
                error_code="GRAIL_LOAD_FAILED",
                context={"path": str(script_path)}
            ) from exc
```

### 7. Add Retry to Signal Processing

**File:** `cairn/signals.py`

```python
from cairn.retry_utils import with_retry, workspace_retry


class SignalProcessor:
    """Signal processor with retry logic."""

    @with_retry(
        **workspace_retry(),
        operation_name="process_signal"
    )
    async def _process_signal(self, signal_file: Path) -> None:
        """Process a single signal file with retry.

        Args:
            signal_file: Path to signal JSON file

        Raises:
            RecoverableError: If processing fails transiently
        """
        try:
            # Read signal
            content = signal_file.read_text(encoding="utf-8")
            signal = json.loads(content)

            # Dispatch signal
            await self._dispatch_signal(signal)

        except (IOError, OSError) as exc:
            raise RecoverableError(
                f"Failed to read signal file: {signal_file}",
                error_code="SIGNAL_READ_FAILED",
                context={"path": str(signal_file)}
            ) from exc
        except json.JSONDecodeError as exc:
            # JSON errors are not recoverable - bad file
            raise FatalError(
                f"Invalid signal JSON: {signal_file}",
                error_code="SIGNAL_INVALID_JSON",
                context={"path": str(signal_file)}
            ) from exc
```

---

## Testing Requirements

### Unit Tests to Add/Update

**File:** `tests/test_retry_utils.py` (NEW FILE)

```python
"""Tests for retry utilities and decorators."""

import pytest
import asyncio
from cairn.retry_utils import with_retry, retry_async
from cairn.exceptions import RecoverableError


@pytest.mark.asyncio
async def test_with_retry_success_first_attempt():
    """Test retry decorator succeeds on first attempt."""
    call_count = 0

    @with_retry(max_attempts=3)
    async def operation():
        nonlocal call_count
        call_count += 1
        return "success"

    result = await operation()
    assert result == "success"
    assert call_count == 1


@pytest.mark.asyncio
async def test_with_retry_success_after_failures():
    """Test retry decorator succeeds after initial failures."""
    call_count = 0

    @with_retry(max_attempts=3, retry_on=(RecoverableError,))
    async def operation():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RecoverableError("Transient failure")
        return "success"

    result = await operation()
    assert result == "success"
    assert call_count == 3


@pytest.mark.asyncio
async def test_with_retry_exhausts_attempts():
    """Test retry decorator raises after max attempts."""
    call_count = 0

    @with_retry(max_attempts=3, retry_on=(RecoverableError,), initial_delay=0.01)
    async def operation():
        nonlocal call_count
        call_count += 1
        raise RecoverableError("Always fails")

    with pytest.raises(RecoverableError):
        await operation()

    assert call_count == 3


@pytest.mark.asyncio
async def test_with_retry_non_retriable_exception():
    """Test retry decorator doesn't retry non-retriable exceptions."""
    call_count = 0

    @with_retry(max_attempts=3, retry_on=(RecoverableError,))
    async def operation():
        nonlocal call_count
        call_count += 1
        raise ValueError("Not retriable")

    with pytest.raises(ValueError):
        await operation()

    assert call_count == 1  # No retries


@pytest.mark.asyncio
async def test_retry_async_with_lambda():
    """Test retry_async with lambda operation."""
    call_count = 0

    async def operation():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise RecoverableError("Fail once")
        return "success"

    result = await retry_async(
        operation,
        max_attempts=3,
        retry_on=(RecoverableError,),
        initial_delay=0.01
    )

    assert result == "success"
    assert call_count == 2
```

**File:** `tests/test_lifecycle_retry.py` (NEW FILE)

```python
"""Tests for lifecycle operations with retry logic."""

import pytest
from cairn.lifecycle import LifecycleStore, LifecycleRecord
from cairn.exceptions import VersionConflictError


@pytest.mark.asyncio
async def test_lifecycle_save_retries_on_recoverable_error(tmp_path):
    """Test lifecycle save retries on recoverable errors."""
    store = LifecycleStore(tmp_path)
    record = LifecycleRecord(agent_id="test-123", state="QUEUED")

    # Mock implementation that fails twice then succeeds
    call_count = 0
    original_save = store._save_impl

    async def mock_save(rec):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RecoverableError("Transient failure")
        return await original_save(rec)

    store._save_impl = mock_save

    # Should succeed after retries
    await store.save(record)
    assert call_count == 3


@pytest.mark.asyncio
async def test_lifecycle_version_conflict_retry():
    """Test lifecycle update retries on version conflict."""
    store = LifecycleStore(tmp_path)
    record = LifecycleRecord(agent_id="test-123", state="QUEUED", version=1)

    await store.save(record)

    # Simulate version conflict on first attempt
    attempt_count = 0

    original_save_with_version = store._save_with_version_check

    async def mock_save_with_version(rec, expected_version):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            raise VersionConflictError("Version mismatch")
        return await original_save_with_version(rec, expected_version)

    store._save_with_version_check = mock_save_with_version

    # Should retry and succeed
    updated = await store.update_with_version_check(
        "test-123",
        lambda r: LifecycleRecord(**{**r.__dict__, "state": "GENERATING"})
    )

    assert updated.state == "GENERATING"
    assert attempt_count >= 1
```

### Integration Test Scenarios

**File:** `tests/test_retry_integration.py` (NEW FILE)

```python
"""Integration tests for retry logic across components."""

import pytest
from cairn.orchestrator import CairnOrchestrator


@pytest.mark.asyncio
async def test_orchestrator_retries_lifecycle_operations(tmp_path):
    """Test orchestrator retries lifecycle operations on failure."""
    orchestrator = CairnOrchestrator(project_root=tmp_path)

    # Simulate transient lifecycle failure
    # Should retry and succeed
    # (Implementation depends on your test setup)
    pass


@pytest.mark.asyncio
async def test_provider_retries_on_network_error(tmp_path):
    """Test code provider retries on network errors."""
    # Test with mock network provider that fails initially
    pass
```

---

## Files to Create

1. `cairn/retry_utils.py` - Retry decorators and utilities
2. `tests/test_retry_utils.py` - Retry utility tests
3. `tests/test_lifecycle_retry.py` - Lifecycle retry tests
4. `tests/test_retry_integration.py` - Integration tests

---

## Files to Modify

1. `cairn/lifecycle.py` - Add retry to save/load operations
2. `cairn/providers.py` - Add retry to code fetch operations
3. `cairn/workspace_manager.py` - Add retry to merge operations
4. `cairn/orchestrator.py` - Add retry to Grail script loading
5. `cairn/signals.py` - Add retry to signal processing

---

## Validation Criteria

### Success Criteria
- ✅ Retry logic applied to all critical operations
- ✅ Transient failures handled gracefully
- ✅ Exponential backoff working correctly
- ✅ All tests pass including new retry tests
- ✅ No infinite retry loops
- ✅ Proper logging of retry attempts

### Breaking Changes
- None - this adds resilience without changing APIs

### Rollback Plan
If issues arise, revert all changes in this step.

---

## Notes for Implementer

### Time Estimates
- Create retry_utils.py: 2 hours
- Update lifecycle.py: 1 hour
- Update providers.py: 0.5 hours
- Update workspace_manager.py: 0.5 hours
- Update orchestrator.py: 0.5 hours
- Update signals.py: 0.5 hours
- Write tests: 2 hours
- Integration testing: 1 hour
- **Total: 8 hours**

---

## References

- CODE_REVIEW.md - Issue #3 (RetryStrategy Module Unused)
- CODE_REVIEW.md - Section 4.2 (Error Recovery)
- retry.py (existing implementation)
- orchestrator.py:109 (No retry on workspace open)
