from __future__ import annotations

import time
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.runtime.agent import AgentState
from cairn.core.exceptions import VersionConflictError
from cairn.orchestrator.lifecycle import LifecycleRecord, LifecycleStore


@pytest.mark.asyncio
async def test_version_conflict_detection(tmp_path: Path) -> None:
    workspace = await Fsdantic.open(path=str(tmp_path / "lifecycle.db"))

    try:
        store = LifecycleStore(workspace)
        now = time.time()
        record = LifecycleRecord(
            agent_id="agent-locking",
            task="start",
            priority=1,
            state=AgentState.QUEUED,
            created_at=now,
            state_changed_at=now,
            db_path=str(tmp_path / "agent-locking.db"),
        )
        await store.save(record)

        first = await store.load("agent-locking")
        second = await store.load("agent-locking")
        assert first is not None
        assert second is not None

        first.state = AgentState.EXECUTING
        first.state_changed_at = time.time()
        await store.save(first)

        second.state = AgentState.REVIEWING
        second.state_changed_at = time.time()
        with pytest.raises(VersionConflictError):
            await store.save(second)
    finally:
        await workspace.close()


@pytest.mark.asyncio
async def test_atomic_update_retries(tmp_path: Path) -> None:
    workspace = await Fsdantic.open(path=str(tmp_path / "lifecycle-atomic.db"))

    try:
        store = LifecycleStore(workspace)
        now = time.time()
        record = LifecycleRecord(
            agent_id="agent-atomic",
            task="start",
            priority=1,
            state=AgentState.QUEUED,
            created_at=now,
            state_changed_at=now,
            db_path=str(tmp_path / "agent-atomic.db"),
        )
        await store.save(record)

        updated = await store.update_atomic(
            "agent-atomic",
            lambda rec: (
                setattr(rec, "state", AgentState.EXECUTING),
                setattr(rec, "state_changed_at", time.time()),
            ),
        )

        assert updated.state is AgentState.EXECUTING
        assert updated.version > 1
    finally:
        await workspace.close()
