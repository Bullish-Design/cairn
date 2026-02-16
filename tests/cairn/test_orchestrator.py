from __future__ import annotations

from pathlib import Path

import pytest
from fsdantic import Fsdantic

from cairn.agent import AgentContext, AgentState
from cairn.lifecycle import LifecycleStore
from cairn.orchestrator import CairnOrchestrator
from cairn.queue import TaskPriority


class StubCodeGenerator:
    async def generate(self, task: str) -> str:
        _ = task
        return "x = 1"


class SuccessfulMonty:
    def __init__(self, *, input_model, tools, limits):
        self.tools = {tool.__name__: tool for tool in tools}

    async def execute_async(self, code: str, payload: dict) -> None:
        _ = code
        _ = payload
        await self.tools["write_file"]("generated.txt", "from monty")
        await self.tools["submit_result"]("ok", ["generated.txt"])


class FailingMonty:
    def __init__(self, *, input_model, tools, limits):
        _ = input_model
        _ = tools
        _ = limits

    async def execute_async(self, code: str, payload: dict) -> None:
        _ = code
        _ = payload
        raise RuntimeError("execution failed")


async def _setup_orchestrator(tmp_path: Path) -> tuple[CairnOrchestrator, object, object, object]:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        code_generator=StubCodeGenerator(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    orch.cairn_home.mkdir(parents=True, exist_ok=True)
    (orch.cairn_home / "workspaces").mkdir(parents=True, exist_ok=True)
    orch.agentfs_dir.mkdir(parents=True, exist_ok=True)

    stable = await Fsdantic.open(path=str(tmp_path / "stable.db"))
    bin_ws = await Fsdantic.open(path=str(tmp_path / "bin.db"))
    agent_ws = await Fsdantic.open(path=str(tmp_path / "agent.db"))

    orch.stable = stable
    orch.bin = bin_ws
    orch.lifecycle = LifecycleStore(bin_ws)

    return orch, stable, bin_ws, agent_ws


@pytest.mark.asyncio
async def test_run_agent_transitions_to_reviewing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orch, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    monkeypatch.setattr("cairn.orchestrator.MontyContext", SuccessfulMonty)

    ctx = AgentContext(
        agent_id="agent-success",
        task="create file",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await orch._run_agent(ctx.agent_id)
        assert ctx.state is AgentState.REVIEWING
        assert ctx.submission is not None
        assert ctx.submission["summary"] == "ok"

        preview_file = orch.cairn_home / "workspaces" / ctx.agent_id / "generated.txt"
        assert preview_file.read_text(encoding="utf-8") == "from monty"
    finally:
        await agent_ws.close()
        await bin_ws.close()
        await stable.close()


@pytest.mark.asyncio
async def test_run_agent_transitions_to_errored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orch, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    monkeypatch.setattr("cairn.orchestrator.MontyContext", FailingMonty)

    ctx = AgentContext(
        agent_id="agent-fail",
        task="explode",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await orch._run_agent(ctx.agent_id)
        assert ctx.state is AgentState.ERRORED
        assert "execution failed" in (ctx.error or "")
    finally:
        await agent_ws.close()
        await bin_ws.close()
        await stable.close()
