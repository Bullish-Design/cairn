"""Priority task queue for orchestrated agent work."""

from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from enum import Enum

from cairn.core.constants import DEFAULT_MAX_QUEUE_SIZE
from cairn.core.exceptions import ResourceLimitError
from cairn.orchestrator.orchestrator_helpers import calculate_priority_score


class TaskPriority(int, Enum):
    """Task scheduling priority."""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


@dataclass(order=True)
class QueuedTask:
    """Task entry stored in the orchestrator queue."""

    _sort_key: tuple[int, float] = field(init=False, repr=False)
    task: str = field(compare=False)
    priority: TaskPriority = field(default=TaskPriority.NORMAL, compare=False)
    created_at: float = field(default_factory=time.time, compare=False)

    def __post_init__(self) -> None:
        self._sort_key = self._calculate_sort_key()

    def _calculate_sort_key(self) -> tuple[int, float]:
        """Build the sort key used for heap ordering."""
        return calculate_priority_score(int(self.priority), self.created_at)


class TaskQueue:
    """Plain async priority queue with bounded capacity."""

    def __init__(self, max_size: int = DEFAULT_MAX_QUEUE_SIZE) -> None:
        self._queue: list[QueuedTask] = []
        self._condition = asyncio.Condition()
        self.max_size = max_size

    async def enqueue(self, task: QueuedTask | str, priority: TaskPriority = TaskPriority.NORMAL) -> None:
        """Add task to queue."""
        queued_task = task if isinstance(task, QueuedTask) else QueuedTask(task=task, priority=priority)

        async with self._condition:
            if self.is_full():
                raise ResourceLimitError(
                    f"Queue is full: {len(self._queue)} tasks (max: {self.max_size})",
                    error_code="QUEUE_FULL",
                    context={"current_size": len(self._queue), "max_size": self.max_size},
                )
            heapq.heappush(self._queue, queued_task)
            self._condition.notify()

    async def dequeue(self) -> QueuedTask | None:
        """Get next task or None when queue is empty."""
        async with self._condition:
            if not self._queue:
                return None

            return heapq.heappop(self._queue)

    async def dequeue_wait(self) -> QueuedTask:
        """Wait until one task is available and return it."""
        async with self._condition:
            await self._condition.wait_for(lambda: bool(self._queue))
            return heapq.heappop(self._queue)

    async def peek(self) -> QueuedTask | None:
        """View the next task without removing it."""
        async with self._condition:
            if not self._queue:
                return None
            return self._queue[0]

    def size(self) -> int:
        """Get current queue size."""
        return len(self._queue)

    def is_empty(self) -> bool:
        """Check whether the queue is empty."""
        return len(self._queue) == 0

    def is_full(self) -> bool:
        """Check whether the queue is at capacity."""
        return self.max_size > 0 and len(self._queue) >= self.max_size

    async def list_all(self) -> list[QueuedTask]:
        """Return a snapshot of all queued tasks."""
        async with self._condition:
            return list(self._queue)

    async def clear(self) -> None:
        """Remove all tasks from the queue."""
        async with self._condition:
            self._queue.clear()
