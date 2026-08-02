from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from cairn.orchestrator.orchestrator import CairnOrchestrator
from cairn.providers.providers import InlineCodeProvider
from cairn.runtime.agent import AgentState
from cairn.runtime.sandbox import SandboxExecutionError, SandboxResult
from cairn.runtime.settings import OrchestratorSettings


class StubExecutor:
    def __init__(self, filename: str, summary: str, *, should_fail: bool = False, **kwargs: object) -> None:
        self.filename = filename
        self.summary = summary
        self.should_fail = should_fail
        self.workdir: Path | None = kwargs.get("workdir")  # type: ignore[assignment]
        self.agent_fs: object | None = kwargs.get("agent_fs")

    async def run(self, *, code: str, task: str) -> SandboxResult:
        _ = code
        _ = task
        if self.should_fail:
            raise SandboxExecutionError("script failed", error_code="SANDBOX_EXECUTION_FAILED")
        assert self.workdir is not None
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / self.filename).write_text("hello", encoding="utf-8")
        cairn_dir = self.workdir / ".cairn"
        cairn_dir.mkdir(parents=True, exist_ok=True)
        (cairn_dir / "submission.json").write_text(
            json.dumps({"summary": self.summary, "changed_files": [self.filename], "submitted_at": 1.0}),
            encoding="utf-8",
        )
        assert self.agent_fs is not None
        await self.agent_fs.files.write(self.filename, "hello")  # type: ignore[union-attr]
        return SandboxResult(
            submission={"summary": self.summary, "changed_files": [self.filename], "submitted_at": 1.0},
            changes={"written": [self.filename], "deleted": []},
            log="",
        )


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


def _build_orchestrator(tmp_path: Path, executor_factory=None) -> CairnOrchestrator:
    orch = CairnOrchestrator(
        project_root=tmp_path / "project",
        cairn_home=tmp_path / "cairn-home",
        config=OrchestratorSettings(max_concurrent_agents=1),
        code_provider=InlineCodeProvider(),
        executor_factory=executor_factory,
    )
    orch.project_root.mkdir(parents=True, exist_ok=True)
    return orch


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complete_agent_lifecycle_accept(tmp_path: Path) -> None:
    orch = _build_orchestrator(
        tmp_path,
        executor_factory=lambda **kwargs: StubExecutor("hello.py", "done", **kwargs),
    )
    await orch.initialize()

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
async def test_agent_rejection_workflow(tmp_path: Path) -> None:
    orch = _build_orchestrator(
        tmp_path,
        executor_factory=lambda **kwargs: StubExecutor("note.txt", "done", **kwargs),
    )
    await orch.initialize()

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
async def test_multiple_agents_processed_sequentially(tmp_path: Path) -> None:
    orch = _build_orchestrator(
        tmp_path,
        executor_factory=lambda **kwargs: StubExecutor("file.txt", "done", **kwargs),
    )
    await orch.initialize()

    try:
        agent_ids = [await orch.spawn_agent(f"task-{i}") for i in range(3)]

        for agent_id in agent_ids:
            await _wait_for_state(orch, agent_id, {AgentState.REVIEWING})

        assert set(agent_ids) == set(orch.active_agents.keys())
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_agent_error_transitions_to_errored(tmp_path: Path) -> None:
    orch = _build_orchestrator(
        tmp_path,
        executor_factory=lambda **kwargs: StubExecutor("boom.py", "fail", should_fail=True, **kwargs),
    )
    await orch.initialize()

    try:
        agent_id = await orch.spawn_agent("raise")
        await _wait_for_state(orch, agent_id, {AgentState.ERRORED})
        ctx = orch.active_agents.get(agent_id)
        assert ctx is not None
        assert "script failed" in (ctx.error or "")
    finally:
        await orch.shutdown()
