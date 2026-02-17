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
