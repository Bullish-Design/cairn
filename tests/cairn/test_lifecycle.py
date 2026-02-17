from __future__ import annotations

import time
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.runtime.agent import AgentState
from cairn.core.exceptions import LifecycleError
from cairn.orchestrator.lifecycle import LifecycleRecord, LifecycleStore


@pytest.mark.asyncio
async def test_lifecycle_store_typed_kv_roundtrip_and_active_filter(tmp_path: Path) -> None:
    workspace = await Fsdantic.open(path=str(tmp_path / "lifecycle.db"))

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


class _FlakyLifecycleRepo:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.calls = 0

    async def save(self, key: str, record: LifecycleRecord) -> None:
        _ = key
        _ = record
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)


@pytest.mark.asyncio
async def test_lifecycle_store_retries_recoverable_errors() -> None:
    record = LifecycleRecord(
        agent_id="agent-retry",
        task="retry",
        priority=1,
        state=AgentState.QUEUED,
        created_at=time.time(),
        state_changed_at=time.time(),
        db_path="/tmp/agent-retry.db",
    )

    store = object.__new__(LifecycleStore)
    store.repo = _FlakyLifecycleRepo([ConnectionError("temporary"), ConnectionError("temporary")])

    await store.save(record)

    assert store.repo.calls == 3


@pytest.mark.asyncio
async def test_lifecycle_store_fails_fast_for_non_retryable_errors() -> None:
    record = LifecycleRecord(
        agent_id="agent-fail-fast",
        task="fail",
        priority=1,
        state=AgentState.QUEUED,
        created_at=time.time(),
        state_changed_at=time.time(),
        db_path="/tmp/agent-fail-fast.db",
    )

    store = object.__new__(LifecycleStore)
    store.repo = _FlakyLifecycleRepo([ValueError("permanent")])

    with pytest.raises(LifecycleError, match="Failed to save lifecycle record"):
        await store.save(record)

    assert store.repo.calls == 1


@pytest.mark.asyncio
async def test_lifecycle_store_recoverable_retry_exhaustion_raises_last_error() -> None:
    now = time.time()
    record = LifecycleRecord(
        agent_id="agent-retry-exhausted",
        task="retry exhausted",
        priority=1,
        state=AgentState.QUEUED,
        created_at=now,
        state_changed_at=now,
        db_path="/tmp/agent-retry-exhausted.db",
    )

    store = object.__new__(LifecycleStore)
    store.repo = _FlakyLifecycleRepo(
        [ConnectionError("temporary 1"), ConnectionError("temporary 2"), ConnectionError("temporary 3")]
    )

    with pytest.raises(ConnectionError, match="temporary 3"):
        await store.save(record)

    assert store.repo.calls == 3
