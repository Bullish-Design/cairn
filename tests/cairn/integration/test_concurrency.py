from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.runtime.agent import AgentState
from cairn.orchestrator.lifecycle import LifecycleRecord, LifecycleStore
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import InlineCodeProvider
from cairn.runtime.settings import OrchestratorSettings


@pytest.mark.asyncio
@pytest.mark.slow
async def test_concurrent_agent_execution_respects_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "home",
        config=OrchestratorSettings(max_concurrent_agents=2),
        code_provider=InlineCodeProvider(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    await orch.initialize()

    active_counts: list[int] = []
    current = 0
    started = asyncio.Event()
    release = asyncio.Event()
    lock = asyncio.Lock()

    async def fake_run(agent_id: str) -> None:
        nonlocal current
        _ = agent_id
        async with lock:
            current += 1
            active_counts.append(current)
            if current >= 2:
                started.set()
        await release.wait()
        async with lock:
            current -= 1
        orch._semaphore.release()

    monkeypatch.setattr(orch, "_run_agent", fake_run)

    try:
        for i in range(4):
            await orch.spawn_agent(f"task-{i}")

        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert max(active_counts) <= 2

        release.set()
        await asyncio.sleep(0.05)
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.slow
async def test_lifecycle_concurrent_updates(tmp_path: Path) -> None:
    workspace = await Fsdantic.open(path=str(tmp_path / "lifecycle.db"))

    try:
        store = LifecycleStore(workspace)
        now = time.time()
        record = LifecycleRecord(
            agent_id="concurrent-update",
            task="start",
            priority=1,
            state=AgentState.QUEUED,
            created_at=now,
            state_changed_at=now,
            db_path=str(tmp_path / "agent.db"),
        )
        await store.save(record)

        async def update_state(state: AgentState) -> AgentState:
            await store.update_atomic(
                "concurrent-update",
                lambda rec: (
                    setattr(rec, "state", state),
                    setattr(rec, "state_changed_at", time.time()),
                ),
            )
            return state

        results = await asyncio.gather(
            update_state(AgentState.EXECUTING),
            update_state(AgentState.SUBMITTING),
            update_state(AgentState.REVIEWING),
        )

        assert set(results) == {AgentState.EXECUTING, AgentState.SUBMITTING, AgentState.REVIEWING}
        final = await store.load("concurrent-update")
        assert final is not None
        assert final.state in {AgentState.EXECUTING, AgentState.SUBMITTING, AgentState.REVIEWING}
    finally:
        await workspace.close()
