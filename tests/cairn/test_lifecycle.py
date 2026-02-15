from __future__ import annotations

import time
from pathlib import Path

import pytest
from fsdantic import AgentFSOptions, Fsdantic

from cairn.agent import AgentState
from cairn.lifecycle import LifecycleRecord, LifecycleStore


@pytest.mark.asyncio
async def test_lifecycle_store_typed_kv_roundtrip_and_active_filter(tmp_path: Path) -> None:
    workspace = await Fsdantic.open_with_options(AgentFSOptions(path=str(tmp_path / "lifecycle.db")))

    try:
        store = LifecycleStore(workspace)

        now = time.time()
        reviewing = LifecycleRecord(
            agent_id="agent-active",
            task="review",
            priority=2,
            state=AgentState.REVIEWING,
            created_at=now,
            state_changed_at=now,
            db_path=str(tmp_path / "agent-active.db"),
        )
        accepted = LifecycleRecord(
            agent_id="agent-terminal",
            task="done",
            priority=2,
            state=AgentState.ACCEPTED,
            created_at=now,
            state_changed_at=now,
            db_path=str(tmp_path / "agent-terminal.db"),
        )

        await store.save(reviewing)
        await store.save(accepted)

        loaded = await store.load("agent-active")
        assert loaded is not None
        assert loaded.agent_id == "agent-active"
        assert loaded.state is AgentState.REVIEWING

        active = await store.list_active()
        assert [record.agent_id for record in active] == ["agent-active"]

        old_time = now - 1000
        errored_db = tmp_path / "agent-error.db"
        errored_db.write_text("placeholder", encoding="utf-8")
        errored = LifecycleRecord(
            agent_id="agent-error",
            task="boom",
            priority=1,
            state=AgentState.ERRORED,
            created_at=old_time,
            state_changed_at=old_time,
            db_path=str(errored_db),
        )
        await store.save(errored)

        cleaned = await store.cleanup_old(max_age_seconds=10, agentfs_dir=tmp_path)
        assert cleaned == 1
        assert await store.load("agent-error") is None
        assert not errored_db.exists()
    finally:
        await workspace.close()


def test_lifecycle_record_rejects_invalid_timestamp(tmp_path: Path) -> None:
    now = time.time()
    with pytest.raises(ValueError, match="state_changed_at"):
        LifecycleRecord(
            agent_id="agent-bad",
            task="x",
            priority=1,
            state=AgentState.QUEUED,
            created_at=now,
            state_changed_at=now - 1,
            db_path=str(tmp_path / "agent-bad.db"),
        )
