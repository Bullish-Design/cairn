from __future__ import annotations

import time
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.runtime.agent import AgentContext, AgentState
from cairn.core.exceptions import RecoverableError
from cairn.orchestrator.lifecycle import LifecycleRecord, LifecycleStore
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.orchestrator.queue import TaskPriority


@pytest.mark.asyncio
@pytest.mark.integration
async def test_lifecycle_save_retries_on_transient_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = await Fsdantic.open(path=str(tmp_path / "lifecycle.db"))

    try:
        store = LifecycleStore(workspace)
        now = time.time()
        record = LifecycleRecord(
            agent_id="retry-lifecycle",
            task="task",
            priority=1,
            state=AgentState.QUEUED,
            created_at=now,
            state_changed_at=now,
            db_path=str(tmp_path / "agent.db"),
        )

        calls = 0
        original_save = store.repo.save

        async def flaky_save(agent_id: str, payload: LifecycleRecord) -> None:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("temporary")
            await original_save(agent_id, payload)

        monkeypatch.setattr(store.repo, "save", flaky_save)

        await store.save(record)
        assert calls == 3
    finally:
        await workspace.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_orchestrator_lifecycle_retry_on_recoverable_error(tmp_path: Path) -> None:
    orch = CairnOrchestrator(project_root=tmp_path, cairn_home=tmp_path / "home")
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    calls = 0

    class FlakyLifecycle:
        async def load(self, agent_id: str) -> None:
            _ = agent_id
            return None

        async def save(self, record: LifecycleRecord) -> None:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RecoverableError("transient")

    orch.lifecycle = FlakyLifecycle()

    ctx = AgentContext(
        agent_id="agent-retry",
        task="task",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_db_path=tmp_path / "agent.db",
    )

    await orch._save_lifecycle_record(ctx)

    assert calls == 3
