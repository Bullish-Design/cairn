from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import asyncio
import pytest

from cairn.runtime.agent import AgentState
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import InlineCodeProvider


async def _build_orchestrator(project_root: Path, cairn_home: Path) -> CairnOrchestrator:
    orch = CairnOrchestrator(
        project_root=project_root,
        cairn_home=cairn_home,
        code_provider=InlineCodeProvider(),
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
    project_root = tmp_path / "project"
    cairn_home = tmp_path / "home"
    project_root.mkdir(parents=True, exist_ok=True)

    orch = await _build_orchestrator(project_root, cairn_home)
    await _cancel_worker(orch)

    try:
        agent_id = await orch.spawn_agent("task")
        ctx = orch.active_agents[agent_id]
        await orch._transition_agent_state(ctx, AgentState.EXECUTING)
    finally:
        await orch.shutdown()

    restored = await _build_orchestrator(project_root, cairn_home)
    await _cancel_worker(restored)

    try:
        assert agent_id in restored.active_agents
        ctx = restored.active_agents[agent_id]
        assert ctx.state is AgentState.EXECUTING
    finally:
        await restored.shutdown()
