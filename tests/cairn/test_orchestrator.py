from __future__ import annotations

from pathlib import Path

import grail
import pytest
from fsdantic import Fsdantic

from cairn.agent import AgentContext, AgentState
from cairn.lifecycle import LifecycleStore
from cairn.orchestrator import CairnOrchestrator
from cairn.queue import TaskPriority


class StubCodeProvider:
    def __init__(self, code: str = "x = 1", is_valid: bool = True, error: str | None = None) -> None:
        self.code = code
        self.is_valid = is_valid
        self.error = error
        self.context: dict | None = None
        self.reference: str | None = None

    async def get_code(self, reference: str, context: dict) -> str:
        self.reference = reference
        self.context = context
        return self.code

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        _ = code
        return self.is_valid, self.error


class CheckResult:
    def __init__(self, valid: bool, errors: list[str] | None = None) -> None:
        self.valid = valid
        self.errors = errors or []


class SuccessfulScript:
    def check(self) -> CheckResult:
        return CheckResult(True)

    async def run(self, *, inputs: dict, externals: list[object]) -> None:
        tools = {tool.__name__: tool for tool in externals}
        assert inputs["task_description"] == "create file"
        await tools["write_file"]("generated.txt", "from grail")
        await tools["submit_result"]("ok", ["generated.txt"])


class FailingScript:
    def check(self) -> CheckResult:
        return CheckResult(True)

    async def run(self, *, inputs: dict, externals: list[object]) -> None:
        _ = inputs
        _ = externals
        raise grail.ExecutionError("execution failed")


class InvalidScript:
    def check(self) -> CheckResult:
        return CheckResult(False, ["invalid code"])

    async def run(self, *, inputs: dict, externals: list[object]) -> None:
        raise AssertionError("run should not be called")


async def _setup_orchestrator(
    tmp_path: Path, code_provider: StubCodeProvider | None = None
) -> tuple[CairnOrchestrator, object, object, object]:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        code_provider=code_provider or StubCodeProvider(),
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
    provider = StubCodeProvider()
    orch, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path, provider)

    monkeypatch.setattr("cairn.orchestrator.grail.load", lambda _: SuccessfulScript())

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
        assert preview_file.read_text(encoding="utf-8") == "from grail"

        pym_file = orch.project_root / ".grail" / "agents" / ctx.agent_id / "task.pym"
        assert pym_file.read_text(encoding="utf-8") == "x = 1"
        assert provider.reference == ctx.task
        assert provider.context is not None
        assert provider.context["agent_id"] == ctx.agent_id
        assert provider.context["workspace"] is agent_ws
        assert provider.context["stable"] is stable
    finally:
        await agent_ws.close()
        await bin_ws.close()
        await stable.close()


@pytest.mark.asyncio
async def test_run_agent_transitions_to_errored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orch, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    monkeypatch.setattr("cairn.orchestrator.grail.load", lambda _: FailingScript())

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


@pytest.mark.asyncio
async def test_run_agent_provider_validation_failure_transitions_to_errored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = StubCodeProvider(is_valid=False, error="provider validation failed")
    orch, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path, provider)

    def _raise(_: str) -> object:
        raise AssertionError("grail.load should not be called")

    monkeypatch.setattr("cairn.orchestrator.grail.load", _raise)

    ctx = AgentContext(
        agent_id="agent-provider-invalid",
        task="bad provider",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await orch._run_agent(ctx.agent_id)
        assert ctx.state is AgentState.ERRORED
        assert "provider validation failed" in (ctx.error or "")
    finally:
        await agent_ws.close()
        await bin_ws.close()
        await stable.close()


@pytest.mark.asyncio
async def test_run_agent_validation_failure_transitions_to_errored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orch, stable, bin_ws, agent_ws = await _setup_orchestrator(tmp_path)

    monkeypatch.setattr("cairn.orchestrator.grail.load", lambda _: InvalidScript())

    ctx = AgentContext(
        agent_id="agent-invalid",
        task="bad code",
        priority=TaskPriority.NORMAL,
        state=AgentState.QUEUED,
        agent_fs=agent_ws,
    )
    orch.active_agents[ctx.agent_id] = ctx

    try:
        await orch._run_agent(ctx.agent_id)
        assert ctx.state is AgentState.ERRORED
        assert "Grail validation failed" in (ctx.error or "")
        assert "invalid code" in (ctx.error or "")
    finally:
        await agent_ws.close()
        await bin_ws.close()
        await stable.close()
