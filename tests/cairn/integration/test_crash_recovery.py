from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import asyncio
import pytest

from cairn.runtime.agent import AgentState
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import InlineCodeProvider
from cairn.runtime.settings import OrchestratorSettings


async def _build_orchestrator(project_root: Path, cairn_home: Path) -> CairnOrchestrator:
    """Build an orchestrator whose worker is NOT auto-started, so tests can
    simulate a daemon crash deterministically (recovery state is inspectable
    before any re-run)."""
    orch = CairnOrchestrator(
        project_root=project_root,
        cairn_home=cairn_home,
        code_provider=InlineCodeProvider(),
        config=OrchestratorSettings(start_worker_on_init=False),
    )
    await orch.initialize()
    return orch


async def _cancel_worker(orch: CairnOrchestrator) -> None:
    if orch._worker_task and not orch._worker_task.done():
        orch._worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await orch._worker_task


@pytest.mark.asyncio
@pytest.mark.integration
async def test_orchestrator_recovers_queued_agents(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    cairn_home = tmp_path / "home"
    project_root.mkdir(parents=True, exist_ok=True)

    orch = await _build_orchestrator(project_root, cairn_home)
    await _cancel_worker(orch)

    try:
        agent_id = await orch.spawn_agent("task")
    finally:
        await orch.shutdown()

    restored = await _build_orchestrator(project_root, cairn_home)
    await _cancel_worker(restored)

    try:
        assert agent_id in restored.active_agents
        ctx = restored.active_agents[agent_id]
        assert ctx.state is AgentState.QUEUED
        assert restored.queue.size() == 1
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_orchestrator_recovers_in_progress_state(tmp_path: Path) -> None:
    """P4.1: an agent interrupted mid-run is failed explicitly on restart,
    not stranded in a state nothing can resolve."""
    project_root = tmp_path / "project"
    cairn_home = tmp_path / "home"
    project_root.mkdir(parents=True, exist_ok=True)

    orch = await _build_orchestrator(project_root, cairn_home)
    await _cancel_worker(orch)

    try:
        agent_id = await orch.spawn_agent("task")
        ctx = orch.active_agents[agent_id]
        await orch._transition_agent_state(ctx, AgentState.GENERATING)
        await orch._transition_agent_state(ctx, AgentState.EXECUTING)
    finally:
        await orch.shutdown()

    restored = await _build_orchestrator(project_root, cairn_home)
    await _cancel_worker(restored)

    try:
        assert agent_id in restored.active_agents
        ctx = restored.active_agents[agent_id]
        assert ctx.state is AgentState.ERRORED
        assert "Interrupted by orchestrator restart" in (ctx.error or "")
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_orchestrator_recovers_in_progress_state_to_errored(tmp_path: Path) -> None:
    """P4.1: an agent interrupted mid-run is failed explicitly on restart, not
    stranded in a state nothing can resolve."""
    project_root = tmp_path / "project"
    cairn_home = tmp_path / "home"
    project_root.mkdir(parents=True, exist_ok=True)

    orch = await _build_orchestrator(project_root, cairn_home)
    await _cancel_worker(orch)

    try:
        agent_id = await orch.spawn_agent("task")
        ctx = orch.active_agents[agent_id]
        await orch._transition_agent_state(ctx, AgentState.GENERATING)
        await orch._transition_agent_state(ctx, AgentState.EXECUTING)
    finally:
        await orch.shutdown()

    restored = await _build_orchestrator(project_root, cairn_home)
    await _cancel_worker(restored)

    try:
        assert agent_id in restored.active_agents
        ctx = restored.active_agents[agent_id]
        assert ctx.state is AgentState.ERRORED
        assert "Interrupted by orchestrator restart" in (ctx.error or "")

        # The recovered agent can be cleaned up via reject.
        await restored.reject_agent(agent_id)
        record = await restored.lifecycle.load(agent_id)
        assert record is not None
        assert record.state is AgentState.REJECTED
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_orchestrator_requeues_interrupted_when_opted_in(tmp_path: Path) -> None:
    """P4.1: requeue_interrupted re-queues the task as a fresh agent."""
    from cairn.runtime.settings import OrchestratorSettings

    project_root = tmp_path / "project"
    cairn_home = tmp_path / "home"
    project_root.mkdir(parents=True, exist_ok=True)

    orch = CairnOrchestrator(
        project_root=project_root,
        cairn_home=cairn_home,
        code_provider=InlineCodeProvider(),
        config=OrchestratorSettings(requeue_interrupted=True, start_worker_on_init=False),
    )
    await orch.initialize()

    try:
        agent_id = await orch.spawn_agent("task")
        ctx = orch.active_agents[agent_id]
        await orch._transition_agent_state(ctx, AgentState.GENERATING)
        await orch._transition_agent_state(ctx, AgentState.EXECUTING)
    finally:
        await orch.shutdown()

    restored = CairnOrchestrator(
        project_root=project_root,
        cairn_home=cairn_home,
        code_provider=InlineCodeProvider(),
        config=OrchestratorSettings(requeue_interrupted=True),
    )
    await restored.initialize()
    await _cancel_worker(restored)

    try:
        # Original agent failed explicitly...
        original = restored.active_agents[agent_id]
        assert original.state is AgentState.ERRORED
        # ...and a fresh agent was queued for the same task.
        queued = [c for c in restored.active_agents.values() if c.state is AgentState.QUEUED]
        assert len(queued) == 1
        assert queued[0].task == "task"
    finally:
        await restored.shutdown()
