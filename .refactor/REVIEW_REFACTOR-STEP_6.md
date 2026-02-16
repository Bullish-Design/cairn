# Refactoring Step 6: Concurrency & Performance

## Overview
This step addresses race conditions, improves signal polling efficiency, and adds resource management optimizations. These changes improve the reliability and performance of concurrent agent execution and reduce unnecessary resource consumption.

**Priority:** 🟡 HIGH
**Estimated Effort:** 6-8 hours
**Dependencies:**
- Step 1 (uses VersionConflictError)
- Step 3 (resource cleanup patterns)
- Step 4 (retry logic)

---

## Issues Addressed

### Issue #1: Race Condition in Lifecycle Persistence
**Location:** `orchestrator.py:444-473`
**Severity:** HIGH

**Problem:**
```python
async def _save_lifecycle_record(self, ctx: AgentContext) -> None:
    existing = await self.lifecycle.load(ctx.agent_id)  # Race here
    record = LifecycleRecord(...)
    if existing:
        record.version = existing.version  # Stale version possible
    await self.lifecycle.save(record)
```

Multiple concurrent updates can create version conflicts.

### Issue #4: Signal Polling Inefficiency
**Location:** `signals.py:38-46`
**Severity:** MEDIUM

**Problem:**
```python
while True:
    await asyncio.sleep(0.5)  # Polls every 500ms
    await self.process_signals_once()
```

Inefficient polling wastes CPU - should use filesystem events.

### Additional Performance Issues

**From CODE_REVIEW.md:**
- No workspace caching limits (memory leak risk)
- `active_agents` dict grows unbounded
- No limit on queue size (memory exhaustion risk)

---

## Detailed Implementation Steps

### 1. Implement Optimistic Locking for Lifecycle

**File:** `cairn/lifecycle.py`

```python
from cairn.exceptions import VersionConflictError
from cairn.error_formatting import format_lifecycle_error


class LifecycleRecord(BaseModel):
    """Lifecycle record with version for optimistic locking."""
    agent_id: str
    state: str
    timestamp: float
    version: int = 0  # Version for optimistic locking
    error: str | None = None
    submission_path: str | None = None
    metadata: dict[str, Any] = {}


class LifecycleStore:
    """Lifecycle persistence with optimistic locking."""

    async def save(self, record: LifecycleRecord) -> None:
        """Save record with version check.

        Args:
            record: Record to save

        Raises:
            VersionConflictError: If version conflict detected
        """
        # Check if record exists
        existing = await self.load(record.agent_id)

        if existing:
            # Verify version matches
            if existing.version != record.version:
                raise VersionConflictError(
                    format_lifecycle_error(
                        "Version conflict - record was modified concurrently",
                        agent_id=record.agent_id,
                        version=record.version,
                        expected_version=existing.version
                    ),
                    error_code="VERSION_CONFLICT",
                    context={
                        "agent_id": record.agent_id,
                        "expected_version": existing.version,
                        "provided_version": record.version,
                    }
                )

            # Increment version for update
            record.version = existing.version + 1
        else:
            # New record starts at version 1
            record.version = 1

        # Save to storage
        await self._write_record(record)

    async def _write_record(self, record: LifecycleRecord) -> None:
        """Write record to storage (implementation specific)."""
        # Implementation depends on your storage backend
        # Could be file-based, database, etc.
        pass

    async def update_atomic(
        self,
        agent_id: str,
        update_fn: Callable[[LifecycleRecord], None],
        max_retries: int = 5,
    ) -> LifecycleRecord:
        """Atomically update a record with automatic retry on conflict.

        Args:
            agent_id: Agent identifier
            update_fn: Function to update record (modifies in-place)
            max_retries: Maximum retry attempts on version conflict

        Returns:
            Updated record

        Raises:
            VersionConflictError: If conflicts persist after retries
            LifecycleError: If record not found
        """
        for attempt in range(1, max_retries + 1):
            # Load current version
            record = await self.load(agent_id)
            if not record:
                raise LifecycleError(
                    f"Cannot update non-existent record: {agent_id}",
                    error_code="LIFECYCLE_NOT_FOUND",
                    context={"agent_id": agent_id}
                )

            # Apply update
            update_fn(record)

            # Try to save
            try:
                await self.save(record)
                return record

            except VersionConflictError as exc:
                if attempt >= max_retries:
                    logger.error(
                        f"Failed to update lifecycle after {max_retries} attempts",
                        extra={
                            "agent_id": agent_id,
                            "attempts": max_retries
                        }
                    )
                    raise

                # Brief delay before retry with exponential backoff
                delay = 0.05 * (2 ** (attempt - 1))
                logger.debug(
                    f"Version conflict on attempt {attempt}, retrying in {delay}s",
                    extra={"agent_id": agent_id, "attempt": attempt}
                )
                await asyncio.sleep(delay)

        # Should not reach here
        raise VersionConflictError("Unexpected retry exhaustion")
```

### 2. Update Orchestrator to Use Atomic Updates

**File:** `cairn/orchestrator.py`

```python
async def _save_lifecycle_record(self, ctx: AgentContext) -> None:
    """Save lifecycle record with optimistic locking.

    Args:
        ctx: Agent context

    This replaces the previous implementation that had race conditions.
    """
    def update_record(record: LifecycleRecord) -> None:
        """Update record with current context."""
        record.state = ctx.state.value
        record.timestamp = time.time()
        record.error = ctx.error
        record.submission_path = str(ctx.submission_path) if ctx.submission_path else None

    try:
        # Check if record exists
        existing = await self.lifecycle.load(ctx.agent_id)

        if existing:
            # Update existing record atomically
            await self.lifecycle.update_atomic(ctx.agent_id, update_record)
        else:
            # Create new record
            record = LifecycleRecord(
                agent_id=ctx.agent_id,
                state=ctx.state.value,
                timestamp=time.time(),
                error=ctx.error,
                submission_path=str(ctx.submission_path) if ctx.submission_path else None,
            )
            await self.lifecycle.save(record)

    except VersionConflictError:
        # This should be rare after retry logic
        logger.error(
            "Persistent version conflict saving lifecycle",
            extra={"agent_id": ctx.agent_id, "state": ctx.state.value}
        )
        # Don't fail the agent - lifecycle is secondary to execution
        # The version conflict means another process updated it, which is okay
        pass
```

### 3. Replace Signal Polling with Filesystem Events

**File:** `cairn/signals.py`

```python
from watchfiles import awatch, Change
import asyncio
from pathlib import Path


class SignalProcessor:
    """Signal processor using filesystem events instead of polling."""

    def __init__(self, signals_dir: Path):
        self.signals_dir = signals_dir
        self._running = False
        self._watch_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start watching for signal files."""
        if self._running:
            return

        self._running = True
        self._watch_task = asyncio.create_task(self._watch_signals())

    async def stop(self) -> None:
        """Stop watching for signal files."""
        self._running = False

        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass

    async def _watch_signals(self) -> None:
        """Watch signal directory for new files using filesystem events.

        This replaces the previous polling implementation with efficient
        filesystem event monitoring.
        """
        logger.info(f"Watching signals directory: {self.signals_dir}")

        # Process any existing signals first
        await self.process_signals_once()

        try:
            # Watch for changes in signals directory
            async for changes in awatch(
                self.signals_dir,
                watch_filter=lambda change, path: str(path).endswith('.json'),
                stop_event=None,  # Run until cancelled
            ):
                if not self._running:
                    break

                # Process new/modified signal files
                for change_type, path in changes:
                    if change_type in (Change.added, Change.modified):
                        await self._process_signal_file(Path(path))

        except asyncio.CancelledError:
            logger.info("Signal watching cancelled")
            raise

        except Exception as exc:
            logger.exception("Error in signal watcher", extra={"error": str(exc)})
            raise

    async def _process_signal_file(self, signal_file: Path) -> None:
        """Process a single signal file.

        Args:
            signal_file: Path to signal JSON file
        """
        try:
            # Read signal
            content = signal_file.read_text(encoding="utf-8")
            signal = json.loads(content)

            # Dispatch signal
            await self._dispatch_signal(signal)

            logger.debug(
                f"Processed signal: {signal_file.name}",
                extra={"signal_file": str(signal_file)}
            )

        except json.JSONDecodeError as exc:
            logger.error(
                f"Invalid signal JSON: {signal_file}",
                extra={"file": str(signal_file), "error": str(exc)}
            )

        except Exception as exc:
            logger.exception(
                f"Error processing signal: {signal_file}",
                extra={"file": str(signal_file)}
            )

        finally:
            # Always try to remove processed signal
            try:
                signal_file.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(
                    f"Failed to remove signal file: {signal_file}",
                    extra={"file": str(signal_file), "error": str(exc)}
                )

    async def process_signals_once(self) -> None:
        """Process all pending signals (for initial sweep).

        This is still useful for processing existing signals on startup.
        """
        signal_files = list(self.signals_dir.glob("*.json"))

        for signal_file in signal_files:
            await self._process_signal_file(signal_file)
```

### 4. Add Workspace Cache with LRU Eviction

**File:** `cairn/workspace_cache.py` (NEW FILE)

```python
"""LRU cache for workspace objects to limit memory usage."""

import asyncio
from collections import OrderedDict
from typing import Any
from pathlib import Path

from cairn.constants import MAX_WORKSPACE_CACHE_SIZE


class WorkspaceCache:
    """LRU cache for workspace objects with size limit."""

    def __init__(self, max_size: int = MAX_WORKSPACE_CACHE_SIZE):
        """Initialize workspace cache.

        Args:
            max_size: Maximum number of cached workspaces
        """
        self.max_size = max_size
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Get workspace from cache.

        Args:
            key: Cache key (typically workspace path)

        Returns:
            Cached workspace or None if not found
        """
        async with self._lock:
            if key in self._cache:
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    async def put(self, key: str, workspace: Any) -> None:
        """Put workspace in cache.

        Args:
            key: Cache key
            workspace: Workspace object to cache

        If cache is full, evicts least recently used item.
        """
        async with self._lock:
            if key in self._cache:
                # Update and move to end
                self._cache[key] = workspace
                self._cache.move_to_end(key)
            else:
                # Add new entry
                self._cache[key] = workspace

                # Evict oldest if over limit
                if len(self._cache) > self.max_size:
                    oldest_key, oldest_ws = self._cache.popitem(last=False)
                    # Close evicted workspace
                    try:
                        if hasattr(oldest_ws, 'close'):
                            await oldest_ws.close()
                    except Exception as exc:
                        import logging
                        logging.warning(
                            f"Error closing evicted workspace: {exc}",
                            extra={"key": oldest_key}
                        )

    async def remove(self, key: str) -> None:
        """Remove workspace from cache.

        Args:
            key: Cache key
        """
        async with self._lock:
            if key in self._cache:
                workspace = self._cache.pop(key)
                # Close removed workspace
                try:
                    if hasattr(workspace, 'close'):
                        await workspace.close()
                except Exception:
                    pass

    async def clear(self) -> None:
        """Clear all cached workspaces."""
        async with self._lock:
            # Close all workspaces
            for workspace in self._cache.values():
                try:
                    if hasattr(workspace, 'close'):
                        await workspace.close()
                except Exception:
                    pass

            self._cache.clear()

    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)
```

### 5. Add Queue Size Limit and Backpressure

**File:** `cairn/queue.py`

```python
from cairn.exceptions import ResourceLimitError


class TaskQueue:
    """Priority queue with size limit and backpressure."""

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
            # Check size limit
            if self.max_size > 0 and len(self._queue) >= self.max_size:
                raise ResourceLimitError(
                    f"Queue is full: {len(self._queue)} tasks (max: {self.max_size})",
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

    def size(self) -> int:
        """Get current queue size."""
        return len(self._queue)

    def is_full(self) -> bool:
        """Check if queue is at capacity."""
        return self.max_size > 0 and len(self._queue) >= self.max_size
```

### 6. Add Active Agents Limit

**File:** `cairn/orchestrator.py`

```python
from cairn.workspace_cache import WorkspaceCache


class CairnOrchestrator:
    """Orchestrator with bounded resources."""

    def __init__(self, ...):
        # Existing init...

        # Add workspace cache
        self.workspace_cache = WorkspaceCache(max_size=100)

        # Active agents dict (no longer unbounded)
        self.active_agents: dict[str, AgentContext] = {}

        # Queue with size limit
        self.queue = TaskQueue(max_size=1000)

    async def _cleanup_completed_agent(self, agent_id: str) -> None:
        """Clean up completed agent from active tracking.

        Args:
            agent_id: Agent identifier
        """
        if agent_id in self.active_agents:
            ctx = self.active_agents.pop(agent_id)

            # Close workspaces
            if ctx.agent_fs:
                try:
                    await ctx.agent_fs.close()
                except Exception:
                    pass

            if ctx.stable_fs:
                try:
                    await ctx.stable_fs.close()
                except Exception:
                    pass

        # Remove from workspace cache if present
        cache_key = f"agent_{agent_id}"
        await self.workspace_cache.remove(cache_key)

    async def _run_agent(self, agent_id: str) -> None:
        """Run agent with proper cleanup."""
        try:
            # ... existing agent execution logic

            pass

        finally:
            # Always clean up
            await self._cleanup_completed_agent(agent_id)
            self._semaphore.release()
```

---

## Testing Requirements

### Unit Tests

**File:** `tests/test_lifecycle_locking.py`

```python
"""Tests for optimistic locking in lifecycle operations."""

import pytest
from cairn.lifecycle import LifecycleStore, LifecycleRecord
from cairn.exceptions import VersionConflictError


@pytest.mark.asyncio
async def test_version_conflict_detection(tmp_path):
    """Test version conflict is detected."""
    store = LifecycleStore(tmp_path)

    # Create initial record
    record1 = LifecycleRecord(agent_id="test", state="QUEUED", version=1)
    await store.save(record1)

    # Load two copies
    copy1 = await store.load("test")
    copy2 = await store.load("test")

    # Update first copy
    copy1.state = "GENERATING"
    await store.save(copy1)

    # Updating second copy should fail (stale version)
    copy2.state = "EXECUTING"
    with pytest.raises(VersionConflictError):
        await store.save(copy2)


@pytest.mark.asyncio
async def test_atomic_update_retries(tmp_path):
    """Test atomic update retries on conflict."""
    store = LifecycleStore(tmp_path)

    # Create initial record
    record = LifecycleRecord(agent_id="test", state="QUEUED")
    await store.save(record)

    # Atomic update should handle conflicts
    updated = await store.update_atomic(
        "test",
        lambda r: setattr(r, "state", "GENERATING") or r
    )

    assert updated.state == "GENERATING"
    assert updated.version > 1
```

**File:** `tests/test_workspace_cache.py`

```python
"""Tests for workspace LRU cache."""

import pytest
from cairn.workspace_cache import WorkspaceCache


@pytest.mark.asyncio
async def test_cache_lru_eviction():
    """Test LRU eviction when cache is full."""
    cache = WorkspaceCache(max_size=3)

    # Add 3 items
    await cache.put("a", {"data": "A"})
    await cache.put("b", {"data": "B"})
    await cache.put("c", {"data": "C"})

    assert cache.size() == 3

    # Add 4th item - should evict 'a' (oldest)
    await cache.put("d", {"data": "D"})

    assert cache.size() == 3
    assert await cache.get("a") is None  # Evicted
    assert await cache.get("b") is not None
    assert await cache.get("c") is not None
    assert await cache.get("d") is not None


@pytest.mark.asyncio
async def test_cache_access_updates_lru():
    """Test accessing item updates LRU order."""
    cache = WorkspaceCache(max_size=3)

    await cache.put("a", {"data": "A"})
    await cache.put("b", {"data": "B"})
    await cache.put("c", {"data": "C"})

    # Access 'a' to make it most recent
    await cache.get("a")

    # Add 'd' - should evict 'b' (now oldest)
    await cache.put("d", {"data": "D"})

    assert await cache.get("a") is not None  # Not evicted
    assert await cache.get("b") is None  # Evicted
```

---

## Files to Create

1. `cairn/workspace_cache.py` - LRU workspace cache
2. `tests/test_lifecycle_locking.py` - Locking tests
3. `tests/test_workspace_cache.py` - Cache tests
4. `tests/test_signal_events.py` - Signal event tests

---

## Files to Modify

1. `cairn/lifecycle.py` - Add optimistic locking
2. `cairn/orchestrator.py` - Use atomic updates, workspace cache
3. `cairn/signals.py` - Replace polling with filesystem events
4. `cairn/queue.py` - Add size limit
5. `pyproject.toml` - Add `watchfiles` dependency if not present

---

## Validation Criteria

### Success Criteria
- ✅ No race conditions in lifecycle updates
- ✅ Signal processing uses filesystem events (no polling)
- ✅ Workspace cache prevents unbounded memory growth
- ✅ Queue size limited to prevent exhaustion
- ✅ All concurrency tests pass

---

## Notes for Implementer

### Time Estimates
- Lifecycle locking: 2 hours
- Signal filesystem events: 2 hours
- Workspace cache: 2 hours
- Queue limits: 1 hour
- Tests: 2 hours
- **Total: 9 hours**

---

## References

- CODE_REVIEW.md - Issue #1 (Race Condition)
- CODE_REVIEW.md - Issue #4 (Signal Polling)
- CODE_REVIEW.md - Section 7.3 (Memory Usage)
