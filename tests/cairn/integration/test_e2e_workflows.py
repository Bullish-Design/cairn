from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from cairn.runtime.agent import AgentState
from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import InlineCodeProvider
from cairn.runtime.settings import OrchestratorSettings


class CheckResult:
    def __init__(self, valid: bool, errors: list[str] | None = None) -> None:
        self.valid = valid
        self.errors = errors or []


class StubScript:
    def __init__(self, filename: str, summary: str, *, should_fail: bool = False) -> None:
        self.filename = filename
        self.summary = summary
        self.should_fail = should_fail

    def check(self) -> CheckResult:
        return CheckResult(True)

    async def run(self, *, inputs: dict, externals: dict[str, object]) -> None:
        _ = inputs
        if self.should_fail:
            raise RuntimeError("script failed")
        tools = externals
        await tools["write_file"](self.filename, "hello")
        await tools["submit_result"](self.summary, [self.filename])


async def _wait_for_state(
    orch: CairnOrchestrator,
    agent_id: str,
    states: set[AgentState],
    *,
    timeout: float = 5.0,
) -> AgentState:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ctx = orch.active_agents.get(agent_id)
        if ctx and ctx.state in states:
            return ctx.state
        if orch.lifecycle is not None:
            record = await orch.lifecycle.load(agent_id)
            if record and record.state in states:
                return record.state
        await asyncio.sleep(0.05)
    pytest.fail(f"Agent {agent_id} did not reach state {states}")


async def _build_orchestrator(tmp_path: Path) -> CairnOrchestrator:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        config=OrchestratorSettings(max_concurrent_agents=1),
        code_provider=InlineCodeProvider(),
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    await orch.initialize()
    return orch


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complete_agent_lifecycle_accept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orch = await _build_orchestrator(tmp_path)
    script = StubScript("hello.py", "done")
    monkeypatch.setattr("cairn.orchestrator.orchestrator._load_grail_script", lambda _: script)

    try:
        agent_id = await orch.spawn_agent("x = 1")
        await _wait_for_state(orch, agent_id, {AgentState.REVIEWING})

        preview_file = orch.cairn_home / "workspaces" / agent_id / "hello.py"
        assert preview_file.exists()
        assert preview_file.read_text(encoding="utf-8") == "hello"

        await orch.accept_agent(agent_id)

        assert orch.stable is not None
        assert await orch.stable.files.read("hello.py") == "hello"
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_agent_rejection_workflow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orch = await _build_orchestrator(tmp_path)
    monkeypatch.setattr(
        "cairn.orchestrator.orchestrator._load_grail_script",
        lambda _: StubScript("note.txt", "done"),
    )

    try:
        agent_id = await orch.spawn_agent("pass")
        await _wait_for_state(orch, agent_id, {AgentState.REVIEWING})

        await orch.reject_agent(agent_id)

        assert orch.lifecycle is not None
        record = await orch.lifecycle.load(agent_id)
        assert record is not None
        assert record.state is AgentState.REJECTED

        preview_dir = orch.cairn_home / "workspaces" / agent_id
        assert not preview_dir.exists()
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_multiple_agents_processed_sequentially(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orch = await _build_orchestrator(tmp_path)
    monkeypatch.setattr(
        "cairn.orchestrator.orchestrator._load_grail_script",
        lambda _: StubScript("file.txt", "done"),
    )

    try:
        agent_ids = [await orch.spawn_agent(f"task-{i}") for i in range(3)]

        for agent_id in agent_ids:
            await _wait_for_state(orch, agent_id, {AgentState.REVIEWING})

        assert set(agent_ids) == set(orch.active_agents.keys())
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_agent_error_transitions_to_errored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orch = await _build_orchestrator(tmp_path)
    monkeypatch.setattr(
        "cairn.orchestrator.orchestrator._load_grail_script", lambda _: StubScript("boom.py", "fail", should_fail=True)
    )

    try:
        agent_id = await orch.spawn_agent("raise")
        await _wait_for_state(orch, agent_id, {AgentState.ERRORED})
        ctx = orch.active_agents.get(agent_id)
        assert ctx is not None
        assert "script failed" in (ctx.error or "")
    finally:
        await orch.shutdown()
