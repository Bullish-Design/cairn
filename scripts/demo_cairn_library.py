#!/usr/bin/env python3
"""Small executable demo to validate core Cairn library behavior.

This script intentionally targets modules that have no external runtime dependencies
in order to run in restricted environments.

Run from repository root:
    python3 scripts/demo_cairn_library.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    """Load a module directly from source without importing `cairn.__init__`."""
    source_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module at {source_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


queue_mod = load_module("cairn_queue", "src/cairn/queue.py")
retry_mod = load_module("cairn_retry", "src/cairn/retry.py")

TaskPriority = queue_mod.TaskPriority
TaskQueue = queue_mod.TaskQueue
RetryStrategy = retry_mod.RetryStrategy


async def check_task_queue() -> None:
    queue = TaskQueue()

    await queue.enqueue("normal task", TaskPriority.NORMAL)
    await queue.enqueue("urgent task", TaskPriority.URGENT)
    await queue.enqueue("low task", TaskPriority.LOW)

    first = await queue.dequeue_wait()
    second = await queue.dequeue_wait()
    third = await queue.dequeue_wait()

    assert [first.task, second.task, third.task] == [
        "urgent task",
        "normal task",
        "low task",
    ], "TaskQueue priority ordering failed"


async def check_retry_strategy() -> None:
    attempts = 0
    retry = RetryStrategy(max_attempts=3, initial_delay=0.01, max_delay=0.02)

    async def flaky_operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient failure")
        return "ok"

    result = await retry.with_retry(flaky_operation, retry_exceptions=(RuntimeError,))
    assert result == "ok"
    assert attempts == 3, "RetryStrategy should have required exactly 3 attempts"


async def main() -> None:
    await check_task_queue()
    await check_retry_strategy()

    print("✅ Cairn core demo passed")
    print("   - TaskQueue priority scheduling works")
    print("   - RetryStrategy retries transient failures")


if __name__ == "__main__":
    asyncio.run(main())
