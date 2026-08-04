from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.core.exceptions import VersionConflictError
from cairn.orchestrator.lifecycle import LifecycleRecord, LifecycleStore
from cairn.runtime.agent import AgentState


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


@pytest.mark.asyncio
async def test_update_atomic_serialized_concurrent_updates(tmp_path: Path) -> None:
    """Concurrent same-process update_atomic calls are serialized by
    Workspace.serialized(): every update lands, none is lost, and no
    version conflicts are raised between in-process callers."""
    workspace = await Fsdantic.open(path=str(tmp_path / "lifecycle-serialized.db"))

    try:
        store = LifecycleStore(workspace)
        now = time.time()
        record = LifecycleRecord(
            agent_id="agent-serialized",
            task="start",
            priority=1,
            state=AgentState.QUEUED,
            created_at=now,
            state_changed_at=now,
            db_path=str(tmp_path / "agent-serialized.db"),
        )
        await store.save(record)

        async def bump() -> None:
            await store.update_atomic(
                "agent-serialized",
                lambda rec: setattr(rec, "priority", rec.priority + 1),
            )

        # All 10 concurrent increments must land (no lost updates, no
        # VersionConflictError surfacing from the in-process contention).
        await asyncio.gather(*(bump() for _ in range(10)))

        final = await store.load("agent-serialized")
        assert final is not None
        assert final.priority == 11
        # Each successful update bumped the version.
        assert final.version >= 11
    finally:
        await workspace.close()
