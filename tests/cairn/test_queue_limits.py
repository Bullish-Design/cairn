from __future__ import annotations

import pytest

from cairn.core.exceptions import ResourceLimitError
from cairn.orchestrator.queue import TaskPriority, TaskQueue


@pytest.mark.asyncio
async def test_queue_enforces_max_size() -> None:
    queue = TaskQueue(max_size=1)

    await queue.enqueue("task-1", TaskPriority.NORMAL)

    with pytest.raises(ResourceLimitError):
        await queue.enqueue("task-2", TaskPriority.NORMAL)

    assert queue.is_full()


@pytest.mark.asyncio
async def test_queue_remove_removes_task() -> None:
    """P4.8: TaskQueue.remove drops a queued task by id."""
    from cairn.orchestrator.queue import TaskQueue

    q = TaskQueue()
    await q.enqueue("agent-1")
    await q.enqueue("agent-2")
    assert q.size() == 2

    assert await q.remove("agent-1") is True
    assert q.size() == 1
    assert await q.remove("agent-1") is False
    assert await q.remove("agent-2") is True
    assert q.size() == 0
