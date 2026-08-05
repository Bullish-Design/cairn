"""Project integration lock tests (review §3.2, §3.7)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cairn.runtime.integration import IntegrationLock


@pytest.mark.asyncio
async def test_lock_is_exclusive_between_instances(tmp_path: Path) -> None:
    """While one holder has the lock, a second (distinct) holder on the same
    lockfile blocks; it proceeds only after the first releases.  A holder on a
    different lockfile does not contend (per-project locking)."""
    lock_path = tmp_path / ".agentfs" / "integration.lock"
    other_path = tmp_path / ".agentfs" / "other.lock"

    async with IntegrationLock(lock_path):
        # Different lockfile: no contention.
        async with IntegrationLock(other_path):
            pass

        acquired = asyncio.Event()

        async def second_holder() -> None:
            async with IntegrationLock(lock_path):
                acquired.set()

        task = asyncio.create_task(second_holder())
        await asyncio.sleep(0.05)
        assert acquired.is_set() is False, "second holder acquired the lock while the first held it"
        # release, then the second holder proceeds
    await asyncio.wait_for(task, timeout=5)
    assert acquired.is_set()


@pytest.mark.asyncio
async def test_lock_reacquires_after_release(tmp_path: Path) -> None:
    """After release the same lockfile is acquirable again."""
    lock_path = tmp_path / ".agentfs" / "integration.lock"
    for _ in range(3):
        async with IntegrationLock(lock_path):
            pass  # each acquisition succeeds sequentially
